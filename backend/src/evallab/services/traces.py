"""Persistencia durable e idempotente de eventos, sellado, export JSONL y su verificación.

El export es un JSONL (un evento canónico RFC 8785 por línea, terminado en `\\n`) y un manifest
que fija contador, último sequence, digest sellado y SHA-256 de los bytes del JSONL. Un export
truncado o alterado no verifica.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from evallab.canonical import canonical_digest, canonical_json, sha256_hex
from evallab.db import models as m
from evallab.runner.errors import TraceIntegrityError
from evallab.runner.sink import MemoryEvent, trace_digest
from evallab.services.errors import DomainError, NotFoundError

EXPORT_FORMAT = "evallab-trace-export"
EXPORT_VERSION = "1.0.0"


class TraceNotSealedError(DomainError):
    status_code = 409
    code = "trace_not_sealed"


def persist_events(
    db: Session, run_id: uuid.UUID, attempt_id: uuid.UUID, events: Sequence[MemoryEvent]
) -> int:
    """Inserta eventos por `event_id`; reenviar el mismo digest confirma sin duplicar.

    Un `event_id` existente con otro digest, tipo, secuencia o run es error de integridad.
    Devuelve cuántos eventos eran nuevos.
    """
    ids = [event.event_id for event in events]
    existing = {
        row.event_id: row
        for row in db.scalars(select(m.TraceEvent).where(m.TraceEvent.event_id.in_(ids)))
    }
    inserted = 0
    for event in events:
        stored = existing.get(event.event_id)
        if stored is not None:
            same = (
                stored.run_id == run_id
                and stored.payload_digest == event.payload_digest
                and stored.type == event.type
                and stored.sequence == event.sequence
            )
            if not same:
                raise TraceIntegrityError(f"event_id {event.event_id} reenviado con otro digest")
            continue
        db.add(
            m.TraceEvent(
                event_id=event.event_id,
                run_id=run_id,
                attempt_id=attempt_id,
                sequence=event.sequence,
                timestamp_utc=event.timestamp_utc,
                elapsed_ms=event.elapsed_ms,
                type=event.type,
                actor_role=event.actor_role,
                parent_event_id=event.parent_event_id,
                payload=event.payload,
                payload_digest=event.payload_digest,
                redaction_metadata=event.redaction_metadata,
                otel_trace_id=event.otel_trace_id,
                otel_span_id=event.otel_span_id,
            )
        )
        inserted += 1
    db.flush()
    return inserted


def seal_trace(
    db: Session,
    trace: m.Trace,
    events: Sequence[MemoryEvent],
    completeness: str,
    sealed_at: datetime,
) -> None:
    """Sella contador, digest y completitud; una traza sellada no admite otro digest."""
    digest = trace_digest((e.event_id, e.sequence, e.type, e.payload_digest) for e in events)
    if trace.sealed_at is not None:
        if trace.digest != digest or trace.event_count != len(events):
            raise TraceIntegrityError(f"traza del run {trace.run_id} ya sellada con otro digest")
        return
    trace.event_count = len(events)
    trace.digest = digest
    trace.completeness = completeness
    trace.sealed_at = sealed_at
    db.flush()


def _event_document(trace: m.Trace, event: m.TraceEvent) -> dict[str, Any]:
    return {
        "schema_version": trace.schema_version,
        "event_id": str(event.event_id),
        "run_id": str(event.run_id),
        "attempt_id": str(event.attempt_id),
        "sequence": event.sequence,
        "timestamp_utc": event.timestamp_utc.isoformat(),
        "elapsed_ms": event.elapsed_ms,
        "type": event.type,
        "actor_role": event.actor_role,
        "parent_event_id": str(event.parent_event_id) if event.parent_event_id else None,
        "otel_trace_id": event.otel_trace_id,
        "otel_span_id": event.otel_span_id,
        "payload": event.payload,
        "payload_digest": event.payload_digest,
        "redaction_metadata": event.redaction_metadata,
    }


@dataclass(frozen=True)
class TraceExport:
    manifest: dict[str, Any]
    jsonl: bytes


def _manifest_digest(manifest: dict[str, Any]) -> str:
    return canonical_digest({k: v for k, v in manifest.items() if k != "manifest_digest"})


def export_trace(db: Session, run_id: uuid.UUID) -> TraceExport:
    """Exporta todos los eventos de una traza sellada, sin sampling."""
    trace = db.scalar(select(m.Trace).where(m.Trace.run_id == run_id))
    if trace is None:
        raise NotFoundError("traza inexistente", run_id=str(run_id))
    if trace.sealed_at is None or trace.digest is None:
        raise TraceNotSealedError("la traza no está sellada", run_id=str(run_id))
    events = list(
        db.scalars(
            select(m.TraceEvent)
            .where(m.TraceEvent.run_id == run_id)
            .order_by(m.TraceEvent.sequence, m.TraceEvent.event_id)
        )
    )
    jsonl = b"".join(canonical_json(_event_document(trace, e)) + b"\n" for e in events)
    manifest: dict[str, Any] = {
        "format": EXPORT_FORMAT,
        "format_version": EXPORT_VERSION,
        "schema_version": trace.schema_version,
        "run_id": str(run_id),
        "event_count": trace.event_count,
        "last_sequence": events[-1].sequence if events else 0,
        "completeness": trace.completeness,
        "trace_digest": trace.digest,
        "events_sha256": sha256_hex(jsonl),
        "redacted_events": sum(1 for e in events if e.redaction_metadata),
        "sealed_at": trace.sealed_at.isoformat(),
    }
    manifest["manifest_digest"] = _manifest_digest(manifest)
    return TraceExport(manifest=manifest, jsonl=jsonl)


def _parse_lines(jsonl: bytes, problems: list[str]) -> list[dict[str, Any]]:
    if jsonl and not jsonl.endswith(b"\n"):
        problems.append("truncated_line")
    events: list[dict[str, Any]] = []
    for number, line in enumerate(jsonl.split(b"\n")[:-1] if jsonl else [], start=1):
        try:
            value = json.loads(line)
        except ValueError:
            problems.append(f"invalid_json:{number}")
            continue
        if not isinstance(value, dict):
            problems.append(f"invalid_event:{number}")
            continue
        events.append(value)
    return events


def _check_events(events: Iterable[dict[str, Any]], problems: list[str]) -> list[tuple[Any, ...]]:
    entries: list[tuple[Any, ...]] = []
    for expected, event in enumerate(events, start=1):
        if event.get("sequence") != expected:
            problems.append(f"sequence_gap:{expected}")
        if canonical_digest(event.get("payload")) != event.get("payload_digest"):
            problems.append(f"payload_digest_mismatch:{event.get('event_id')}")
        entries.append(
            (
                event.get("event_id"),
                event.get("sequence"),
                event.get("type"),
                event.get("payload_digest"),
            )
        )
    return entries


def verify_export(manifest: dict[str, Any], jsonl: bytes) -> list[str]:
    """Problemas del export frente a su manifest; lista vacía significa verificado."""
    problems: list[str] = []
    if manifest.get("format") != EXPORT_FORMAT:
        problems.append("unknown_format")
    if manifest.get("manifest_digest") != _manifest_digest(manifest):
        problems.append("manifest_digest_mismatch")
    if sha256_hex(jsonl) != manifest.get("events_sha256"):
        problems.append("events_sha256_mismatch")
    events = _parse_lines(jsonl, problems)
    if len(events) != manifest.get("event_count"):
        problems.append("event_count_mismatch")
    if (events[-1].get("sequence") if events else 0) != manifest.get("last_sequence"):
        problems.append("last_sequence_mismatch")
    entries = _check_events(events, problems)
    if trace_digest(entries) != manifest.get("trace_digest"):
        problems.append("trace_digest_mismatch")
    return problems
