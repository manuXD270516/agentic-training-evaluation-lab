"""`agentic-retrieval-v1` (10.2): distractores, sin respuesta, inyección, cita falsa y top-k."""

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from evallab.benchmarks import runner
from evallab.benchmarks.retrieval_v1 import RETRIEVAL_V1
from evallab.benchmarks.suite import Published, publish_suite
from evallab.db import models as m
from evallab.evaluation.retrieval import SUITE_HASH as RETRIEVAL_SUITE_HASH

pytestmark = pytest.mark.integration

UNANSWERABLE = {"rt-v1-battery-life-unanswerable", "rt-v1-ceo-unanswerable"}
# Error deliberado del agente defectuoso: (dimensiones que fallan, motivo del check de citas).
FAULTY: dict[str, tuple[set[str], str | None]] = {
    "rt-v1-current-returns": ({"outcome", "evidence"}, "citation_unsupported"),
    "rt-v1-warranty-electronics": ({"evidence"}, "citation_not_retrieved"),
    "rt-v1-battery-life-unanswerable": ({"outcome"}, None),
    "rt-v1-support-hours-injection": ({"policy"}, "citations_supported"),
    "rt-v1-islands-shipping": ({"outcome"}, "citations_supported"),
    "rt-v1-payment-methods": (
        {"outcome", "required_tool", "semantic_arguments", "retrieval", "evidence"},
        "citation_not_retrieved",
    ),
    "rt-v1-data-retention": (
        {"outcome", "semantic_arguments", "retrieval", "evidence"},
        "citation_unsupported",
    ),
    "rt-v1-ceo-unanswerable": ({"outcome"}, None),
    "rt-v1-size-exchange": ({"output_structure", "evidence"}, "no_citations"),
    "rt-v1-shipping-cost": ({"outcome", "evidence"}, "citation_unsupported"),
}


def _publish(engine: Engine) -> Published:
    with Session(engine, expire_on_commit=False) as db, db.begin():
        return publish_suite(db, RETRIEVAL_V1)


def _evaluate_all(engine: Engine, published: Published) -> list[tuple[m.Run, m.Evaluation]]:
    agents = [published.agents[n] for n in sorted(published.agents)]
    with Session(engine, expire_on_commit=False) as db, db.begin():
        experiment = runner.create_experiment(
            db, published, sorted(published.agents), hypothesis="retrieval v1", seeds=[11]
        )
        run_ids = runner.enqueue(db, experiment, runner.plan_cells(published.scenarios, agents, 1))
    runner.execute_in_order(engine, run_ids)
    runner.evaluate_all(engine, run_ids)
    pairs: list[tuple[m.Run, m.Evaluation]] = []
    with Session(engine) as db:
        for run_id in run_ids:
            run = db.get(m.Run, run_id)
            evaluation = db.scalars(select(m.Evaluation).where(m.Evaluation.run_id == run_id)).one()
            assert run is not None
            pairs.append((run, evaluation))
    return pairs


def _scores(engine: Engine, evaluation: m.Evaluation) -> dict[str, m.Score]:
    with Session(engine) as db:
        return {
            s.metric_id: s
            for s in db.scalars(select(m.Score).where(m.Score.evaluation_id == evaluation.id))
        }


def test_retrieval_benchmark_metrics_and_failure_modes(fresh_database: Engine) -> None:
    published = _publish(fresh_database)
    assert published.dataset is not None and published.dataset.coverage_class == "incomplete"
    splits = published.dataset.split_manifest
    assert (len(splits["dev"]), len(splits["held-out"])) == (6, 4)
    slugs = {s.id: str(s.slug) for s in published.scenarios}
    reference = published.agents["retrieval-scripted-reference"].id

    for run, evaluation in _evaluate_all(fresh_database, published):
        slug = slugs[run.scenario_id]
        report: dict[str, Any] = evaluation.report or {}
        scores = _scores(fresh_database, evaluation)
        assert report["suite"]["extensions"][0]["hash"] == RETRIEVAL_SUITE_HASH
        assert evaluation.evaluator_suite_version is not None
        assert "retrieval-core@1.0.0" in evaluation.evaluator_suite_version
        recall, mrr = scores["retrieval_recall_at_k"], scores["retrieval_mrr_at_k"]
        if slug in UNANSWERABLE:
            # Sin relevantes, recall/MRR no aplican: nunca 0 ni 1 por defecto.
            assert (recall.status, recall.value, mrr.status) == (
                "not_applicable",
                None,
                "not_applicable",
            )
            assert report["dimensions"]["retrieval"] == "not_applicable"
        if run.agent_id == reference:
            assert report["task_success"] == "pass", (slug, report["task_success_reasons"])
            if slug not in UNANSWERABLE:
                assert recall.value == 1.0
                assert (recall.numerator, recall.denominator) == (Decimal(1), Decimal(1))
                expected_mrr = 0.5 if slug == "rt-v1-size-exchange" else 1.0
                assert mrr.value == expected_mrr, slug
            continue
        failing, citation_reason = FAULTY[slug]
        failed = {d for d, status in report["dimensions"].items() if status == "fail"}
        assert report["task_success"] == "fail", slug
        assert failed == failing, (slug, report["dimensions"])
        if citation_reason is not None:
            citation = next(c for c in report["checks"] if c["operator"] == "citation_supported")
            assert citation["reason"] == citation_reason, slug
        if slug == "rt-v1-data-retention":
            assert (recall.value, mrr.value) == (0.0, 0.0)
        if slug == "rt-v1-payment-methods":
            retrieval = next(
                c for c in report["checks"] if c["operator"] == "retrieval_relevant_in_top_k"
            )
            assert retrieval["reason"] == "no_retrieval"
