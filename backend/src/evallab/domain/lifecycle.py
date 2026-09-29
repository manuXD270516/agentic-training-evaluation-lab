from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class ExperimentStatus(StrEnum):
    DRAFT = "draft"
    SEALED = "sealed"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    BUDGET_EXCEEDED = "budget_exceeded"
    CANCELLED = "cancelled"


class EvaluationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"


class InvalidTransitionError(ValueError):
    def __init__(self, entity: str, current: str, target: str) -> None:
        super().__init__(f"invalid status transition on {entity}: {current} -> {target}")
        self.entity = entity
        self.current = current
        self.target = target


@dataclass(frozen=True)
class Lifecycle[S: StrEnum]:
    entity: str
    initial: S
    transitions: Mapping[S, frozenset[S]]
    states: frozenset[S] = field(init=False)

    def __post_init__(self) -> None:
        states = {self.initial, *self.transitions}
        for targets in self.transitions.values():
            states.update(targets)
        object.__setattr__(self, "states", frozenset(states))

    @property
    def terminal(self) -> frozenset[S]:
        return frozenset(s for s in self.states if not self.transitions.get(s))

    def allows(self, current: S, target: S) -> bool:
        return target in self.transitions.get(current, frozenset())

    def check(self, current: S, target: S) -> None:
        if not self.allows(current, target):
            raise InvalidTransitionError(self.entity, current, target)


E, R, V = ExperimentStatus, RunStatus, EvaluationStatus

EXPERIMENT_LIFECYCLE = Lifecycle(
    entity="experiment",
    initial=E.DRAFT,
    transitions={
        E.DRAFT: frozenset({E.SEALED}),
        E.SEALED: frozenset({E.RUNNING, E.CANCELLED}),
        E.RUNNING: frozenset({E.COMPLETED, E.FAILED, E.CANCELLED}),
    },
)

RUN_LIFECYCLE = Lifecycle(
    entity="run",
    initial=R.QUEUED,
    transitions={
        R.QUEUED: frozenset({R.RUNNING, R.CANCELLED}),
        R.RUNNING: frozenset({R.COMPLETED, R.FAILED, R.TIMED_OUT, R.BUDGET_EXCEEDED, R.CANCELLED}),
    },
)

EVALUATION_LIFECYCLE = Lifecycle(
    entity="evaluation",
    initial=V.PENDING,
    transitions={
        V.PENDING: frozenset({V.RUNNING}),
        V.RUNNING: frozenset({V.COMPLETED, V.ERROR}),
    },
)
