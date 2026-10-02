"""Ejecución live opcional (M6, 7.2): un agente ReAct con un modelo real tras el gateway.

Nunca se activa sola. Exige `MODEL_GATEWAY_LIVE_ENABLED=true`, `MODEL_GATEWAY_BASE_URL` y
`MODEL_GATEWAY_API_KEY` (ver `.env.example` y `docs/live-run.md`). La configuración del modelo
(proveedor, nombre pedido, revisión resuelta, temperatura, tarifa) se publica como
ModelConfiguration inmutable cuyo nombre deriva de su contenido, así que dos ejecuciones con la
misma configuración comparten identidad y una distinta no puede pisarla.

Con un servidor local (Ollama, compatible con la API de OpenAI) no hay coste monetario: la
tarifa queda sin declarar y el coste se informa `unknown`, nunca 0. Con un proveedor de pago,
`--price-input/--price-output` publican un PriceSnapshot y `--max-cost-usd` fija el límite
monetario que el runner aplica antes de cada llamada.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from evallab.benchmarks.suite import AgentDef, ModelDef, Suite
from evallab.canonical import canonical_digest
from evallab.runner.live import LIVE_PROVIDER
from evallab.runner.react import REACT_PROMPT_HASH, REACT_VERSION


def live_model(
    model: str,
    *,
    revision: str | None,
    seed_support: str,
    temperature: float | None,
    max_tokens: int,
    price_input: str | None,
    price_output: str | None,
    currency: str,
    price_source: str | None,
) -> ModelDef:
    price: dict[str, Any] | None = None
    if price_input is not None and price_output is not None:
        price = {
            "provider": LIVE_PROVIDER,
            "model": model,
            "currency": currency,
            "input_per_mtok": price_input,
            "output_per_mtok": price_output,
            "effective_date": datetime.now(UTC).date().isoformat(),
            "source": price_source or "tarifa declarada por el operador al ejecutar",
            "synthetic": False,
        }
    config = {
        "provider": LIVE_PROVIDER,
        "model": model,
        "revision": revision,
        "seed_support": seed_support,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "price": price,
    }
    return ModelDef(
        name=f"live-{canonical_digest(config)[:16]}",
        provider=LIVE_PROVIDER,
        requested_model=model,
        resolved_revision=revision,
        temperature=temperature,
        max_tokens=max_tokens,
        seed_support=seed_support,
        price=price,
    )


def live_react_suite(base: Suite, model: ModelDef, tools: tuple[str, ...]) -> tuple[Suite, str]:
    """La suite base (mismo dataset, benchmark y locks) con un único agente ReAct live."""
    agent = AgentDef(
        name=f"react-{model.name}",
        pattern="react",
        pattern_version=REACT_VERSION,
        prompt_hash=REACT_PROMPT_HASH,
        pattern_parameters={"max_tokens_per_call": model.max_tokens or 512},
        tools=tools,
        roles=(("executor", model.name),),
    )
    return replace(base, agents=(agent,), models=(model,)), agent.name
