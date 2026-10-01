"""Piloto M5 (6.2): cinco repeticiones scripted y reporte descriptivo con sus denominadores."""

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
from evallab.benchmarks.pilot import FAULTY_SCRIPTS, PILOT, REFERENCE_SCRIPTS, SEEDS
from evallab.benchmarks.suite import Published, publish_suite
from evallab.domain.vocabulary import PRIMARY_CATEGORIES
from evallab.services.reports import experiment_report, nearest_rank

pytestmark = pytest.mark.integration

AGENTS = ["pilot-scripted-reference", "pilot-scripted-faulty"]


def _publish(engine: Engine) -> Published:
    with Session(engine, expire_on_commit=False) as db, db.begin():
        return publish_suite(db, PILOT)


def _experiment(
    engine: Engine, published: Published, *, repetitions: int, skip: int = 0
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """Crea el experimento completo; `skip` celdas del final quedan sin ejecutar ni evaluar."""
    with Session(engine, expire_on_commit=False) as db, db.begin():
        experiment = runner.create_experiment(
            db,
            published,
            AGENTS,
            hypothesis="descripción del piloto scripted",
            seeds=SEEDS[:repetitions],
        )
        agents = [published.agents[n] for n in AGENTS]
        cells = runner.plan_cells(published.scenarios, agents, repetitions)
        run_ids = runner.enqueue(db, experiment, cells[: len(cells) - skip])
    runner.execute_in_order(engine, run_ids)
    runner.evaluate_all(engine, run_ids)
    return experiment.id, run_ids


def _agent(report: dict[str, Any], name: str, published: Published) -> dict[str, Any]:
    agent_id = str(published.agents[name].id)
    return next(a for a in report["agents"] if a["agent"]["id"] == agent_id)


def _tool_steps(scripts: dict[str, list[dict[str, Any]]]) -> int:
    return sum(1 for steps in scripts.values() for step in steps if step["type"] == "tool")


@pytest.fixture
def client(fresh_database: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(engine=fresh_database)) as test_client:
        yield test_client


def test_five_repetitions_cover_every_cell(fresh_database: Engine, client: TestClient) -> None:
    published = _publish(fresh_database)
    experiment_id, run_ids = _experiment(fresh_database, published, repetitions=5)
    assert len(run_ids) == 14 * 2 * 5

    response = client.get(f"/experiments/{experiment_id}/report")
    assert response.status_code == 200
    report = response.json()
    assert report["planned_cells"] == 140
    assert report["labels"] == {
        "cohort": "pilot",
        "mode": "live",
        "analysis": "descriptive_only",
        "statistical_claims": "none",
        "reason": "2 escenarios por categoría (< 5): sin intervalos ni superioridad",
    }
    assert report["scenarios_per_category"] == dict.fromkeys(sorted(PRIMARY_CATEGORIES), 2)
    # Sólo descripción: ningún bloque de intervalos, bootstrap ni comparación inferencial.
    assert not {"confidence_interval", "bootstrap", "comparison", "p_value"} & set(report)

    reference = _agent(report, "pilot-scripted-reference", published)
    faulty = _agent(report, "pilot-scripted-faulty", published)
    for agent in (reference, faulty):
        assert agent["agent"]["attribution"] == "harness_baseline"
        summary = agent["summary"]
        assert summary["cells"] == summary["evaluated"] == 70
        assert summary["task_success"]["coverage"] == 1.0
        assert summary["task_success"]["unknown"] == 0
        assert set(agent["by_category"]) == set(PRIMARY_CATEGORIES)
        assert all(c["task_success"]["cells"] == 10 for c in agent["by_category"].values())
        # Scripted es determinista: cada escenario produce la misma salida en las 5 seeds.
        assert {row["distinct_outputs"] for row in agent["by_scenario"]} == {1}
        usage = agent["usage"]
        assert usage["runs_with_usage"] == 70 and usage["totals"]["model_calls"] == 0
        assert usage["tokens"]["status"] == "not_applicable"
        assert usage["estimated_cost"]["status"] == "not_applicable"
        assert agent["latency_ms"]["n"] == 70 and agent["latency_ms"]["coverage"] == 1.0

    assert reference["summary"]["task_success"]["conservative_rate"] == 1.0
    assert reference["summary"]["run_statuses"] == {"completed": 70}
    assert faulty["summary"]["task_success"]["successes"] == 0
    assert faulty["summary"]["task_success"]["failures"] == 70
    # raw_outcome_pass separado: 7 escenarios del agente defectuoso aciertan el valor final.
    assert faulty["summary"]["raw_outcome_pass"]["successes"] == 7 * 5

    # El consumo cuenta todas las llamadas ejecutadas, retries incluidos y denegadas excluidas.
    ref_usage = reference["usage"]["totals"]
    assert ref_usage["retries"] == 5  # un retry transitorio por repetición
    assert ref_usage["tool_calls"] == 5 * (_tool_steps(REFERENCE_SCRIPTS) + 1)
    faulty_usage = faulty["usage"]["totals"]
    denied = 3  # cantidad "3" (schema), refund-issue y email-send fuera de la allowlist
    assert faulty_usage["tool_calls"] == 5 * (_tool_steps(FAULTY_SCRIPTS) - denied)

    # Recuperación (metrics.md): dos escenarios con fallo inyectado por 5 repeticiones. La
    # referencia llega a ambos fallos y se recupera; el agente defectuoso responde sin llamar
    # en el transitorio (no expuesto, sin crédito) y reintenta a ciegas el ambiguo (expuesto,
    # no recuperado).
    assert reference["recovery"] == {
        "programmed": 10,
        "exposed": 10,
        "exposure_rate": 1.0,
        "recovered": 10,
        "recovery_success": {"status": "observed", "value": 1.0, "reason": None},
    }
    assert faulty["recovery"]["exposed"] == 5 and faulty["recovery"]["exposure_rate"] == 0.5
    assert faulty["recovery"]["recovery_success"]["value"] == 0.0

    tool_accuracy = reference["metrics"]["tool_accuracy"]["statuses"]
    # Sin llamadas (respuesta directa, reembolso escalado, franja): N/A, nunca 1.0 ni 0.
    assert tool_accuracy["not_applicable"] == 3 * 5
    args = faulty["metrics"]["argument_accuracy"]
    assert args["statuses"]["fail"] > 0 and args["micro"]["denominator"] > 0
    schema = faulty["metrics"]["schema_argument_validity"]
    # No validadas: el tipo incorrecto y las dos denegadas por allowlist (core-metrics 4.3).
    assert schema["micro"]["numerator"] == schema["micro"]["denominator"] - 5 * denied


def test_missing_cells_count_as_unknown(fresh_database: Engine) -> None:
    published = _publish(fresh_database)
    experiment_id, run_ids = _experiment(fresh_database, published, repetitions=1, skip=3)
    assert len(run_ids) == 25
    with Session(fresh_database) as db:
        report = experiment_report(db, experiment_id)
    assert report["planned_cells"] == 28
    total_unknown = sum(a["summary"]["task_success"]["unknown"] for a in report["agents"])
    assert total_unknown == 3
    for agent in report["agents"]:
        task = agent["summary"]["task_success"]
        assert task["cells"] == 14
        if task["unknown"]:
            low, high = task["missingness_range"]
            assert task["coverage"] == (14 - task["unknown"]) / 14
            assert high - low == pytest.approx(task["unknown"] / 14)
            assert agent["summary"]["run_statuses"].get("missing") == task["unknown"]


def test_unsealed_experiment_has_no_report(client: TestClient) -> None:
    body = {"hypothesis": "draft", "repetitions": 1, "seeds": [11]}
    created = client.post("/experiments", json=body, headers={"Idempotency-Key": uuid.uuid4().hex})
    response = client.get(f"/experiments/{created.json()['id']}/report")
    assert response.status_code == 409


def test_nearest_rank_percentiles() -> None:
    values = [15.0, 20.0, 35.0, 40.0, 50.0]
    assert nearest_rank(values, 50) == 35.0
    assert nearest_rank(values, 95) == 50.0
    assert nearest_rank([7.0], 95) == 7.0
    assert nearest_rank([], 50) is None


def test_cli_writes_report_files(
    fresh_database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POSTGRES_DB", str(fresh_database.url.database))
    out = tmp_path / "pilot"
    assert benchmark_cli(["run", "pilot", "--repetitions", "1", "--out", str(out)]) == 0
    assert {p.name for p in out.iterdir()} == {
        "report.json",
        "report.md",
        "lock.json",
        "manifest.json",
        "run.json",
    }
    markdown = (out / "report.md").read_text(encoding="utf-8")
    assert "afirmaciones estadísticas: `none`" in markdown
    assert "harness_baseline" in markdown
