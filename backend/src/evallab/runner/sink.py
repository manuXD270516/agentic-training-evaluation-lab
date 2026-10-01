from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from evallab.canonical import canonical_digest
from evallab.runner.errors import TraceIntegrityError
from evallab.runner.redaction import redact, redaction_metadata
from evallab.telemetry import current_ids

# Un campo redactado en estas llamadas impide reconstruirlas en replay.
REPLAYED_PREFIXES = ("model.", "tool.")


@dataclass(frozen=True)
class MemoryEvent:
    event_id: uuid.UUID
    sequence: int
    timestamp_utc: datetime
    elapsed_ms: int
    type: str
    actor_role: str
    parent_event_id: uuid.UUID | None
    payload: dict[str, Any]
    payload_digest: str
    redaction_metadata: dict[str, Any]
    # Correlación con el span activo; no forma parte de ningún digest.
    otel_trace_id: str | None = None
    otel_span_id: str | None = None


@dataclass
class MemoryTraceSink:
    """Acumula eventos redactados en memoria; el worker los persiste al sellar."""

    started_at: datetime
    schema_version: str
    run_id: uuid.UUID
    attempt_id: uuid.UUID
    _events: list[MemoryEvent] = field(default_factory=list)
    _by_id: dict[uuid.UUID, MemoryEvent] = field(default_factory=dict)
    completeness: str = "complete"

    def append(
        self,
        event_type: str,
        actor_role: str,
        payload: dict[str, Any],
        *,
        parent_event_id: uuid.UUID | None = None,
        event_id: uuid.UUID | None = None,
    ) -> MemoryEvent:
        assigned = event_id or uuid.uuid4()
        redacted, fields = redact(payload)
        digest = canonical_digest(redacted)
        existing = self._by_id.get(assigned)
        if existing is not None:
            if existing.payload_digest == digest and existing.type == event_type:
                return existing
            self.completeness = "invalid"
            raise TraceIntegrityError(
                "event_id reenviado con digest distinto",
            )
        now = datetime.now(UTC)
        otel_trace_id, otel_span_id = current_ids()
        elapsed = max(0, int((now - self.started_at).total_seconds() * 1000))
        event = MemoryEvent(
            event_id=assigned,
            sequence=len(self._events) + 1,
            timestamp_utc=now,
            elapsed_ms=elapsed,
            type=event_type,
            actor_role=actor_role,
            parent_event_id=parent_event_id,
            payload=redacted,
            payload_digest=digest,
            redaction_metadata=redaction_metadata(
                fields, replayable=not event_type.startswith(REPLAYED_PREFIXES)
            ),
            otel_trace_id=otel_trace_id,
            otel_span_id=otel_span_id,
        )
        self._events.append(event)
        self._by_id[assigned] = event
        return event

    @property
    def events(self) -> Sequence[MemoryEvent]:
        return tuple(self._events)

    def digest(self) -> str:
        return trace_digest(
            (e.event_id, e.sequence, e.type, e.payload_digest) for e in self._events
        )


def trace_digest(entries: Iterable[tuple[uuid.UUID | str, int, str, str]]) -> str:
    """Digest sellado: lista ordenada de (event_id, sequence, type, payload_digest)."""
    return canonical_digest(
        [
            {
                "event_id": str(event_id),
                "sequence": sequence,
                "type": event_type,
                "payload_digest": payload_digest,
            }
            for event_id, sequence, event_type, payload_digest in entries
        ]
    )


def mark_incomplete(sink: MemoryTraceSink) -> None:
    if sink.completeness == "complete":
        sink.completeness = "incomplete"
