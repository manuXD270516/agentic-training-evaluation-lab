"""Registro de patrones: elige el PatternAdapter de una configuración de agente."""

from __future__ import annotations

from evallab.runner.contracts import AgentSnapshot, PatternAdapter
from evallab.runner.errors import UnsupportedPatternError
from evallab.runner.scripted import SCRIPTED_PATTERN, ScriptedPatternAdapter
from evallab.schemas import ScenarioPublicOut


def adapter_for(agent: AgentSnapshot, scenario: ScenarioPublicOut) -> PatternAdapter:
    if agent.pattern == SCRIPTED_PATTERN:
        return ScriptedPatternAdapter.from_agent(agent, scenario)
    raise UnsupportedPatternError(f"patrón no implementado: {agent.pattern}")
