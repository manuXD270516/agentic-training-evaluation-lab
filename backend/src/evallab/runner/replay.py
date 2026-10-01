"""Replay estricto offline: sirve los resultados grabados en la traza de origen.

Cada request emitido se compara por digest (tool + argumentos redactados, en orden) con el
grabado; cualquier divergencia termina `replay_mismatch`. Nunca ejecuta fixtures ni llama a
proveedores: no existe camino de fallback a live.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from evallab.canonical import canonical_digest
from evallab.runner.contracts import AllowedTool, ToolOutcome
from evallab.runner.errors import RunnerError
from evallab.runner.redaction import redact

# Campos de run.started que deben coincidir para considerar la reproducción idéntica.
START_FIELDS = ("manifest_hash", "scenario_ref", "agent_ref", "limits", "seed", "tools", "models")


class ReplayMismatchError(RunnerError):
    error_class = "replay_mismatch"


def request_digest(tool: str, arguments: Any) -> str:
    redacted, _ = redact(arguments)
    return canonical_digest({"tool": tool, "arguments": redacted})


@dataclass
class RecordedCall:
    tool: str
    digest: str
    outcomes: list[ToolOutcome] = field(default_factory=list)
    replayable: bool = True


def _outcome(event_type: str, payload: Mapping[str, Any]) -> ToolOutcome | None:
    reasons = tuple(payload.get("reason_codes") or ())
    if event_type == "tool.denied":
        if payload.get("policy_result") == "denied":
            return ToolOutcome(kind="denied", validated=False, reason_codes=reasons)
        return ToolOutcome(
            kind="invalid",
            validated=False,
            reason_codes=reasons,
            schema_errors=tuple(dict(e) for e in payload.get("schema_errors") or ()),
            error_class=payload.get("error_class"),
        )
    if event_type == "tool.failed":
        return ToolOutcome(
            kind="failed",
            validated=True,
            reason_codes=reasons,
            error=payload.get("error"),
            error_class=payload.get("error_class"),
            retriable=bool(payload.get("retriable")),
            ambiguous_effect=bool(payload.get("ambiguous_effect")),
            fault_id=payload.get("fault_id"),
            timeout_ms=payload.get("timeout_ms"),
        )
    if event_type == "tool.completed":
        return ToolOutcome(
            kind="completed",
            validated=True,
            result=payload.get("result"),
            state_digest=payload.get("state_digest"),
            idempotent_replay=bool(payload.get("idempotent_replay")),
        )
    return None


@dataclass(frozen=True)
class Recording:
    started: Mapping[str, Any]
    calls: tuple[RecordedCall, ...]

    @classmethod
    def from_events(cls, events: Iterable[Mapping[str, Any]]) -> Recording:
        ordered = sorted(events, key=lambda e: int(e["sequence"]))
        started: Mapping[str, Any] = {}
        calls: dict[str, RecordedCall] = {}
        for event in ordered:
            kind = str(event["type"])
            payload: Mapping[str, Any] = event.get("payload") or {}
            unavailable = (event.get("redaction_metadata") or {}).get("replay") == "unavailable"
            if kind == "run.started":
                started = payload
                continue
            call_id = payload.get("call_id")
            if call_id is None:
                continue
            if kind == "tool.requested":
                calls[call_id] = RecordedCall(
                    tool=str(payload.get("tool")),
                    digest=request_digest(str(payload.get("tool")), payload.get("arguments")),
                    replayable=not unavailable,
                )
                continue
            recorded = calls.get(call_id)
            outcome = _outcome(kind, payload)
            if recorded is None or outcome is None:
                continue
            recorded.outcomes.append(outcome)
            recorded.replayable = recorded.replayable and not unavailable
        return cls(started=started, calls=tuple(calls.values()))

    def start_mismatch(self, started: Mapping[str, Any]) -> str | None:
        differing = [f for f in START_FIELDS if self.started.get(f) != started.get(f)]
        if differing:
            return "condiciones distintas de la grabación: " + ", ".join(differing)
        return None


class ReplayToolGateway:
    """ToolGateway que responde con la grabación; nunca ejecuta la tool."""

    def __init__(self, allowed: Sequence[AllowedTool], recording: Recording) -> None:
        self._allowed = {tool.name: tool for tool in allowed}
        self._recording = recording
        self._position = 0
        self._current: uuid.UUID | None = None
        self._attempt = 0

    @property
    def recording(self) -> Recording:
        return self._recording

    def allowed_tools(self) -> Sequence[AllowedTool]:
        return tuple(self._allowed.values())

    def resolve(self, name: str) -> AllowedTool | None:
        return self._allowed.get(name)

    def invoke(self, name: str, call_id: uuid.UUID, arguments: dict[str, Any]) -> ToolOutcome:
        if call_id != self._current:
            self._current, self._attempt = call_id, 0
            self._position += 1
        self._attempt += 1
        index = self._position - 1
        calls = self._recording.calls
        if index >= len(calls):
            raise ReplayMismatchError(f"request {index + 1} no existe en la grabación")
        recorded = calls[index]
        if recorded.digest != request_digest(name, arguments):
            raise ReplayMismatchError(f"request {index + 1} no coincide con el grabado")
        if not recorded.replayable:
            raise ReplayMismatchError(f"request {index + 1} no reproducible: evidencia redactada")
        if self._attempt > len(recorded.outcomes):
            raise ReplayMismatchError(f"request {index + 1} sin resultado grabado")
        return recorded.outcomes[self._attempt - 1]

    def pending(self) -> int:
        """Llamadas grabadas que el replay no volvió a emitir."""
        return len(self._recording.calls) - self._position
