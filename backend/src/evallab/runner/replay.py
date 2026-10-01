"""Replay estricto offline: sirve los resultados grabados en la traza de origen.

Cada request emitido se compara por digest (tool + argumentos redactados, en orden) con el
grabado; cualquier divergencia termina `replay_mismatch`. Nunca ejecuta fixtures ni llama a
proveedores: no existe camino de fallback a live.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from evallab.canonical import canonical_digest
from evallab.runner.contracts import AllowedTool, ToolOutcome
from evallab.runner.errors import RunnerError
from evallab.runner.models import (
    CostEstimate,
    ModelCallError,
    ModelRequest,
    ModelResult,
    ModelSnapshot,
    ProviderResponse,
    TokenUsage,
    tool_calls_from_json,
)
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
class RecordedModelAttempt:
    """Un intento grabado de llamada a modelo: respuesta completa o fallo tipado."""

    digest: str
    role: str
    replayable: bool
    completed: Mapping[str, Any] | None = None
    failed: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class Recording:
    started: Mapping[str, Any]
    calls: tuple[RecordedCall, ...]
    model_attempts: tuple[RecordedModelAttempt, ...] = ()

    @classmethod
    def from_events(cls, events: Iterable[Mapping[str, Any]]) -> Recording:
        ordered = sorted(events, key=lambda e: int(e["sequence"]))
        started: Mapping[str, Any] = {}
        calls: dict[str, RecordedCall] = {}
        requests: dict[str, Mapping[str, Any]] = {}
        model_attempts: list[RecordedModelAttempt] = []
        for event in ordered:
            kind = str(event["type"])
            payload: Mapping[str, Any] = event.get("payload") or {}
            unavailable = (event.get("redaction_metadata") or {}).get("replay") == "unavailable"
            if kind == "run.started":
                started = payload
                continue
            if kind == "model.requested":
                requests[str(event["event_id"])] = {**payload, "_unavailable": unavailable}
                continue
            if kind in ("model.completed", "model.failed"):
                request = requests.get(str(payload.get("request_event_id")), {})
                model_attempts.append(
                    RecordedModelAttempt(
                        digest=str(request.get("request_digest")),
                        role=str((request.get("input") or {}).get("role")),
                        replayable=not unavailable and not request.get("_unavailable"),
                        completed=payload if kind == "model.completed" else None,
                        failed=payload if kind == "model.failed" else None,
                    )
                )
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
            if kind == "retrieval.completed" and recorded is not None and recorded.outcomes:
                # El ranking grabado acompaña al resultado para reemitir retrieval.completed.
                ranking = {
                    key: value
                    for key, value in payload.items()
                    if key not in ("call_id", "query_id")
                }
                recorded.outcomes[-1] = dataclasses.replace(
                    recorded.outcomes[-1], retrieval=ranking
                )
                continue
            outcome = _outcome(kind, payload)
            if recorded is None or outcome is None:
                continue
            recorded.outcomes.append(outcome)
            recorded.replayable = recorded.replayable and not unavailable
        return cls(
            started=started, calls=tuple(calls.values()), model_attempts=tuple(model_attempts)
        )

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


def _recorded_result(attempt: RecordedModelAttempt, model: ModelSnapshot) -> ModelResult:
    payload = attempt.completed or {}
    output = payload.get("output") or {}
    usage = payload.get("usage") or {}
    cost = payload.get("cost") or {}
    source = usage.get("source")
    amount = cost.get("amount")
    return ModelResult(
        response=ProviderResponse(
            content=output.get("content"),
            tool_calls=tool_calls_from_json(output.get("tool_calls") or []),
            finish_reason=payload.get("finish_reason"),
            usage=TokenUsage(
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                cached_input_tokens=usage.get("cached_input_tokens"),
                reasoning_tokens=usage.get("reasoning_tokens"),
                source=source if source in ("observed", "estimated") else "unknown",
            ),
            provider_request_id=payload.get("provider_request_id"),
        ),
        model=model,
        cost=CostEstimate(
            status="estimated" if cost.get("status") == "estimated" else "unknown",
            amount=None if amount is None else Decimal(str(amount)),
            currency=str(cost.get("currency") or "USD"),
            price_ref=cost.get("price_snapshot_ref"),
            reason=cost.get("reason"),
            synthetic_price=bool(cost.get("synthetic_price")),
        ),
        revision=str(payload.get("resolved_model") or "unknown"),
        revision_status=payload.get("revision_status") or "unknown",
    )


class ReplayModelGateway:
    """ModelGateway que sirve respuestas grabadas en orden; nunca llama a un proveedor.

    Cada `generate` consume el siguiente intento grabado (los retries grabados se reproducen
    como fallos y reintentos idénticos) y exige el mismo `request_digest`.
    """

    def __init__(self, models: Mapping[str, ModelSnapshot], recording: Recording) -> None:
        self._models = dict(models)
        self._attempts = recording.model_attempts
        self._position = 0

    def models(self) -> Mapping[str, ModelSnapshot]:
        return dict(self._models)

    def generate(self, request: ModelRequest) -> ModelResult:
        index = self._position
        self._position += 1
        if index >= len(self._attempts):
            raise ReplayMismatchError(f"llamada a modelo {index + 1} no existe en la grabación")
        attempt = self._attempts[index]
        if attempt.digest != request.digest() or attempt.role != request.role:
            raise ReplayMismatchError(f"llamada a modelo {index + 1} no coincide con la grabada")
        if not attempt.replayable:
            raise ReplayMismatchError(
                f"llamada a modelo {index + 1} no reproducible: evidencia redactada"
            )
        model = self._models.get(request.role)
        if model is None:
            raise ReplayMismatchError(f"rol {request.role} sin modelo en el replay")
        if attempt.failed is not None:
            raise ModelCallError(
                attempt.failed.get("kind") or "provider_error",
                str(attempt.failed.get("error") or "fallo grabado"),
                retriable=bool(attempt.failed.get("retriable")),
            )
        return _recorded_result(attempt, model)

    def pending(self) -> int:
        """Intentos grabados que el replay no volvió a emitir."""
        return len(self._attempts) - self._position
