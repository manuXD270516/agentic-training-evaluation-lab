from typing import Any

import pytest

from evallab.domain.lifecycle import (
    EVALUATION_LIFECYCLE,
    EXPERIMENT_LIFECYCLE,
    RUN_LIFECYCLE,
    EvaluationStatus,
    ExperimentStatus,
    InvalidTransitionError,
    Lifecycle,
    RunStatus,
)


def test_terminal_states_match_design() -> None:
    assert EXPERIMENT_LIFECYCLE.terminal == {
        ExperimentStatus.COMPLETED,
        ExperimentStatus.FAILED,
        ExperimentStatus.CANCELLED,
    }
    assert RUN_LIFECYCLE.terminal == {
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.TIMED_OUT,
        RunStatus.BUDGET_EXCEEDED,
        RunStatus.CANCELLED,
    }
    assert EVALUATION_LIFECYCLE.terminal == {EvaluationStatus.COMPLETED, EvaluationStatus.ERROR}


def _reachable(lifecycle: Lifecycle[Any]) -> set[Any]:
    seen = {lifecycle.initial}
    frontier = [lifecycle.initial]
    while frontier:
        for target in lifecycle.transitions.get(frontier.pop(), frozenset()):
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    return seen


@pytest.mark.parametrize("lifecycle", [EXPERIMENT_LIFECYCLE, RUN_LIFECYCLE, EVALUATION_LIFECYCLE])
def test_every_state_is_reachable_from_initial(lifecycle: Lifecycle[Any]) -> None:
    assert _reachable(lifecycle) == lifecycle.states


def test_cancellation_before_start_is_allowed() -> None:
    EXPERIMENT_LIFECYCLE.check(ExperimentStatus.SEALED, ExperimentStatus.CANCELLED)
    RUN_LIFECYCLE.check(RunStatus.QUEUED, RunStatus.CANCELLED)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (RunStatus.COMPLETED, RunStatus.RUNNING),
        (RunStatus.FAILED, RunStatus.QUEUED),
        (RunStatus.QUEUED, RunStatus.COMPLETED),
        (RunStatus.RUNNING, RunStatus.QUEUED),
    ],
)
def test_invalid_run_transitions_are_rejected(current: RunStatus, target: RunStatus) -> None:
    with pytest.raises(InvalidTransitionError) as excinfo:
        RUN_LIFECYCLE.check(current, target)
    assert (excinfo.value.current, excinfo.value.target) == (current, target)


def test_draft_cannot_be_cancelled_or_run_directly() -> None:
    assert not EXPERIMENT_LIFECYCLE.allows(ExperimentStatus.DRAFT, ExperimentStatus.CANCELLED)
    assert not EXPERIMENT_LIFECYCLE.allows(ExperimentStatus.DRAFT, ExperimentStatus.RUNNING)
