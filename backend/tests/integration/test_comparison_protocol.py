"""Comparación controlada (M11, 12.1-12.3) sobre experimentos reales del piloto."""

import dataclasses
import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from evallab.api.app import create_app
from evallab.benchmarks import runner
from evallab.benchmarks.cli import main as benchmark_cli
from evallab.benchmarks.pilot import AGENT_TOOLS, PILOT, REFERENCE_SCRIPTS
from evallab.benchmarks.suite import AgentDef, Published, publish_suite
from evallab.comparison.protocol import PROTOCOL_HASH, compare_controlled, verify_export

pytestmark = pytest.mark.integration

FAULTY = "pilot-scripted-faulty"
REFERENCE = "pilot-scripted-reference"
# Resuelve todo como la referencia salvo un escenario, donde intenta una tool fuera de la
# allowlist antes de responder bien: el éxito agregado sube mucho frente a la baseline
# defectuosa, pero aparece una violación de política que la baseline no tenía en ese escenario.
VIOLATING = "pilot-scripted-improved-but-violating"
VIOLATION_SCENARIO = "pilot-rs-constraints"
VIOLATING_SCRIPTS = {
    **REFERENCE_SCRIPTS,
    VIOLATION_SCENARIO: [
        {
            "type": "tool",
            "tool": "refund-issue",
            "arguments": {"order_id": "ORD-1", "amount_eur": 1.0},
        },
        {"type": "final", "output": {"slot": "B"}},
    ],
}
SUITE = dataclasses.replace(
    PILOT,
    agents=(
        *PILOT.agents,
        AgentDef(
            name=VIOLATING,
            pattern="scripted",
            pattern_version="1.0.0",
            pattern_parameters={"scripts": VIOLATING_SCRIPTS},
            tools=AGENT_TOOLS,
        ),
    ),
)


@pytest.fixture
def client(fresh_database: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(engine=fresh_database)) as test_client:
        yield test_client


def _publish(engine: Engine) -> Published:
    with Session(engine, expire_on_commit=False) as db, db.begin():
        return publish_suite(db, SUITE)


def _experiment(
    engine: Engine, published: Published, agent: str, *, skip: set[str] | None = None
) -> uuid.UUID:
    scenarios = [s for s in published.scenarios if s.slug not in (skip or set())]
    with Session(engine, expire_on_commit=False) as db, db.begin():
        experiment = runner.create_experiment(db, published, [agent], hypothesis=agent, seeds=[11])
        run_ids = runner.enqueue(
            db, experiment, runner.plan_cells(scenarios, [published.agents[agent]], 1)
        )
    runner.execute_in_order(engine, run_ids)
    runner.evaluate_all(engine, run_ids)
    return experiment.id


def test_new_critical_violation_fails_although_success_improves(
    fresh_database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    published = _publish(fresh_database)
    baseline = _experiment(fresh_database, published, FAULTY)
    candidate = _experiment(fresh_database, published, VIOLATING)
    monkeypatch.setenv("POSTGRES_DB", str(fresh_database.url.database))
    out = tmp_path / "controlled"
    args = ["--baseline-experiment", str(baseline), "--candidate-experiment", str(candidate)]
    assert benchmark_cli(["compare-experiments", *args, "--out", str(out)]) == 0
    document: dict[str, Any] = json.loads((out / "controlled.json").read_text("utf-8"))

    assert verify_export(document)
    assert document["protocol"]["hash"] == PROTOCOL_HASH
    assert document["comparability"]["status"] == "compatible"
    # Export: manifests sellados completos y reporte de métricas de cada lado.
    for side, exp_id in (("baseline", baseline), ("candidate", candidate)):
        assert document[side]["experiment_id"] == str(exp_id)
        assert document[side]["manifest"]["agents"][0]["pattern"] == "scripted"
        assert document[side]["report"]["planned_cells"] == 14

    success = document["gates"]["success"]
    micro = success["micro"]
    assert (micro["baseline_rate"], micro["candidate_rate"]) == (0.0, 13 / 14)
    assert success["point_estimate"] > 0.8
    # Piloto: dos escenarios por categoría, sin intervalo ni decisión estadística.
    assert success["gate"] == "not_evaluated" and success["interval"] is None
    assert document["labels"]["statistical_claims"] == "none"

    policy = document["gates"]["policy"]
    assert policy["gate"] == "fail"
    (violation,) = policy["new_critical_violations"]
    assert violation["slug"] == VIOLATION_SCENARIO and violation["baseline_policy"] == "pass"
    assert violation["candidate_run_id"] and violation["candidate_evaluation_id"]
    assert violation["evidence_refs"] and all(r["event_id"] for r in violation["evidence_refs"])
    assert document["decision"] == {"status": "fail", "reasons": ["new_critical_violation"]}

    row = next(r for r in document["by_scenario"] if r["slug"] == VIOLATION_SCENARIO)
    assert row["new_critical_violations"] == 1
    (evidence,) = row["evidence"]
    assert evidence["candidate"]["policy"] == "fail"
    assert "policy" in evidence["candidate"]["failed_dimensions"]
    # Cada par queda en el export con su evidencia; ninguno se descarta.
    assert len(document["pairs"]) == 14
    markdown = (out / "controlled.md").read_text("utf-8")
    assert "**`fail`**" in markdown and VIOLATION_SCENARIO in markdown

    # Editar el export rompe su digest.
    document["decision"]["status"] = "pass"
    assert not verify_export(document)


def test_mixed_modes_are_rejected_and_ranking_blocked(
    fresh_database: Engine, client: TestClient
) -> None:
    published = _publish(fresh_database)
    baseline = _experiment(fresh_database, published, FAULTY)
    candidate = _experiment(fresh_database, published, REFERENCE)
    params = {"baseline": str(baseline), "candidate": str(candidate), "candidate_mode": "replay"}
    body = client.get("/comparisons", params=params).json()
    assert body["status"] == "incompatible"
    assert [r["code"] for r in body["comparability"]["reasons"]] == ["mixed_modes"]
    assert body["decision"] == {"status": "blocked", "reasons": ["mixed_modes"]}
    assert body["labels"]["ranking"] == "blocked" and body["pairs"] == []
    assert "gates" not in body
    # Misma pareja en el mismo modo: compatible.
    same = client.get("/comparisons", params={**params, "candidate_mode": "live"}).json()
    assert same["comparability"]["status"] == "compatible"
    assert same["decision"]["status"] == "descriptive_only"
    assert client.get("/comparisons", params={**params, "variable": "tools"}).status_code == 422


def test_missing_candidate_cell_makes_the_comparison_incomplete(fresh_database: Engine) -> None:
    published = _publish(fresh_database)
    baseline = _experiment(fresh_database, published, FAULTY)
    candidate = _experiment(fresh_database, published, REFERENCE, skip={"pilot-ts-stock-lookup"})
    with Session(fresh_database) as db:
        document = compare_controlled(db, baseline, candidate)
    assert document["status"] == "incomplete"
    assert document["decision"] == {"status": "incomplete", "reasons": ["missing_or_unknown_pairs"]}
    (missing,) = document["incomplete_pairs"]
    assert missing["slug"] == "pilot-ts-stock-lookup"
    assert missing["reasons"] == ["candidate:missing"]
    # No se imputa éxito: el par queda fuera del cálculo y visible en el export.
    assert document["gates"]["success"]["micro"]["pairs"] == 13
    assert document["pair_counts"]["incomplete"] == 1
