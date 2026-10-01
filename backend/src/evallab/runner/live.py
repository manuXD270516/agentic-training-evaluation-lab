"""Proveedor live opcional compatible con la API de chat completions de OpenAI (M6, 7.2).

Desactivado por defecto: sólo se registra con `MODEL_GATEWAY_LIVE_ENABLED=true`, una URL base
y una clave en `MODEL_GATEWAY_API_KEY`. El repositorio no incluye ninguna configuración live ni
la ha ejecutado contra un proveedor real: el adaptador se prueba contra un servidor local que
imita el formato documentado. El worker de Compose no tiene salida a Internet; usarlo exige
además una ruta de red explícita para el gateway (design.md §9).

Mapeo de errores: 429 → `rate_limited`; 408/504 o timeout de socket → `timeout`; 5xx →
`provider_error` (reintentables); 400 con `context_length_exceeded` → `context_length`;
otra 4xx → `provider_error` no reintentable; cuerpo no JSON o tool call con argumentos
inválidos → `invalid_response`. Sin `usage` en la respuesta, el uso queda `unknown`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from evallab.runner.models import (
    ModelProviderError,
    ModelRequest,
    ModelSnapshot,
    ModelToolCall,
    ProviderResponse,
    TokenUsage,
)

LIVE_PROVIDER = "openai-compatible"


class ModelGatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MODEL_GATEWAY_", extra="ignore")

    live_enabled: bool = False
    base_url: str | None = None
    api_key: SecretStr | None = None
    timeout_s: float = Field(default=60.0, gt=0, le=600)

    def live_ready(self) -> bool:
        return self.live_enabled and bool(self.base_url) and self.api_key is not None


def _messages(request: ModelRequest) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in request.messages:
        role = message.get("role")
        if role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": message.get("content")}
            calls = message.get("tool_calls") or []
            if calls:
                entry["tool_calls"] = [
                    {
                        "id": call.get("id") or f"call-{index}",
                        "type": "function",
                        "function": {
                            "name": call.get("name"),
                            "arguments": json.dumps(call.get("arguments") or {}),
                        },
                    }
                    for index, call in enumerate(calls)
                ]
            out.append(entry)
        elif role == "tool":
            body = {k: v for k, v in message.items() if k not in ("role", "tool_call_id")}
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": message.get("tool_call_id") or "",
                    "content": json.dumps(body, ensure_ascii=False, sort_keys=True),
                }
            )
        else:
            out.append({"role": role, "content": message.get("content")})
    return out


def build_body(request: ModelRequest, model: ModelSnapshot) -> dict[str, Any]:
    body: dict[str, Any] = {"model": model.requested_model, "messages": _messages(request)}
    if request.tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {"name": tool.name, "parameters": dict(tool.input_schema)},
            }
            for tool in request.tools
        ]
    max_tokens = request.max_tokens or model.max_tokens
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if model.temperature is not None:
        body["temperature"] = float(model.temperature)
    if model.seed_support == "supported" and request.seed is not None:
        body["seed"] = request.seed
    return body


def _usage(raw: Any) -> TokenUsage:
    if not isinstance(raw, dict):
        return TokenUsage(source="unknown")
    prompt = raw.get("prompt_tokens")
    completion = raw.get("completion_tokens")
    if not isinstance(prompt, int) or not isinstance(completion, int):
        return TokenUsage(source="unknown")
    cached = (raw.get("prompt_tokens_details") or {}).get("cached_tokens")
    reasoning = (raw.get("completion_tokens_details") or {}).get("reasoning_tokens")
    return TokenUsage(
        input_tokens=prompt,
        output_tokens=completion,
        cached_input_tokens=cached if isinstance(cached, int) else None,
        reasoning_tokens=reasoning if isinstance(reasoning, int) else None,
        source="observed",
    )


def parse_response(document: Any) -> ProviderResponse:
    try:
        choice = document["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelProviderError("invalid_response", "respuesta sin choices[0].message") from exc
    calls: list[ModelToolCall] = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function") or {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except ValueError as exc:
            raise ModelProviderError("invalid_response", "argumentos de tool no son JSON") from exc
        if not isinstance(arguments, dict):
            raise ModelProviderError("invalid_response", "argumentos de tool no son un objeto")
        calls.append(
            ModelToolCall(name=str(function.get("name")), arguments=arguments, id=raw.get("id"))
        )
    return ProviderResponse(
        content=message.get("content"),
        tool_calls=tuple(calls),
        finish_reason=choice.get("finish_reason"),
        usage=_usage(document.get("usage")),
        provider_request_id=document.get("id"),
        resolved_model=document.get("model"),
    )


def _http_error(exc: urllib.error.HTTPError) -> ModelProviderError:
    detail = ""
    try:
        detail = exc.read().decode("utf-8", errors="replace")
    except OSError:
        detail = ""
    if exc.code == 429:
        return ModelProviderError("rate_limited", "límite de peticiones del proveedor")
    if exc.code in (408, 504):
        return ModelProviderError("timeout", f"timeout del proveedor ({exc.code})")
    if exc.code >= 500:
        return ModelProviderError("provider_error", f"error del proveedor ({exc.code})")
    if exc.code == 400 and "context_length" in detail:
        return ModelProviderError("context_length", "el request supera el contexto del modelo")
    return ModelProviderError("provider_error", f"petición rechazada ({exc.code})", retriable=False)


class OpenAICompatibleProvider:
    """Adaptador HTTP mínimo (stdlib). Nunca registra la clave ni el cuerpo en la traza."""

    def __init__(self, base_url: str, api_key: str, timeout_s: float = 60.0) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._timeout = timeout_s

    def complete(self, request: ModelRequest, model: ModelSnapshot) -> ProviderResponse:
        payload = json.dumps(build_body(request, model)).encode("utf-8")
        http_request = urllib.request.Request(
            self._url,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise _http_error(exc) from exc
        except TimeoutError as exc:
            raise ModelProviderError("timeout", "timeout de red hacia el proveedor") from exc
        except urllib.error.URLError as exc:
            raise ModelProviderError("provider_error", "proveedor inalcanzable") from exc
        try:
            document = json.loads(raw)
        except ValueError as exc:
            raise ModelProviderError("invalid_response", "cuerpo no JSON") from exc
        return parse_response(document)


def live_providers(settings: ModelGatewaySettings | None = None) -> dict[str, Any]:
    cfg = settings or ModelGatewaySettings()
    if not cfg.live_ready():
        return {}
    assert cfg.base_url is not None and cfg.api_key is not None
    return {
        LIVE_PROVIDER: OpenAICompatibleProvider(
            cfg.base_url, cfg.api_key.get_secret_value(), cfg.timeout_s
        )
    }
