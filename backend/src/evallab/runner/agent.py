from __future__ import annotations

import uuid
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


class _Run:
    """Estado mutable de una ejecución: sink, consumo, evidencia y violaciones."""

    def __init__(self, sink: MemoryTraceSink, pattern: str, pattern_version: str) -> None:
        self.sink = sink
        self.pattern = pattern
        self.pattern_version = pattern_version
        self.usage = Usage(model_calls=0, tool_calls=0)
        self.evidence: list[EvidenceRef] = []
        self.violations = 0

    def fail(self, error_class: str, error: str) -> RunResult:
        self.sink.append(
            "run.failed",
            "harness",
            {
                "error": error,
                "error_class": error_class,
                "usage": self.usage.as_json(),
                "policy_violations": self.violations,
                "completeness": self.sink.completeness,
            },
        )
        return RunResult(
            status=RunStatus.FAILED,
            pattern=self.pattern,
            pattern_version=self.pattern_version,
            output=None,
            evidence_refs=tuple(self.evidence),
            usage=self.usage,
            error_class=error_class,
            error=error,
            completeness=self.sink.completeness,
            policy_violations=self.violations,
        )

    def complete(self, output: Any) -> RunResult:
        self.sink.append(
            "run.completed",
            "harness",
            {
                "output": output,
                "usage": self.usage.as_json(),
                "policy_violations": self.violations,
                "completeness": self.sink.completeness,
            },
        )
        return RunResult(
            status=RunStatus.COMPLETED,
            pattern=self.pattern,
            pattern_version=self.pattern_version,
            output=output,
            evidence_refs=tuple(self.evidence),
            usage=self.usage,
            completeness=self.sink.completeness,
            policy_violations=self.violations,
        )

    def end_step(self, step_id: str, started: MemoryEvent, status: str) -> None:
        self.sink.append(
            "step.completed",
            "executor",
            {"step_id": step_id, "role": "executor", "status": status},
            parent_event_id=started.event_id,
        )

    def record_tool(
        self, call: ToolCall, tools: ToolGateway, step_event: MemoryEvent
    ) -> Observation:
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
        outcome = tools.invoke(call.tool, call_id, call.arguments)
        self._emit_outcome(call, call_id, requested, outcome)
        return Observation(
            call_id=call_id,
            tool=call.tool,
            status=outcome.kind,
            result=outcome.result,
            error_class=outcome.error_class,
        )

    def _emit_outcome(
        self, call: ToolCall, call_id: uuid.UUID, requested: MemoryEvent, outcome: ToolOutcome
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
            self.usage = Usage(model_calls=0, tool_calls=self.usage.tool_calls + 1)
        if outcome.kind == "failed":
            self.sink.append(
                "tool.failed",
                "harness",
                {
                    "call_id": str(call_id),
                    "error": outcome.error,
                    "error_class": outcome.error_class,
                    "reason_codes": list(outcome.reason_codes),
                    "retriable": False,
                },
                parent_event_id=parent,
            )
            return
        event_id = uuid.uuid4()
        self.sink.append(
            "tool.completed",
            "executor",
            {
                "call_id": str(call_id),
                "result": outcome.result,
                "evidence_ids": [str(event_id)],
                "state_digest": outcome.state_digest,
                "retriable": False,
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

    sink.append(
        "run.started",
        "harness",
        {
            "manifest_hash": context.manifest_hash,
            "scenario_ref": _scenario_ref(scenario),
            "agent_ref": _ref(agent),
            "mode": context.mode,
            "limits": context.limits,
            "seed": context.seed,
            "tools": [
                {"name": t.name, "version": t.version, "content_hash": t.content_hash}
                for t in tools.allowed_tools()
            ],
        },
    )
    run = _Run(sink, agent.pattern, agent.pattern_version)
    if context.mode != "live":
        return run.fail(ReplayNotImplementedError.error_class, "replay no implementado")
    try:
        adapter = adapter_for(agent)
    except RunnerError as exc:
        return run.fail(exc.error_class, exc.message)
    run.pattern, run.pattern_version = adapter.pattern, adapter.pattern_version

    observations: list[Observation] = []
    step = 0
    while True:
        step += 1
        step_id = f"s{step}"
        started = sink.append(
            "step.started",
            "executor",
            {"step_id": step_id, "role": "executor", "status": "running"},
        )
        try:
            action: Action = adapter.next_action(context, observations, BudgetRemaining())
        except RunnerError as exc:
            run.end_step(step_id, started, "failed")
            return run.fail(exc.error_class, exc.message)

        if isinstance(action, FinalAnswer):
            run.end_step(step_id, started, "completed")
            return run.complete(action.output)
        if isinstance(action, ToolCall):
            observation = run.record_tool(action, tools, started)
            observations.append(observation)
            run.end_step(
                step_id, started, "completed" if observation.status == "completed" else "failed"
            )
            continue
        if isinstance(action, ModelCall):
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
