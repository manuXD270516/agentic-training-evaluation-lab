"""Piloto M5 (6.1) sin base de datos: forma, categorías, familias y scripts de los agentes."""

import uuid
from collections import Counter
from unittest.mock import MagicMock

import pytest

from evallab.benchmarks.pilot import AGENTS, PILOT, SCENARIOS
from evallab.benchmarks.suite import stable_id
from evallab.domain.vocabulary import PRIMARY_CATEGORIES
from evallab.runner.contracts import AgentSnapshot, BudgetRemaining, FinalAnswer, Observation
from evallab.runner.errors import InvalidScriptError
from evallab.runner.scripted import ScriptedPatternAdapter
from evallab.schemas import ScenarioCreate


def _validated(raw: dict[str, object]) -> ScenarioCreate:
    data = dict(raw)
    names = data["tools"]
    assert isinstance(names, list)
    data["tools"] = [
        {"id": str(stable_id("tool", n)), "version": "1.0.0", "content_hash": "0" * 64}
        for n in names
    ]
    if isinstance(data.get("retrieval"), dict):
        data["retrieval"] = {**data["retrieval"], "corpus_ref": "1" * 64}  # type: ignore[dict-item]
    return ScenarioCreate.model_validate({**data, "version": "1.0.0"})


def test_fourteen_dev_scenarios_two_per_category() -> None:
    assert len(SCENARIOS) == 14
    counts = Counter(s["primary_category"] for s in SCENARIOS)
    assert counts == dict.fromkeys(PRIMARY_CATEGORIES, 2)
    assert {s["split"] for s in SCENARIOS} == {"dev"}
    slugs = [s["slug"] for s in SCENARIOS]
    families = [s["family_id"] for s in SCENARIOS]
    assert len(set(slugs)) == 14 and len(set(families)) == 14


def test_every_scenario_validates_against_the_schema() -> None:
    declared = {tool.name for tool in PILOT.tools}
    for raw in SCENARIOS:
        scenario = _validated(raw)
        assert set(raw["tools"]) <= declared
        operators = {check.operator for check in scenario.expected.checks}
        assert operators, scenario.slug
        assert "outcome" in scenario.evaluation.required_checks
        # retrieval/recovery no tienen evaluador hasta M9: no pueden ser obligatorios aquí.
        assert not {"retrieval", "recovery"} & set(scenario.evaluation.required_checks)


def test_both_scripted_agents_cover_every_scenario() -> None:
    slugs = {s["slug"] for s in SCENARIOS}
    for agent in AGENTS:
        assert agent.pattern == "scripted"
        scripts = agent.pattern_parameters["scripts"]
        assert set(scripts) == slugs, agent.name
        for steps in scripts.values():
            assert steps[-1]["type"] == "final"


def test_identities_are_stable() -> None:
    assert stable_id("scenario", "pilot-ts-stock-lookup") == stable_id(
        "scenario", "pilot-ts-stock-lookup"
    )
    assert isinstance(stable_id("tool", "calculator"), uuid.UUID)
    assert stable_id("tool", "calculator") != stable_id("agent", "calculator")


def _snapshot(params: dict[str, object]) -> AgentSnapshot:
    return AgentSnapshot(
        id=uuid.uuid4(),
        version="1.0.0",
        pattern="scripted",
        pattern_version="1.0.0",
        content_hash="0" * 64,
        pattern_parameters=params,
        prompt_hash=None,
        roles=("executor",),
    )


def test_scripts_by_slug_fail_typed_without_script() -> None:
    agent = _snapshot({"scripts": {"other": [{"type": "final", "output": {}}]}})
    scenario = MagicMock(slug="pilot-ts-stock-lookup")
    with pytest.raises(InvalidScriptError, match="no hay script"):
        ScriptedPatternAdapter.from_agent(agent, scenario)


def test_evidence_placeholder_is_filled_from_completed_calls() -> None:
    script = [{"type": "final", "output": {"ids": ["{{evidence:0}}", "{{evidence:3}}"]}}]
    adapter = ScriptedPatternAdapter.from_agent(_snapshot({"script": script}))
    evidence = uuid.uuid4()
    observation = Observation(
        call_id=uuid.uuid4(), tool="t", status="completed", result={}, evidence_id=evidence
    )
    action = adapter.next_action(MagicMock(), [observation], BudgetRemaining())
    assert isinstance(action, FinalAnswer)
    # Sin una cuarta llamada completada el marcador no se inventa: la cita quedará sin soporte.
    assert action.output == {"ids": [str(evidence), "{{evidence:3}}"]}
