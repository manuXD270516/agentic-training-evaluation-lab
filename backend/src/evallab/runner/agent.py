from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from evallab.domain.lifecycle import RunStatus
from evallab.runner.contracts import (
    Action,
    AgentSnapshot,
    AllowedTool,
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
    Usage,
)
from evallab.runner.errors import (
    ModelNotAllowedError,
    ReplayNotImplementedError,
    RunnerError,
    ToolDeniedError,
    UnsupportedPatternError,
)
from evallab.runner.scripted import adapter_for
from evallab.runner.sink import MemoryTraceSink, mark_incomplete
from evallab.schemas import ScenarioPublicOut


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


def _fail(
    sink: MemoryTraceSink,
    *,
    pattern: str,
    pattern_version: str,
    error_class: str,
    error: str,
    usage: Usage,
    evidence: tuple[EvidenceRef, ...] = (),
    completeness: str = "complete",
    status: RunStatus = RunStatus.FAILED,
) -> RunResult:
    if completeness != "complete":
        mark_incomplete(sink)
    sink.append(
        "run.failed",
        "harness",
        {
            "error": error,
            "error_class": error_class,
            "usage": usage.as_json(),
            "completeness": completeness,
        },
    )
    return RunResult(
        status=status,
        pattern=pattern,
        pattern_version=pattern_version,
        output=None,
        evidence_refs=evidence,
        usage=usage,
        error_class=error_class,
        error=error,
        completeness=completeness,
    )


def execute_agent(
    context: RunContext,
    agent: AgentSnapshot,
    scenario: ScenarioPublicOut,
    allowed_tools: Sequence[AllowedTool],
    model: ModelGateway,
    tools: ToolGateway,
    sink: MemoryTraceSink,
) -> RunResult:
    del tools
    public = scenario.model_dump(mode="json")
    for leaked in ("expected", "oracle_ref", "qrels", "fault_schedule", "split", "family_id"):
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
        },
    )
    usage = Usage(model_calls=0, tool_calls=0)
    if context.mode != "live":
        return _fail(
            sink,
            pattern=agent.pattern,
            pattern_version=agent.pattern_version,
            error_class=ReplayNotImplementedError.error_class,
            error="replay no implementado",
            usage=usage,
        )
    try:
        adapter = adapter_for(agent)
    except RunnerError as exc:
        return _fail(
            sink,
            pattern=agent.pattern,
            pattern_version=agent.pattern_version,
            error_class=exc.error_class,
            error=exc.message,
            usage=usage,
        )

    by_name = {tool.name: tool for tool in allowed_tools}
    observations: list[Observation] = []
    evidence: list[EvidenceRef] = []
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
            sink.append(
                "step.completed",
                "executor",
                {"step_id": step_id, "role": "executor", "status": "failed"},
                parent_event_id=started.event_id,
            )
            return _fail(
                sink,
                pattern=adapter.pattern,
                pattern_version=adapter.pattern_version,
                error_class=exc.error_class,
                error=exc.message,
                usage=usage,
                evidence=tuple(evidence),
            )
        if isinstance(action, ModelCall):
            try:
                model.generate(action.messages, allowed_tools)
            except ModelNotAllowedError as exc:
                message = exc.message
            else:
                message = "el patrón emitió una llamada a modelo"
            sink.append(
                "step.completed",
                "executor",
                {"step_id": step_id, "role": "executor", "status": "failed"},
                parent_event_id=started.event_id,
            )
            return _fail(
                sink,
                pattern=adapter.pattern,
                pattern_version=adapter.pattern_version,
                error_class=ModelNotAllowedError.error_class,
                error=message,
                usage=usage,
                evidence=tuple(evidence),
            )
        if isinstance(action, FinalAnswer):
            sink.append(
                "step.completed",
                "executor",
                {"step_id": step_id, "role": "executor", "status": "completed"},
                parent_event_id=started.event_id,
            )
            usage_json = usage.as_json()
            sink.append(
                "run.completed",
                "harness",
                {
                    "output": action.output,
                    "usage": usage_json,
                    "completeness": "complete",
                },
            )
            return RunResult(
                status=RunStatus.COMPLETED,
                pattern=adapter.pattern,
                pattern_version=adapter.pattern_version,
                output=action.output,
                evidence_refs=tuple(evidence),
                usage=usage,
            )
        if not isinstance(action, ToolCall):
            sink.append(
                "step.completed",
                "executor",
                {"step_id": step_id, "role": "executor", "status": "failed"},
                parent_event_id=started.event_id,
            )
            return _fail(
                sink,
                pattern=adapter.pattern,
                pattern_version=adapter.pattern_version,
                error_class=UnsupportedPatternError.error_class,
                error="acción de patrón no soportada",
                usage=usage,
                evidence=tuple(evidence),
            )
        call_id = uuid.uuid4()
        requested = sink.append(
            "tool.requested",
            "executor",
            {
                "call_id": str(call_id),
                "tool": action.tool,
                "version": by_name[action.tool].version if action.tool in by_name else None,
                "arguments": action.arguments,
            },
            parent_event_id=started.event_id,
        )
        if action.tool not in by_name:
            sink.append(
                "tool.denied",
                "harness",
                {
                    "call_id": str(call_id),
                    "reason_codes": ["tool_not_allowed"],
                    "policy_result": "denied",
                },
                parent_event_id=requested.event_id,
            )
            sink.append(
                "step.completed",
                "executor",
                {"step_id": step_id, "role": "executor", "status": "failed"},
                parent_event_id=started.event_id,
            )
            return _fail(
                sink,
                pattern=adapter.pattern,
                pattern_version=adapter.pattern_version,
                error_class=ToolDeniedError.error_class,
                error=f"tool no permitida: {action.tool}",
                usage=usage,
                evidence=tuple(evidence),
            )
        sink.append(
            "tool.validated",
            "harness",
            {
                "call_id": str(call_id),
                "schema_result": "deferred",
                "policy_result": "allowlisted",
            },
            parent_event_id=requested.event_id,
        )
        completed = sink.append(
            "tool.completed",
            "executor",
            {
                "call_id": str(call_id),
                "result": action.result,
                "evidence_ids": [action.evidence_id] if action.evidence_id else [],
                "retriable": False,
            },
            parent_event_id=requested.event_id,
        )
        evidence.append(EvidenceRef(event_id=completed.event_id, pointer="/result"))
        usage = Usage(model_calls=0, tool_calls=usage.tool_calls + 1)
        observations.append(Observation(call_id=call_id, tool=action.tool, result=action.result))
        sink.append(
            "step.completed",
            "executor",
            {"step_id": step_id, "role": "executor", "status": "completed"},
            parent_event_id=started.event_id,
        )
