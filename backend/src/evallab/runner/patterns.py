"""Registro de patrones: elige el PatternAdapter de una configuración de agente."""

from __future__ import annotations

from collections.abc import Sequence

from evallab.runner.contracts import AgentSnapshot, AllowedTool, PatternAdapter
from evallab.runner.errors import UnsupportedPatternError
from evallab.runner.planner_executor import PLANNER_EXECUTOR_PATTERN, PlannerExecutorAdapter
from evallab.runner.react import REACT_PATTERN, ReactPatternAdapter
from evallab.runner.scripted import SCRIPTED_PATTERN, ScriptedPatternAdapter
from evallab.schemas import ScenarioPublicOut


def adapter_for(
    agent: AgentSnapshot, scenario: ScenarioPublicOut, tools: Sequence[AllowedTool] = ()
) -> PatternAdapter:
    if agent.pattern == SCRIPTED_PATTERN:
        return ScriptedPatternAdapter.from_agent(agent, scenario)
    if agent.pattern == REACT_PATTERN:
        return ReactPatternAdapter(agent, scenario, tools)
    if agent.pattern == PLANNER_EXECUTOR_PATTERN:
        return PlannerExecutorAdapter(agent, scenario, tools)
    raise UnsupportedPatternError(f"patrón no implementado: {agent.pattern}")
