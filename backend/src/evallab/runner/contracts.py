from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

from evallab.domain.lifecycle import RunStatus
from evallab.runner.models import ModelRequest, ModelResult, ModelSnapshot, ModelToolCall
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
    models: Mapping[str, ModelSnapshot] = field(default_factory=dict)


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
    clock: Callable[[], float] = field(default=time.monotonic, compare=False)


@dataclass(frozen=True)
class BudgetRemaining:
    max_steps: int | None = None
    steps_used: int = 0
    max_tool_calls: int | None = None
    tool_calls_used: int = 0
    deadline_ms: int | None = None
    elapsed_ms: int = 0
    max_model_calls: int | None = None
    model_calls_used: int = 0
    max_tokens: int | None = None
    tokens_used: int | None = 0


ToolOutcomeKind = Literal["completed", "denied", "invalid", "failed"]


@dataclass(frozen=True)
class Observation:
    """Lo que el patrón ve de una llamada: estado, resultado y el id de evidencia citable."""

    call_id: uuid.UUID
    tool: str
    status: ToolOutcomeKind
    result: Any
    error_class: str | None = None
    # event_id del `tool.completed`; es lo que una respuesta puede citar como evidencia.
    evidence_id: uuid.UUID | None = None
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolCall:
    tool: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolOutcome:
    """Resultado de ToolGateway.invoke; `validated` indica si pasó identidad, permiso y schema."""

    kind: ToolOutcomeKind
    validated: bool
    reason_codes: tuple[str, ...] = ()
    schema_errors: tuple[dict[str, str], ...] = ()
    result: Any = None
    error_class: str | None = None
    error: str | None = None
    state_digest: str | None = None
    retriable: bool = False
    ambiguous_effect: bool = False
    fault_id: str | None = None
    timeout_ms: int | None = None
    idempotent_replay: bool = False


@dataclass(frozen=True)
class FinalAnswer:
    output: Any


@dataclass(frozen=True)
class ModelCall:
    """El patrón pide una llamada al modelo de un rol; el runner la ejecuta y la traza."""

    request: ModelRequest


@dataclass(frozen=True)
class ModelObservation:
    """Respuesta del modelo tal como la ve el patrón (tras validación del gateway)."""

    role: str
    content: str | None
    tool_calls: tuple[ModelToolCall, ...]
    finish_reason: str | None
    event_id: uuid.UUID


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    description: str
    tool: str | None = None
    arguments: dict[str, Any] | None = None
    depends_on: tuple[str, ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "description": self.description,
            "tool": self.tool,
            "arguments": self.arguments,
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True)
class PlanCreated:
    """Plan público estructurado (Planner/Executor); el runner lo valida y emite plan.created."""

    steps: tuple[PlanStep, ...]
    source_event_id: uuid.UUID | None = None


Action = ToolCall | FinalAnswer | ModelCall | PlanCreated
PatternObservation = Observation | ModelObservation


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
    steps: int = 0
    retries: int = 0
    tokens: Mapping[str, Any] | None = None
    cost: Mapping[str, Any] | None = None
    by_role: Mapping[str, Any] | None = None

    def as_json(self) -> dict[str, JsonValue]:
        document: dict[str, JsonValue] = {
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "steps": self.steps,
            "retries": self.retries,
            "source": self.source,
            "scope": self.scope,
        }
        if self.tokens is not None:
            document["tokens"] = dict(self.tokens)
        if self.cost is not None:
            document["cost"] = dict(self.cost)
        if self.by_role is not None:
            document["by_role"] = dict(self.by_role)
        return document


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
    policy_violations: int = 0
    termination: dict[str, Any] | None = None
    providers: tuple[str, ...] = ()


class PatternAdapter(Protocol):
    pattern: str
    pattern_version: str

    def next_action(
        self,
        context: RunContext,
        observations: Sequence[PatternObservation],
        remaining: BudgetRemaining,
    ) -> Action: ...


class ModelGateway(Protocol):
    def models(self) -> Mapping[str, ModelSnapshot]: ...

    def generate(self, request: ModelRequest) -> ModelResult: ...


class ToolGateway(Protocol):
    def allowed_tools(self) -> Sequence[AllowedTool]: ...

    def resolve(self, name: str) -> AllowedTool | None: ...

    def invoke(self, name: str, call_id: uuid.UUID, arguments: dict[str, Any]) -> ToolOutcome: ...


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
