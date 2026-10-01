"""Baseline scripted: reproduce acciones declaradas; prueba el harness, no la inteligencia.

`pattern_parameters.script` es una lista de pasos `{type: tool, tool, arguments}` y un único
`{type: final, output}` al final. Para un benchmark con varios escenarios,
`pattern_parameters.scripts` asigna un script por `slug` del escenario (dato de la vista
pública); un escenario sin script falla de forma tipada. En la salida final, el string
`"{{evidence:N}}"` se sustituye por el `event_id` del `tool.completed` de la N-ésima llamada
completada (desde 0), que es lo único que el patrón puede citar como evidencia.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import Field

from evallab.runner.contracts import (
    Action,
    AgentSnapshot,
    BudgetRemaining,
    FinalAnswer,
    Observation,
    PatternObservation,
    RunContext,
    ToolCall,
)
from evallab.runner.errors import InvalidScriptError
from evallab.schemas import JsonObject, JsonValue, ScenarioPublicOut, StrictModel

SCRIPTED_PATTERN = "scripted"
EVIDENCE_PLACEHOLDER = re.compile(r"^\{\{evidence:(\d+)\}\}$")


class ToolStep(StrictModel):
    type: Literal["tool"]
    tool: str = Field(min_length=1)
    arguments: JsonObject = Field(default_factory=dict)


class FinalStep(StrictModel):
    type: Literal["final"]
    output: JsonValue


def _parse_script(raw: Any) -> list[ToolStep | FinalStep]:
    if not isinstance(raw, list) or not raw:
        raise InvalidScriptError("pattern_parameters.script debe ser una lista no vacía")
    steps: list[ToolStep | FinalStep] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or "type" not in item:
            raise InvalidScriptError(f"paso {index} del script inválido")
        step_type = item["type"]
        try:
            if step_type == "tool":
                steps.append(ToolStep.model_validate(item))
            elif step_type == "final":
                steps.append(FinalStep.model_validate(item))
            else:
                raise InvalidScriptError(f"tipo de paso no soportado: {step_type}")
        except InvalidScriptError:
            raise
        except ValueError as exc:
            raise InvalidScriptError(f"paso {index} del script inválido") from exc
    if not isinstance(steps[-1], FinalStep):
        raise InvalidScriptError("el script debe terminar en una acción final")
    if sum(1 for step in steps if isinstance(step, FinalStep)) != 1:
        raise InvalidScriptError("el script admite una sola acción final")
    return steps


def _fill_evidence(value: Any, evidence: Sequence[str]) -> Any:
    if isinstance(value, dict):
        return {k: _fill_evidence(v, evidence) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill_evidence(v, evidence) for v in value]
    if isinstance(value, str):
        match = EVIDENCE_PLACEHOLDER.fullmatch(value)
        if match is not None:
            index = int(match.group(1))
            # Sin esa llamada completada el marcador queda tal cual: la cita no existirá.
            return evidence[index] if index < len(evidence) else value
    return value


class ScriptedPatternAdapter:
    pattern = SCRIPTED_PATTERN

    def __init__(self, pattern_version: str, steps: list[ToolStep | FinalStep]) -> None:
        self.pattern_version = pattern_version
        self._steps = steps
        self._index = 0

    @classmethod
    def from_agent(
        cls, agent: AgentSnapshot, scenario: ScenarioPublicOut | None = None
    ) -> ScriptedPatternAdapter:
        params = agent.pattern_parameters if isinstance(agent.pattern_parameters, dict) else {}
        scripts = params.get("scripts")
        if scripts is not None:
            if not isinstance(scripts, dict):
                raise InvalidScriptError("pattern_parameters.scripts debe ser un objeto por slug")
            slug = scenario.slug if scenario is not None else None
            if slug is None or slug not in scripts:
                raise InvalidScriptError(f"no hay script para el escenario {slug}")
            return cls(agent.pattern_version, _parse_script(scripts[slug]))
        return cls(agent.pattern_version, _parse_script(params.get("script")))

    def next_action(
        self,
        context: RunContext,
        observations: Sequence[PatternObservation],
        remaining: BudgetRemaining,
    ) -> Action:
        del context, remaining
        if self._index >= len(self._steps):
            raise InvalidScriptError("el script no emitió una acción final")
        step = self._steps[self._index]
        self._index += 1
        if isinstance(step, ToolStep):
            return ToolCall(tool=step.tool, arguments=step.arguments)
        evidence = [
            str(o.evidence_id)
            for o in observations
            if isinstance(o, Observation) and o.evidence_id is not None
        ]
        return FinalAnswer(output=_fill_evidence(step.output, evidence))
