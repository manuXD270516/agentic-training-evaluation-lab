from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from evallab.canonical import canonical_digest
from evallab.runner.errors import TraceIntegrityError


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


@dataclass
class MemoryTraceSink:
    """Acumula eventos en memoria; el worker los persiste al sellar (M4 añade export/OTel)."""

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
        digest = canonical_digest(payload)
        existing = self._by_id.get(assigned)
        if existing is not None:
            if existing.payload_digest == digest and existing.type == event_type:
                return existing
            self.completeness = "invalid"
            raise TraceIntegrityError(
                "event_id reenviado con digest distinto",
            )
        now = datetime.now(UTC)
        elapsed = max(0, int((now - self.started_at).total_seconds() * 1000))
        event = MemoryEvent(
            event_id=assigned,
            sequence=len(self._events) + 1,
            timestamp_utc=now,
            elapsed_ms=elapsed,
            type=event_type,
            actor_role=actor_role,
            parent_event_id=parent_event_id,
            payload=payload,
            payload_digest=digest,
            redaction_metadata={},
        )
        self._events.append(event)
        self._by_id[assigned] = event
        return event

    @property
    def events(self) -> Sequence[MemoryEvent]:
        return tuple(self._events)

    def digest(self) -> str:
        return canonical_digest(
            [
                {
                    "event_id": str(event.event_id),
                    "sequence": event.sequence,
                    "type": event.type,
                    "payload_digest": event.payload_digest,
                }
                for event in self._events
            ]
        )


def mark_incomplete(sink: MemoryTraceSink) -> None:
    if sink.completeness == "complete":
        sink.completeness = "incomplete"
