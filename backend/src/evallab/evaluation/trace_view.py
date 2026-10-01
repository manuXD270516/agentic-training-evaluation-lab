"""Vista de sólo lectura de una traza sellada, indexada por llamada de tool."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolCallView:
    call_id: str
    tool: str
    arguments: Any
    requested_event_id: str
    denied: Mapping[str, Any] | None = None
    validated: bool = False
    completed_event_id: str | None = None
    result: Any = None
    failures: tuple[Mapping[str, Any], ...] = ()

    @property
    def schema_valid(self) -> bool:
        return self.validated

    @property
    def executed(self) -> bool:
        return self.completed_event_id is not None


@dataclass(frozen=True)
class TraceView:
    completeness: str
    calls: tuple[ToolCallView, ...]
    violations: tuple[Mapping[str, Any], ...]
    event_types: Mapping[str, str] = field(default_factory=dict)
    completed_calls: Mapping[str, ToolCallView] = field(default_factory=dict)
    # Eventos `retrieval.completed` en orden: ranking real devuelto por el retriever.
    retrievals: tuple[Mapping[str, Any], ...] = ()

    @classmethod
    def build(cls, events: Iterable[Mapping[str, Any]], completeness: str) -> TraceView:
        ordered = sorted(events, key=lambda e: int(e["sequence"]))
        requests: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        violations: list[Mapping[str, Any]] = []
        retrievals: list[Mapping[str, Any]] = []
        types: dict[str, str] = {}
        for event in ordered:
            event_id = str(event["event_id"])
            kind = str(event["type"])
            payload = event.get("payload") or {}
            types[event_id] = kind
            call_id = payload.get("call_id") if isinstance(payload, dict) else None
            if kind == "tool.requested" and call_id is not None:
                requests[call_id] = {
                    "call_id": call_id,
                    "tool": payload.get("tool"),
                    "arguments": payload.get("arguments"),
                    "requested_event_id": event_id,
                    "failures": [],
                }
                order.append(call_id)
            elif kind == "policy.violation":
                violations.append({"event_id": event_id, **payload})
            elif kind == "retrieval.completed":
                retrievals.append({"event_id": event_id, **payload})
            elif call_id in requests:
                call = requests[call_id]
                if kind == "tool.denied":
                    call["denied"] = payload
                elif kind == "tool.validated":
                    call["validated"] = True
                elif kind == "tool.completed":
                    call["completed_event_id"] = event_id
                    call["result"] = payload.get("result")
                elif kind == "tool.failed":
                    call["failures"].append(payload)
        calls = tuple(
            ToolCallView(
                call_id=c["call_id"],
                tool=str(c["tool"]),
                arguments=c["arguments"],
                requested_event_id=c["requested_event_id"],
                denied=c.get("denied"),
                validated=bool(c.get("validated")),
                completed_event_id=c.get("completed_event_id"),
                result=c.get("result"),
                failures=tuple(c["failures"]),
            )
            for c in (requests[i] for i in order)
        )
        completed = {c.completed_event_id: c for c in calls if c.completed_event_id is not None}
        return cls(
            completeness=completeness,
            calls=calls,
            violations=tuple(violations),
            event_types=types,
            completed_calls=completed,
            retrievals=tuple(retrievals),
        )

    @property
    def reliable(self) -> bool:
        return self.completeness == "complete"

    def calls_to(self, tool: str) -> tuple[ToolCallView, ...]:
        return tuple(c for c in self.calls if c.tool == tool)
