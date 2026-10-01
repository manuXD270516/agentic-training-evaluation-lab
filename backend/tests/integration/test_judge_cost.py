"""Coste, tokens y latencia del judge por separado (9.3): ni en el agente ni fuera del total."""

import json
import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from evallab.api.app import create_app
from evallab.benchmarks import runner
from evallab.benchmarks import tools as t
from evallab.benchmarks.fixture_models import SYNTHETIC_PRICE
from evallab.benchmarks.report_md import render
from evallab.benchmarks.suite import AgentDef, ModelDef, Suite, publish_model_def, publish_suite
from evallab.services.reports import combine_consumption, experiment_report

pytestmark = pytest.mark.integration

VOTE = {
    "content": json.dumps(
        {"rating": 3, "abstain": False, "evidence": ["/stock"], "rationale": "clara"}
    ),
    "usage": {"input_tokens": 250, "output_tokens": 30},
}
SCENARIO = {
    "slug": "judge-cost-stock",
    "primary_category": "tool_selection",
    "difficulty": "easy",
    "split": "dev",
    "family_id": "family-judge-cost",
    "task": {"instruction": "Indica el stock del SKU y explícalo con claridad.", "input": {}},
    "tools": ["inventory-lookup"],
    "limits": {
        "max_steps": 4,
        "max_model_calls": 4,
        "max_tool_calls": 2,
        "max_tokens": 4000,
        "deadline_ms": 30000,
        "max_retries": 0,
    },
    "expected": {
        "output_schema": {"type": "object", "properties": {"stock": {"type": "integer"}}},
        "checks": [{"operator": "json_value_equals", "path": "/stock", "value": 37}],
    },
    "evaluation": {
        "required_checks": ["outcome", "output_structure"],
        "applicable_metrics": ["task_success"],
        "judge": {
            "dimension": "clarity",
            "rubric_ref": "answer-clarity@1.0.0",
            "deterministic_unavailable_reason": "la claridad de la explicación no tiene oráculo",
        },
    },
}
SUITE = Suite(
    dataset_name="judge-cost-test",
    dataset_version="0.0.1",
    license="CC-BY-4.0",
    generator_version="1.0.0",
    tools=(t.inventory_lookup(),),
    scenarios=(SCENARIO,),
    agents=(
        AgentDef(
            name="judge-cost-scripted",
            pattern="scripted",
            pattern_version="1.0.0",
            pattern_parameters={
                "script": [
                    {"type": "tool", "tool": "inventory-lookup", "arguments": {"sku": "A-100"}},
                    {"type": "final", "output": {"stock": 37}},
                ]
            },
            tools=("inventory-lookup",),
        ),
    ),
    benchmark={
        "evaluator_suite": {"id": "deterministic-core"},
        "metric_profile": {"id": "core-metrics"},
        "comparison_rules": {"mode": "descriptive_only"},
        "seed_schedule": [11],
    },
)


def test_combine_keeps_parts_separate_and_unknown_visible() -> None:
    agent = {
        "tokens": {"status": "observed", "known_subtotal": 1000, "total": 1000},
        "estimated_cost": {"status": "estimated", "known_subtotal": "0.002"},
    }
    judge = {
        "tokens": {"status": "observed", "known_subtotal": 560, "total": 560},
        "estimated_cost": {"status": "estimated", "known_subtotal": "0.0006"},
    }
    total = combine_consumption(agent, judge)
    assert total["tokens"]["total"] == 1560
    assert Decimal(total["estimated_cost"]["amount"]) == Decimal("0.0026")
    unknown_agent = {**agent, "tokens": {"status": "unknown", "known_subtotal": 1000}}
    partial = combine_consumption(unknown_agent, judge)
    assert partial["tokens"]["status"] == "unknown" and partial["tokens"]["total"] is None
    assert partial["tokens"]["known_subtotal"] == 1560
    none = {"tokens": {"status": "not_applicable"}, "estimated_cost": {"status": "not_applicable"}}
    assert combine_consumption(none, none)["tokens"]["status"] == "not_applicable"
    assert combine_consumption(none, judge)["tokens"]["total"] == 560


def test_judge_consumption_is_reported_apart_and_in_total(fresh_database: Engine) -> None:
    with Session(fresh_database, expire_on_commit=False) as db, db.begin():
        published = publish_suite(db, SUITE)
        judge = publish_model_def(
            db,
            ModelDef(
                name="judge-cost-model",
                script={"kind": "model_script", "default": [VOTE]},
                price=SYNTHETIC_PRICE,
            ),
            published,
        )
        experiment = runner.create_experiment(
            db, published, ["judge-cost-scripted"], hypothesis="coste del judge", seeds=[11]
        )
        run_ids = runner.enqueue(
            db,
            experiment,
            runner.plan_cells(published.scenarios, [published.agents["judge-cost-scripted"]], 1),
        )
    runner.execute_in_order(fresh_database, run_ids)
    judge_model = {"judge_model": {"id": str(judge.id), "version": judge.version}}
    with TestClient(create_app(engine=fresh_database)) as client:
        for body in (judge_model, None, judge_model):
            response = client.post(
                f"/runs/{run_ids[0]}/evaluations",
                json=body,
                headers={"Idempotency-Key": uuid.uuid4().hex},
            )
            assert response.status_code == 201, response.text
        latest: dict[str, Any] = response.json()
    scopes = {(s["metric_id"], s["scope"]) for s in latest["scores"]}
    assert ("judge_task_rating", "judge") in scopes
    assert all(scope == "agent" for metric, scope in scopes if metric != "judge_task_rating")

    with Session(fresh_database) as db:
        report = experiment_report(db, experiment.id)
    (agent,) = report["agents"]
    # El agente scripted no llamó a ningún modelo: su consumo de tokens sigue N/A.
    assert agent["usage"]["totals"]["model_calls"] == 0
    assert agent["usage"]["tokens"]["status"] == "not_applicable"
    judge_usage = agent["judge_usage"]
    # Dos evaluaciones con judge (la intermedia es sólo determinística): ambas cuentan.
    assert judge_usage["calls"] == 2
    assert judge_usage["tokens"]["total"] == 2 * 280
    expected_cost = 2 * (Decimal(250) * 1 + Decimal(30) * 4) / Decimal(1_000_000)
    assert Decimal(judge_usage["estimated_cost"]["amount"]) == expected_cost
    assert judge_usage["estimated_cost"]["synthetic_price"] is True
    assert judge_usage["latency_ms"]["n"] == 2
    total = agent["total_consumption"]
    assert total["tokens"]["total"] == 560
    assert total["tokens"]["parts"] == {"agent": "not_applicable", "judge": "observed"}
    assert Decimal(total["estimated_cost"]["amount"]) == expected_cost
    markdown = render(report, title="judge")
    assert "Judge (scope judge, no se suma al agente): 2 llamadas" in markdown
