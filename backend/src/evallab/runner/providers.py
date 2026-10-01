"""Proveedores de modelo. Sólo `fixture` está habilitado por defecto y no abre red.

`FixtureModelProvider` responde con un guion publicado como Fixture (`kind: model_script`) e
identificado por su hash, que la ModelConfiguration fija en `requested_model`: el
comportamiento del "modelo" queda sellado por contenido. Elige la respuesta por `slug` del
escenario (vista pública) y turno del rol dentro del run. Es un doble de prueba del harness: sus
tokens son los declarados en el guion y no miden ningún LLM.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Literal

from pydantic import Field, ValidationError

from evallab.runner.models import (
    ModelProviderError,
    ModelRequest,
    ModelSnapshot,
    ModelToolCall,
    ProviderResponse,
    TokenUsage,
)
from evallab.schemas import JsonObject, StrictModel

FIXTURE_PROVIDER = "fixture"


class ScriptedToolCall(StrictModel):
    name: str = Field(min_length=1)
    arguments: JsonObject = Field(default_factory=dict)
    id: str | None = None


class ScriptedUsage(StrictModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)


class ScriptedError(StrictModel):
    kind: Literal["rate_limited", "timeout", "provider_error", "context_length", "content_filter"]
    message: str = "error simulado del proveedor"
    retriable: bool | None = None


class ScriptedResponse(StrictModel):
    content: str | None = None
    tool_calls: list[ScriptedToolCall] = Field(default_factory=list)
    finish_reason: str = "stop"
    # Ausente = el proveedor no informa uso (tokens y coste quedan unknown).
    usage: ScriptedUsage | None = None
    resolved_model: str | None = None
    error: ScriptedError | None = None
    # Respuesta deliberadamente malformada para probar la validación del gateway.
    malformed: bool = False


class ModelScript(StrictModel):
    kind: Literal["model_script"]
    responses: dict[str, list[ScriptedResponse]] = Field(default_factory=dict)
    default: list[ScriptedResponse] | None = None


Loader = Callable[[str], Any | None]


class FixtureModelProvider:
    def __init__(self, load: Loader) -> None:
        self._load = load
        self._scripts: dict[str, ModelScript] = {}

    def _script(self, fixture_hash: str) -> ModelScript:
        script = self._scripts.get(fixture_hash)
        if script is None:
            payload = self._load(fixture_hash)
            if payload is None:
                raise ModelProviderError("provider_unavailable", "guion de modelo inexistente")
            try:
                script = ModelScript.model_validate(payload)
            except ValidationError as exc:
                raise ModelProviderError(
                    "provider_unavailable", "guion de modelo inválido", retriable=False
                ) from exc
            self._scripts[fixture_hash] = script
        return script

    def complete(self, request: ModelRequest, model: ModelSnapshot) -> ProviderResponse:
        script = self._script(model.requested_model)
        slug = request.metadata.get("scenario_slug")
        turn = int(request.metadata.get("turn") or 0)
        responses = script.responses.get(str(slug)) if slug is not None else None
        if responses is None:
            responses = script.default or []
        if turn >= len(responses):
            raise ModelProviderError(
                "invalid_response", f"guion agotado en el turno {turn}", retriable=False
            )
        scripted = responses[turn]
        if scripted.error is not None:
            raise ModelProviderError(
                scripted.error.kind, scripted.error.message, retriable=scripted.error.retriable
            )
        usage = (
            TokenUsage(source="unknown")
            if scripted.usage is None
            else TokenUsage(
                input_tokens=scripted.usage.input_tokens,
                output_tokens=scripted.usage.output_tokens,
                cached_input_tokens=scripted.usage.cached_input_tokens,
                reasoning_tokens=scripted.usage.reasoning_tokens,
                source="observed",
            )
        )
        tool_calls = tuple(
            ModelToolCall(name=c.name, arguments=dict(c.arguments), id=c.id)
            for c in scripted.tool_calls
        )
        if scripted.malformed:
            return ProviderResponse(content=None, tool_calls=(), usage=usage)
        return ProviderResponse(
            content=scripted.content,
            tool_calls=tool_calls,
            finish_reason=scripted.finish_reason,
            usage=usage,
            provider_request_id=f"fixture:{model.requested_model[:12]}:{slug}:{turn}",
            resolved_model=scripted.resolved_model,
        )


def default_providers(load_fixture: Loader) -> Mapping[str, Any]:
    """`fixture` siempre; el live compatible con OpenAI sólo si se habilita por entorno."""
    from evallab.runner.live import live_providers

    return {FIXTURE_PROVIDER: FixtureModelProvider(load_fixture), **live_providers()}
