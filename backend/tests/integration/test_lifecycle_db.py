"""Los triggers de PostgreSQL aceptan exactamente las transiciones del dominio."""

import itertools
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from evallab.db import models as m
from evallab.domain.lifecycle import (
    EVALUATION_LIFECYCLE,
    EXPERIMENT_LIFECYCLE,
    RUN_LIFECYCLE,
    Lifecycle,
)
from tests.integration import factories as f

pytestmark = pytest.mark.integration

type Row = m.Experiment | m.Run | m.Evaluation


def _apply(row: Row, status: str) -> None:
    """Fija el estado y las columnas que las restricciones CHECK exigen para él."""
    row.status = status
    if isinstance(row, m.Experiment):
        # El sellado nunca se deshace: volver a draft sólo cambia el estado y lo rechaza el trigger.
        if status != "draft" and row.manifest_hash is None:
            row.manifest, row.manifest_hash, row.sealed_at = {}, f.digest(), f.now()
    elif isinstance(row, m.Run):
        if status == "queued":
            row.started_at = None
        elif status == "running":
            row.started_at = row.started_at or f.now()
            row.fencing_token = max(row.fencing_token or 0, 1)
        row.ended_at = None if status in m.RUN_ACTIVE else f.now()
        row.error_class = "infrastructure_error" if status == "failed" else None
    else:
        terminal = status in ("completed", "error")
        row.completed_at = f.now() if terminal else None
        row.report = {} if status == "completed" else None
        row.metric_profile_version = "1.0.0" if status == "completed" else None
        row.metric_profile_hash = f.digest() if status == "completed" else None
        row.error = "test" if status == "error" else None


def _path(lifecycle: Lifecycle[Any], target: str) -> list[str]:
    paths: dict[str, list[str]] = {lifecycle.initial: [lifecycle.initial]}
    frontier = [lifecycle.initial]
    while frontier:
        current = frontier.pop(0)
        for nxt in sorted(lifecycle.transitions.get(current, frozenset())):
            if nxt not in paths:
                paths[nxt] = [*paths[current], nxt]
                frontier.append(nxt)
    return paths[target]


def _attempt(
    db: Session,
    make: Callable[[Session], Row],
    lifecycle: Lifecycle[Any],
    current: str,
    target: str,
) -> DBAPIError | None:
    nested = db.begin_nested()
    try:
        row = make(db)
        for step in _path(lifecycle, current)[1:]:
            _apply(row, step)
            db.flush()
        _apply(row, target)
        try:
            db.flush()
        except DBAPIError as exc:
            return exc
        return None
    finally:
        nested.rollback()


CASES: list[tuple[str, Callable[[Session], Row], Lifecycle[Any]]] = [
    ("experiment", f.experiment, EXPERIMENT_LIFECYCLE),
    ("run", f.run, RUN_LIFECYCLE),
    ("evaluation", f.evaluation, EVALUATION_LIFECYCLE),
]


@pytest.mark.parametrize(("entity", "make", "lifecycle"), CASES, ids=[c[0] for c in CASES])
def test_database_accepts_exactly_the_domain_transitions(
    session: Session,
    entity: str,
    make: Callable[[Session], Row],
    lifecycle: Lifecycle[Any],
) -> None:
    for current, target in itertools.permutations(sorted(lifecycle.states), 2):
        error = _attempt(session, make, lifecycle, current, target)
        if lifecycle.allows(current, target):
            assert error is None, f"{entity}: {current} -> {target} debería aceptarse: {error}"
        else:
            assert error is not None, f"{entity}: {current} -> {target} debería rechazarse"
            assert getattr(error.orig, "sqlstate", None) == "23514"
            assert "invalid status transition" in str(error.orig)


def test_reopening_a_terminal_run_is_rejected(session: Session) -> None:
    run = f.run(session)
    for status in ("running", "completed"):
        _apply(run, status)
        session.flush()
    _apply(run, "running")
    with pytest.raises(
        DBAPIError, match=r"invalid status transition on runs: completed -> running"
    ):
        session.flush()


def test_queued_runs_can_be_cancelled_without_starting(session: Session) -> None:
    run = f.run(session)
    _apply(run, "cancelled")
    session.flush()
    session.refresh(run)
    assert (run.status, run.started_at) == ("cancelled", None)
    assert run.ended_at is not None


@pytest.mark.parametrize(("entity", "make", "lifecycle"), CASES, ids=[c[0] for c in CASES])
def test_rows_must_be_created_in_initial_state(
    session: Session,
    entity: str,
    make: Callable[[Session], Row],
    lifecycle: Lifecycle[Any],
) -> None:
    template = make(session)
    table = m.Base.metadata.tables[template.__tablename__]
    values = {c.name: getattr(template, c.name) for c in table.columns if c.name != "id"}
    for status in sorted(lifecycle.states - {lifecycle.initial}):
        nested = session.begin_nested()
        with pytest.raises(DBAPIError, match=f"invalid initial status on {table.name}: {status}"):
            session.execute(table.insert().values(**{**values, "status": status}))
        nested.rollback()
