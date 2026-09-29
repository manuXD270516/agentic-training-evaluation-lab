"""Suite determinística: checks del oráculo, gates implícitos y agregación por dimensión.

`raw_outcome_pass` sólo mira el outcome; `task_success` exige run completado, outcome,
estructura obligatoria, política y cada dimensión de `required_checks`. Un error del
evaluador o una dimensión sin evidencia deja `unknown`, nunca `pass`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from evallab.canonical import canonical_digest
from evallab.evaluation.checks import (
    OPERATOR_DIMENSIONS,
    OPERATOR_VERSION,
    OPERATORS,
    CheckContext,
    CheckResult,
    CheckStatus,
    Dimension,
)
from evallab.evaluation.trace_view import TraceView

SUITE_ID = "deterministic-core"
SUITE_VERSION = "1.0.0"
SUITE_DESCRIPTOR: Mapping[str, Any] = {
    "suite_id": SUITE_ID,
    "version": SUITE_VERSION,
    "operators": {name: OPERATOR_VERSION for name in sorted(OPERATORS)},
    "implicit_checks": ["output_structure", "policy_trace"],
    "gates": ["run_completed", "outcome", "output_structure", "policy", "required_checks"],
}
SUITE_HASH = canonical_digest(dict(SUITE_DESCRIPTOR))

DIMENSIONS: tuple[Dimension, ...] = (
    "outcome",
    "output_structure",
    "required_tool",
    "semantic_arguments",
    "evidence",
    "policy",
)
ALWAYS_GATED: tuple[Dimension, ...] = ("outcome", "output_structure", "policy")
INFRASTRUCTURE_ERRORS = frozenset({"infrastructure_error", "trace_error"})


@dataclass(frozen=True)
class EvaluationInput:
    expected: Mapping[str, Any]
    evaluation: Mapping[str, Any]
    run_status: str
    run_error_class: str | None
    run_result: Mapping[str, Any] | None
    events: Sequence[Mapping[str, Any]]
    completeness: str


@dataclass(frozen=True)
class EvaluationReport:
    checks: tuple[CheckResult, ...]
    dimensions: Mapping[str, CheckStatus]
    run_outcome: str
    raw_outcome_pass: CheckStatus
    task_success: CheckStatus
    task_success_reasons: tuple[str, ...]
    trace: TraceView

    def as_json(self) -> dict[str, Any]:
        return {
            "suite": {"id": SUITE_ID, "version": SUITE_VERSION, "hash": SUITE_HASH},
            "run_outcome": self.run_outcome,
            "raw_outcome_pass": self.raw_outcome_pass,
            "task_success": self.task_success,
            "task_success_reasons": list(self.task_success_reasons),
            "dimensions": dict(self.dimensions),
            "checks": [check.as_json() for check in self.checks],
        }


def aggregate(statuses: Iterable[CheckStatus]) -> CheckStatus:
    values = list(statuses)
    precedence: tuple[CheckStatus, ...] = ("fail", "error", "unknown", "pass")
    for status in precedence:
        if status in values:
            return status
    return "not_applicable"


def classify_run(status: str, error_class: str | None) -> str:
    if status == "completed":
        return "completed"
    if status in {"timed_out", "budget_exceeded"}:
        return "agent_failure"
    if status == "failed":
        return "infrastructure" if error_class in INFRASTRUCTURE_ERRORS else "agent_failure"
    return "not_evaluable"


def _policy_trace_check(trace: TraceView) -> CheckResult:
    base: dict[str, Any] = {
        "check_id": "policy_trace",
        "operator": "policy_trace",
        "dimension": "policy",
    }
    if not trace.reliable:
        return CheckResult(**base, status="unknown", reason="trace_not_complete")
    if not trace.violations:
        return CheckResult(**base, status="pass", reason="no_violations")
    refs = tuple({"event_id": str(v["event_id"]), "pointer": ""} for v in trace.violations)
    return CheckResult(
        **base,
        status="fail",
        reason="policy_violation_recorded",
        evidence_refs=refs,
        detail={
            "violations": len(trace.violations),
            "executed": sum(1 for v in trace.violations if v.get("executed")),
            "rules": sorted({str(v.get("rule")) for v in trace.violations}),
        },
    )


def _run_check(check_id: str, check: Mapping[str, Any], ctx: CheckContext) -> CheckResult:
    operator = str(check.get("operator"))
    implementation = OPERATORS.get(operator)
    if implementation is None:
        return CheckResult(
            check_id=check_id,
            operator=operator,
            dimension=OPERATOR_DIMENSIONS.get(operator, "outcome"),
            status="error",
            reason="unknown_operator",
        )
    try:
        return implementation(check_id, check, ctx)
    except Exception as exc:
        return CheckResult(
            check_id=check_id,
            operator=operator,
            dimension=OPERATOR_DIMENSIONS[operator],
            status="error",
            reason="evaluator_error",
            detail={"exception": type(exc).__name__},
        )


def evaluate(data: EvaluationInput) -> EvaluationReport:
    trace = TraceView.build(data.events, data.completeness)
    result = data.run_result or {}
    output_available = data.run_status == "completed" and "output" in result
    schema = data.expected.get("output_schema")
    ctx = CheckContext(
        output=result.get("output"),
        output_available=output_available,
        output_schema=schema if isinstance(schema, dict) else {},
        trace=trace,
    )
    raw_checks = data.expected.get("checks")
    oracle_checks = (
        [c for c in raw_checks if isinstance(c, dict)] if isinstance(raw_checks, list) else []
    )

    results: list[CheckResult] = []
    if not any(c.get("operator") == "output_schema_valid" for c in oracle_checks):
        results.append(_run_check("output_structure", {"operator": "output_schema_valid"}, ctx))
    results.extend(_run_check(f"c{i}", check, ctx) for i, check in enumerate(oracle_checks))
    results.append(_policy_trace_check(trace))

    dimensions: dict[str, CheckStatus] = {
        dim: aggregate(r.status for r in results if r.dimension == dim) for dim in DIMENSIONS
    }
    run_outcome = classify_run(data.run_status, data.run_error_class)
    raw = _raw_outcome(run_outcome, dimensions["outcome"])
    task, reasons = _task_success(run_outcome, dimensions, data.evaluation)
    return EvaluationReport(
        checks=tuple(results),
        dimensions=dimensions,
        run_outcome=run_outcome,
        raw_outcome_pass=raw,
        task_success=task,
        task_success_reasons=reasons,
        trace=trace,
    )


def _raw_outcome(run_outcome: str, outcome: CheckStatus) -> CheckStatus:
    if run_outcome == "agent_failure":
        return "fail"
    if run_outcome != "completed":
        return "unknown"
    if outcome in {"error", "not_applicable"}:
        return "unknown"
    return outcome


def _task_success(
    run_outcome: str, dimensions: Mapping[str, CheckStatus], evaluation: Mapping[str, Any]
) -> tuple[CheckStatus, tuple[str, ...]]:
    if run_outcome == "agent_failure":
        return "fail", ("run_not_completed",)
    if run_outcome != "completed":
        return "unknown", ("run_not_evaluable",)
    declared = evaluation.get("required_checks")
    required = [str(r) for r in declared] if isinstance(declared, list) else []
    gated: list[str] = list(dict.fromkeys([*ALWAYS_GATED, *required]))
    failed: list[str] = []
    missing: list[str] = []
    for dim in gated:
        status = dimensions.get(dim)
        if status is None:
            missing.append(f"{dim}:not_implemented")
        elif status == "fail":
            failed.append(f"{dim}:fail")
        elif status != "pass":
            missing.append(f"{dim}:{status}")
    if failed:
        return "fail", tuple(failed + missing)
    if missing:
        return "unknown", tuple(missing)
    return "pass", ()
