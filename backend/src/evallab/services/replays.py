"""Replays: celda `replay` que reproduce offline la traza sellada de un run `live`.

El replay es una cohorte distinta (mismo escenario, agente, repetición y seed, `mode=replay`)
y nunca sustituye al run de origen ni a sus evaluaciones.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from evallab.canonical import canonical_digest
from evallab.db import models as m
from evallab.domain.lifecycle import ExperimentStatus, RunStatus
from evallab.runner.replay import Recording
from evallab.runner.sink import trace_digest
from evallab.services.errors import (
    DomainError,
    ExperimentNotAcceptingRunsError,
    NotFoundError,
    RunCellExistsError,
)
from evallab.services.experiments import ACCEPTS_RUNS, get_experiment

ACTIVE = {RunStatus.QUEUED, RunStatus.RUNNING}


class ReplayUnavailableError(DomainError):
    status_code = 409
    code = "replay_unavailable"


def source_events(db: Session, run_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(m.TraceEvent)
        .where(m.TraceEvent.run_id == run_id)
        .order_by(m.TraceEvent.sequence, m.TraceEvent.event_id)
    )
    return [
        {
            "event_id": row.event_id,
            "sequence": row.sequence,
            "type": row.type,
            "payload": row.payload,
            "payload_digest": row.payload_digest,
            "redaction_metadata": row.redaction_metadata,
        }
        for row in rows
    ]


def load_recording(db: Session, source_run_id: uuid.UUID) -> tuple[Recording, m.Trace]:
    trace = db.scalar(select(m.Trace).where(m.Trace.run_id == source_run_id))
    if trace is None or trace.sealed_at is None:
        raise ReplayUnavailableError("el run de origen no tiene traza sellada")
    if trace.completeness != "complete":
        raise ReplayUnavailableError(
            "la traza de origen no está completa", completeness=trace.completeness
        )
    events = source_events(db, source_run_id)
    altered = [
        str(e["event_id"]) for e in events if canonical_digest(e["payload"]) != e["payload_digest"]
    ]
    sealed = trace_digest(
        (e["event_id"], e["sequence"], e["type"], e["payload_digest"]) for e in events
    )
    if altered or sealed != trace.digest or len(events) != trace.event_count:
        raise ReplayUnavailableError(
            "la traza de origen no coincide con su digest sellado", events=altered
        )
    unavailable = [
        str(e["event_id"])
        for e in events
        if (e["redaction_metadata"] or {}).get("replay") == "unavailable"
    ]
    if unavailable:
        raise ReplayUnavailableError(
            "la traza de origen tiene llamadas redactadas no reproducibles", events=unavailable
        )
    return Recording.from_events(events), trace


def create_replay(db: Session, source_run_id: uuid.UUID) -> m.Run:
    source = db.get(m.Run, source_run_id)
    if source is None:
        raise NotFoundError("run inexistente", run_id=str(source_run_id))
    if source.mode != "live":
        raise ReplayUnavailableError("sólo se reproduce un run live", mode=source.mode)
    if source.status in ACTIVE:
        raise ReplayUnavailableError("el run de origen no ha terminado", status=source.status)
    load_recording(db, source.id)
    exp = get_experiment(db, source.experiment_id)
    # Un replay reproduce una celda ya ejecutada: también se admite en experimentos completed.
    if exp.status not in {*ACCEPTS_RUNS, ExperimentStatus.COMPLETED}:
        raise ExperimentNotAcceptingRunsError(
            "sólo se reproducen runs de experimentos sellados, en ejecución o completados",
            status=exp.status,
        )
    existing = db.scalar(select(m.Run).where(m.Run.source_run_id == source.id))
    if existing is not None:
        raise RunCellExistsError("el replay de este run ya existe", run_id=str(existing.id))
    run = m.Run(
        experiment_id=source.experiment_id,
        scenario_id=source.scenario_id,
        scenario_version=source.scenario_version,
        agent_id=source.agent_id,
        agent_version=source.agent_version,
        repetition=source.repetition,
        seed=source.seed,
        mode="replay",
        source_run_id=source.id,
    )
    db.add(run)
    db.flush()
    db.refresh(run)
    return run
