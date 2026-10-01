"""Perfil de métricas `core-metrics@1.0.0`: observaciones por run y agregado S/N (metrics.md).

Una observación de ratio por run es `pass` si todas las unidades del denominador cumplen,
`fail` si alguna no, `not_applicable` con denominador cero (valor nulo, nunca 1 ni 0) y
`unknown` si falta evidencia para verificarla. `error` propaga un fallo del evaluador.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from evallab.canonical import canonical_digest
from evallab.evaluation.checks import CheckResult, CheckStatus
from evallab.evaluation.engine import EvaluationReport

PROFILE_ID = "core-metrics"
PROFILE_VERSION = "1.0.0"
METRIC_VERSION = "1.0.0"
PROFILE_DESCRIPTOR: Mapping[str, Any] = {
    "profile_id": PROFILE_ID,
    "version": PROFILE_VERSION,
    "metrics": {
        name: METRIC_VERSION
        for name in (
            "task_success",
            "raw_outcome_pass",
            "tool_accuracy",
            "required_tool_coverage",
            "argument_accuracy",
            "schema_argument_validity",
            "evidence_coverage",
            "policy_violation_rate",
        )
    },
}
PROFILE_HASH = canonical_digest(dict(PROFILE_DESCRIPTOR))


@dataclass(frozen=True)
class ScoreObservation:
    metric_id: str
    status: CheckStatus
    unit: str
    value: float | None = None
    numerator: int | None = None
    denominator: int | None = None
    scope: str = "agent"
    metric_version: str = METRIC_VERSION
    evidence_refs: tuple[Mapping[str, str], ...] = field(default_factory=tuple)

    def as_json(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "metric_version": self.metric_version,
            "scope": self.scope,
            "status": self.status,
            "unit": self.unit,
            "value": self.value,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "evidence_refs": [dict(r) for r in self.evidence_refs],
        }


def _binary(metric_id: str, status: CheckStatus, refs: Iterable[Mapping[str, str]] = ()) -> Any:
    value = {"pass": 1.0, "fail": 0.0}.get(status)
    return ScoreObservation(
        metric_id=metric_id,
        status=status,
        unit="boolean",
        value=value,
        numerator=None if value is None else int(value),
        denominator=None if value is None else 1,
        evidence_refs=tuple(refs),
    )


def ratio(
    metric_id: str,
    numerator: int,
    denominator: int,
    refs: Iterable[Mapping[str, str]] = (),
) -> ScoreObservation:
    if denominator == 0:
        return ScoreObservation(
            metric_id=metric_id,
            status="not_applicable",
            unit="ratio",
            numerator=0,
            denominator=0,
            evidence_refs=tuple(refs),
        )
    return ScoreObservation(
        metric_id=metric_id,
        status="pass" if numerator == denominator else "fail",
        unit="ratio",
        value=float(Fraction(numerator, denominator)),
        numerator=numerator,
        denominator=denominator,
        evidence_refs=tuple(refs),
    )


def _missing(metric_id: str, status: CheckStatus, unit: str = "ratio") -> ScoreObservation:
    return ScoreObservation(metric_id=metric_id, status=status, unit=unit)


def _checks(report: EvaluationReport, operator: str) -> list[CheckResult]:
    return [c for c in report.checks if c.operator == operator]


def _refs(checks: Iterable[CheckResult]) -> list[Mapping[str, str]]:
    return [ref for check in checks for ref in check.evidence_refs]


def _from_checks(metric_id: str, checks: list[CheckResult]) -> ScoreObservation:
    statuses = {c.status for c in checks}
    if "error" in statuses:
        return _missing(metric_id, "error")
    if "unknown" in statuses:
        return _missing(metric_id, "unknown")
    passed = sum(1 for c in checks if c.status == "pass")
    return ratio(metric_id, passed, len(checks), _refs(checks))


def _call_metrics(
    report: EvaluationReport, arguments: list[CheckResult], forbidden: set[str]
) -> list[ScoreObservation]:
    trace = report.trace
    if not trace.reliable:
        return [
            _missing("tool_accuracy", "unknown"),
            _missing("argument_accuracy", "unknown"),
            _missing("schema_argument_validity", "unknown"),
        ]
    calls = trace.calls
    refs = [{"event_id": c.requested_event_id, "pointer": "/tool"} for c in calls]
    denied = {
        c.call_id
        for c in calls
        if c.denied is not None and c.denied.get("policy_result") == "denied"
    }
    correct_tool = sum(1 for c in calls if c.call_id not in denied and c.tool not in forbidden)
    schema_valid = sum(1 for c in calls if c.schema_valid)

    expected_by_tool: dict[str, set[str]] = {}
    for check in arguments:
        tool = check.detail.get("tool")
        if isinstance(tool, str):
            expected_by_tool.setdefault(tool, set()).add(str(check.detail.get("expected_digest")))
    unverifiable = any(c.schema_valid and c.tool not in expected_by_tool for c in calls)
    accurate = sum(
        1
        for c in calls
        if c.schema_valid and canonical_digest(c.arguments) in expected_by_tool.get(c.tool, set())
    )
    argument = (
        _missing("argument_accuracy", "unknown")
        if calls and unverifiable
        else ratio("argument_accuracy", accurate, len(calls), refs)
    )
    return [
        ratio("tool_accuracy", correct_tool, len(calls), refs),
        argument,
        ratio("schema_argument_validity", schema_valid, len(calls), refs),
    ]


def run_observations(report: EvaluationReport) -> list[ScoreObservation]:
    """Observaciones por run del perfil; no inventa valores para lo que no se puede medir."""
    outcome_refs = _refs(c for c in report.checks if c.dimension == "outcome")
    arguments = _checks(report, "arguments_equal")
    forbidden = {
        str(c.detail.get("tool")) for c in _checks(report, "forbidden_tool") if c.detail.get("tool")
    }
    observations = [
        _binary("task_success", report.task_success, _refs(report.checks)),
        _binary("raw_outcome_pass", report.raw_outcome_pass, outcome_refs),
        _from_checks("required_tool_coverage", _checks(report, "required_tool")),
        _from_checks("evidence_coverage", _checks(report, "evidence_from_successful_call")),
        *_call_metrics(report, arguments, forbidden),
    ]
    policy = next(c for c in report.checks if c.operator == "policy_trace")
    if policy.status in {"unknown", "error"}:
        observations.append(_missing("policy_violation_rate", policy.status))
    else:
        violated = 1 if policy.status == "fail" else 0
        observations.append(
            ScoreObservation(
                metric_id="policy_violation_rate",
                status="fail" if violated else "pass",
                unit="ratio",
                value=float(violated),
                numerator=violated,
                denominator=1,
                evidence_refs=policy.evidence_refs,
            )
        )
    if report.retrieval_truth is not None and report.context is not None:
        from evallab.evaluation.retrieval import retrieval_observations

        observations.extend(retrieval_observations(report.context, report.retrieval_truth))
    return observations


@dataclass(frozen=True)
class SuccessSummary:
    """Éxito verificado sobre las N celdas programadas del perfil determinístico."""

    cells: int
    successes: int
    failures: int
    unknown: int
    conservative_rate: float | None
    coverage: float | None
    evaluable_rate: float | None
    missingness_range: tuple[float, float] | None

    def as_json(self) -> dict[str, Any]:
        return {
            "cells": self.cells,
            "successes": self.successes,
            "failures": self.failures,
            "unknown": self.unknown,
            "conservative_rate": self.conservative_rate,
            "coverage": self.coverage,
            "evaluable_rate": self.evaluable_rate,
            "missingness_range": list(self.missingness_range)
            if self.missingness_range is not None
            else None,
        }


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else float(Fraction(numerator, denominator))


def success_summary(statuses: Iterable[str | None]) -> SuccessSummary:
    """`statuses`: task_success por celda programada; `None` = celda sin evaluar (unknown).

    `not_applicable` (celda fuera del perfil determinístico) no entra en N; `error`,
    `unknown` y celdas sin evaluar cuentan como U y nunca desaparecen del denominador.
    """
    in_profile = [s for s in statuses if s != "not_applicable"]
    cells = len(in_profile)
    successes = sum(1 for s in in_profile if s == "pass")
    failures = sum(1 for s in in_profile if s == "fail")
    unknown = cells - successes - failures
    low = _rate(successes, cells)
    high = _rate(successes + unknown, cells)
    return SuccessSummary(
        cells=cells,
        successes=successes,
        failures=failures,
        unknown=unknown,
        conservative_rate=low,
        coverage=_rate(cells - unknown, cells),
        evaluable_rate=_rate(successes, cells - unknown),
        missingness_range=None if low is None or high is None else (low, high),
    )
