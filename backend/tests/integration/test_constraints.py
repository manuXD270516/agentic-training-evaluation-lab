import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from evallab.db import models as m
from tests.integration import factories as f

pytestmark = pytest.mark.integration

UNIQUE, FOREIGN_KEY, CHECK = "23505", "23503", "23514"


@contextmanager
def rejected(db: Session, sqlstate: str, constraint: str) -> Iterator[None]:
    nested = db.begin_nested()
    with pytest.raises(DBAPIError) as excinfo:
        yield
        db.flush()
    nested.rollback()
    diag = getattr(excinfo.value.orig, "diag", None)
    assert getattr(excinfo.value.orig, "sqlstate", None) == sqlstate
    assert getattr(diag, "constraint_name", None) == constraint


def test_duplicate_experimental_cell_is_rejected(session: Session) -> None:
    run = f.run(session)
    duplicate = m.Run(
        experiment_id=run.experiment_id,
        scenario_id=run.scenario_id,
        scenario_version=run.scenario_version,
        agent_id=run.agent_id,
        agent_version=run.agent_version,
        repetition=run.repetition,
        seed=run.seed,
        mode="live",
    )
    with rejected(session, UNIQUE, "uq_runs_experimental_cell"):
        session.add(duplicate)


def test_run_requires_agent_declared_in_experiment(session: Session) -> None:
    run = f.run(session)
    other = f.agent(session)
    with rejected(session, FOREIGN_KEY, "fk_runs_experiment_id_experiment_agents"):
        session.add(
            m.Run(
                experiment_id=run.experiment_id,
                scenario_id=run.scenario_id,
                scenario_version=run.scenario_version,
                agent_id=other.id,
                agent_version=other.version,
                repetition=1,
                seed=11,
                mode="live",
            )
        )


def test_run_rejects_reevaluation_as_mode(session: Session) -> None:
    run = f.run(session)
    with rejected(session, CHECK, "ck_runs_mode"):
        run.mode = "reevaluation"


def test_failed_run_requires_error_class(session: Session) -> None:
    run = f.run(session)
    run.status, run.started_at = "running", f.now()
    session.flush()
    with rejected(session, CHECK, "ck_runs_failed_has_error"):
        run.status, run.ended_at = "failed", f.now()


def test_evaluation_requires_sealed_trace_digest(session: Session) -> None:
    run = f.run(session)
    session.add(m.Trace(run_id=run.id, schema_version="1.0"))
    session.flush()
    with rejected(session, FOREIGN_KEY, "fk_evaluations_run_id_traces"):
        session.add(
            m.Evaluation(run_id=run.id, trace_digest=f.digest(), evaluator_suite_hash=f.digest())
        )


def test_reevaluation_preserves_previous_evaluation(session: Session) -> None:
    first = f.evaluation(session)
    second = m.Evaluation(
        run_id=first.run_id,
        trace_digest=first.trace_digest,
        evaluator_suite_hash=f.digest(),
        parent_evaluation_id=first.id,
    )
    session.add(second)
    session.flush()
    rows = session.scalars(select(m.Evaluation).where(m.Evaluation.run_id == first.run_id)).all()
    assert {r.id for r in rows} == {first.id, second.id}
    assert first.evaluator_suite_hash != second.evaluator_suite_hash


def _score(evaluation: m.Evaluation, **overrides: object) -> m.Score:
    values: dict[str, object] = {
        "evaluation_id": evaluation.id,
        "metric_id": "task_success_rate",
        "metric_version": "1.0.0",
        "scope": "agent",
        "unit": "ratio",
        "status": "pass",
        "value": 0.7,
        "numerator": Decimal(7),
        "denominator": Decimal(10),
    }
    return m.Score(**(values | overrides))


def test_missing_scores_keep_null_value(session: Session) -> None:
    evaluation = f.evaluation(session)
    unknown = _score(evaluation, status="unknown", value=None, numerator=None, denominator=None)
    not_applicable = _score(
        evaluation,
        metric_id="tool_accuracy",
        status="not_applicable",
        value=None,
        numerator=Decimal(0),
        denominator=Decimal(0),
    )
    session.add_all([unknown, not_applicable])
    session.flush()
    session.refresh(unknown)
    assert unknown.value is None


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"status": "unknown"}, "ck_scores_missing_has_no_value"),
        ({"status": "error"}, "ck_scores_missing_has_no_value"),
        (
            {"value": 1.0, "numerator": Decimal(0), "denominator": Decimal(0)},
            "ck_scores_zero_denominator_no_value",
        ),
        ({"value": float("nan")}, "ck_scores_value_finite"),
        ({"numerator": Decimal(11)}, "ck_scores_ratio_bounds"),
        ({"denominator": None}, "ck_scores_ratio_complete"),
        ({"scope": "system"}, "ck_scores_scope"),
    ],
)
def test_invalid_scores_are_rejected(
    session: Session, overrides: dict[str, object], constraint: str
) -> None:
    evaluation = f.evaluation(session)
    with rejected(session, CHECK, constraint):
        session.add(_score(evaluation, **overrides))


def test_duplicate_score_per_metric_version_and_scope_is_rejected(session: Session) -> None:
    evaluation = f.evaluation(session)
    session.add(_score(evaluation))
    session.flush()
    with rejected(session, UNIQUE, "uq_scores_evaluation_id_metric_id_metric_version_scope"):
        session.add(_score(evaluation))


def test_sealed_experiment_requires_manifest_hash(session: Session) -> None:
    experiment = f.experiment(session)
    with rejected(session, CHECK, "ck_experiments_sealed_has_manifest"):
        experiment.status = "sealed"


def test_experiment_requires_one_seed_per_repetition(session: Session) -> None:
    bench = f.benchmark(session)
    with rejected(session, CHECK, "ck_experiments_one_seed_per_repetition"):
        session.add(
            m.Experiment(
                hypothesis="test",
                benchmark_id=bench.id,
                benchmark_version=bench.version,
                repetitions=5,
                seeds=[11, 23],
            )
        )


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"primary_category": "chat"}, "ck_scenarios_category"),
        ({"version": "latest"}, "ck_scenarios_version_semver"),
        ({"content_hash": "not-a-digest"}, "ck_scenarios_content_hash_sha256"),
    ],
)
def test_invalid_scenarios_are_rejected(
    session: Session, overrides: dict[str, object], constraint: str
) -> None:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "version": "1.0.0",
        "primary_category": "reasoning",
        "input": {},
        "oracle_ref": {},
        "limits": {},
        "content_hash": f.digest(),
    }
    with rejected(session, CHECK, constraint):
        session.add(m.Scenario(**(values | overrides)))


def test_dataset_scenario_order_is_unique(session: Session) -> None:
    bench = f.benchmark(session)
    first, second = f.scenario(session), f.scenario(session)
    link = {"dataset_id": bench.dataset_id, "dataset_version": bench.dataset_version}
    session.add(
        m.DatasetScenario(**link, scenario_id=first.id, scenario_version=first.version, position=0)
    )
    session.flush()
    with rejected(session, UNIQUE, "uq_dataset_scenarios_dataset_id_dataset_version_position"):
        session.add(
            m.DatasetScenario(
                **link, scenario_id=second.id, scenario_version=second.version, position=0
            )
        )
