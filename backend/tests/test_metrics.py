"""Perfil core-metrics: N/A, unknown, error, denominador cero y agregado S/N."""

import math
from typing import Any

import pytest

from evallab.evaluation.metrics import ScoreObservation, ratio, run_observations, success_summary
from tests.test_evaluation import ARGS_CALC, REQUIRED_CALC, TOTAL_42, _evaluate, _run
from tests.test_runner import ADD_STEP, FINAL_STEP


def _by_metric(observations: list[ScoreObservation]) -> dict[str, ScoreObservation]:
    return {o.metric_id: o for o in observations}


def test_partial_experiment_seven_of_ten() -> None:
    summary = success_summary(["pass"] * 7 + ["fail"] * 2 + [None])
    assert (summary.cells, summary.successes, summary.failures, summary.unknown) == (10, 7, 2, 1)
    assert summary.conservative_rate == 0.7
    assert summary.coverage == 0.9
    assert summary.evaluable_rate == pytest.approx(7 / 9)
    assert summary.missingness_range == (0.7, 0.8)


def test_error_counts_as_unknown_and_not_applicable_leaves_profile() -> None:
    summary = success_summary(["pass", "error", "unknown", "not_applicable"])
    assert (summary.cells, summary.unknown) == (3, 2)
    assert summary.conservative_rate == pytest.approx(1 / 3)
    assert summary.missingness_range is not None
    low, high = summary.missingness_range
    assert low == pytest.approx(1 / 3)
    assert high == 1.0


@pytest.mark.parametrize("statuses", [[], ["not_applicable"]])
def test_zero_cells_is_not_applicable_not_nan(statuses: list[str]) -> None:
    summary = success_summary(statuses)
    assert summary.cells == 0
    assert summary.conservative_rate is None
    assert summary.coverage is None
    assert summary.evaluable_rate is None
    assert summary.missingness_range is None


def test_all_unknown_has_no_evaluable_rate() -> None:
    summary = success_summary([None, "unknown"])
    assert summary.conservative_rate == 0.0
    assert summary.coverage == 0.0
    assert summary.evaluable_rate is None
    assert summary.missingness_range == (0.0, 1.0)


def test_zero_denominator_ratio_is_not_applicable() -> None:
    observation = ratio("tool_accuracy", 0, 0)
    assert observation.status == "not_applicable"
    assert observation.value is None
    assert (observation.numerator, observation.denominator) == (0, 0)


@pytest.mark.parametrize(
    ("numerator", "denominator", "status", "value"),
    [(2, 2, "pass", 1.0), (1, 2, "fail", 0.5), (0, 3, "fail", 0.0)],
)
def test_ratio_status_and_value(
    numerator: int, denominator: int, status: str, value: float
) -> None:
    observation = ratio("schema_argument_validity", numerator, denominator)
    assert (observation.status, observation.value) == (status, value)
    assert observation.value is not None and math.isfinite(observation.value)


def test_no_tool_calls_when_required() -> None:
    events, _ = _run(FINAL_STEP)
    metrics = _by_metric(run_observations(_evaluate(events, {"total": 42})))
    assert metrics["tool_accuracy"].status == "not_applicable"
    assert metrics["tool_accuracy"].value is None
    assert metrics["required_tool_coverage"].value == 0.0
    assert metrics["required_tool_coverage"].status == "fail"
    assert metrics["task_success"].status == "fail"
    assert metrics["task_success"].value == 0.0


def test_valid_json_wrong_units_splits_schema_and_semantics() -> None:
    wrong = {**ADD_STEP, "arguments": {"op": "add", "a": 40, "b": 2}}
    events, _ = _run(wrong, FINAL_STEP)
    expected = {**ARGS_CALC, "value": {"op": "add", "a": 4000, "b": 200}}
    report = _evaluate(events, {"total": 42}, checks=[TOTAL_42, REQUIRED_CALC, expected])
    metrics = _by_metric(run_observations(report))
    assert (
        metrics["schema_argument_validity"].status,
        metrics["schema_argument_validity"].value,
    ) == (
        "pass",
        1.0,
    )
    assert (metrics["argument_accuracy"].status, metrics["argument_accuracy"].value) == (
        "fail",
        0.0,
    )


def test_unverifiable_argument_semantics_is_unknown() -> None:
    events, _ = _run()
    report = _evaluate(events, {"total": 42}, checks=[TOTAL_42, REQUIRED_CALC])
    argument = _by_metric(run_observations(report))["argument_accuracy"]
    assert (argument.status, argument.value, argument.denominator) == ("unknown", None, None)


def test_incomplete_trace_leaves_process_metrics_unknown() -> None:
    events, _ = _run()
    metrics = _by_metric(
        run_observations(_evaluate(events, {"total": 42}, completeness="incomplete"))
    )
    for metric in ("tool_accuracy", "argument_accuracy", "required_tool_coverage"):
        assert metrics[metric].status == "unknown"
        assert metrics[metric].value is None
    assert metrics["policy_violation_rate"].status == "unknown"
    assert metrics["task_success"].status == "unknown"
    assert metrics["task_success"].value is None


def test_evaluator_error_propagates_to_metric() -> None:
    events, _ = _run()
    broken: dict[str, Any] = {**REQUIRED_CALC, "min_calls": "many"}
    metrics = _by_metric(
        run_observations(_evaluate(events, {"total": 42}, checks=[TOTAL_42, broken]))
    )
    assert metrics["required_tool_coverage"].status == "error"
    assert metrics["required_tool_coverage"].value is None
    assert metrics["task_success"].status == "unknown"


def test_forbidden_attempt_counts_against_tool_accuracy_and_policy() -> None:
    shell = {"type": "tool", "tool": "shell", "arguments": {"cmd": "id"}}
    events, _ = _run(shell, ADD_STEP, FINAL_STEP)
    metrics = _by_metric(run_observations(_evaluate(events, {"total": 42})))
    assert (metrics["tool_accuracy"].numerator, metrics["tool_accuracy"].denominator) == (1, 2)
    assert metrics["schema_argument_validity"].value == 0.5
    assert metrics["policy_violation_rate"].value == 1.0
    assert metrics["policy_violation_rate"].evidence_refs
    assert metrics["raw_outcome_pass"].status == "pass"
    assert metrics["task_success"].status == "fail"
