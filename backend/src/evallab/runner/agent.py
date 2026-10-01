from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Any

from evallab import telemetry
from evallab.domain.lifecycle import RunStatus
from evallab.runner.accounting import ModelAccounting
from evallab.runner.contracts import (
    Action,
    AgentSnapshot,
    BudgetRemaining,
    EvidenceRef,
    FinalAnswer,
    ModelCall,
    ModelGateway,
    ModelObservation,
    Observation,
    PatternObservation,
    PlanCreated,
    RunContext,
    RunResult,
    SkipPlanStep,
    ToolCall,
    ToolGateway,
    ToolOutcome,
    Usage,
)
from evallab.runner.errors import (
    InvalidLimitsError,
    ModelNotAllowedError,
    ReplayNotImplementedError,
    RunnerError,
    UnsupportedPatternError,
)
from evallab.runner.limits import Limits
from evallab.runner.models import ModelCallError, ModelRequest, ModelSnapshot, max_call_cost
from evallab.runner.patterns import adapter_for
from evallab.runner.replay import ReplayMismatchError, ReplayToolGateway
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
    value: int | str
    used: int | str


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
        self.accounting = ModelAccounting()
        self.model_turns: dict[str, int] = {}
        self.providers: tuple[str, ...] = ()

    @property
    def usage(self) -> Usage:
        return Usage(
            model_calls=self.model_calls,
            tool_calls=self.tool_calls,
            steps=self.steps,
            retries=self.retries,
            tokens=self.accounting.tokens_json(),
            cost=self.accounting.cost_json(),
            by_role=self.accounting.by_role_json(),
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
            max_model_calls=self.limits.max_model_calls,
            model_calls_used=self.model_calls,
            max_tokens=self.limits.max_tokens,
            tokens_used=self.accounting.known_tokens,
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
            providers=self.providers,
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

    def before_model_call(
        self, model: ModelSnapshot | None = None, request: ModelRequest | None = None
    ) -> _Stop | None:
        maximum = self.limits.max_model_calls
        if maximum is not None and self.model_calls >= maximum:
            return _Stop("max_model_calls", maximum, self.model_calls)
        tokens = self.limits.max_tokens
        # Sólo se puede cortar sobre tokens conocidos: es una cota inferior del consumo real.
        if tokens is not None and self.accounting.known_tokens >= tokens:
            return _Stop("max_tokens", tokens, self.accounting.known_tokens)
        budget = self.limits.max_cost_usd
        if budget is not None and model is not None and request is not None:
            reservation = max_call_cost(request, model)
            if reservation is None:
                raise InvalidLimitsError("límite monetario sin price snapshot del modelo")
            committed = self.accounting.committed_cost()
            if committed + reservation > budget:
                return _Stop("max_cost_usd", format(budget, "f"), format(committed, "f"))
        return self._deadline()

    def end_step(
        self,
        step_id: str,
        started: MemoryEvent,
        status: str,
        role: str = "executor",
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.sink.append(
            "step.completed",
            role,
            {"step_id": step_id, "role": role, "status": status, **(extra or {})},
            parent_event_id=started.event_id,
        )

    def record_tool(
        self, call: ToolCall, tools: ToolGateway, step_event: MemoryEvent
    ) -> Observation | _Stop:
        stopped = self.before_tool_call()
        if stopped is not None:
            return stopped
        call_id = uuid.uuid4()
        with telemetry.span(
            "tool.invoke",
            **{
                "evallab.run_id": str(self.context.run_id),
                "evallab.attempt_id": str(self.context.attempt_id),
                "evallab.tool.name": call.tool,
                "evallab.tool.call_id": str(call_id),
            },
        ) as current:
            recorded = self._record_tool(call, call_id, tools, step_event)
            if isinstance(recorded, Observation):
                telemetry.set_attributes(
                    current,
                    **{
                        "evallab.tool.status": recorded.status,
                        "evallab.tool.error_class": recorded.error_class,
                    },
                )
                if recorded.status != "completed":
                    telemetry.mark_error(current, recorded.status)
            return recorded

    def _record_tool(
        self, call: ToolCall, call_id: uuid.UUID, tools: ToolGateway, step_event: MemoryEvent
    ) -> Observation | _Stop:
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
        outcome = self._invoke(tools, call, call_id, requested, attempt)
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
            outcome = self._invoke(tools, call, call_id, requested, attempt)
            self._emit_outcome(call, call_id, requested, outcome, attempt, retries_left)
        completed = outcome.kind == "completed" and bool(self.evidence)
        return Observation(
            call_id=call_id,
            tool=call.tool,
            status=outcome.kind,
            result=outcome.result,
            error_class=outcome.error_class,
            evidence_id=self.evidence[-1].event_id if completed else None,
            reason_codes=outcome.reason_codes,
        )

    def record_model(
        self,
        request: ModelRequest,
        gateway: ModelGateway,
        step_event: MemoryEvent,
        scenario_slug: str | None,
    ) -> ModelObservation | _Stop:
        """Ejecuta una llamada a modelo con límites previos, retries trazados y contabilidad."""
        model = gateway.models().get(request.role)
        stopped = self.before_model_call(model, request)
        if stopped is not None:
            return stopped
        role = request.role
        turn = self.model_turns.get(role, 0)
        requested = self.sink.append(
            "model.requested",
            role,
            {
                "request_digest": request.digest(),
                "model_config_ref": model.ref() if model is not None else None,
                "input": request.as_json(),
                "tools": [tool.name for tool in request.tools],
                "turn": turn,
            },
            parent_event_id=step_event.event_id,
        )
        attempt = 1
        retries_left = self.limits.retries_per_call
        while True:
            self.model_turns[role] = self.model_turns.get(role, 0) + 1
            call = replace(
                request,
                metadata={
                    **request.metadata,
                    "scenario_slug": scenario_slug,
                    "turn": self.model_turns[role] - 1,
                },
            )
            reservation = max_call_cost(call, model) if model is not None else None
            with telemetry.span(
                "model.generate",
                **{
                    "evallab.run_id": str(self.context.run_id),
                    "evallab.model.role": role,
                    "evallab.model.provider": model.provider if model else None,
                    "evallab.model.requested": model.requested_model if model else None,
                    "evallab.model.attempt": attempt,
                },
            ) as current:
                try:
                    result = gateway.generate(call)
                except ReplayMismatchError as exc:
                    # Toda llamada iniciada queda con resultado explícito, también la divergente.
                    telemetry.mark_error(current, exc.error_class)
                    self.sink.append(
                        "model.failed",
                        role,
                        {
                            "request_event_id": str(requested.event_id),
                            "attempt": attempt,
                            "error_class": exc.error_class,
                            "kind": exc.error_class,
                            "error": exc.message,
                            "retriable": False,
                        },
                        parent_event_id=requested.event_id,
                    )
                    raise
                except ModelCallError as exc:
                    self.model_calls += 1
                    self.accounting.record_failure(role)
                    telemetry.mark_error(current, exc.kind)
                    self.sink.append(
                        "model.failed",
                        role,
                        {
                            "request_event_id": str(requested.event_id),
                            "attempt": attempt,
                            "error_class": exc.error_class,
                            "kind": exc.kind,
                            "error": exc.message,
                            "retriable": exc.retriable,
                        },
                        parent_event_id=requested.event_id,
                    )
                    if not exc.retriable or retries_left == 0:
                        raise
                    stopped = self.before_model_call(model, request)
                    if stopped is not None:
                        return stopped
                    attempt += 1
                    retries_left -= 1
                    self.retries += 1
                    self.sink.append(
                        "retry.scheduled",
                        "harness",
                        {
                            "origin_call_id": str(requested.event_id),
                            "attempt_number": attempt,
                            "reason": exc.kind,
                            "delay_ms": 0,
                        },
                        parent_event_id=requested.event_id,
                    )
                    continue
                self.model_calls += 1
                self.accounting.record_result(result, reservation)
                budget = self.limits.max_cost_usd
                if budget is not None and self.accounting.committed_cost() > budget:
                    # Contabilización tardía: el coste real superó el límite tras llamar.
                    self.accounting.overrun = True
                response = result.response
                telemetry.set_attributes(
                    current,
                    **{
                        "evallab.model.revision": result.revision,
                        "evallab.model.finish_reason": response.finish_reason,
                    },
                )
                completed = self.sink.append(
                    "model.completed",
                    role,
                    {
                        "request_event_id": str(requested.event_id),
                        "attempt": attempt,
                        "output": {
                            "content": response.content,
                            "tool_calls": [call.as_json() for call in response.tool_calls],
                        },
                        "usage": response.usage.as_json(),
                        "cost": result.cost.as_json(),
                        "resolved_model": result.revision,
                        "revision_status": result.revision_status,
                        "finish_reason": response.finish_reason,
                        "provider_request_id": response.provider_request_id,
                    },
                    parent_event_id=requested.event_id,
                )
                return ModelObservation(
                    role=role,
                    content=response.content,
                    tool_calls=response.tool_calls,
                    finish_reason=response.finish_reason,
                    event_id=completed.event_id,
                )

    def record_plan(self, plan: PlanCreated, step_event: MemoryEvent) -> None:
        self.sink.append(
            "plan.created",
            "planner",
            {
                "steps": [step.as_json() for step in plan.steps],
                "source_event_id": None
                if plan.source_event_id is None
                else str(plan.source_event_id),
            },
            parent_event_id=step_event.event_id,
        )

    def _invoke(
        self,
        tools: ToolGateway,
        call: ToolCall,
        call_id: uuid.UUID,
        requested: MemoryEvent,
        attempt: int,
    ) -> ToolOutcome:
        try:
            return tools.invoke(call.tool, call_id, call.arguments)
        except ReplayMismatchError as exc:
            # Toda llamada iniciada queda con resultado explícito, también la divergente.
            self.sink.append(
                "tool.failed",
                "harness",
                {
                    "call_id": str(call_id),
                    "attempt": attempt,
                    "error": exc.message,
                    "error_class": exc.error_class,
                    "reason_codes": [exc.error_class],
                    "retriable": False,
                },
                parent_event_id=requested.event_id,
            )
            raise

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
    """Ejecuta el patrón dentro del span `agent.execute`; los eventos lo referencian."""
    with telemetry.span(
        "agent.execute",
        **{
            "evallab.run_id": str(context.run_id),
            "evallab.attempt_id": str(context.attempt_id),
            "evallab.run.mode": context.mode,
            "evallab.agent.pattern": agent.pattern,
            "evallab.agent.pattern_version": agent.pattern_version,
            "evallab.scenario_id": str(scenario.id),
            "evallab.scenario_version": scenario.version,
        },
    ) as current:
        result = _execute_agent(context, agent, scenario, model, tools, sink)
        telemetry.set_attributes(
            current,
            **{
                "evallab.run.status": str(result.status),
                "evallab.run.error_class": result.error_class,
                "evallab.trace.event_count": len(sink.events),
                "evallab.trace.completeness": sink.completeness,
            },
        )
        if result.error_class is not None:
            telemetry.mark_error(current, result.error_class)
        return result


def _execute_agent(
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

    started_payload: dict[str, Any] = {
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
    }
    models = model.models()
    if models:
        started_payload["models"] = [models[role].ref() for role in sorted(models)]
    started_event = sink.append("run.started", "harness", started_payload)
    run = _Run(sink, context, limits, agent.pattern, agent.pattern_version)
    run.providers = tuple(sorted({snapshot.provider for snapshot in models.values()}))
    if limits_error is not None:
        return run.fail(limits_error.error_class, limits_error.message)
    if context.mode == "replay":
        if not isinstance(tools, ReplayToolGateway):
            return run.fail(ReplayNotImplementedError.error_class, "replay sin grabación de origen")
        mismatch = tools.recording.start_mismatch(started_event.payload)
        if mismatch is not None:
            return run.fail(ReplayMismatchError.error_class, mismatch)
    try:
        adapter = adapter_for(agent, scenario, tools.allowed_tools())
    except RunnerError as exc:
        return run.fail(exc.error_class, exc.message)
    run.pattern, run.pattern_version = adapter.pattern, adapter.pattern_version
    return _loop(run, context, adapter, scenario, model, tools)


def _step_role(adapter: Any) -> str:
    role = getattr(adapter, "step_role", None)
    return str(role()) if callable(role) else "executor"


def _step_metadata(adapter: Any) -> dict[str, Any]:
    metadata = getattr(adapter, "step_metadata", None)
    return dict(metadata()) if callable(metadata) else {}


def _continues_step(adapter: Any) -> bool:
    """El patrón puede encadenar acciones en el mismo ciclo de decisión (p. ej. ReAct ejecuta
    las tool calls de una respuesta del modelo dentro del paso que la pidió)."""
    continues = getattr(adapter, "continues_step", None)
    return bool(continues()) if callable(continues) else False


def _replay_pending(tools: ToolGateway, model: ModelGateway) -> int:
    pending = tools.pending() if isinstance(tools, ReplayToolGateway) else 0
    model_pending = getattr(model, "pending", None)
    return pending + (int(model_pending()) if callable(model_pending) else 0)


def _loop(
    run: _Run,
    context: RunContext,
    adapter: Any,
    scenario: ScenarioPublicOut,
    model: ModelGateway,
    tools: ToolGateway,
) -> RunResult:
    sink = run.sink
    observations: list[PatternObservation] = []
    step: tuple[str, MemoryEvent, str] | None = None
    status = "completed"
    step_extra: dict[str, Any] = {}

    def close(final_status: str) -> None:
        nonlocal step, step_extra
        if step is not None:
            step_id, started, role = step
            run.end_step(step_id, started, final_status, role, step_extra)
            step = None
            step_extra = {}

    while True:
        if step is None or not _continues_step(adapter):
            close(status)
            stopped = run.before_step()
            if stopped is not None:
                return run.stop(stopped)
            run.steps += 1
            role = _step_role(adapter)
            step_id = f"s{run.steps}"
            started = sink.append(
                "step.started",
                role,
                {"step_id": step_id, "role": role, "status": "running", **_step_metadata(adapter)},
            )
            step = (step_id, started, role)
            status = "completed"
        _, started, _ = step
        try:
            action: Action = adapter.next_action(context, observations, run.remaining())
        except RunnerError as exc:
            close("failed")
            return run.fail(exc.error_class, exc.message)

        if isinstance(action, FinalAnswer):
            pending = _replay_pending(tools, model)
            if context.mode == "replay" and pending:
                close("failed")
                return run.fail(
                    ReplayMismatchError.error_class,
                    f"el replay terminó con {pending} llamadas grabadas sin emitir",
                )
            close("completed" if status == "completed" else status)
            return run.complete(action.output)
        if isinstance(action, ToolCall):
            try:
                recorded = run.record_tool(action, tools, started)
            except ReplayMismatchError as exc:
                close("failed")
                return run.fail(exc.error_class, exc.message)
            if isinstance(recorded, _Stop):
                close("interrupted")
                return run.stop(recorded)
            observations.append(recorded)
            if recorded.status != "completed":
                status = "failed"
            continue
        if isinstance(action, ModelCall):
            try:
                observed = run.record_model(action.request, model, started, scenario.slug)
            except ModelNotAllowedError as exc:
                close("failed")
                return run.fail(ModelNotAllowedError.error_class, exc.message)
            except (ModelCallError, ReplayMismatchError, InvalidLimitsError) as exc:
                close("failed")
                return run.fail(exc.error_class, exc.message)
            if isinstance(observed, _Stop):
                close("interrupted")
                return run.stop(observed)
            observations.append(observed)
            continue
        if isinstance(action, PlanCreated):
            run.record_plan(action, started)
            continue
        if isinstance(action, SkipPlanStep):
            # No se inicia ninguna llamada: el paso queda registrado como omitido y por qué.
            status = "skipped"
            step_extra = {
                "plan_step_id": action.plan_step_id,
                "reason": "dependency_failed",
                "failed_dependencies": list(action.failed_dependencies),
            }
            continue
        close("failed")
        return run.fail(UnsupportedPatternError.error_class, "acción de patrón no soportada")
