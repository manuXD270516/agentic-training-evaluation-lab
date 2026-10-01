"""ReAct `react@1.0.0` (7.2) con modelo de fixture: alternancia, terminación y replay grabado."""

import uuid
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from evallab.api.app import create_app
from evallab.benchmarks import runner
from evallab.benchmarks.pilot_models import PILOT_MODELS, REACT_AGENT
from evallab.benchmarks.registry import committed_lock
from evallab.benchmarks.suite import Published, publish_agent_def, publish_suite
from evallab.db import models as m
from evallab.runner.providers import FixtureModelProvider
from evallab.services.reports import experiment_report

pytestmark = pytest.mark.integration

MULTI_STEP = "pilot-ms-stock-then-order"


def _publish(engine: Engine) -> Published:
    with Session(engine, expire_on_commit=False) as db, db.begin():
        return publish_suite(db, PILOT_MODELS)


def _experiment(
    engine: Engine,
    published: Published,
    agents: list[str],
    *,
    budgets: dict[str, int] | None = None,
    slugs: set[str] | None = None,
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    scenarios = [s for s in published.scenarios if slugs is None or s.slug in slugs]
    with Session(engine, expire_on_commit=False) as db, db.begin():
        experiment = runner.create_experiment(
            db,
            published,
            agents,
            hypothesis="react con modelo de fixture",
            seeds=[11],
            budgets=budgets,
        )
        configs = [published.agents[n] for n in agents]
        run_ids = runner.enqueue(db, experiment, runner.plan_cells(scenarios, configs, 1))
    runner.execute_in_order(engine, run_ids)
    runner.evaluate_all(engine, run_ids)
    return experiment.id, run_ids


def _events(engine: Engine, run_id: uuid.UUID) -> list[m.TraceEvent]:
    with Session(engine) as db:
        return list(
            db.scalars(
                select(m.TraceEvent)
                .where(m.TraceEvent.run_id == run_id)
                .order_by(m.TraceEvent.sequence)
            )
        )


def _run(engine: Engine, run_id: uuid.UUID) -> m.Run:
    with Session(engine) as db:
        run = db.get(m.Run, run_id)
        assert run is not None
        return run


def _slug_run(engine: Engine, run_ids: list[uuid.UUID], slug: str) -> m.Run:
    with Session(engine) as db:
        for run_id in run_ids:
            run = db.get(m.Run, run_id)
            assert run is not None
            scenario = db.get(m.Scenario, (run.scenario_id, run.scenario_version))
            if scenario is not None and scenario.slug == slug:
                return run
    raise AssertionError(slug)


@pytest.fixture
def client(fresh_database: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(engine=fresh_database)) as test_client:
        yield test_client


def test_react_alternates_decisions_actions_and_observations(fresh_database: Engine) -> None:
    published = _publish(fresh_database)
    assert published.lock() == committed_lock("pilot-models")
    experiment_id, run_ids = _experiment(fresh_database, published, ["pilot-react-fixture"])
    with Session(fresh_database) as db:
        report = experiment_report(db, experiment_id)
    agent = report["agents"][0]
    assert agent["agent"]["pattern"] == "react"
    assert agent["summary"]["task_success"]["successes"] == 14

    run = _slug_run(fresh_database, run_ids, MULTI_STEP)
    assert run.status == "completed" and run.result is not None
    assert (run.result["label"], run.result["attribution"]) == ("react", "fixture_model")
    usage = run.result["usage"]
    assert (usage["model_calls"], usage["tool_calls"], usage["steps"]) == (3, 2, 3)
    assert usage["tokens"]["status"] == "observed"
    # Tokens sintéticos del guion: (400+40) + (550+40) + (700+60).
    assert usage["tokens"]["total_tokens"] == 1790
    assert usage["cost"]["status"] == "estimated" and usage["cost"]["synthetic_price"] is True

    flow = [
        e.type
        for e in _events(fresh_database, run.id)
        if e.type.startswith(("model.", "tool.requested", "tool.completed", "run.", "step."))
    ]
    assert flow == [
        "run.started",
        "step.started",
        "model.requested",
        "model.completed",
        "tool.requested",
        "tool.completed",
        "step.completed",
        "step.started",
        "model.requested",
        "model.completed",
        "tool.requested",
        "tool.completed",
        "step.completed",
        "step.started",
        "model.requested",
        "model.completed",
        "step.completed",
        "run.completed",
    ]
    events = _events(fresh_database, run.id)
    second_request = [e for e in events if e.type == "model.requested"][1]
    messages = second_request.payload["input"]["messages"]
    # La observación vuelve al modelo con alias estable, nunca con el event_id real.
    assert messages[-1]["role"] == "tool" and messages[-1]["evidence"] == "E1"
    completed_tools = [str(e.event_id) for e in events if e.type == "tool.completed"]
    assert run.result["output"]["evidence_ids"] == completed_tools


def test_react_terminates_on_budget_and_on_exhausted_model(fresh_database: Engine) -> None:
    published = _publish(fresh_database)
    _, run_ids = _experiment(
        fresh_database,
        published,
        ["pilot-react-fixture"],
        budgets={"max_steps": 1},
        slugs={MULTI_STEP},
    )
    run = _run(fresh_database, run_ids[0])
    assert run.status == "budget_exceeded"
    assert run.result is not None and run.result["termination"]["limit"] == "max_steps"
    types = [e.type for e in _events(fresh_database, run.id)]
    assert types.count("model.requested") == 1 and types[-1] == "run.budget_exceeded"


def test_wrong_prompt_hash_fails_typed(fresh_database: Engine) -> None:
    published = _publish(fresh_database)
    altered = replace(REACT_AGENT, name="pilot-react-other-prompt", prompt_hash="0" * 64)
    with Session(fresh_database, expire_on_commit=False) as db, db.begin():
        published.agents[altered.name] = publish_agent_def(
            db, altered, published.tools, published.models
        )
    _, run_ids = _experiment(
        fresh_database, published, [altered.name], slugs={"pilot-rs-constraints"}
    )
    run = _run(fresh_database, run_ids[0])
    assert (run.status, run.error_class) == ("failed", "invalid_arguments")
    assert run.result is not None and "prompt_hash" in run.result["error"]


def test_recorded_react_run_replays_without_calling_the_provider(
    fresh_database: Engine, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    published = _publish(fresh_database)
    _, run_ids = _experiment(fresh_database, published, ["pilot-react-fixture"], slugs={MULTI_STEP})
    source = run_ids[0]
    calls: list[Any] = []

    def forbidden(self: Any, request: Any, model: Any) -> Any:
        calls.append(request)
        raise AssertionError("el replay no puede llamar al proveedor")

    monkeypatch.setattr(FixtureModelProvider, "complete", forbidden)
    created = client.post(f"/runs/{source}/replays", headers={"Idempotency-Key": "r1"})
    assert created.status_code == 202, created.text
    replay_id = uuid.UUID(created.json()["id"])
    runner.execute_in_order(fresh_database, [replay_id])
    replay = _run(fresh_database, replay_id)
    assert calls == []
    assert replay.status == "completed", replay.result
    assert replay.result is not None
    assert replay.result["replay"]["output_matches_source"] is True
    original = [e for e in _events(fresh_database, source) if e.type == "model.requested"]
    replayed = [e for e in _events(fresh_database, replay_id) if e.type == "model.requested"]
    assert [e.payload["request_digest"] for e in replayed] == [
        e.payload["request_digest"] for e in original
    ]
    original_run = _run(fresh_database, source)
    assert original_run.result is not None
    assert replay.result["usage"]["tokens"] == original_run.result["usage"]["tokens"]
