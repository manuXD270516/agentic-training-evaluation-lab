"""Planner/Executor (8.1) sin base de datos: plan validado, dependencias y presupuesto global."""

import json
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from evallab.domain.lifecycle import RunStatus
from evallab.runner.agent import execute_agent
from evallab.runner.contracts import AgentSnapshot, RunContext, RunResult
from evallab.runner.models import ModelSnapshot, PriceInfo, ProviderModelGateway
from evallab.runner.planner_executor import (
    PLANNER_EXECUTOR_PROMPT_HASH,
    InvalidPlanError,
    parse_plan,
)
from evallab.runner.providers import FixtureModelProvider
from evallab.runner.sink import MemoryTraceSink
from evallab.runner.tools import FixtureToolGateway
from evallab.schemas import ScenarioPublicOut
from evallab.settings import SandboxPolicy
from tests.test_tools import ADD, binding

PLANNER_HASH = "1" * 64
EXECUTOR_HASH = "2" * 64
PRICE = PriceInfo(
    ref="3" * 64, currency="USD", input_per_mtok=Decimal(1), output_per_mtok=Decimal(4)
)


def _usage(inp: int, out: int) -> dict[str, int]:
    return {"input_tokens": inp, "output_tokens": out}


def _plan(*steps: dict[str, Any]) -> dict[str, Any]:
    return {"content": json.dumps({"steps": list(steps)}), "usage": _usage(100, 50)}


def _model(role: str, script_hash: str) -> ModelSnapshot:
    return ModelSnapshot(
        role=role,
        id=uuid.uuid4(),
        version="1.0.0",
        content_hash=script_hash,
        provider="fixture",
        requested_model=script_hash,
        resolved_revision=None,
        temperature="0",
        max_tokens=128,
        seed_support="unsupported",
        price=PRICE,
    )


def _run(
    planner: list[dict[str, Any]],
    executor: list[dict[str, Any]],
    limits: dict[str, Any] | None = None,
) -> tuple[RunResult, MemoryTraceSink]:
    scripts = {
        PLANNER_HASH: {"kind": "model_script", "default": planner},
        EXECUTOR_HASH: {"kind": "model_script", "default": executor},
    }
    gateway = ProviderModelGateway(
        {"planner": _model("planner", PLANNER_HASH), "executor": _model("executor", EXECUTOR_HASH)},
        {"fixture": FixtureModelProvider(scripts.get)},
    )
    context = RunContext(
        run_id=uuid.uuid4(),
        attempt_id=uuid.uuid4(),
        experiment_id=uuid.uuid4(),
        mode="live",
        seed=11,
        started_at=datetime.now(UTC),
        limits=limits or {"max_steps": 12},
        sandbox=SandboxPolicy(),
        manifest_hash=None,
        experiment_budgets={},
        clock=time.monotonic,
    )
    agent = AgentSnapshot(
        id=uuid.uuid4(),
        version="1.0.0",
        pattern="planner_executor",
        pattern_version="1.0.0",
        content_hash="a" * 64,
        pattern_parameters={},
        prompt_hash=PLANNER_EXECUTOR_PROMPT_HASH,
        roles=("executor", "planner"),
    )
    scenario = ScenarioPublicOut(
        id=uuid.uuid4(),
        version="1.0.0",
        schema_version="1.0",
        slug="plan-test",
        content_hash="f" * 64,
        primary_category="multi_step_execution",
        tags=[],
        difficulty="easy",
        task={"instruction": "suma", "input": {}},
        tools=[],
        environment={},
        limits={},
        created_at=datetime.now(UTC),
    )
    sink = MemoryTraceSink(
        started_at=context.started_at,
        schema_version="1.0",
        run_id=context.run_id,
        attempt_id=context.attempt_id,
    )
    tools = FixtureToolGateway([binding()])
    return execute_agent(context, agent, scenario, gateway, tools, sink), sink


def _calc(turn_id: str) -> dict[str, Any]:
    return {
        "tool_calls": [{"id": turn_id, "name": "calculator", "arguments": ADD}],
        "usage": _usage(200, 20),
    }


FINAL = {"content": json.dumps({"total": 42, "evidence_ids": ["E1"]}), "usage": _usage(300, 30)}


def _steps(sink: MemoryTraceSink) -> list[dict[str, Any]]:
    return [e.payload for e in sink.events if e.type == "step.completed"]


def test_plan_steps_respect_dependencies_and_share_budget() -> None:
    planner = [
        _plan(
            {"id": "p1", "description": "calcular", "tool": "calculator", "depends_on": []},
            {"id": "p2", "description": "verificar", "tool": "calculator", "depends_on": ["p1"]},
        )
    ]
    result, sink = _run(planner, [_calc("c1"), _calc("c2"), FINAL])
    assert result.status == RunStatus.COMPLETED
    completed = next(e for e in sink.events if e.type == "tool.completed")
    assert result.output == {"total": 42, "evidence_ids": [str(completed.event_id)]}
    plan = next(e for e in sink.events if e.type == "plan.created")
    assert plan.actor_role == "planner"
    assert [s["depends_on"] for s in plan.payload["steps"]] == [[], ["p1"]]
    started = [e.payload for e in sink.events if e.type == "step.started"]
    assert [(s["role"], s.get("plan_step_id")) for s in started] == [
        ("planner", None),
        ("executor", "p1"),
        ("executor", "p2"),
        ("executor", "final"),
    ]
    usage = result.usage.as_json()
    by_role: Any = usage["by_role"]
    # Un único presupuesto: el consumo del run es la suma de ambos roles.
    assert (usage["model_calls"], usage["steps"], usage["tool_calls"]) == (4, 4, 2)
    assert by_role["planner"]["model_calls"] == 1 and by_role["executor"]["model_calls"] == 3
    tokens: Any = usage["tokens"]
    role_tokens = sum(r["input_tokens"] + r["output_tokens"] for r in by_role.values())
    assert tokens["total_tokens"] == role_tokens == 150 + 220 + 220 + 330
    cost: Any = usage["cost"]
    assert Decimal(cost["amount"]) == Decimal(
        sum(r["input_tokens"] for r in by_role.values()) * 1
        + sum(r["output_tokens"] for r in by_role.values()) * 4
    ) / Decimal(1_000_000)


def test_failed_dependency_skips_the_dependent_step() -> None:
    planner = [
        _plan(
            {"id": "p1", "description": "x", "tool": "shell", "depends_on": []},
            {"id": "p2", "description": "y", "tool": "calculator", "depends_on": ["p1"]},
        )
    ]
    shell = {"tool_calls": [{"id": "c1", "name": "shell", "arguments": {}}], "usage": _usage(1, 1)}
    final = {"content": json.dumps({"total": None}), "usage": _usage(1, 1)}
    result, sink = _run(planner, [shell, final])
    assert result.status == RunStatus.COMPLETED
    skipped = [s for s in _steps(sink) if s["status"] == "skipped"]
    assert skipped == [
        {
            "step_id": "s3",
            "role": "executor",
            "status": "skipped",
            "plan_step_id": "p2",
            "reason": "dependency_failed",
            "failed_dependencies": ["p1"],
        }
    ]
    requested = [e.payload["tool"] for e in sink.events if e.type == "tool.requested"]
    assert requested == ["shell"]  # p2 no llegó a pedir calculator
    assert result.policy_violations == 1
    # El ejecutor no se consultó para el paso omitido: planner + p1 + final.
    assert result.usage.model_calls == 3


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ('{"steps": [{"id": "p1", "depends_on": ["p2"]}, {"id": "p2"}]}', "no anteriores"),
        ('{"steps": [{"id": "p1"}, {"id": "p1"}]}', "repetido"),
        ("plan en prosa", "no es JSON"),
        ('{"pasos": []}', "steps"),
    ],
)
def test_invalid_plans_fail_typed_without_plan_event(content: str, reason: str) -> None:
    with pytest.raises(InvalidPlanError, match=reason):
        parse_plan(content)
    result, sink = _run([{"content": content, "usage": _usage(1, 1)}], [FINAL])
    assert (result.status, result.error_class) == (RunStatus.FAILED, "model_error")
    assert "plan.created" not in [e.type for e in sink.events]


def test_global_model_call_budget_spans_both_roles() -> None:
    planner = [
        _plan(
            {"id": "p1", "description": "a", "tool": "calculator", "depends_on": []},
            {"id": "p2", "description": "b", "tool": "calculator", "depends_on": ["p1"]},
        )
    ]
    result, _ = _run(
        planner, [_calc("c1"), _calc("c2"), FINAL], limits={"max_steps": 12, "max_model_calls": 2}
    )
    assert result.status == RunStatus.BUDGET_EXCEEDED
    assert result.termination == {"limit": "max_model_calls", "limit_value": 2, "used": 2}
    by_role: Any = result.usage.as_json()["by_role"]
    assert (by_role["planner"]["model_calls"], by_role["executor"]["model_calls"]) == (1, 1)
