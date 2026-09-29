"""Persistencia idempotente, conflicto de digest, export verificable y secretos ausentes."""

import dataclasses
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from evallab.api.app import create_app
from evallab.db import models as m
from evallab.domain.lifecycle import RunStatus
from evallab.runner.errors import TraceIntegrityError
from evallab.runner.redaction import MARKER
from evallab.services.execution import (
    AttemptOutcome,
    claim_next_run,
    execute_run,
    finish_attempt,
    run_attempt,
)
from evallab.services.traces import export_trace, persist_events, verify_export
from evallab.verify_trace import main as verify_main
from tests.integration.test_execution import _world

pytestmark = pytest.mark.integration

API_KEY = "sk-live-0123456789abcdefXYZ"
PASSWORD = "hunter2-synthetic"
SECRET_RESULT = {"total": 42, "api_key": API_KEY, "owner": {"password": PASSWORD}}


@pytest.fixture
def client(migrated_database: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(engine=migrated_database)) as test_client:
        yield test_client


def _event_count(db: Session, run_id: uuid.UUID) -> int:
    return (
        db.scalar(
            select(func.count()).select_from(m.TraceEvent).where(m.TraceEvent.run_id == run_id)
        )
        or 0
    )


def _finished_attempt(engine: Engine) -> AttemptOutcome:
    """Reclama, ejecuta y cierra un intento; devuelve el outcome para reenviarlo."""
    with Session(engine) as db, db.begin():
        world = _world(db)
    with Session(engine) as db, db.begin():
        claim = claim_next_run(
            db, worker_id="w1", lease_s=300, max_attempts=2, now=datetime.now(UTC)
        )
    assert claim is not None and claim.run_id == world.run_ids[0]
    with Session(engine) as db:
        outcome = run_attempt(db, claim)
    with Session(engine) as db, db.begin():
        assert finish_attempt(db, outcome)
    return outcome


def test_resent_attempt_is_confirmed_without_duplicates(fresh_database: Engine) -> None:
    outcome = _finished_attempt(fresh_database)
    run_id = outcome.claim.run_id
    assert outcome.sink is not None
    with Session(fresh_database) as db, db.begin():
        before = _event_count(db, run_id)
        trace = db.scalars(select(m.Trace).where(m.Trace.run_id == run_id)).one()
        digest = trace.digest
        assert finish_attempt(db, outcome)
        assert persist_events(db, run_id, outcome.claim.attempt_id, outcome.sink.events) == 0
    with Session(fresh_database) as db:
        assert _event_count(db, run_id) == before == len(outcome.sink.events)
        trace = db.scalars(select(m.Trace).where(m.Trace.run_id == run_id)).one()
        assert trace.digest == digest
        attempt = db.get(m.RunAttempt, outcome.claim.attempt_id)
        assert attempt is not None and attempt.status == "finished"
        run = db.get(m.Run, run_id)
        assert run is not None and run.status == RunStatus.COMPLETED


def test_resent_event_with_other_digest_is_integrity_error(fresh_database: Engine) -> None:
    outcome = _finished_attempt(fresh_database)
    assert outcome.sink is not None
    events = list(outcome.sink.events)
    events[-1] = dataclasses.replace(events[-1], payload_digest="0" * 64)
    with Session(fresh_database) as db, pytest.raises(TraceIntegrityError):
        persist_events(db, outcome.claim.run_id, outcome.claim.attempt_id, events)


def test_resent_attempt_with_other_evidence_is_rejected(fresh_database: Engine) -> None:
    outcome = _finished_attempt(fresh_database)
    assert outcome.sink is not None
    extra = dataclasses.replace(outcome.sink, _events=[], _by_id={})
    for event in outcome.sink.events[:-1]:
        extra.append(event.type, event.actor_role, dict(event.payload), event_id=event.event_id)
    altered = dataclasses.replace(outcome, sink=extra)
    with Session(fresh_database) as db, pytest.raises(TraceIntegrityError):
        finish_attempt(db, altered)


def _executed(engine: Engine, **world: object) -> uuid.UUID:
    with Session(engine) as db, db.begin():
        built = _world(db, **world)  # type: ignore[arg-type]
        execute_run(db, built.run_ids[0])
        return built.run_ids[0]


def test_export_verifies_and_matches_sealed_trace(
    migrated_database: Engine, client: TestClient
) -> None:
    run_id = _executed(migrated_database)
    manifest = client.get(f"/runs/{run_id}/trace/manifest").json()
    response = client.get(f"/runs/{run_id}/trace/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.headers["x-trace-digest"] == manifest["trace_digest"]
    jsonl = response.content
    assert verify_export(manifest, jsonl) == []
    trace = client.get(f"/runs/{run_id}/trace").json()
    lines = [json.loads(line) for line in jsonl.splitlines()]
    assert manifest["trace_digest"] == trace["digest"]
    assert manifest["event_count"] == len(lines) == trace["event_count"]
    assert [e["event_id"] for e in lines] == [e["event_id"] for e in trace["events"]]
    assert {e["schema_version"] for e in lines} == {trace["schema_version"]}


def test_truncated_or_altered_export_fails_verification(migrated_database: Engine) -> None:
    run_id = _executed(migrated_database)
    with Session(migrated_database) as db:
        export = export_trace(db, run_id)
    lines = export.jsonl.splitlines(keepends=True)

    dropped = b"".join(lines[:-1])
    problems = verify_export(export.manifest, dropped)
    assert {"event_count_mismatch", "events_sha256_mismatch", "trace_digest_mismatch"} <= set(
        problems
    )

    cut = export.jsonl[: len(export.jsonl) - 10]
    assert "truncated_line" in verify_export(export.manifest, cut)

    event = json.loads(lines[0])
    event["payload"]["tampered"] = True
    tampered = json.dumps(event).encode() + b"\n" + b"".join(lines[1:])
    assert any(
        p.startswith("payload_digest_mismatch") for p in verify_export(export.manifest, tampered)
    )

    forged = {**export.manifest, "event_count": export.manifest["event_count"] - 1}
    assert "manifest_digest_mismatch" in verify_export(forged, dropped)


def test_verify_cli(migrated_database: Engine, tmp_path: Path) -> None:
    run_id = _executed(migrated_database)
    with Session(migrated_database) as db:
        export = export_trace(db, run_id)
    manifest = tmp_path / "manifest.json"
    events = tmp_path / "events.jsonl"
    manifest.write_text(json.dumps(export.manifest), encoding="utf-8")
    events.write_bytes(export.jsonl)
    assert verify_main([str(manifest), str(events)]) == 0
    events.write_bytes(export.jsonl.splitlines(keepends=True)[0])
    assert verify_main([str(manifest), str(events)]) == 1
    assert verify_main([]) == 2


def test_secrets_are_absent_from_trace_export_and_result(
    migrated_database: Engine, client: TestClient
) -> None:
    run_id = _executed(migrated_database, calculator_result=SECRET_RESULT)
    run = client.get(f"/runs/{run_id}").json()
    trace = client.get(f"/runs/{run_id}/trace")
    export = client.get(f"/runs/{run_id}/trace/export")
    manifest = client.get(f"/runs/{run_id}/trace/manifest").json()
    for body in (json.dumps(run), trace.text, export.text, json.dumps(manifest)):
        assert API_KEY not in body
        assert PASSWORD not in body
    assert run["status"] == "completed"
    completed = next(e for e in trace.json()["events"] if e["type"] == "tool.completed")
    assert completed["payload"]["result"]["total"] == 42
    assert completed["payload"]["result"]["api_key"] == MARKER
    metadata = completed["redaction_metadata"]
    assert metadata["replay"] == "unavailable"
    assert {f["pointer"] for f in metadata["fields"]} == {
        "/result/api_key",
        "/result/owner/password",
    }
    assert manifest["redacted_events"] >= 1
    assert verify_export(manifest, export.content) == []
