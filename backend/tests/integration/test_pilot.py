"""Piloto M5 (6.1): publicación validada, lock de hashes, vista pública y oráculos ejecutables."""

import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from evallab.api.app import create_app
from evallab.benchmarks import runner
from evallab.benchmarks.pilot import PILOT
from evallab.benchmarks.registry import committed_lock
from evallab.benchmarks.suite import Published, publish_suite
from evallab.db import models as m
from evallab.domain.vocabulary import PRIMARY_CATEGORIES

pytestmark = pytest.mark.integration

PRIVATE_KEYS = ("expected", "qrels", "fault_schedule", "split", "family_id", "recovery")

# Fallo deliberado del agente `scripted-faulty`: dimensiones que deben fallar y si el valor
# final, por sí solo, coincide con el oráculo (raw_outcome_pass).
FAULTY_EXPECTED: dict[str, tuple[set[str], str]] = {
    "pilot-ts-stock-lookup": (
        {"outcome", "required_tool", "semantic_arguments", "evidence"},
        "fail",
    ),
    "pilot-ts-no-tool-needed": ({"policy"}, "pass"),
    "pilot-ta-unit-conversion": ({"outcome", "semantic_arguments"}, "fail"),
    "pilot-ta-order-quantity": ({"semantic_arguments"}, "pass"),
    "pilot-rt-returns-policy": ({"evidence"}, "pass"),
    "pilot-rt-unanswerable": ({"outcome"}, "fail"),
    "pilot-rs-arithmetic-chain": ({"outcome"}, "fail"),
    "pilot-rs-constraints": ({"outcome"}, "fail"),
    "pilot-ms-stock-then-order": ({"required_tool", "semantic_arguments"}, "pass"),
    "pilot-ms-convert-then-sum": ({"required_tool", "semantic_arguments"}, "pass"),
    "pilot-er-transient-retry": (
        {"outcome", "required_tool", "semantic_arguments", "evidence"},
        "fail",
    ),
    "pilot-er-ambiguous-timeout": ({"outcome"}, "fail"),
    "pilot-pc-refund-denied": ({"policy"}, "pass"),
    "pilot-pc-injection-exfiltration": ({"policy"}, "pass"),
}


def _publish(engine: Engine) -> Published:
    with Session(engine, expire_on_commit=False) as db, db.begin():
        return publish_suite(db, PILOT)


@pytest.fixture
def client(fresh_database: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(engine=fresh_database)) as test_client:
        yield test_client


def _count(db: Session, model: type[Any]) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


def test_pilot_publishes_as_pilot_coverage_and_matches_lock(fresh_database: Engine) -> None:
    published = _publish(fresh_database)
    lock = published.lock()
    assert lock["dataset"]["coverage_class"] == "pilot"
    assert lock == committed_lock("pilot")

    with Session(fresh_database) as db:
        dataset = db.get(m.Dataset, (uuid.UUID(lock["dataset"]["id"]), "0.1.0"))
        assert dataset is not None
        assert dataset.category_counts == dict.fromkeys(PRIMARY_CATEGORIES, 2)
        assert len(dataset.split_manifest["dev"]) == 14
        assert dataset.split_manifest["held-out"] == []
        assert dataset.license == "CC-BY-4.0" and dataset.synthetic is True
        counts = {model: _count(db, model) for model in (m.Scenario, m.ToolDefinition, m.Fixture)}

    # Republicar el mismo contenido reutiliza las versiones: ni filas nuevas ni otros hashes.
    again = _publish(fresh_database)
    assert again.lock() == lock
    with Session(fresh_database) as db:
        assert {model: _count(db, model) for model in counts} == counts


def test_public_view_hides_oracles_and_private_labels(
    fresh_database: Engine, client: TestClient
) -> None:
    published = _publish(fresh_database)
    for scenario in published.scenarios:
        path = f"/scenarios/{scenario.id}/versions/{scenario.version}"
        public = client.get(path)
        assert public.status_code == 200
        body = public.json()
        for key in PRIVATE_KEYS:
            assert key not in body
            assert key not in body["environment"]
        blob = json.dumps(body)
        assert "pilot-er-001-transient" not in blob and str(scenario.family_id) not in blob
        oracle = client.get(f"{path}/oracle").json()
        assert oracle["expected"]["checks"]
        assert oracle["family_id"] == scenario.family_id and oracle["split"] == "dev"


def test_reference_passes_and_faulty_mutations_are_detected(fresh_database: Engine) -> None:
    published = _publish(fresh_database)
    with Session(fresh_database, expire_on_commit=False) as db, db.begin():
        experiment = runner.create_experiment(
            db,
            published,
            ["pilot-scripted-reference", "pilot-scripted-faulty"],
            hypothesis="los oráculos del piloto aceptan la referencia y detectan los fallos",
            seeds=[11],
        )
        agents = [published.agents[n] for n in sorted(published.agents)]
        run_ids = runner.enqueue(
            db, experiment, runner.plan_cells(published.scenarios, agents, repetitions=1)
        )
    assert runner.execute_in_order(fresh_database, run_ids) == 28
    runner.evaluate_all(fresh_database, run_ids)

    reference = published.agents["pilot-scripted-reference"].id
    slugs = {s.id: s.slug for s in published.scenarios}
    with Session(fresh_database) as db:
        for run in runner.experiment_runs(db, experiment.id):
            evaluation = db.scalars(select(m.Evaluation).where(m.Evaluation.run_id == run.id)).one()
            assert evaluation.status == "completed"
            report: dict[str, Any] = evaluation.report or {}
            slug = slugs[run.scenario_id]
            assert all(c["status"] != "error" for c in report["checks"]), slug
            if run.agent_id == reference:
                assert run.status == "completed", slug
                assert report["task_success"] == "pass", (slug, report["task_success_reasons"])
                continue
            failing, raw = FAULTY_EXPECTED[str(slug)]
            failed = {d for d, status in report["dimensions"].items() if status == "fail"}
            assert report["task_success"] == "fail", slug
            assert failed == failing, (slug, report["dimensions"])
            assert report["raw_outcome_pass"] == raw, slug
