"""Planner/Executor (8.1) sobre el piloto con modelos de fixture: roles, plan, consumo y replay."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from evallab.api.app import create_app
from evallab.benchmarks import runner
from evallab.benchmarks.pilot_models import PILOT_MODELS
from evallab.benchmarks.registry import committed_lock
from evallab.benchmarks.suite import Published, publish_suite
from evallab.db import models as m
from evallab.runner.providers import FixtureModelProvider

pytestmark = pytest.mark.integration

AGENT = "pilot-planner-executor-fixture"
PARTIAL_ORDER = "pilot-ms-convert-then-sum"


def _publish(engine: Engine) -> Published:
    with Session(engine, expire_on_commit=False) as db, db.begin():
        return publish_suite(db, PILOT_MODELS)


def _runs(engine: Engine, published: Published, slugs: set[str] | None = None) -> list[m.Run]:
    scenarios = [s for s in published.scenarios if slugs is None or s.slug in slugs]
    with Session(engine, expire_on_commit=False) as db, db.begin():
        experiment = runner.create_experiment(
            db, published, [AGENT], hypothesis="planner/executor de fixture", seeds=[11]
        )
        run_ids = runner.enqueue(
            db, experiment, runner.plan_cells(scenarios, [published.agents[AGENT]], 1)
        )
    runner.execute_in_order(engine, run_ids)
    runner.evaluate_all(engine, run_ids)
    with Session(engine) as db:
        return [r for r in (db.get(m.Run, i) for i in run_ids) if r is not None]


def _events(engine: Engine, run_id: uuid.UUID) -> list[m.TraceEvent]:
    with Session(engine) as db:
        return list(
            db.scalars(
                select(m.TraceEvent)
                .where(m.TraceEvent.run_id == run_id)
                .order_by(m.TraceEvent.sequence)
            )
        )


def _task_success(engine: Engine, run_id: uuid.UUID) -> str:
    with Session(engine) as db:
        evaluation = db.scalars(select(m.Evaluation).where(m.Evaluation.run_id == run_id)).one()
        report: dict[str, Any] = evaluation.report or {}
        return str(report["task_success"])


@pytest.fixture
def client(fresh_database: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(engine=fresh_database)) as test_client:
        yield test_client


def test_pilot_runs_keep_scripted_failures(fresh_database: Engine) -> None:
    published = _publish(fresh_database)
    assert published.lock() == committed_lock("pilot-models")
    runs = _runs(fresh_database, published)
    slugs = {s.id: s.slug for s in published.scenarios}
    outcomes = {slugs[r.scenario_id]: _task_success(fresh_database, r.id) for r in runs}
    # Los dos fallos los fija el guion de fixture (ver pilot_models): se conservan, no se ocultan.
    assert {s for s, v in outcomes.items() if v != "pass"} == {
        "pilot-pc-injection-exfiltration",
        "pilot-rs-constraints",
    }
    assert all(r.result is not None and r.result["attribution"] == "fixture_model" for r in runs)


def test_plan_partial_order_roles_and_summed_consumption(fresh_database: Engine) -> None:
    published = _publish(fresh_database)
    (run,) = _runs(fresh_database, published, {PARTIAL_ORDER})
    assert run.status == "completed" and run.result is not None
    events = _events(fresh_database, run.id)
    plan = next(e for e in events if e.type == "plan.created")
    assert plan.actor_role == "planner"
    assert [(s["step_id"], s["depends_on"]) for s in plan.payload["steps"]] == [
        ("p1", []),
        ("p2", []),
        ("p3", ["p1", "p2"]),
    ]
    planner_completed = next(e for e in events if e.type == "model.completed")
    assert plan.payload["source_event_id"] == str(planner_completed.event_id)
    started = [e.payload for e in events if e.type == "step.started"]
    assert [(s["role"], s.get("plan_step_id")) for s in started] == [
        ("planner", None),
        ("executor", "p1"),
        ("executor", "p2"),
        ("executor", "p3"),
        ("executor", "final"),
    ]
    usage = run.result["usage"]
    by_role = usage["by_role"]
    assert (by_role["planner"]["model_calls"], by_role["executor"]["model_calls"]) == (1, 4)
    assert usage["model_calls"] == 5 and usage["steps"] == 5 and usage["tool_calls"] == 3
    summed = sum(r["input_tokens"] + r["output_tokens"] for r in by_role.values())
    assert usage["tokens"]["total_tokens"] == summed
    assert _task_success(fresh_database, run.id) == "pass"


def test_recorded_planner_executor_run_replays_both_roles(
    fresh_database: Engine, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    published = _publish(fresh_database)
    (source,) = _runs(fresh_database, published, {PARTIAL_ORDER})

    def forbidden(self: Any, request: Any, model: Any) -> Any:
        raise AssertionError("el replay no puede llamar al proveedor")

    monkeypatch.setattr(FixtureModelProvider, "complete", forbidden)
    created = client.post(f"/runs/{source.id}/replays", headers={"Idempotency-Key": "pe-1"})
    assert created.status_code == 202, created.text
    replay_id = uuid.UUID(created.json()["id"])
    runner.execute_in_order(fresh_database, [replay_id])
    with Session(fresh_database) as db:
        replay = db.get(m.Run, replay_id)
        assert replay is not None and replay.status == "completed", replay
        assert replay.result is not None
        assert replay.result["replay"]["output_matches_source"] is True
    roles = [
        e.actor_role for e in _events(fresh_database, replay_id) if e.type == "model.requested"
    ]
    assert roles == ["planner", "executor", "executor", "executor", "executor"]
