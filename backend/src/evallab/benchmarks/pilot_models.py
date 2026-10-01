"""Piloto con patrones de modelo (M6 y M7) sobre el mismo dataset y benchmark del piloto M5.

Los agentes usan el proveedor `fixture`: un guion versionado hace de modelo, con tokens y tarifa
sintéticos. Sirve para verificar el patrón ReAct (y Planner/Executor en M7), su trazabilidad,
la contabilidad y el replay sin red ni llamadas de pago. Sus resultados se etiquetan
`attribution=fixture_model` y no miden la capacidad de ningún LLM.
"""

from __future__ import annotations

from dataclasses import replace

from evallab.benchmarks.fixture_models import SYNTHETIC_PRICE, react_script
from evallab.benchmarks.pilot import AGENT_TOOLS, PILOT, REFERENCE_SCRIPTS
from evallab.benchmarks.suite import AgentDef, ModelDef
from evallab.runner.react import REACT_PROMPT_HASH, REACT_VERSION

REACT_MODEL = ModelDef(
    name="pilot-fixture-react-model",
    script=react_script(REFERENCE_SCRIPTS),
    price=SYNTHETIC_PRICE,
    max_tokens=512,
)

REACT_AGENT = AgentDef(
    name="pilot-react-fixture",
    pattern="react",
    pattern_version=REACT_VERSION,
    prompt_hash=REACT_PROMPT_HASH,
    pattern_parameters={"max_tokens_per_call": 256},
    tools=AGENT_TOOLS,
    roles=(("executor", REACT_MODEL.name),),
)

PILOT_MODELS = replace(PILOT, agents=(REACT_AGENT,), models=(REACT_MODEL,))
