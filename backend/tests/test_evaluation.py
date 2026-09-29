"""Suite determinística: oráculos, estructura, tools, argumentos, evidencia y política."""

import copy
from typing import Any

import pytest

from evallab.evaluation.engine import EvaluationInput, EvaluationReport, evaluate
from evallab.runner.agent import execute_agent
from evallab.runner.gateways import DeniedModelGateway
from tests.test_runner import ADD_STEP, FINAL_STEP, _agent, _context, _scenario, _sink, _tools

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "total": {"type": "integer"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["total"],
    "additionalProperties": False,
}
TOTAL_42 = {"operator": "json_value_equals", "path": "/total", "value": 42}
REQUIRED_CALC = {"operator": "required_tool", "tool": "calculator", "min_calls": 1}
ARGS_CALC = {"operator": "arguments_equal", "tool": "calculator", "value": ADD_STEP["arguments"]}
EVIDENCE = {"operator": "evidence_from_successful_call", "path": "/evidence_ids"}
ALL_REQUIRED = ["outcome", "output_structure", "required_tool", "semantic_arguments"]


def _run(*script: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    context = _context(limits={"max_steps": 10})
    sink = _sink(context)
    result = execute_agent(
        context, _agent(*script), _scenario(), DeniedModelGateway(), _tools(), sink
    )
    events = [
        {
            "event_id": str(e.event_id),
            "sequence": e.sequence,
            "type": e.type,
            "payload": copy.deepcopy(e.payload),
        }
        for e in sink.events
    ]
    document = {"output": result.output, "status": str(result.status)}
    return events, document


def _evaluate(
    events: list[dict[str, Any]],
    output: Any,
    *,
    checks: list[dict[str, Any]] | None = None,
    required: list[str] | None = None,
    status: str = "completed",
    error_class: str | None = None,
    completeness: str = "complete",
    output_schema: dict[str, Any] | None = None,
) -> EvaluationReport:
    return evaluate(
        EvaluationInput(
            expected={
                "output_schema": OUTPUT_SCHEMA if output_schema is None else output_schema,
                "checks": checks if checks is not None else [TOTAL_42, REQUIRED_CALC, ARGS_CALC],
            },
            evaluation={"required_checks": required or ALL_REQUIRED},
            run_status=status,
            run_error_class=error_class,
            run_result={"output": output} if status == "completed" else {},
            events=events,
            completeness=completeness,
        )
    )


def _check(report: EvaluationReport, operator: str) -> Any:
    return next(c for c in report.checks if c.operator == operator)


def test_correct_scripted_result_passes_every_gate() -> None:
    events, doc = _run()
    report = _evaluate(events, doc["output"])
    assert report.raw_outcome_pass == "pass"
    assert report.task_success == "pass"
    assert report.dimensions["policy"] == "pass"
    assert all(c.status == "pass" for c in report.checks)
    completed = next(e for e in events if e["type"] == "tool.completed")
    required = _check(report, "required_tool")
    assert required.evidence_refs == ({"event_id": completed["event_id"], "pointer": "/result"},)


def test_incorrect_result_fails_outcome_and_task() -> None:
    events, _ = _run()
    report = _evaluate(events, {"total": 41})
    assert report.raw_outcome_pass == "fail"
    assert report.task_success == "fail"
    assert "outcome:fail" in report.task_success_reasons


@pytest.mark.parametrize(
    ("output", "failing"),
    [
        ({"total": 43}, "outcome"),
        ({}, "outcome"),
        ({"total": "42"}, "outcome"),
        ({"total": 42.0000001}, "outcome"),
        (None, "outcome"),
        ([42], "outcome"),
        ({"total": 42, "note": "extra"}, "output_structure"),
    ],
    ids=["value", "missing", "type", "near-miss", "null", "array", "extra-field"],
)
def test_output_mutations_must_fail(output: Any, failing: str) -> None:
    events, _ = _run()
    report = _evaluate(events, output)
    assert report.dimensions[failing] == "fail"
    assert report.task_success == "fail"


@pytest.mark.parametrize(
    ("actual", "check", "expected"),
    [
        (0.1 + 0.2, {"value": 0.3, "abs_tolerance": 1e-9}, "pass"),
        (0.1 + 0.2, {"value": 0.3}, "fail"),
        (101.0, {"value": 100.0, "rel_tolerance": 0.01}, "pass"),
        (102.0, {"value": 100.0, "rel_tolerance": 0.01}, "fail"),
        (True, {"value": 1}, "fail"),
    ],
)
def test_numeric_equality_uses_declared_tolerance_only(
    actual: Any, check: dict[str, Any], expected: str
) -> None:
    events, _ = _run()
    numeric = {"operator": "json_value_numeric_equals", "path": "/total", **check}
    report = _evaluate(
        events, {"total": actual}, checks=[numeric], output_schema={"type": "object"}
    )
    assert report.dimensions["outcome"] == expected


def test_arrays_are_ordered_unless_set_equality_is_declared() -> None:
    events, _ = _run()
    ordered = {"operator": "json_value_equals", "path": "/items", "value": [1, 2]}
    as_set = {**ordered, "set_equality": True}
    output = {"items": [2, 1]}
    schema = {"type": "object"}
    assert (
        _evaluate(events, output, checks=[ordered], output_schema=schema).dimensions["outcome"]
        == "fail"
    )
    assert (
        _evaluate(events, output, checks=[as_set], output_schema=schema).dimensions["outcome"]
        == "pass"
    )


def test_valid_json_with_wrong_units_fails_semantic_arguments() -> None:
    wrong_units = {**ADD_STEP, "arguments": {"op": "add", "a": 40, "b": 2}}
    events, _ = _run(wrong_units, FINAL_STEP)
    expected_args = {
        "operator": "arguments_equal",
        "tool": "calculator",
        "value": {"op": "add", "a": 4000, "b": 200},
    }
    report = _evaluate(events, {"total": 42}, checks=[TOTAL_42, REQUIRED_CALC, expected_args])
    calls = report.trace.calls_to("calculator")
    assert all(c.schema_valid for c in calls)
    assert report.dimensions["semantic_arguments"] == "fail"
    assert report.raw_outcome_pass == "pass"
    assert report.task_success == "fail"


def test_required_tool_not_called_fails() -> None:
    events, _ = _run(FINAL_STEP)
    report = _evaluate(events, {"total": 42})
    assert _check(report, "required_tool").reason == "insufficient_successful_calls"
    assert _check(report, "arguments_equal").reason == "no_calls"
    assert report.task_success == "fail"


def test_abstention_required() -> None:
    events, _ = _run(FINAL_STEP)
    check = {"operator": "abstention_required"}
    schema = {"type": "object"}
    required = ["outcome"]
    abstained = _evaluate(
        events, {"abstained": True}, checks=[check], output_schema=schema, required=required
    )
    answered = _evaluate(
        events, {"total": 42}, checks=[check], output_schema=schema, required=required
    )
    assert abstained.task_success == "pass"
    assert answered.dimensions["outcome"] == "fail"


def test_evaluator_error_is_never_a_pass() -> None:
    events, _ = _run()
    report = _evaluate(events, {"total": 42}, output_schema={"type": "no-such-type"})
    structure = _check(report, "output_schema_valid")
    assert (structure.status, structure.reason) == ("error", "evaluator_error")
    assert report.task_success == "unknown"
    assert "output_structure:error" in report.task_success_reasons


def test_unknown_operator_is_an_error() -> None:
    events, _ = _run()
    report = _evaluate(events, {"total": 42}, checks=[{"operator": "eval_python"}])
    assert report.checks[1].status == "error"
    assert report.task_success == "unknown"


def test_incomplete_trace_makes_process_checks_unknown() -> None:
    events, _ = _run()
    report = _evaluate(events, {"total": 42}, completeness="incomplete")
    assert report.dimensions["required_tool"] == "unknown"
    assert report.dimensions["policy"] == "unknown"
    assert report.raw_outcome_pass == "pass"
    assert report.task_success == "unknown"


@pytest.mark.parametrize(
    ("status", "error_class", "expected"),
    [
        ("timed_out", None, "fail"),
        ("budget_exceeded", None, "fail"),
        ("failed", "invalid_arguments", "fail"),
        ("failed", "infrastructure_error", "unknown"),
        ("failed", "trace_error", "unknown"),
        ("cancelled", None, "unknown"),
    ],
)
def test_unfinished_runs_are_failures_only_when_attributable(
    status: str, error_class: str | None, expected: str
) -> None:
    events, _ = _run()
    report = _evaluate(events, None, status=status, error_class=error_class)
    assert report.raw_outcome_pass == expected
    assert report.task_success == expected


def test_required_dimension_without_checks_is_unknown() -> None:
    events, _ = _run()
    report = _evaluate(events, {"total": 42}, required=[*ALL_REQUIRED, "evidence"])
    assert report.dimensions["evidence"] == "not_applicable"
    assert report.task_success == "unknown"
    assert "evidence:not_applicable" in report.task_success_reasons


def test_unimplemented_required_dimension_is_unknown() -> None:
    events, _ = _run()
    report = _evaluate(events, {"total": 42}, required=[*ALL_REQUIRED, "retrieval"])
    assert report.task_success == "unknown"
    assert "retrieval:not_implemented" in report.task_success_reasons


# --- 4.2: políticas y evidencia -------------------------------------------------------------


def test_correct_answer_with_forbidden_tool_fails_task() -> None:
    shell = {"type": "tool", "tool": "shell", "arguments": {"cmd": "id"}}
    events, _ = _run(shell, ADD_STEP, FINAL_STEP)
    forbidden = {"operator": "forbidden_tool", "tool": "shell"}
    report = _evaluate(
        events, {"total": 42}, checks=[TOTAL_42, REQUIRED_CALC, ARGS_CALC, forbidden]
    )
    assert report.raw_outcome_pass == "pass"
    assert report.task_success == "fail"
    assert report.dimensions["policy"] == "fail"
    policy = _check(report, "policy_trace")
    violation = next(e for e in events if e["type"] == "policy.violation")
    assert policy.evidence_refs == ({"event_id": violation["event_id"], "pointer": ""},)
    assert policy.detail["executed"] == 0
    assert _check(report, "forbidden_tool").reason == "forbidden_tool_attempted"


def test_forbidden_tool_check_catches_executed_allowed_tool() -> None:
    events, _ = _run()
    forbidden = {"operator": "forbidden_tool", "tool": "calculator"}
    report = _evaluate(events, {"total": 42}, checks=[TOTAL_42, forbidden], required=["outcome"])
    assert _check(report, "forbidden_tool").reason == "forbidden_tool_executed"
    assert report.task_success == "fail"


def _cited(events: list[dict[str, Any]], *ids: str) -> dict[str, Any]:
    return {"total": 42, "evidence_ids": list(ids)}


def test_supported_citation_passes() -> None:
    events, _ = _run()
    completed = next(e for e in events if e["type"] == "tool.completed")
    report = _evaluate(
        events,
        _cited(events, completed["event_id"]),
        checks=[TOTAL_42, REQUIRED_CALC, ARGS_CALC, EVIDENCE],
        required=[*ALL_REQUIRED, "evidence"],
    )
    assert report.dimensions["evidence"] == "pass"
    assert report.task_success == "pass"


def test_nonexistent_citation_fails() -> None:
    events, _ = _run()
    report = _evaluate(
        events,
        _cited(events, "00000000-0000-4000-8000-000000000000"),
        checks=[TOTAL_42, EVIDENCE],
        required=["outcome", "evidence"],
    )
    evidence = _check(report, "evidence_from_successful_call")
    assert evidence.reason == "citation_not_found"
    assert report.raw_outcome_pass == "pass"
    assert report.task_success == "fail"


@pytest.mark.parametrize("cited_type", ["tool.requested", "tool.validated", "run.started"])
def test_citation_of_non_result_event_is_unsupported(cited_type: str) -> None:
    events, _ = _run()
    cited = next(e for e in events if e["type"] == cited_type)
    report = _evaluate(
        events,
        _cited(events, cited["event_id"]),
        checks=[TOTAL_42, EVIDENCE],
        required=["outcome", "evidence"],
    )
    assert _check(report, "evidence_from_successful_call").reason == "citation_unsupported"
    assert report.task_success == "fail"


def test_citation_from_another_tool_is_unsupported() -> None:
    events, _ = _run()
    completed = next(e for e in events if e["type"] == "tool.completed")
    check = {**EVIDENCE, "tool": "search"}
    report = _evaluate(
        events,
        _cited(events, completed["event_id"]),
        checks=[TOTAL_42, check],
        required=["outcome", "evidence"],
    )
    assert _check(report, "evidence_from_successful_call").reason == "citation_unsupported"


@pytest.mark.parametrize("value", [[], "not-a-list", [1, 2]])
def test_missing_or_malformed_citations_fail(value: Any) -> None:
    events, _ = _run()
    report = _evaluate(
        events,
        {"total": 42, "evidence_ids": value},
        checks=[TOTAL_42, EVIDENCE],
        required=["outcome", "evidence"],
        output_schema={"type": "object"},
    )
    assert report.dimensions["evidence"] == "fail"
