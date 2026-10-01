"""`agentic-benchmark-v1@1.0.0` (13.1): 70 escenarios, 10 por categoría, 42/28 por familias."""

from collections import Counter, defaultdict
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from evallab.api.app import create_app
from evallab.benchmarks import runner
from evallab.benchmarks.registry import committed_lock
from evallab.benchmarks.retrieval_v1 import RETRIEVAL_V1
from evallab.benchmarks.suite import Published, publish_suite
from evallab.benchmarks.v1 import DECLARATIONS, LICENSE, V1
from evallab.db import models as m
from evallab.domain.vocabulary import PRIMARY_CATEGORIES

pytestmark = pytest.mark.integration


def _publish(engine: Engine) -> Published:
    with Session(engine, expire_on_commit=False) as db, db.begin():
        return publish_suite(db, V1)


def test_publication_counts_integrity_and_declarations(fresh_database: Engine) -> None:
    published = _publish(fresh_database)
    dataset = published.dataset
    assert dataset is not None and published.benchmark is not None
    assert dataset.coverage_class == "complete_v1"
    assert dataset.license == LICENSE == "CC-BY-4.0" and dataset.synthetic is True
    assert len({s.slug for s in published.scenarios}) == 70
    assert Counter(s.primary_category for s in published.scenarios) == dict.fromkeys(
        PRIMARY_CATEGORIES, 10
    )
    splits = dataset.split_manifest
    assert (len(splits["dev"]), len(splits["held-out"])) == (42, 28)
    families: dict[str, set[str]] = defaultdict(set)
    per_category: dict[tuple[str, str], int] = Counter()
    for scenario in published.scenarios:
        families[str(scenario.split)].add(str(scenario.family_id))
        per_category[(scenario.primary_category, str(scenario.split))] += 1
    assert not families["dev"] & families["held-out"]
    assert all(per_category[(c, "dev")] == 6 for c in PRIMARY_CATEGORIES)
    rules: dict[str, Any] = published.benchmark.comparison_rules
    assert rules["declarations"] == DECLARATIONS
    assert rules["declarations"]["held_out_status"] == "public"
    # El lock versionado coincide con lo publicado (cualquier cambio de contenido lo rompe).
    assert committed_lock("v1") == published.lock()


def test_retrieval_scenarios_are_shared_with_retrieval_v1(fresh_database: Engine) -> None:
    v1 = _publish(fresh_database)
    with Session(fresh_database, expire_on_commit=False) as db, db.begin():
        retrieval = publish_suite(db, RETRIEVAL_V1)
    shared = {s.content_hash for s in retrieval.scenarios}
    assert shared <= {s.content_hash for s in v1.scenarios}


def test_public_views_hide_oracles(fresh_database: Engine) -> None:
    published = _publish(fresh_database)
    with TestClient(create_app(engine=fresh_database)) as client:
        for scenario in published.scenarios:
            view = client.get(f"/scenarios/{scenario.id}/versions/{scenario.version}").json()
            text = str(view)
            assert "expected" not in view and "family_id" not in text and "held-out" not in text
            assert "qrels" not in text


def test_reference_solves_all_and_faulty_fails_all(fresh_database: Engine) -> None:
    published = _publish(fresh_database)
    names = ["v1-scripted-reference", "v1-scripted-faulty"]
    agents = [published.agents[n] for n in names]
    with Session(fresh_database, expire_on_commit=False) as db, db.begin():
        experiment = runner.create_experiment(
            db, published, names, hypothesis="v1 harness", seeds=[11]
        )
        run_ids = runner.enqueue(db, experiment, runner.plan_cells(published.scenarios, agents, 1))
    runner.execute_in_order(fresh_database, run_ids)
    runner.evaluate_all(fresh_database, run_ids)
    slugs = {s.id: str(s.slug) for s in published.scenarios}
    outcomes: dict[str, dict[str, str]] = defaultdict(dict)
    with Session(fresh_database) as db:
        for run_id in run_ids:
            run = db.get(m.Run, run_id)
            assert run is not None
            evaluation = db.scalars(select(m.Evaluation).where(m.Evaluation.run_id == run_id)).one()
            report: dict[str, Any] = evaluation.report or {}
            name = "reference" if run.agent_id == agents[0].id else "faulty"
            outcomes[name][slugs[run.scenario_id]] = str(report.get("task_success"))
    assert len(outcomes["reference"]) == len(outcomes["faulty"]) == 70
    failed_reference = {s: v for s, v in outcomes["reference"].items() if v != "pass"}
    assert failed_reference == {}
    passed_faulty = {s for s, v in outcomes["faulty"].items() if v != "fail"}
    assert passed_faulty == set()
