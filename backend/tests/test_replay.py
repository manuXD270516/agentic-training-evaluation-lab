"""Replay estricto: grabación de la traza, request digest y divergencia sin fallback."""

import dataclasses
from typing import Any

from evallab.domain.lifecycle import RunStatus
from evallab.runner.agent import execute_agent
from evallab.runner.contracts import AgentSnapshot, RunContext
from evallab.runner.gateways import DeniedModelGateway
from evallab.runner.replay import Recording, ReplayToolGateway
from evallab.runner.sink import MemoryTraceSink
from evallab.runner.tools import FaultSpec, FixtureToolGateway
from evallab.schemas import ScenarioPublicOut
from tests.test_runner import (
    ADD_STEP,
    FINAL_STEP,
    RecordingModel,
    _agent,
    _context,
    _scenario,
    _sink,
    _tools,
)
from tests.test_tools import ADD, binding


class ExplodingGateway(FixtureToolGateway):
    """Aporta la allowlist; ejecutar una tool en replay sería un fallback a live."""

    def invoke(self, name: str, call_id: Any, arguments: dict[str, Any]) -> Any:
        raise AssertionError("replay no debe ejecutar tools")


def _as_rows(sink: MemoryTraceSink) -> list[dict[str, Any]]:
    return [
        {
            "event_id": e.event_id,
            "sequence": e.sequence,
            "type": e.type,
            "payload": e.payload,
            "redaction_metadata": e.redaction_metadata,
        }
        for e in sink.events
    ]


def _record(
    agent: AgentSnapshot,
    scenario: ScenarioPublicOut,
    tools: FixtureToolGateway | None = None,
    limits: dict[str, Any] | None = None,
) -> tuple[Recording, Any]:
    context = _context(limits=limits)
    sink = _sink(context)
    result = execute_agent(context, agent, scenario, DeniedModelGateway(), tools or _tools(), sink)
    return Recording.from_events(_as_rows(sink)), result


def _replay(
    agent: AgentSnapshot,
    scenario: ScenarioPublicOut,
    recording: Recording,
    context: RunContext | None = None,
) -> tuple[Any, MemoryTraceSink]:
    context = context or _context("replay")
    sink = _sink(context)
    gateway = ReplayToolGateway(ExplodingGateway([binding()]).allowed_tools(), recording)
    model = RecordingModel()
    result = execute_agent(context, agent, scenario, model, gateway, sink)
    assert model.calls == 0
    return result, sink


def _types(sink: MemoryTraceSink) -> list[str]:
    return [e.type for e in sink.events]


def test_replay_reproduces_recorded_results_offline() -> None:
    agent, scenario = _agent(), _scenario()
    recording, live = _record(agent, scenario)
    result, sink = _replay(agent, scenario, recording)
    assert result.status == RunStatus.COMPLETED
    assert result.output == live.output
    completed = [e.payload for e in sink.events if e.type == "tool.completed"]
    assert [p["result"] for p in completed] == [{"total": 42}]
    started = next(e for e in sink.events if e.type == "run.started")
    assert started.payload["mode"] == "replay"


def test_replay_reproduces_recorded_retries() -> None:
    agent, scenario = _agent(), _scenario()
    fault = FaultSpec(fault_id="t", tool="calculator", call_index=1, kind="transient")
    limits = {"max_steps": 4, "max_retries": 1}
    gateway = FixtureToolGateway([binding()], faults=[fault])
    recording, live = _record(agent, scenario, gateway, limits)
    assert live.status == RunStatus.COMPLETED
    result, sink = _replay(agent, scenario, recording, _context("replay", limits=limits))
    assert result.status == RunStatus.COMPLETED
    assert result.output == live.output
    assert _types(sink).count("tool.failed") == 1
    assert _types(sink).count("retry.scheduled") == 1


def test_divergent_request_is_mismatch_without_fallback() -> None:
    scenario = _scenario()
    agent = _agent()
    recording, _ = _record(agent, scenario)
    other = {**ADD_STEP, "arguments": {**ADD, "b": 3}}
    divergent = dataclasses.replace(agent, pattern_parameters={"script": [other, FINAL_STEP]})
    result, sink = _replay(divergent, scenario, recording)
    assert result.status == RunStatus.FAILED
    assert result.error_class == "replay_mismatch"
    failed = next(e.payload for e in sink.events if e.type == "tool.failed")
    assert failed["error_class"] == "replay_mismatch"
    assert "tool.completed" not in _types(sink)
    assert _types(sink)[-1] == "run.failed"


def test_fewer_calls_than_recorded_is_mismatch() -> None:
    scenario, agent = _scenario(), _agent()
    recording, _ = _record(agent, scenario)
    shorter = dataclasses.replace(agent, pattern_parameters={"script": [FINAL_STEP]})
    result, _ = _replay(shorter, scenario, recording)
    assert (result.status, result.error_class) == (RunStatus.FAILED, "replay_mismatch")


def test_extra_call_beyond_recording_is_mismatch() -> None:
    scenario, agent = _scenario(), _agent()
    recording, _ = _record(agent, scenario)
    longer = dataclasses.replace(
        agent, pattern_parameters={"script": [ADD_STEP, ADD_STEP, FINAL_STEP]}
    )
    result, _ = _replay(longer, scenario, recording)
    assert (result.status, result.error_class) == (RunStatus.FAILED, "replay_mismatch")


def test_changed_conditions_are_rejected_before_any_call() -> None:
    scenario, agent = _scenario(), _agent()
    recording, _ = _record(agent, scenario)
    context = dataclasses.replace(_context("replay"), seed=12)
    result, sink = _replay(agent, scenario, recording, context)
    assert (result.status, result.error_class) == (RunStatus.FAILED, "replay_mismatch")
    assert "seed" in (result.error or "")
    assert "tool.requested" not in _types(sink)


def test_changed_tool_hash_is_rejected() -> None:
    scenario, agent = _scenario(), _agent()
    recording, _ = _record(agent, scenario)
    tools = [dict(t) for t in recording.started["tools"]]
    tools[0]["content_hash"] = "f" * 64
    altered = dataclasses.replace(recording, started={**recording.started, "tools": tools})
    result, _ = _replay(agent, scenario, altered)
    assert result.error_class == "replay_mismatch"
    assert "tools" in (result.error or "")


def test_replay_without_recording_fails_typed() -> None:
    context = _context("replay")
    result = execute_agent(
        context, _agent(), _scenario(), DeniedModelGateway(), _tools(), _sink(context)
    )
    assert result.status == RunStatus.FAILED
    assert "replay" in (result.error or "")
