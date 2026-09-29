from collections.abc import Sequence
from typing import Literal

from pydantic import Field

from evallab.runner.contracts import (
    Action,
    AgentSnapshot,
    BudgetRemaining,
    FinalAnswer,
    Observation,
    RunContext,
    ToolCall,
)
from evallab.runner.errors import InvalidScriptError, UnsupportedPatternError
from evallab.schemas import JsonObject, JsonValue, StrictModel

SCRIPTED_PATTERN = "scripted"


class ToolStep(StrictModel):
    type: Literal["tool"]
    tool: str = Field(min_length=1)
    arguments: JsonObject = Field(default_factory=dict)


class FinalStep(StrictModel):
    type: Literal["final"]
    output: JsonValue


class ScriptedPatternAdapter:
    pattern = SCRIPTED_PATTERN

    def __init__(self, pattern_version: str, steps: list[ToolStep | FinalStep]) -> None:
        self.pattern_version = pattern_version
        self._steps = steps
        self._index = 0

    @classmethod
    def from_agent(cls, agent: AgentSnapshot) -> ScriptedPatternAdapter:
        params = agent.pattern_parameters
        raw = params.get("script") if isinstance(params, dict) else None
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
        return cls(agent.pattern_version, steps)

    def next_action(
        self,
        context: RunContext,
        observations: Sequence[Observation],
        remaining: BudgetRemaining,
    ) -> Action:
        del context, observations, remaining
        if self._index >= len(self._steps):
            raise InvalidScriptError("el script no emitió una acción final")
        step = self._steps[self._index]
        self._index += 1
        if isinstance(step, ToolStep):
            return ToolCall(tool=step.tool, arguments=step.arguments)
        return FinalAnswer(output=step.output)


def adapter_for(agent: AgentSnapshot) -> ScriptedPatternAdapter:
    if agent.pattern != SCRIPTED_PATTERN:
        raise UnsupportedPatternError(f"patrón no implementado: {agent.pattern}")
    return ScriptedPatternAdapter.from_agent(agent)
