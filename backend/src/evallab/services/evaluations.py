"""Evaluación de runs terminales: cada llamada crea una Evaluation nueva sobre la traza sellada."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from evallab.db import models as m
from evallab.domain.lifecycle import EVALUATION_LIFECYCLE, EvaluationStatus, RunStatus
from evallab.evaluation.engine import SUITE_HASH, SUITE_VERSION, EvaluationInput, evaluate
from evallab.evaluation.metrics import PROFILE_HASH, PROFILE_VERSION, run_observations
from evallab.schemas import EvaluationOut, ScoreOut
from evallab.services.errors import DomainError, NotFoundError


class RunNotEvaluableError(DomainError):
    status_code = 409
    code = "run_not_evaluable"


def _event_dicts(events: list[m.TraceEvent]) -> list[dict[str, Any]]:
    return [
        {
            "event_id": str(e.event_id),
            "sequence": e.sequence,
            "type": e.type,
            "payload": e.payload,
        }
        for e in events
    ]


def _transition(evaluation: m.Evaluation, target: EvaluationStatus, db: Session) -> None:
    EVALUATION_LIFECYCLE.check(EvaluationStatus(evaluation.status), target)
    evaluation.status = target
    db.flush()


def evaluate_run(db: Session, run_id: uuid.UUID) -> m.Evaluation:
    """Evalúa un run terminal con traza sellada. Una evaluación previa de la misma traza queda
    intacta y se referencia como `parent_evaluation_id`."""
    run = db.get(m.Run, run_id, with_for_update=True)
    if run is None:
        raise NotFoundError("run inexistente", run_id=str(run_id))
    if run.status in (RunStatus.QUEUED, RunStatus.RUNNING):
        raise RunNotEvaluableError("el run no ha terminado", status=run.status)
    trace = db.scalar(select(m.Trace).where(m.Trace.run_id == run_id))
    if trace is None or trace.digest is None or trace.completeness is None:
        raise RunNotEvaluableError("el run no tiene traza sellada", status=run.status)
    scenario = db.get(m.Scenario, (run.scenario_id, run.scenario_version))
    if scenario is None:
        raise RunNotEvaluableError("escenario irresoluble")
    parent = db.scalar(
        select(m.Evaluation)
        .where(m.Evaluation.run_id == run_id)
        .order_by(m.Evaluation.created_at.desc(), m.Evaluation.id.desc())
        .limit(1)
    )
    evaluation = m.Evaluation(
        id=uuid.uuid4(),
        run_id=run.id,
        trace_digest=trace.digest,
        evaluator_suite_hash=SUITE_HASH,
        evaluator_suite_version=SUITE_VERSION,
        metric_profile_version=PROFILE_VERSION,
        metric_profile_hash=PROFILE_HASH,
        parent_evaluation_id=parent.id if parent is not None else None,
        status=EvaluationStatus.PENDING,
    )
    db.add(evaluation)
    db.flush()
    _transition(evaluation, EvaluationStatus.RUNNING, db)

    events = list(
        db.scalars(
            select(m.TraceEvent)
            .where(m.TraceEvent.run_id == run_id)
            .order_by(m.TraceEvent.sequence)
        )
    )
    try:
        report = evaluate(
            EvaluationInput(
                expected=scenario.oracle_ref if isinstance(scenario.oracle_ref, dict) else {},
                evaluation=scenario.evaluation if isinstance(scenario.evaluation, dict) else {},
                run_status=run.status,
                run_error_class=run.error_class,
                run_result=run.result if isinstance(run.result, dict) else None,
                events=_event_dicts(events),
                completeness=trace.completeness,
            )
        )
        observations = run_observations(report)
    except Exception as exc:
        evaluation.error = f"evaluator_error: {type(exc).__name__}"
        evaluation.completed_at = datetime.now(UTC)
        _transition(evaluation, EvaluationStatus.ERROR, db)
        return evaluation

    for obs in observations:
        db.add(
            m.Score(
                evaluation_id=evaluation.id,
                metric_id=obs.metric_id,
                metric_version=obs.metric_version,
                scope=obs.scope,
                value=obs.value,
                unit=obs.unit,
                status=obs.status,
                numerator=None if obs.numerator is None else Decimal(obs.numerator),
                denominator=None if obs.denominator is None else Decimal(obs.denominator),
                evidence_refs=[dict(r) for r in obs.evidence_refs],
            )
        )
    evaluation.report = {
        **report.as_json(),
        "metric_profile": {"version": PROFILE_VERSION, "hash": PROFILE_HASH},
    }
    evaluation.completed_at = datetime.now(UTC)
    _transition(evaluation, EvaluationStatus.COMPLETED, db)
    return evaluation


def get_evaluation(db: Session, evaluation_id: uuid.UUID) -> m.Evaluation:
    evaluation = db.get(m.Evaluation, evaluation_id)
    if evaluation is None:
        raise NotFoundError("evaluación inexistente", evaluation_id=str(evaluation_id))
    return evaluation


def list_evaluations(db: Session, run_id: uuid.UUID) -> list[m.Evaluation]:
    if db.get(m.Run, run_id) is None:
        raise NotFoundError("run inexistente", run_id=str(run_id))
    return list(
        db.scalars(
            select(m.Evaluation)
            .where(m.Evaluation.run_id == run_id)
            .order_by(m.Evaluation.created_at, m.Evaluation.id)
        )
    )


def _number(value: Decimal | None) -> int | None:
    return None if value is None else int(value)


def to_out(db: Session, evaluation: m.Evaluation) -> EvaluationOut:
    scores = db.scalars(
        select(m.Score)
        .where(m.Score.evaluation_id == evaluation.id)
        .order_by(m.Score.metric_id, m.Score.scope)
    )
    return EvaluationOut(
        id=evaluation.id,
        run_id=evaluation.run_id,
        trace_digest=evaluation.trace_digest,
        evaluator_suite_hash=evaluation.evaluator_suite_hash,
        evaluator_suite_version=evaluation.evaluator_suite_version,
        metric_profile_version=evaluation.metric_profile_version,
        metric_profile_hash=evaluation.metric_profile_hash,
        parent_evaluation_id=evaluation.parent_evaluation_id,
        status=evaluation.status,
        error=evaluation.error,
        report=evaluation.report,
        scores=[
            ScoreOut(
                metric_id=s.metric_id,
                metric_version=s.metric_version,
                scope=s.scope,
                status=s.status,
                unit=s.unit,
                value=s.value,
                numerator=_number(s.numerator),
                denominator=_number(s.denominator),
                evidence_refs=list(s.evidence_refs),
            )
            for s in scores
        ],
        created_at=evaluation.created_at,
        completed_at=evaluation.completed_at,
    )
