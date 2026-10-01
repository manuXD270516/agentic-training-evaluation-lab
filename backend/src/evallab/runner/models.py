"""ModelGateway neutral (M6, 7.1): request/response del proveedor, uso, coste y errores tipados.

El gateway no conoce ningún proveedor concreto: delega en un `ModelProvider` registrado por
nombre (`ModelConfiguration.provider`). La respuesta se valida antes de entregarla al patrón;
el uso que el proveedor no informa queda `unknown` (nunca cero) y el coste sólo se estima si hay
uso y `PriceSnapshot`. La revisión resuelta es la que informa el proveedor, la declarada en la
configuración o `unknown`, en ese orden, y siempre se registra de forma explícita.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, Protocol

from evallab.canonical import canonical_digest, canonical_json
from evallab.runner.errors import RunnerError
from evallab.runner.redaction import redact

UsageSource = Literal["observed", "estimated", "unknown"]
ModelErrorKind = Literal[
    "rate_limited",
    "timeout",
    "provider_error",
    "invalid_response",
    "context_length",
    "content_filter",
    "provider_unavailable",
    "unknown_role",
]
RETRIABLE_KINDS = frozenset({"rate_limited", "timeout", "provider_error"})
MILLION = Decimal(1_000_000)
CHARS_PER_TOKEN_ESTIMATE = 4


@dataclass(frozen=True)
class PriceInfo:
    ref: str
    currency: str
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cached_input_per_mtok: Decimal | None = None
    synthetic: bool = False


@dataclass(frozen=True)
class ModelSnapshot:
    """ModelConfiguration resuelta para un rol del agente."""

    role: str
    id: uuid.UUID
    version: str
    content_hash: str
    provider: str
    requested_model: str
    resolved_revision: str | None
    temperature: str | None
    max_tokens: int | None
    seed_support: str
    price: PriceInfo | None = None

    def ref(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "id": str(self.id),
            "version": self.version,
            "content_hash": self.content_hash,
            "provider": self.provider,
            "requested_model": self.requested_model,
            "resolved_revision": self.resolved_revision,
            "revision_status": "declared" if self.resolved_revision else "unknown",
            "price_snapshot_ref": self.price.ref if self.price is not None else None,
        }


@dataclass(frozen=True)
class ToolSpec:
    name: str
    input_schema: Mapping[str, Any]
    description: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": dict(self.input_schema),
        }


@dataclass(frozen=True)
class ModelRequest:
    """Petición neutral. `metadata` sólo la usan proveedores de fixture (escenario y turno)."""

    role: str
    messages: tuple[Mapping[str, Any], ...]
    tools: tuple[ToolSpec, ...] = ()
    max_tokens: int | None = None
    seed: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "messages": [dict(m) for m in self.messages],
            "tools": [t.as_json() for t in self.tools],
            "max_tokens": self.max_tokens,
            "seed": self.seed,
        }

    def digest(self) -> str:
        """Digest canónico del request redactado; base de la comparación en replay."""
        redacted, _ = redact(self.as_json())
        return canonical_digest(redacted)

    def estimated_input_tokens(self) -> int:
        """Estimación declarada (no medida): bytes del request canónico / 4."""
        return math.ceil(len(canonical_json(self.as_json())) / CHARS_PER_TOKEN_ESTIMATE)


@dataclass(frozen=True)
class ModelToolCall:
    name: str
    arguments: dict[str, Any]
    id: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    source: UsageSource = "unknown"

    @property
    def known(self) -> bool:
        return (
            self.source != "unknown"
            and self.input_tokens is not None
            and (self.output_tokens is not None)
        )

    @property
    def total(self) -> int | None:
        if not self.known:
            return None
        assert self.input_tokens is not None and self.output_tokens is not None
        return self.input_tokens + self.output_tokens

    def as_json(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total,
            "source": self.source if self.known else "unknown",
        }


@dataclass(frozen=True)
class ProviderResponse:
    content: str | None
    tool_calls: tuple[ModelToolCall, ...] = ()
    finish_reason: str | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)
    provider_request_id: str | None = None
    resolved_model: str | None = None


class ModelProviderError(Exception):
    def __init__(self, kind: ModelErrorKind, message: str, *, retriable: bool | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retriable = kind in RETRIABLE_KINDS if retriable is None else retriable


class ModelCallError(RunnerError):
    """Fallo tipado de una llamada a modelo; termina el run como `model_error`."""

    error_class = "model_error"

    def __init__(self, kind: ModelErrorKind, message: str, *, retriable: bool = False) -> None:
        super().__init__(message)
        self.kind = kind
        self.retriable = retriable


class ModelProvider(Protocol):
    def complete(self, request: ModelRequest, model: ModelSnapshot) -> ProviderResponse: ...


@dataclass(frozen=True)
class CostEstimate:
    status: Literal["estimated", "unknown"]
    amount: Decimal | None
    currency: str
    price_ref: str | None
    reason: str | None = None
    synthetic_price: bool = False

    def as_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "amount": None if self.amount is None else format(self.amount, "f"),
            "currency": self.currency,
            "price_snapshot_ref": self.price_ref,
            "reason": self.reason,
            "synthetic_price": self.synthetic_price,
        }


def estimate_cost(usage: TokenUsage, price: PriceInfo | None) -> CostEstimate:
    """Tokens facturables por tarifa del snapshot; sin uso o sin precio, `unknown` sin valor."""
    if price is None:
        return CostEstimate("unknown", None, "USD", None, "sin price snapshot")
    if not usage.known:
        return CostEstimate("unknown", None, price.currency, price.ref, "uso no informado")
    assert usage.input_tokens is not None and usage.output_tokens is not None
    cached = usage.cached_input_tokens or 0
    cached_price = price.cached_input_per_mtok
    if cached and cached_price is None:
        cached_price = price.input_per_mtok
    uncached = max(0, usage.input_tokens - cached)
    amount = (
        Decimal(uncached) * price.input_per_mtok
        + Decimal(cached) * (cached_price or Decimal(0))
        + Decimal(usage.output_tokens) * price.output_per_mtok
    ) / MILLION
    return CostEstimate(
        "estimated", amount, price.currency, price.ref, synthetic_price=price.synthetic
    )


def max_call_cost(request: ModelRequest, model: ModelSnapshot) -> Decimal | None:
    """Reserva antes de llamar: entrada estimada + `max_tokens` de salida a tarifa completa."""
    if model.price is None:
        return None
    output = request.max_tokens or model.max_tokens or 0
    return (
        Decimal(request.estimated_input_tokens()) * model.price.input_per_mtok
        + Decimal(output) * model.price.output_per_mtok
    ) / MILLION


@dataclass(frozen=True)
class ModelResult:
    response: ProviderResponse
    model: ModelSnapshot
    cost: CostEstimate
    revision: str
    revision_status: Literal["reported", "declared", "unknown"]


def _validate(response: ProviderResponse) -> None:
    if response.content is not None and not isinstance(response.content, str):
        raise ModelCallError("invalid_response", "content no es texto")
    for call in response.tool_calls:
        if not isinstance(call.name, str) or not call.name:
            raise ModelCallError("invalid_response", "tool call sin nombre")
        if not isinstance(call.arguments, dict):
            raise ModelCallError("invalid_response", "argumentos de tool call no son un objeto")
    if response.content is None and not response.tool_calls:
        raise ModelCallError("invalid_response", "respuesta vacía: ni contenido ni tool calls")


class ModelGatewayProtocol(Protocol):
    def models(self) -> Mapping[str, ModelSnapshot]: ...

    def generate(self, request: ModelRequest) -> ModelResult: ...


class ProviderModelGateway:
    """Gateway por run: un `ModelSnapshot` por rol y proveedores registrados por nombre."""

    def __init__(
        self, models: Mapping[str, ModelSnapshot], providers: Mapping[str, ModelProvider]
    ) -> None:
        self._models = dict(models)
        self._providers = dict(providers)

    def models(self) -> Mapping[str, ModelSnapshot]:
        return dict(self._models)

    def generate(self, request: ModelRequest) -> ModelResult:
        model = self._models.get(request.role)
        if model is None:
            raise ModelCallError("unknown_role", f"el agente no tiene modelo para {request.role}")
        provider = self._providers.get(model.provider)
        if provider is None:
            raise ModelCallError(
                "provider_unavailable", f"proveedor no habilitado: {model.provider}"
            )
        try:
            response = provider.complete(request, model)
        except ModelProviderError as exc:
            raise ModelCallError(exc.kind, exc.message, retriable=exc.retriable) from exc
        _validate(response)
        if response.resolved_model:
            revision, status = response.resolved_model, "reported"
        elif model.resolved_revision:
            revision, status = model.resolved_revision, "declared"
        else:
            revision, status = "unknown", "unknown"
        return ModelResult(
            response=response,
            model=model,
            cost=estimate_cost(response.usage, model.price),
            revision=revision,
            revision_status=status,  # type: ignore[arg-type]
        )


def tool_calls_from_json(raw: Sequence[Mapping[str, Any]]) -> tuple[ModelToolCall, ...]:
    return tuple(
        ModelToolCall(
            name=str(item.get("name")),
            arguments=dict(item.get("arguments") or {}),
            id=None if item.get("id") is None else str(item.get("id")),
        )
        for item in raw
    )
