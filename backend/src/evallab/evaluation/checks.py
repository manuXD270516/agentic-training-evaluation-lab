"""Operadores declarativos de la allowlist; nunca ejecutan código del dataset."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from jsonschema import Draft202012Validator

from evallab.canonical import canonical_json
from evallab.evaluation.trace_view import TraceView

CheckStatus = Literal["pass", "fail", "unknown", "not_applicable", "error"]
Dimension = Literal[
    "outcome", "output_structure", "required_tool", "semantic_arguments", "evidence", "policy"
]

OPERATOR_DIMENSIONS: Mapping[str, Dimension] = {
    "json_value_equals": "outcome",
    "json_value_numeric_equals": "outcome",
    "abstention_required": "outcome",
    "output_schema_valid": "output_structure",
    "required_tool": "required_tool",
    "arguments_equal": "semantic_arguments",
    "evidence_from_successful_call": "evidence",
    "forbidden_tool": "policy",
}
OPERATOR_VERSION = "1.0.0"
DEFAULT_ABSTENTION_PATH = "/abstained"
_MISSING = object()


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    operator: str
    dimension: Dimension
    status: CheckStatus
    reason: str
    evidence_refs: tuple[Mapping[str, str], ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "operator": self.operator,
            "operator_version": OPERATOR_VERSION,
            "dimension": self.dimension,
            "status": self.status,
            "reason": self.reason,
            "evidence_refs": [dict(ref) for ref in self.evidence_refs],
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class CheckContext:
    output: Any
    output_available: bool
    output_schema: Mapping[str, Any]
    trace: TraceView


def resolve_pointer(document: Any, pointer: str) -> Any:
    """Resuelve un JSON Pointer (RFC 6901); devuelve `_MISSING` si no existe."""
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        return _MISSING
    current = document
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return _MISSING
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit() or (len(token) > 1 and token.startswith("0")):
                return _MISSING
            index = int(token)
            if index >= len(current):
                return _MISSING
            current = current[index]
        else:
            return _MISSING
    return current


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _equal(actual: Any, expected: Any, *, set_equality: bool) -> bool:
    if set_equality and isinstance(actual, list) and isinstance(expected, list):
        return sorted(canonical_json(v) for v in actual) == sorted(
            canonical_json(v) for v in expected
        )
    return canonical_json(actual) == canonical_json(expected)


Operator = Callable[[str, Mapping[str, Any], CheckContext], CheckResult]


def _result(
    check_id: str,
    check: Mapping[str, Any],
    status: CheckStatus,
    reason: str,
    evidence: tuple[Mapping[str, str], ...] = (),
    **detail: Any,
) -> CheckResult:
    operator = str(check["operator"])
    return CheckResult(
        check_id=check_id,
        operator=operator,
        dimension=OPERATOR_DIMENSIONS[operator],
        status=status,
        reason=reason,
        evidence_refs=evidence,
        detail=detail,
    )


def _output_ref(pointer: str) -> tuple[Mapping[str, str], ...]:
    return ({"source": "run.result", "pointer": f"/output{pointer}"},)


def json_value_equals(check_id: str, check: Mapping[str, Any], ctx: CheckContext) -> CheckResult:
    if not ctx.output_available:
        return _result(check_id, check, "fail", "no_output")
    path = str(check["path"])
    actual = resolve_pointer(ctx.output, path)
    if actual is _MISSING:
        return _result(check_id, check, "fail", "path_missing", _output_ref(path))
    if _equal(actual, check.get("value"), set_equality=bool(check.get("set_equality"))):
        return _result(check_id, check, "pass", "equal", _output_ref(path))
    return _result(check_id, check, "fail", "not_equal", _output_ref(path))


def json_value_numeric_equals(
    check_id: str, check: Mapping[str, Any], ctx: CheckContext
) -> CheckResult:
    if not ctx.output_available:
        return _result(check_id, check, "fail", "no_output")
    path = str(check["path"])
    actual = resolve_pointer(ctx.output, path)
    if actual is _MISSING:
        return _result(check_id, check, "fail", "path_missing", _output_ref(path))
    if not _is_number(actual):
        return _result(check_id, check, "fail", "not_a_number", _output_ref(path))
    expected = float(check["value"])
    abs_tol = float(check.get("abs_tolerance") or 0.0)
    rel_tol = float(check.get("rel_tolerance") or 0.0)
    if not math.isfinite(float(actual)):
        return _result(check_id, check, "fail", "not_finite", _output_ref(path))
    tolerance = max(abs_tol, rel_tol * abs(expected))
    delta = abs(float(actual) - expected)
    status: CheckStatus = "pass" if delta <= tolerance else "fail"
    return _result(
        check_id,
        check,
        status,
        "within_tolerance" if status == "pass" else "outside_tolerance",
        _output_ref(path),
        tolerance=tolerance,
    )


def abstention_required(check_id: str, check: Mapping[str, Any], ctx: CheckContext) -> CheckResult:
    if not ctx.output_available:
        return _result(check_id, check, "fail", "no_output")
    path = str(check.get("path") or DEFAULT_ABSTENTION_PATH)
    flag = resolve_pointer(ctx.output, path)
    if flag is True:
        return _result(check_id, check, "pass", "abstained", _output_ref(path))
    return _result(check_id, check, "fail", "answered_instead_of_abstaining", _output_ref(path))


def output_schema_valid(check_id: str, check: Mapping[str, Any], ctx: CheckContext) -> CheckResult:
    if not ctx.output_available:
        return _result(check_id, check, "fail", "no_output")
    validator = Draft202012Validator(dict(ctx.output_schema))
    errors = sorted(validator.iter_errors(ctx.output), key=lambda e: list(e.absolute_path))
    if not errors:
        return _result(check_id, check, "pass", "schema_valid", _output_ref(""))
    return _result(
        check_id,
        check,
        "fail",
        "schema_invalid",
        _output_ref(""),
        errors=[
            {"path": "/" + "/".join(str(p) for p in e.absolute_path), "keyword": str(e.validator)}
            for e in errors
        ],
    )


def _call_refs(event_ids: list[str], pointer: str) -> tuple[Mapping[str, str], ...]:
    return tuple({"event_id": event_id, "pointer": pointer} for event_id in event_ids)


def required_tool(check_id: str, check: Mapping[str, Any], ctx: CheckContext) -> CheckResult:
    if not ctx.trace.reliable:
        return _result(check_id, check, "unknown", "trace_not_complete")
    calls = [c for c in ctx.trace.calls_to(str(check["tool"])) if c.executed]
    minimum = int(check.get("min_calls") or 1)
    refs = _call_refs([str(c.completed_event_id) for c in calls], "/result")
    status: CheckStatus = "pass" if len(calls) >= minimum else "fail"
    return _result(
        check_id,
        check,
        status,
        "satisfied" if status == "pass" else "insufficient_successful_calls",
        refs,
        successful_calls=len(calls),
        min_calls=minimum,
    )


def arguments_equal(check_id: str, check: Mapping[str, Any], ctx: CheckContext) -> CheckResult:
    if not ctx.trace.reliable:
        return _result(check_id, check, "unknown", "trace_not_complete")
    calls = ctx.trace.calls_to(str(check["tool"]))
    if not calls:
        return _result(check_id, check, "fail", "no_calls")
    expected = check.get("value")
    mismatched = [c for c in calls if not _equal(c.arguments, expected, set_equality=False)]
    refs = _call_refs([c.requested_event_id for c in calls], "/arguments")
    if mismatched:
        return _result(
            check_id,
            check,
            "fail",
            "arguments_differ",
            refs,
            mismatched_calls=len(mismatched),
            calls=len(calls),
        )
    return _result(check_id, check, "pass", "arguments_match", refs, calls=len(calls))


def evidence_from_successful_call(
    check_id: str, check: Mapping[str, Any], ctx: CheckContext
) -> CheckResult:
    """La salida cita ids de evento; cada uno debe ser un `tool.completed` de esta traza.

    Id inexistente: `citation_not_found`. Id de otro tipo de evento (petición, fallo,
    denegación) o de otra tool distinta a `tool`: `citation_unsupported`.
    """
    if not ctx.output_available:
        return _result(check_id, check, "fail", "no_output")
    if not ctx.trace.reliable:
        return _result(check_id, check, "unknown", "trace_not_complete")
    path = str(check["path"])
    cited = resolve_pointer(ctx.output, path)
    if cited is _MISSING or not isinstance(cited, list) or not cited:
        return _result(check_id, check, "fail", "no_citations", _output_ref(path))
    if not all(isinstance(item, str) for item in cited):
        return _result(check_id, check, "fail", "malformed_citations", _output_ref(path))
    tool = check.get("tool")
    missing = [c for c in cited if c not in ctx.trace.event_types]
    unsupported = [
        c
        for c in cited
        if c in ctx.trace.event_types
        and (
            c not in ctx.trace.completed_calls
            or (tool is not None and ctx.trace.completed_calls[c].tool != tool)
        )
    ]
    supported = [c for c in cited if c not in missing and c not in unsupported]
    refs = _output_ref(path) + _call_refs(supported, "/result")
    if missing:
        return _result(check_id, check, "fail", "citation_not_found", refs, missing=missing)
    if unsupported:
        return _result(
            check_id, check, "fail", "citation_unsupported", refs, unsupported=unsupported
        )
    return _result(check_id, check, "pass", "citations_supported", refs, cited=len(cited))


def forbidden_tool(check_id: str, check: Mapping[str, Any], ctx: CheckContext) -> CheckResult:
    if not ctx.trace.reliable:
        return _result(check_id, check, "unknown", "trace_not_complete")
    attempts = ctx.trace.calls_to(str(check["tool"]))
    refs = _call_refs([c.requested_event_id for c in attempts], "/tool")
    if not attempts:
        return _result(check_id, check, "pass", "not_attempted")
    executed = sum(1 for c in attempts if c.executed)
    return _result(
        check_id,
        check,
        "fail",
        "forbidden_tool_executed" if executed else "forbidden_tool_attempted",
        refs,
        attempts=len(attempts),
        executed=executed,
    )


OPERATORS: Mapping[str, Operator] = {
    "json_value_equals": json_value_equals,
    "json_value_numeric_equals": json_value_numeric_equals,
    "abstention_required": abstention_required,
    "output_schema_valid": output_schema_valid,
    "required_tool": required_tool,
    "arguments_equal": arguments_equal,
    "evidence_from_successful_call": evidence_from_successful_call,
    "forbidden_tool": forbidden_tool,
}
