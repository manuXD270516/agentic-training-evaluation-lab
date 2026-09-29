import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from evallab.domain.lifecycle import RunStatus
from evallab.runner.agent import execute_agent
from evallab.runner.contracts import AgentSnapshot, AllowedTool, RunContext
from evallab.runner.errors import TraceIntegrityError
from evallab.runner.gateways import DeniedModelGateway, DeniedToolGateway
from evallab.runner.sink import MemoryTraceSink
from evallab.schemas import ScenarioPublicOut
from evallab.settings import SandboxPolicy

DIGEST = "a" * 64


def _context() -> RunContext:
    now = datetime.now(UTC)
    return RunContext(
        run_id=uuid.uuid4(),
        attempt_id=uuid.uuid4(),
        experiment_id=uuid.uuid4(),
        mode="live",
        seed=11,
        started_at=now,
        limits={"max_steps": 4},
        sandbox=SandboxPolicy(),
        manifest_hash=DIGEST,
        experiment_budgets={"max_steps": 4},
    )


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
    *, pattern: str = "scripted", pattern_parameters: dict[str, Any] | None = None
) -> AgentSnapshot:
    script = [
        {
            "type": "tool",
            "tool": "calculator",
            "arguments": {"a": 40, "b": 2},
            "result": {"total": 42},
            "evidence_id": "e1",
        },
        {"type": "final", "output": {"total": 42, "evidence_ids": ["e1"]}},
    ]
    return AgentSnapshot(
        id=uuid.uuid4(),
        version="1.0.0",
        pattern=pattern,
        pattern_version="1.0.0",
        content_hash=DIGEST,
        pattern_parameters=(
            pattern_parameters if pattern_parameters is not None else {"script": script}
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


def _calculator() -> AllowedTool:
    return AllowedTool(id=uuid.uuid4(), version="1.0.0", name="calculator", content_hash=DIGEST)


class RecordingModel:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, messages: Sequence[Any], tools: Sequence[AllowedTool]) -> Any:
        self.calls += 1
        raise AssertionError("scripted no debe llamar al modelo")


def test_scripted_run_emits_tool_evidence_without_model_calls() -> None:
    context = _context()
    model = RecordingModel()
    result = execute_agent(
        context,
        _agent(),
        _scenario(),
        [_calculator()],
        model,
        DeniedToolGateway(),
        _sink(context),
    )
    assert result.status == RunStatus.COMPLETED
    assert result.pattern == "scripted"
    assert result.usage.model_calls == 0
    assert result.usage.tool_calls == 1
    assert result.output == {"total": 42, "evidence_ids": ["e1"]}
    assert result.evidence_refs
    assert model.calls == 0


def test_scripted_run_does_not_see_oracle_fields() -> None:
    context = _context()
    scenario = _scenario()
    dumped = scenario.model_dump(mode="json")
    for leaked in ("expected", "oracle_ref", "qrels", "split", "family_id", "fault_schedule"):
        assert leaked not in dumped
    sink = _sink(context)
    execute_agent(
        context,
        _agent(),
        scenario,
        [_calculator()],
        DeniedModelGateway(),
        DeniedToolGateway(),
        sink,
    )
    blob = str([event.payload for event in sink.events])
    assert "SECRET_ORACLE" not in blob
    assert "expected" not in blob


def test_unknown_tool_is_denied_and_recorded() -> None:
    context = _context()
    sink = _sink(context)
    result = execute_agent(
        context, _agent(), _scenario(), [], DeniedModelGateway(), DeniedToolGateway(), sink
    )
    assert result.status == RunStatus.FAILED
    assert result.error_class == "denied"
    assert [event.type for event in sink.events if event.type.startswith("tool.")] == [
        "tool.requested",
        "tool.denied",
    ]


def test_deferred_pattern_is_not_presented_as_implemented() -> None:
    context = _context()
    sink = _sink(context)
    result = execute_agent(
        context,
        _agent(pattern="react", pattern_parameters={}),
        _scenario(),
        [_calculator()],
        DeniedModelGateway(),
        DeniedToolGateway(),
        sink,
    )
    assert result.status == RunStatus.FAILED
    assert result.error_class == "infrastructure_error"
    assert result.pattern == "react"
    assert "no implementado" in (result.error or "")


def test_replay_is_not_implemented() -> None:
    live = _context()
    replay = RunContext(
        run_id=live.run_id,
        attempt_id=live.attempt_id,
        experiment_id=live.experiment_id,
        mode="replay",
        seed=live.seed,
        started_at=live.started_at,
        limits=live.limits,
        sandbox=live.sandbox,
        manifest_hash=live.manifest_hash,
        experiment_budgets=live.experiment_budgets,
    )
    result = execute_agent(
        replay,
        _agent(),
        _scenario(),
        [_calculator()],
        DeniedModelGateway(),
        DeniedToolGateway(),
        _sink(replay),
    )
    assert result.status == RunStatus.FAILED
    assert "replay" in (result.error or "")


def test_duplicate_event_id_same_digest_is_idempotent() -> None:
    context = _context()
    sink = _sink(context)
    event_id = uuid.uuid4()
    first = sink.append("run.started", "harness", {"mode": "live"}, event_id=event_id)
    second = sink.append("run.started", "harness", {"mode": "live"}, event_id=event_id)
    assert first.event_id == second.event_id
    assert len(sink.events) == 1


def test_duplicate_event_id_conflicting_digest_is_invalid() -> None:
    context = _context()
    sink = _sink(context)
    event_id = uuid.uuid4()
    sink.append("run.started", "harness", {"mode": "live"}, event_id=event_id)
    try:
        sink.append("run.started", "harness", {"mode": "replay"}, event_id=event_id)
        raise AssertionError("debía fallar integridad")
    except TraceIntegrityError:
        assert sink.completeness == "invalid"
