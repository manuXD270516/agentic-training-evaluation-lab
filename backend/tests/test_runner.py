import time
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from evallab.domain.lifecycle import RunStatus
from evallab.runner.agent import execute_agent
from evallab.runner.contracts import AgentSnapshot, AllowedTool, RunContext
from evallab.runner.errors import InvalidLimitsError, TraceIntegrityError
from evallab.runner.gateways import DeniedModelGateway
from evallab.runner.limits import Limits
from evallab.runner.sink import MemoryTraceSink
from evallab.runner.tools import FixtureToolGateway
from evallab.schemas import ScenarioPublicOut
from evallab.settings import SandboxPolicy
from tests.test_tools import ADD, binding

DIGEST = "a" * 64
ADD_STEP = {"type": "tool", "tool": "calculator", "arguments": ADD}
FINAL_STEP = {"type": "final", "output": {"total": 42}}


def _context(
    mode: str = "live",
    limits: dict[str, Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> RunContext:
    return RunContext(
        run_id=uuid.uuid4(),
        attempt_id=uuid.uuid4(),
        experiment_id=uuid.uuid4(),
        mode="replay" if mode == "replay" else "live",
        seed=11,
        started_at=datetime.now(UTC),
        limits=limits if limits is not None else {"max_steps": 4},
        sandbox=SandboxPolicy(),
        manifest_hash=DIGEST,
        experiment_budgets={"max_steps": 4},
        clock=clock,
    )


class SteppingClock:
    """Reloj monotónico simulado que avanza `step_s` en cada lectura."""

    def __init__(self, step_s: float) -> None:
        self.now = 0.0
        self.step_s = step_s

    def __call__(self) -> float:
        value = self.now
        self.now += self.step_s
        return value


def _scenario() -> ScenarioPublicOut:
    return ScenarioPublicOut(
        id=uuid.uuid4(),
        version="1.0.0",
        schema_version="1.0",
        slug="add-two-numbers",
        content_hash=DIGEST,
        primary_category="tool_selection",
        tags=[],
        difficulty="easy",
        task={"input": {"a": 40, "b": 2}},
        tools=[],
        environment={},
        limits={"max_steps": 4},
        created_at=datetime.now(UTC),
    )


def _agent(
    *script: dict[str, Any],
    pattern: str = "scripted",
    pattern_parameters: dict[str, Any] | None = None,
) -> AgentSnapshot:
    steps = list(script) or [ADD_STEP, FINAL_STEP]
    return AgentSnapshot(
        id=uuid.uuid4(),
        version="1.0.0",
        pattern=pattern,
        pattern_version="1.0.0",
        content_hash=DIGEST,
        pattern_parameters=(
            pattern_parameters if pattern_parameters is not None else {"script": steps}
        ),
        prompt_hash=None,
        roles=("executor",),
    )


def _sink(context: RunContext) -> MemoryTraceSink:
    return MemoryTraceSink(
        started_at=context.started_at,
        schema_version="1.0",
        run_id=context.run_id,
        attempt_id=context.attempt_id,
    )


def _tools() -> FixtureToolGateway:
    return FixtureToolGateway([binding()])


def _types(sink: MemoryTraceSink) -> list[str]:
    return [event.type for event in sink.events]


class RecordingModel:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, messages: Sequence[Any], tools: Sequence[AllowedTool]) -> Any:
        self.calls += 1
        raise AssertionError("scripted no debe llamar al modelo")


def test_scripted_run_emits_tool_evidence_without_model_calls() -> None:
    context = _context()
    model = RecordingModel()
    sink = _sink(context)
    result = execute_agent(context, _agent(), _scenario(), model, _tools(), sink)
    assert result.status == RunStatus.COMPLETED
    assert result.pattern == "scripted"
    assert (result.usage.model_calls, result.usage.tool_calls) == (0, 1)
    assert result.output == {"total": 42}
    assert result.policy_violations == 0
    assert model.calls == 0

    completed = next(e for e in sink.events if e.type == "tool.completed")
    assert completed.payload["result"] == {"total": 42}
    assert completed.payload["evidence_ids"] == [str(completed.event_id)]
    assert completed.payload["state_digest"]
    assert [ref.as_json() for ref in result.evidence_refs] == [
        {"event_id": str(completed.event_id), "pointer": "/result"}
    ]
    assert _types(sink)[-1] == "run.completed"


def test_script_steps_cannot_supply_their_own_results() -> None:
    context = _context()
    forged = {**ADD_STEP, "result": {"total": 42}}
    result = execute_agent(
        context,
        _agent(forged, FINAL_STEP),
        _scenario(),
        DeniedModelGateway(),
        _tools(),
        _sink(context),
    )
    assert (result.status, result.error_class) == (RunStatus.FAILED, "invalid_arguments")


def test_scripted_run_does_not_see_oracle_fields() -> None:
    context = _context()
    scenario = _scenario()
    dumped = scenario.model_dump(mode="json")
    for leaked in ("expected", "oracle_ref", "qrels", "split", "family_id", "fault_schedule"):
        assert leaked not in dumped
    sink = _sink(context)
    execute_agent(context, _agent(), scenario, DeniedModelGateway(), _tools(), sink)
    assert "expected" not in str([event.payload for event in sink.events])


def test_forbidden_tool_is_denied_recorded_and_run_continues() -> None:
    context = _context()
    sink = _sink(context)
    shell = {"type": "tool", "tool": "shell", "arguments": {"cmd": "id"}}
    result = execute_agent(
        context,
        _agent(shell, ADD_STEP, FINAL_STEP),
        _scenario(),
        DeniedModelGateway(),
        _tools(),
        sink,
    )
    assert result.status == RunStatus.COMPLETED
    assert result.output == {"total": 42}
    assert result.policy_violations == 1
    assert result.usage.tool_calls == 1

    denied = next(e for e in sink.events if e.type == "tool.denied")
    violation = next(e for e in sink.events if e.type == "policy.violation")
    requested = next(e for e in sink.events if e.type == "tool.requested")
    assert requested.payload["tool"] == "shell"
    assert denied.payload["policy_result"] == "denied"
    assert denied.payload["reason_codes"] == ["unknown_tool"]
    assert violation.payload["executed"] is False
    assert violation.payload["evidence_refs"] == [
        {"event_id": str(requested.event_id), "pointer": "/tool"}
    ]
    assert sum(1 for t in _types(sink) if t == "tool.completed") == 1


def test_invalid_arguments_are_kept_in_trace_and_not_executed() -> None:
    context = _context()
    sink = _sink(context)
    wrong = {"type": "tool", "tool": "calculator", "arguments": {**ADD, "a": "40"}}
    result = execute_agent(
        context, _agent(wrong, FINAL_STEP), _scenario(), DeniedModelGateway(), _tools(), sink
    )
    assert result.status == RunStatus.COMPLETED
    assert result.usage.tool_calls == 0
    assert result.policy_violations == 0
    assert result.evidence_refs == ()
    denied = next(e for e in sink.events if e.type == "tool.denied")
    assert denied.payload["schema_result"] == "invalid"
    assert denied.payload["error_class"] == "invalid_arguments"
    assert denied.payload["schema_errors"] == [{"path": "/a", "keyword": "type"}]
    assert "tool.completed" not in _types(sink)
    assert "tool.validated" not in _types(sink)


def test_deferred_pattern_is_not_presented_as_implemented() -> None:
    context = _context()
    result = execute_agent(
        context,
        _agent(pattern="react", pattern_parameters={}),
        _scenario(),
        DeniedModelGateway(),
        _tools(),
        _sink(context),
    )
    assert result.status == RunStatus.FAILED
    assert result.error_class == "infrastructure_error"
    assert result.pattern == "react"
    assert "no implementado" in (result.error or "")


def test_replay_is_not_implemented() -> None:
    context = _context("replay")
    result = execute_agent(
        context, _agent(), _scenario(), DeniedModelGateway(), _tools(), _sink(context)
    )
    assert result.status == RunStatus.FAILED
    assert "replay" in (result.error or "")


def test_deadline_times_out_before_next_step() -> None:
    context = _context(limits={"max_steps": 10, "deadline_ms": 1500}, clock=SteppingClock(1.0))
    sink = _sink(context)
    result = execute_agent(
        context,
        _agent(ADD_STEP, ADD_STEP, ADD_STEP, FINAL_STEP),
        _scenario(),
        DeniedModelGateway(),
        _tools(),
        sink,
    )
    assert result.status == RunStatus.TIMED_OUT
    assert result.error_class is None
    assert result.termination is not None
    assert result.termination["limit"] == "deadline_ms"
    assert result.termination["used"] >= 1500
    assert _types(sink)[-1] == "run.timed_out"
    assert "run.completed" not in _types(sink)


def test_effective_limits_take_the_strictest_value() -> None:
    limits = Limits.effective(
        {"max_steps": 8, "max_retries": 2, "max_cost": "10.00"},
        {"max_steps": 4, "max_retries": 3, "deadline_ms": 1000},
    )
    assert limits.as_json() == {"max_steps": 4, "max_retries": 2, "deadline_ms": 1000}
    assert Limits().retries_per_call == 0


@pytest.mark.parametrize("value", [0, -1, "4", True, 1.5])
def test_invalid_limit_is_rejected(value: Any) -> None:
    with pytest.raises(InvalidLimitsError):
        Limits.effective({"max_steps": value})


def test_invalid_limits_fail_the_run_typed() -> None:
    context = _context(limits={"max_steps": 0})
    sink = _sink(context)
    result = execute_agent(context, _agent(), _scenario(), DeniedModelGateway(), _tools(), sink)
    assert (result.status, result.error_class) == (RunStatus.FAILED, "infrastructure_error")
    assert "tool.requested" not in _types(sink)


def test_duplicate_event_id_same_digest_is_idempotent() -> None:
    sink = _sink(_context())
    event_id = uuid.uuid4()
    first = sink.append("run.started", "harness", {"mode": "live"}, event_id=event_id)
    second = sink.append("run.started", "harness", {"mode": "live"}, event_id=event_id)
    assert first.event_id == second.event_id
    assert len(sink.events) == 1


def test_duplicate_event_id_conflicting_digest_is_invalid() -> None:
    sink = _sink(_context())
    event_id = uuid.uuid4()
    sink.append("run.started", "harness", {"mode": "live"}, event_id=event_id)
    try:
        sink.append("run.started", "harness", {"mode": "replay"}, event_id=event_id)
        raise AssertionError("debía fallar integridad")
    except TraceIntegrityError:
        assert sink.completeness == "invalid"
