"""FixtureToolGateway: identidad, permiso, schema, fixture y estado aislado por run."""

import copy
import uuid
from typing import Any

import pytest

from evallab.canonical import canonical_digest
from evallab.runner.contracts import AllowedTool
from evallab.runner.tools import FixtureToolGateway, ToolBinding

DIGEST = "b" * 64

CALCULATOR_INPUT = {
    "type": "object",
    "properties": {
        "op": {"enum": ["add"]},
        "a": {"type": "integer"},
        "b": {"type": "integer"},
    },
    "required": ["op", "a", "b"],
    "additionalProperties": False,
}
TOTAL_OUTPUT = {
    "type": "object",
    "properties": {"total": {"type": "integer"}},
    "required": ["total"],
}
ADD = {"op": "add", "a": 40, "b": 2}


def binding(
    name: str = "calculator",
    *,
    effect_class: str = "read_only",
    fixture: Any = None,
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
) -> ToolBinding:
    return ToolBinding(
        tool=AllowedTool(id=uuid.uuid4(), version="1.0.0", name=name, content_hash=DIGEST),
        input_schema=CALCULATOR_INPUT if input_schema is None else input_schema,
        output_schema=TOTAL_OUTPUT if output_schema is None else output_schema,
        effect_class=effect_class,
        fixture=(
            {"kind": "lookup", "cases": [{"arguments": ADD, "result": {"total": 42}}]}
            if fixture is None
            else fixture
        ),
    )


def ledger(fixture: Any | None = None) -> ToolBinding:
    return binding(
        "ledger-append",
        effect_class="side_effect",
        input_schema={
            "type": "object",
            "properties": {"entry": {"type": "string"}},
            "required": ["entry"],
        },
        output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        fixture=fixture
        or {
            "kind": "lookup",
            "cases": [
                {
                    "arguments": {"entry": "x"},
                    "result": {"ok": True},
                    "state_patch": {"last_entry": "x"},
                }
            ],
        },
    )


def invoke(gateway: FixtureToolGateway, name: str, arguments: dict[str, Any]) -> Any:
    return gateway.invoke(name, uuid.uuid4(), arguments)


def test_allowed_tool_executes_fixture_case() -> None:
    gateway = FixtureToolGateway([binding()])
    outcome = invoke(gateway, "calculator", ADD)
    assert (outcome.kind, outcome.validated, outcome.result) == ("completed", True, {"total": 42})
    assert outcome.state_digest == canonical_digest({})


@pytest.mark.parametrize(
    ("scenario_only", "agent_only", "reason"),
    [
        (set(), set(), "unknown_tool"),
        ({"shell"}, set(), "not_in_agent"),
        (set(), {"shell"}, "not_in_scenario"),
    ],
)
def test_forbidden_tool_is_denied_without_execution(
    scenario_only: set[str], agent_only: set[str], reason: str
) -> None:
    gateway = FixtureToolGateway([binding()], scenario_only=scenario_only, agent_only=agent_only)
    outcome = invoke(gateway, "shell", {"cmd": "id"})
    assert (outcome.kind, outcome.validated, outcome.reason_codes) == ("denied", False, (reason,))
    assert outcome.result is None
    assert gateway.resolve("shell") is None


def test_duplicate_tool_names_are_denied_as_ambiguous() -> None:
    gateway = FixtureToolGateway([binding(), binding()])
    outcome = invoke(gateway, "calculator", ADD)
    assert (outcome.kind, outcome.reason_codes) == ("denied", ("ambiguous_tool",))


@pytest.mark.parametrize(
    ("arguments", "path", "keyword"),
    [
        ({"op": "add", "a": "40", "b": 2}, "/a", "type"),
        ({"op": "add", "a": 40}, "", "required"),
        ({"op": "mul", "a": 40, "b": 2}, "/op", "enum"),
        ({**ADD, "extra": 1}, "", "additionalProperties"),
    ],
)
def test_invalid_arguments_are_not_executed(
    arguments: dict[str, Any], path: str, keyword: str
) -> None:
    gateway = FixtureToolGateway([ledger(), binding()])
    before = gateway.state_digest()
    outcome = invoke(gateway, "calculator", arguments)
    assert (outcome.kind, outcome.validated, outcome.error_class) == (
        "invalid",
        False,
        "invalid_arguments",
    )
    assert {"path": path, "keyword": keyword} in outcome.schema_errors
    assert outcome.result is None
    assert gateway.state_digest() == before


def test_invalid_arguments_to_side_effect_tool_do_not_change_state() -> None:
    gateway = FixtureToolGateway([ledger()])
    before = gateway.state_digest()
    assert invoke(gateway, "ledger-append", {"entry": 1}).kind == "invalid"
    assert gateway.state_digest() == before


def test_side_effect_state_is_isolated_per_gateway() -> None:
    shared = ledger()
    fixture_before = copy.deepcopy(shared.fixture)
    initial = {"seeded": True}
    first = FixtureToolGateway([shared], initial_state=initial)
    second = FixtureToolGateway([shared], initial_state=initial)

    outcome = invoke(first, "ledger-append", {"entry": "x"})
    assert outcome.kind == "completed"
    assert first.state == {"seeded": True, "last_entry": "x"}
    assert outcome.state_digest == canonical_digest(first.state)

    assert second.state == {"seeded": True}
    assert second.state_digest() == canonical_digest(initial)
    assert initial == {"seeded": True}
    assert shared.fixture == fixture_before


def test_returned_result_is_a_copy_of_the_fixture() -> None:
    shared = binding()
    gateway = FixtureToolGateway([shared])
    outcome = invoke(gateway, "calculator", ADD)
    outcome.result["total"] = 0
    assert invoke(gateway, "calculator", ADD).result == {"total": 42}


def test_default_case_is_used_when_no_case_matches() -> None:
    fixture = {"kind": "lookup", "cases": [], "default": {"result": {"total": 0}}}
    outcome = invoke(FixtureToolGateway([binding(fixture=fixture)]), "calculator", ADD)
    assert (outcome.kind, outcome.result) == ("completed", {"total": 0})


@pytest.mark.parametrize(
    ("tool", "reason"),
    [
        (binding(fixture={"kind": "lookup", "cases": []}), "no_case"),
        (binding(fixture={"kind": "script", "code": "rm -rf /"}), "fixture_invalid"),
        (
            binding(
                fixture={
                    "kind": "lookup",
                    "cases": [
                        {"arguments": ADD, "result": {"total": 42}},
                        {"arguments": ADD, "result": {"total": 43}},
                    ],
                }
            ),
            "fixture_invalid",
        ),
        (
            binding(
                fixture={
                    "kind": "lookup",
                    "cases": [{"arguments": ADD, "result": {"total": "42"}}],
                }
            ),
            "output_schema_invalid",
        ),
        (
            binding(
                fixture={
                    "kind": "lookup",
                    "cases": [{"arguments": ADD, "result": {"total": 42}, "state_patch": {"x": 1}}],
                }
            ),
            "effect_class_violation",
        ),
    ],
)
def test_fixture_problems_fail_as_infrastructure_errors(tool: ToolBinding, reason: str) -> None:
    gateway = FixtureToolGateway([tool])
    outcome = invoke(gateway, "calculator", ADD)
    assert (outcome.kind, outcome.validated, outcome.error_class, outcome.reason_codes) == (
        "failed",
        True,
        "infrastructure_error",
        (reason,),
    )
    assert gateway.state_digest() == canonical_digest({})


def test_tool_without_fixture_fails() -> None:
    tool = ToolBinding(
        tool=AllowedTool(id=uuid.uuid4(), version="1.0.0", name="calculator", content_hash=DIGEST),
        input_schema=CALCULATOR_INPUT,
        output_schema=TOTAL_OUTPUT,
        effect_class="read_only",
        fixture=None,
    )
    outcome = invoke(FixtureToolGateway([tool]), "calculator", ADD)
    assert (outcome.kind, outcome.reason_codes) == ("failed", ("fixture_missing",))


def test_invalid_input_schema_fails_before_validation() -> None:
    tool = binding(input_schema={"type": "no-such-type"})
    outcome = invoke(FixtureToolGateway([tool]), "calculator", ADD)
    assert (outcome.kind, outcome.validated, outcome.reason_codes) == (
        "failed",
        False,
        ("invalid_input_schema",),
    )


def test_remote_refs_are_not_fetched() -> None:
    tool = binding(input_schema={"$ref": "https://example.invalid/schema.json"})
    outcome = invoke(FixtureToolGateway([tool]), "calculator", ADD)
    assert (outcome.kind, outcome.reason_codes) == ("failed", ("input_schema_unresolvable",))
