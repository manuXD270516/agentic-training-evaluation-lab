from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from evallab.domain.lifecycle import RunStatus
from evallab.schemas import JsonValue, ScenarioPublicOut
from evallab.settings import SandboxPolicy


@dataclass(frozen=True)
class AllowedTool:
    id: uuid.UUID
    version: str
    name: str
    content_hash: str


@dataclass(frozen=True)
class AgentSnapshot:
    id: uuid.UUID
    version: str
    pattern: str
    pattern_version: str
    content_hash: str
    pattern_parameters: dict[str, Any]
    prompt_hash: str | None
    roles: tuple[str, ...]


@dataclass(frozen=True)
class RunContext:
    run_id: uuid.UUID
    attempt_id: uuid.UUID
    experiment_id: uuid.UUID
    mode: Literal["live", "replay"]
    seed: int
    started_at: datetime
    limits: dict[str, Any]
    sandbox: SandboxPolicy
    manifest_hash: str | None
    experiment_budgets: dict[str, Any]


@dataclass(frozen=True)
class BudgetRemaining:
    max_steps: int | None = None
    steps_used: int = 0


@dataclass(frozen=True)
class Observation:
    call_id: uuid.UUID
    tool: str
    result: Any


@dataclass(frozen=True)
class ToolCall:
    tool: str
    arguments: dict[str, Any]
    result: Any
    evidence_id: str | None = None


@dataclass(frozen=True)
class FinalAnswer:
    output: Any


@dataclass(frozen=True)
class ModelCall:
    """Reservado para ReAct (M6); scripted no lo emite."""

    messages: tuple[Any, ...] = ()


Action = ToolCall | FinalAnswer | ModelCall


@dataclass(frozen=True)
class EvidenceRef:
    event_id: uuid.UUID
    pointer: str

    def as_json(self) -> dict[str, str]:
        return {"event_id": str(self.event_id), "pointer": self.pointer}


@dataclass(frozen=True)
class Usage:
    model_calls: int
    tool_calls: int
    source: Literal["observed", "estimated", "unknown"] = "observed"
    scope: Literal["agent", "judge"] = "agent"

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "source": self.source,
            "scope": self.scope,
        }


@dataclass(frozen=True)
class RunResult:
    status: RunStatus
    pattern: str
    pattern_version: str
    output: Any | None
    evidence_refs: tuple[EvidenceRef, ...]
    usage: Usage
    error_class: str | None = None
    error: str | None = None
    completeness: str = "complete"


class PatternAdapter(Protocol):
    pattern: str
    pattern_version: str

    def next_action(
        self,
        context: RunContext,
        observations: Sequence[Observation],
        remaining: BudgetRemaining,
    ) -> Action: ...


class ModelGateway(Protocol):
    def generate(self, messages: Sequence[Any], tools: Sequence[AllowedTool]) -> Any: ...


class ToolGateway(Protocol):
    def invoke(
        self, tool: str, version: str, call_id: uuid.UUID, arguments: dict[str, Any]
    ) -> Any: ...


class StoredEvent(Protocol):
    event_id: uuid.UUID
    sequence: int
    type: str
    payload_digest: str


class TraceSink(Protocol):
    def append(
        self,
        event_type: str,
        actor_role: str,
        payload: dict[str, Any],
        *,
        parent_event_id: uuid.UUID | None = None,
        event_id: uuid.UUID | None = None,
    ) -> StoredEvent: ...

    events: Sequence[StoredEvent]
    completeness: str

    def digest(self) -> str: ...


# ScenarioPublicOut es la vista que consume el runner; se reexporta para los contratos.
ScenarioPublicView = ScenarioPublicOut
