"""OpenTelemetry operativo (M4, 5.2): spans de API, worker, tools, modelo y evaluador.

La telemetría es muestreable y prescindible; la evidencia de evaluación no depende de ella. Los
eventos de traza sólo copian `otel_trace_id`/`otel_span_id` del span activo para correlacionar;
esos ids no entran en `payload_digest` ni en el digest sellado. Los spans llevan ids y nombres,
nunca payloads, argumentos ni resultados, de modo que nada sin redactar llega al collector.

Sin `OTEL_EXPORTER_OTLP_ENDPOINT` no se configura exportador: los spans son no-op y los eventos
quedan con ids nulos. Con endpoint, el `BatchSpanProcessor` exporta en segundo plano: un
collector caído sólo pierde spans (se registra en el log), nunca eventos de la traza.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator, Mapping
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import Span, Status, StatusCode
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from evallab import __version__

logger = logging.getLogger("evallab.telemetry")

TRACER_NAME = "evallab"
AttributeValue = str | bool | int | float

_provider: trace.TracerProvider = trace.NoOpTracerProvider()


class TelemetrySettings(BaseSettings):
    """Variables estándar de OTel; sin endpoint la telemetría queda desactivada."""

    model_config = SettingsConfigDict(env_prefix="OTEL_", extra="ignore")

    exporter_otlp_endpoint: str | None = None
    exporter_otlp_timeout_s: float = Field(default=2.0, gt=0, le=60)
    sdk_disabled: bool = False


def configure(provider: trace.TracerProvider | None) -> trace.TracerProvider:
    """Fija el provider del proceso (o no-op con None) y devuelve el anterior."""
    global _provider
    previous = _provider
    _provider = provider if provider is not None else trace.NoOpTracerProvider()
    return previous


def build_provider(service_name: str, *exporters: SpanExporter) -> TracerProvider:
    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name, "service.version": __version__})
    )
    for exporter in exporters:
        provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider


def configure_from_env(service_name: str, settings: TelemetrySettings | None = None) -> bool:
    """Configura exportación OTLP/HTTP si hay endpoint; devuelve si quedó activa."""
    cfg = settings or TelemetrySettings()
    if cfg.sdk_disabled or not cfg.exporter_otlp_endpoint:
        configure(None)
        return False
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    endpoint = cfg.exporter_otlp_endpoint.rstrip("/") + "/v1/traces"
    exporter = OTLPSpanExporter(endpoint=endpoint, timeout=cfg.exporter_otlp_timeout_s)
    configure(build_provider(service_name, exporter))
    logger.info("telemetría OTLP activa hacia %s", endpoint)
    return True


def shutdown() -> None:
    provider = _provider
    if isinstance(provider, TracerProvider):
        provider.shutdown()


def tracer() -> trace.Tracer:
    return _provider.get_tracer(TRACER_NAME, __version__)


def _clean(attributes: Mapping[str, Any]) -> dict[str, AttributeValue]:
    clean: dict[str, AttributeValue] = {}
    for key, value in attributes.items():
        if value is None:
            continue
        clean[key] = value if isinstance(value, str | bool | int | float) else str(value)
    return clean


@contextlib.contextmanager
def span(name: str, **attributes: Any) -> Iterator[Span]:
    """Span activo con atributos `evallab.*`; una excepción marca error y se propaga."""
    with tracer().start_as_current_span(
        name, attributes=_clean(attributes), record_exception=True
    ) as current:
        yield current


def set_attributes(current: Span, **attributes: Any) -> None:
    current.set_attributes(_clean(attributes))


def mark_error(current: Span, description: str) -> None:
    current.set_status(Status(StatusCode.ERROR, description))


def current_ids() -> tuple[str | None, str | None]:
    """`(trace_id, span_id)` en hex del span activo; nulos si no hay span grabándose."""
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None, None
    return format(context.trace_id, "032x"), format(context.span_id, "016x")
