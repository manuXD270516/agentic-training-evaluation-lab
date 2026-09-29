from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from evallab.domain.lifecycle import RunStatus
from evallab.runner.contracts import (
    Action,
    AgentSnapshot,
    BudgetRemaining,
    EvidenceRef,
    FinalAnswer,
    ModelCall,
    ModelGateway,
    Observation,
    RunContext,
    RunResult,
    ToolCall,
    ToolGateway,
    ToolOutcome,
    Usage,
)
from evallab.runner.errors import (
    ModelNotAllowedError,
    ReplayNotImplementedError,
    RunnerError,
    UnsupportedPatternError,
)
from evallab.runner.limits import Limits
from evallab.runner.scripted import adapter_for
from evallab.runner.sink import MemoryEvent, MemoryTraceSink
from evallab.schemas import ScenarioPublicOut

PRIVATE_SCENARIO_FIELDS = (
    "expected",
    "oracle_ref",
    "qrels",
    "fault_schedule",
    "split",
    "family_id",
)
TOOL_ALLOWLIST_RULE = {"rule": "tool_allowlist", "version": "1.0.0"}


def _ref(agent: AgentSnapshot) -> dict[str, Any]:
    return {
        "id": str(agent.id),
        "version": agent.version,
        "content_hash": agent.content_hash,
        "pattern": agent.pattern,
        "pattern_version": agent.pattern_version,
    }


def _scenario_ref(scenario: ScenarioPublicOut) -> dict[str, Any]:
    return {
        "id": str(scenario.id),
        "version": scenario.version,
        "content_hash": scenario.content_hash,
    }


@dataclass(frozen=True)
class _Stop:
    """Límite alcanzado antes de iniciar un paso o una llamada; no se inicia la acción."""

    limit: str
    value: int
    used: int


class _Run:
    """Estado mutable de una ejecución: sink, límites, consumo, evidencia y violaciones."""

    def __init__(
        self,
        sink: MemoryTraceSink,
        context: RunContext,
        limits: Limits,
        pattern: str,
        pattern_version: str,
    ) -> None:
        self.sink = sink
        self.context = context
        self.limits = limits
        self.pattern = pattern
        self.pattern_version = pattern_version
        self.started = context.clock()
        self.steps = 0
        self.tool_calls = 0
        self.model_calls = 0
        self.retries = 0
        self.evidence: list[EvidenceRef] = []
        self.violations = 0

    @property
    def usage(self) -> Usage:
        return Usage(
            model_calls=self.model_calls,
            tool_calls=self.tool_calls,
            steps=self.steps,
            retries=self.retries,
        )

    def elapsed_ms(self) -> int:
        return max(0, int((self.context.clock() - self.started) * 1000))

    def remaining(self) -> BudgetRemaining:
        return BudgetRemaining(
            max_steps=self.limits.max_steps,
            steps_used=self.steps,
            max_tool_calls=self.limits.max_tool_calls,
            tool_calls_used=self.tool_calls,
            deadline_ms=self.limits.deadline_ms,
            elapsed_ms=self.elapsed_ms(),
        )

    def _terminal(self, **extra: Any) -> dict[str, Any]:
        return {
            **extra,
            "usage": self.usage.as_json(),
            "policy_violations": self.violations,
            "completeness": self.sink.completeness,
        }

    def _result(self, status: RunStatus, **fields: Any) -> RunResult:
        return RunResult(
            status=status,
            pattern=self.pattern,
            pattern_version=self.pattern_version,
            output=fields.pop("output", None),
            evidence_refs=tuple(self.evidence),
            usage=self.usage,
            completeness=self.sink.completeness,
            policy_violations=self.violations,
            **fields,
        )

    def fail(self, error_class: str, error: str) -> RunResult:
        self.sink.append(
            "run.failed", "harness", self._terminal(error=error, error_class=error_class)
        )
        return self._result(RunStatus.FAILED, error_class=error_class, error=error)

    def complete(self, output: Any) -> RunResult:
        self.sink.append("run.completed", "harness", self._terminal(output=output))
        return self._result(RunStatus.COMPLETED, output=output)

    def stop(self, stop: _Stop) -> RunResult:
        termination = {"limit": stop.limit, "limit_value": stop.value, "used": stop.used}
        if stop.limit == "deadline_ms":
            self.sink.append("run.timed_out", "harness", self._terminal(**termination))
            return self._result(RunStatus.TIMED_OUT, termination=termination)
        self.sink.append("run.budget_exceeded", "harness", self._terminal(**termination))
        return self._result(RunStatus.BUDGET_EXCEEDED, termination=termination)

    def _deadline(self) -> _Stop | None:
        deadline = self.limits.deadline_ms
        elapsed = self.elapsed_ms()
        if deadline is None or elapsed < deadline:
            return None
        return _Stop("deadline_ms", deadline, elapsed)

    def before_step(self) -> _Stop | None:
        timed_out = self._deadline()
        if timed_out is not None:
            return timed_out
        if self.limits.max_steps is not None and self.steps >= self.limits.max_steps:
            return _Stop("max_steps", self.limits.max_steps, self.steps)
        return None

    def before_tool_call(self) -> _Stop | None:
        if self.limits.max_tool_calls is not None and self.tool_calls >= self.limits.max_tool_calls:
            return _Stop("max_tool_calls", self.limits.max_tool_calls, self.tool_calls)
        return self._deadline()

    def before_model_call(self) -> _Stop | None:
        maximum = self.limits.max_model_calls
        if maximum is not None and self.model_calls >= maximum:
            return _Stop("max_model_calls", maximum, self.model_calls)
        return self._deadline()

    def end_step(self, step_id: str, started: MemoryEvent, status: str) -> None:
        self.sink.append(
            "step.completed",
            "executor",
            {"step_id": step_id, "role": "executor", "status": status},
            parent_event_id=started.event_id,
        )

    def record_tool(
        self, call: ToolCall, tools: ToolGateway, step_event: MemoryEvent
    ) -> Observation | _Stop:
        stopped = self.before_tool_call()
        if stopped is not None:
            return stopped
        call_id = uuid.uuid4()
        resolved = tools.resolve(call.tool)
        requested = self.sink.append(
            "tool.requested",
            "executor",
            {
                "call_id": str(call_id),
                "tool": call.tool,
                "version": resolved.version if resolved is not None else None,
                "arguments": call.arguments,
            },
            parent_event_id=step_event.event_id,
        )
        attempt = 1
        outcome = tools.invoke(call.tool, call_id, call.arguments)
        retries_left = self.limits.retries_per_call
        self._emit_outcome(call, call_id, requested, outcome, attempt, retries_left)
        while outcome.kind == "failed" and outcome.retriable and retries_left > 0:
            stopped = self.before_tool_call()
            if stopped is not None:
                return stopped
            attempt += 1
            retries_left -= 1
            self.retries += 1
            self.sink.append(
                "retry.scheduled",
                "harness",
                {
                    "origin_call_id": str(call_id),
                    "attempt_number": attempt,
                    "reason": outcome.error_class,
                    "delay_ms": 0,
                },
                parent_event_id=requested.event_id,
            )
            outcome = tools.invoke(call.tool, call_id, call.arguments)
            self._emit_outcome(call, call_id, requested, outcome, attempt, retries_left)
        return Observation(
            call_id=call_id,
            tool=call.tool,
            status=outcome.kind,
            result=outcome.result,
            error_class=outcome.error_class,
        )

    def _emit_outcome(
        self,
        call: ToolCall,
        call_id: uuid.UUID,
        requested: MemoryEvent,
        outcome: ToolOutcome,
        attempt: int,
        retries_left: int,
    ) -> None:
        parent = requested.event_id
        if outcome.kind == "denied":
            self.sink.append(
                "tool.denied",
                "harness",
                {
                    "call_id": str(call_id),
                    "schema_result": "not_checked",
                    "policy_result": "denied",
                    "reason_codes": list(outcome.reason_codes),
                },
                parent_event_id=parent,
            )
            self.sink.append(
                "policy.violation",
                "harness",
                {
                    **TOOL_ALLOWLIST_RULE,
                    "severity": "high",
                    "attempted": call.tool,
                    "executed": False,
                    "reason_codes": list(outcome.reason_codes),
                    "evidence_refs": [{"event_id": str(parent), "pointer": "/tool"}],
                },
                parent_event_id=parent,
            )
            self.violations += 1
            return
        if outcome.kind == "invalid":
            self.sink.append(
                "tool.denied",
                "harness",
                {
                    "call_id": str(call_id),
                    "schema_result": "invalid",
                    "policy_result": "allowed",
                    "reason_codes": list(outcome.reason_codes),
                    "schema_errors": [dict(e) for e in outcome.schema_errors],
                    "error_class": outcome.error_class,
                },
                parent_event_id=parent,
            )
            return
        if outcome.validated:
            if attempt == 1:
                self.sink.append(
                    "tool.validated",
                    "harness",
                    {
                        "call_id": str(call_id),
                        "schema_result": "valid",
                        "policy_result": "allowed",
                        "reason_codes": [],
                    },
                    parent_event_id=parent,
                )
            self.tool_calls += 1
        if outcome.kind == "failed":
            payload: dict[str, Any] = {
                "call_id": str(call_id),
                "attempt": attempt,
                "error": outcome.error,
                "error_class": outcome.error_class,
                "reason_codes": list(outcome.reason_codes),
                "retriable": outcome.retriable,
            }
            if outcome.retriable and retries_left == 0:
                payload["retries_exhausted"] = True
            if outcome.ambiguous_effect:
                payload["ambiguous_effect"] = True
                payload["reconciliation"] = "required"
            if outcome.fault_id is not None:
                payload["fault_id"] = outcome.fault_id
            if outcome.timeout_ms is not None:
                payload["timeout_ms"] = outcome.timeout_ms
            self.sink.append("tool.failed", "harness", payload, parent_event_id=parent)
            return
        event_id = uuid.uuid4()
        self.sink.append(
            "tool.completed",
            "executor",
            {
                "call_id": str(call_id),
                "attempt": attempt,
                "result": outcome.result,
                "evidence_ids": [str(event_id)],
                "state_digest": outcome.state_digest,
                "retriable": False,
                "idempotent_replay": outcome.idempotent_replay,
            },
            parent_event_id=parent,
            event_id=event_id,
        )
        self.evidence.append(EvidenceRef(event_id=event_id, pointer="/result"))


def execute_agent(
    context: RunContext,
    agent: AgentSnapshot,
    scenario: ScenarioPublicOut,
    model: ModelGateway,
    tools: ToolGateway,
    sink: MemoryTraceSink,
) -> RunResult:
    public = scenario.model_dump(mode="json")
    for leaked in PRIVATE_SCENARIO_FIELDS:
        if leaked in public:
            raise RuntimeError(f"ScenarioPublicView contiene campo privado {leaked}")

    limits_error: RunnerError | None = None
    try:
        limits = Limits.effective(context.limits)
    except RunnerError as exc:
        limits, limits_error = Limits(), exc

    sink.append(
        "run.started",
        "harness",
        {
            "manifest_hash": context.manifest_hash,
            "scenario_ref": _scenario_ref(scenario),
            "agent_ref": _ref(agent),
            "mode": context.mode,
            "limits": limits.as_json(),
            "seed": context.seed,
            "tools": [
                {"name": t.name, "version": t.version, "content_hash": t.content_hash}
                for t in tools.allowed_tools()
            ],
        },
    )
    run = _Run(sink, context, limits, agent.pattern, agent.pattern_version)
    if limits_error is not None:
        return run.fail(limits_error.error_class, limits_error.message)
    if context.mode != "live":
        return run.fail(ReplayNotImplementedError.error_class, "replay no implementado")
    try:
        adapter = adapter_for(agent)
    except RunnerError as exc:
        return run.fail(exc.error_class, exc.message)
    run.pattern, run.pattern_version = adapter.pattern, adapter.pattern_version

    observations: list[Observation] = []
    while True:
        stopped = run.before_step()
        if stopped is not None:
            return run.stop(stopped)
        run.steps += 1
        step_id = f"s{run.steps}"
        started = sink.append(
            "step.started",
            "executor",
            {"step_id": step_id, "role": "executor", "status": "running"},
        )
        try:
            action: Action = adapter.next_action(context, observations, run.remaining())
        except RunnerError as exc:
            run.end_step(step_id, started, "failed")
            return run.fail(exc.error_class, exc.message)

        if isinstance(action, FinalAnswer):
            run.end_step(step_id, started, "completed")
            return run.complete(action.output)
        if isinstance(action, ToolCall):
            recorded = run.record_tool(action, tools, started)
            if isinstance(recorded, _Stop):
                run.end_step(step_id, started, "interrupted")
                return run.stop(recorded)
            observations.append(recorded)
            run.end_step(
                step_id, started, "completed" if recorded.status == "completed" else "failed"
            )
            continue
        if isinstance(action, ModelCall):
            stopped = run.before_model_call()
            if stopped is not None:
                run.end_step(step_id, started, "interrupted")
                return run.stop(stopped)
            try:
                model.generate(action.messages, tools.allowed_tools())
            except ModelNotAllowedError as exc:
                message = exc.message
            else:
                message = "el patrón emitió una llamada a modelo"
            run.end_step(step_id, started, "failed")
            return run.fail(ModelNotAllowedError.error_class, message)
        run.end_step(step_id, started, "failed")
        return run.fail(UnsupportedPatternError.error_class, "acción de patrón no soportada")
