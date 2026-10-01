"""Guiones de modelo de fixture derivados de los scripts del piloto (dobles de prueba, no LLM).

Un guion ReAct reproduce las acciones de un script scripted como decisiones de un "modelo":
una tool call por turno y, al final, la respuesta JSON citando evidencia con alias `E<n>`. El
uso de tokens es sintético y fijo por turno, sólo para ejercitar contabilidad y coste; no
describe a ningún modelo real.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

EVIDENCE = re.compile(r"^\{\{evidence:(\d+)\}\}$")
SYNTHETIC_PRICE = {
    "provider": "fixture",
    "model": "fixture-model",
    "currency": "USD",
    "input_per_mtok": "1",
    "output_per_mtok": "4",
    "effective_date": "2026-10-01",
    "source": "tarifa sintética para pruebas del harness; no corresponde a ningún proveedor",
    "synthetic": True,
}


def synthetic_usage(turn: int, *, output: int = 40) -> dict[str, int]:
    """Entrada creciente con el historial; salida fija. Valores inventados y deterministas."""
    return {"input_tokens": 400 + 150 * turn, "output_tokens": output}


def _alias_evidence(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _alias_evidence(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_alias_evidence(v) for v in value]
    if isinstance(value, str):
        match = EVIDENCE.fullmatch(value)
        if match is not None:
            return f"E{int(match.group(1)) + 1}"
    return value


def final_content(output: Any) -> str:
    return json.dumps(_alias_evidence(output), ensure_ascii=False, sort_keys=True)


def react_turns(steps: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    tool_steps = [s for s in steps if s["type"] == "tool"]
    for index, step in enumerate(tool_steps):
        turns.append(
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call-{index + 1}",
                        "name": step["tool"],
                        "arguments": step["arguments"],
                    }
                ],
                "finish_reason": "tool_calls",
                "usage": synthetic_usage(index),
            }
        )
    final = next(s for s in steps if s["type"] == "final")
    turns.append(
        {
            "content": final_content(final["output"]),
            "finish_reason": "stop",
            "usage": synthetic_usage(len(tool_steps), output=60),
        }
    )
    return turns


def react_script(scripts: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    return {
        "kind": "model_script",
        "responses": {slug: react_turns(steps) for slug, steps in sorted(scripts.items())},
    }


def plan_steps(
    steps: Sequence[Mapping[str, Any]], dependencies: Mapping[str, Sequence[str]] | None = None
) -> list[dict[str, Any]]:
    """Un paso de plan por tool del script; por defecto cada paso depende del anterior."""
    tool_steps = [s for s in steps if s["type"] == "tool"]
    plan: list[dict[str, Any]] = []
    for index, step in enumerate(tool_steps):
        step_id = f"p{index + 1}"
        default = [f"p{index}"] if index else []
        plan.append(
            {
                "id": step_id,
                "description": f"usar {step['tool']}",
                "tool": step["tool"],
                "depends_on": list((dependencies or {}).get(step_id, default)),
            }
        )
    return plan


def planner_turns(
    steps: Sequence[Mapping[str, Any]], dependencies: Mapping[str, Sequence[str]] | None = None
) -> list[dict[str, Any]]:
    content = json.dumps({"steps": plan_steps(steps, dependencies)}, ensure_ascii=False)
    return [{"content": content, "finish_reason": "stop", "usage": synthetic_usage(0, output=90)}]


def executor_turns(steps: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Una decisión por paso del plan (su tool call) y la respuesta final."""
    turns = react_turns(steps)
    # El ejecutor recibe el plan en cada ciclo: su entrada es algo mayor que en ReAct.
    for index, turn in enumerate(turns):
        usage = turn["usage"]
        turn["usage"] = {**usage, "input_tokens": usage["input_tokens"] + 120 + 30 * index}
    return turns


def planner_executor_scripts(
    scripts: Mapping[str, Sequence[Mapping[str, Any]]],
    dependencies: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Guiones del planner (un plan por escenario) y del executor (paso a paso y final)."""
    deps = dependencies or {}
    planner = {
        "kind": "model_script",
        "responses": {
            slug: planner_turns(steps, deps.get(slug)) for slug, steps in sorted(scripts.items())
        },
    }
    executor = {
        "kind": "model_script",
        "responses": {slug: executor_turns(steps) for slug, steps in sorted(scripts.items())},
    }
    return planner, executor
