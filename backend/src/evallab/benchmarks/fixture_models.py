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
