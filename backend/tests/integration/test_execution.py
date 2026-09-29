import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from evallab.api.app import create_app
from evallab.canonical import canonical_digest
from evallab.db import models as m
from evallab.db.migrate import upgrade_head
from evallab.domain.lifecycle import ExperimentStatus, RunStatus
from evallab.schemas import FixtureCreate
from evallab.services.catalog import publish_fixture
from evallab.services.execution import claim_queued_run, execute_claimed_run, execute_run
from evallab.services.experiments import seal
from tests.integration import factories as f
from tests.test_tools import ADD, CALCULATOR_INPUT, TOTAL_OUTPUT

pytestmark = pytest.mark.integration

ORACLE_SECRET = "SECRET_ORACLE_42"
INITIAL_STATE = {"entries": 0}


@dataclass(frozen=True)
class World:
    experiment_id: uuid.UUID
    run_ids: list[uuid.UUID]
    calculator: str
    ledger: str
    shell: str
    ledger_fixture_hash: str


def _tool(
    db: Session,
    prefix: str,
    *,
    effect_class: str,
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
    fixture: dict[str, Any] | None,
) -> m.ToolDefinition:
    name = f"{prefix}-{uuid.uuid4().hex[:8]}"
    fixture_hash = None
    if fixture is not None:
        fixture_hash = publish_fixture(db, FixtureCreate(name=name, payload=fixture)).content_hash
    tool = m.ToolDefinition(
        id=uuid.uuid4(),
        version="1.0.0",
        name=name,
        input_schema=input_schema,
        output_schema=output_schema,
        effect_class=effect_class,
        timeout_ms=1000,
        fixture_hash=fixture_hash,
        content_hash=f.digest(),
    )
    db.add(tool)
    db.flush()
    return tool


def _world(
    db: Session,
    *,
    pattern: str = "scripted",
    script: list[dict[str, Any]] | None = None,
    runs: int = 1,
) -> World:
    bench = f.benchmark(db)
    calculator = _tool(
        db,
        "calculator",
        effect_class="read_only",
        input_schema=CALCULATOR_INPUT,
        output_schema=TOTAL_OUTPUT,
        fixture={"kind": "lookup", "cases": [{"arguments": ADD, "result": {"total": 42}}]},
    )
    ledger = _tool(
        db,
        "ledger",
        effect_class="side_effect",
        input_schema={
            "type": "object",
            "properties": {"entry": {"type": "string"}},
            "required": ["entry"],
        },
        output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        fixture={
            "kind": "lookup",
            "cases": [
                {
                    "arguments": {"entry": "x"},
                    "result": {"ok": True},
                    "state_patch": {"entries": 1},
                }
            ],
        },
    )
    shell = _tool(
        db,
        "shell",
        effect_class="side_effect",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        fixture=None,
    )
    scenario = m.Scenario(
        id=uuid.uuid4(),
        version="1.0.0",
        primary_category="tool_selection",
        split="dev",
        family_id="family-secret",
        input={"a": 40, "b": 2},
        task={"input": {"a": 40, "b": 2}},
        tools=[
            {"id": str(t.id), "version": t.version, "content_hash": t.content_hash}
            for t in (calculator, ledger)
        ],
        environment={"initial_state": INITIAL_STATE, "fault_schedule": [{"fault": "hidden"}]},
        oracle_ref={"checks": [{"value": ORACLE_SECRET}]},
        limits={"max_steps": 4},
        content_hash=f.digest(),
    )
    db.add(scenario)
    db.flush()
    db.add(
        m.DatasetScenario(
            dataset_id=bench.dataset_id,
            dataset_version=bench.dataset_version,
            scenario_id=scenario.id,
            scenario_version=scenario.version,
            position=0,
        )
    )
    steps = script or [
        {"type": "tool", "tool": "{calculator}", "arguments": ADD},
        {"type": "final", "output": {"total": 42}},
    ]
    names = {"calculator": calculator.name, "ledger": ledger.name, "shell": shell.name}
    resolved = [
        {**s, "tool": s["tool"].format(**names)} if s["type"] == "tool" else s for s in steps
    ]
    params = {"script": resolved} if pattern == "scripted" else {}
    agent = f.agent(db, pattern=pattern, pattern_parameters=params)
    db.add_all(
        m.AgentTool(
            agent_id=agent.id, agent_version=agent.version, tool_id=t.id, tool_version=t.version
        )
        for t in (calculator, ledger, shell)
    )
    seeds = [11 + i for i in range(runs)]
    exp = m.Experiment(
        hypothesis="scripted harness",
        benchmark_id=bench.id,
        benchmark_version=bench.version,
        budgets={"max_steps": 4},
        repetitions=runs,
        seeds=seeds,
    )
    db.add(exp)
    db.flush()
    db.add(m.ExperimentAgent(experiment_id=exp.id, agent_id=agent.id, agent_version=agent.version))
    sealed = seal(db, exp.id)
    run_rows = [
        m.Run(
            experiment_id=sealed.id,
            scenario_id=scenario.id,
            scenario_version=scenario.version,
            agent_id=agent.id,
            agent_version=agent.version,
            repetition=i + 1,
            seed=seed,
            mode="live",
        )
        for i, seed in enumerate(seeds)
    ]
    db.add_all(run_rows)
    db.flush()
    assert ledger.fixture_hash is not None
    return World(
        experiment_id=sealed.id,
        run_ids=[r.id for r in run_rows],
        calculator=calculator.name,
        ledger=ledger.name,
        shell=shell.name,
        ledger_fixture_hash=ledger.fixture_hash,
    )


def _events(client: TestClient, run_id: uuid.UUID) -> list[dict[str, Any]]:
    response = client.get(f"/runs/{run_id}/trace")
    assert response.status_code == 200
    events: list[dict[str, Any]] = response.json()["events"]
    return events


@pytest.fixture
def client(migrated_database: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(engine=migrated_database)) as test_client:
        yield test_client


def test_scripted_run_reaches_terminal_state_with_evidence(
    migrated_database: Engine, client: TestClient
) -> None:
    with Session(migrated_database) as db, db.begin():
        world = _world(db)
        run = execute_run(db, world.run_ids[0])
        assert run.status == RunStatus.COMPLETED
        assert run.result is not None
        assert run.result["label"] == "scripted"
        assert run.result["attribution"] == "harness_baseline"
        assert run.result["usage"]["model_calls"] == 0
        assert run.result["usage"]["tool_calls"] == 1
        assert run.result["policy_violations"] == 0
        assert run.result["output"] == {"total": 42}
        assert run.result["evidence_refs"]

    with Session(migrated_database) as db:
        exp = db.get(m.Experiment, world.experiment_id)
        assert exp is not None
        assert exp.status == ExperimentStatus.RUNNING

    body = client.get(f"/runs/{world.run_ids[0]}").json()
    assert body["status"] == "completed"

    trace = client.get(f"/runs/{world.run_ids[0]}/trace").json()
    assert trace["completeness"] == "complete"
    assert trace["event_count"] == len(trace["events"])
    types = [event["type"] for event in trace["events"]]
    assert types[0] == "run.started"
    assert types[-1] == "run.completed"
    assert "tool.validated" in types and "tool.completed" in types
    assert "model.requested" not in types
    completed = next(e for e in trace["events"] if e["type"] == "tool.completed")
    assert completed["payload"]["result"] == {"total": 42}
    assert body["result"]["evidence_refs"] == [
        {"event_id": completed["event_id"], "pointer": "/result"}
    ]
    blob = json.dumps(trace)
    assert ORACLE_SECRET not in blob
    assert "family-secret" not in blob
    assert "fault_schedule" not in blob


def test_forbidden_tool_and_invalid_arguments_are_traced_not_executed(
    migrated_database: Engine, client: TestClient
) -> None:
    script = [
        {"type": "tool", "tool": "{shell}", "arguments": {"cmd": "id"}},
        {"type": "tool", "tool": "curl", "arguments": {"url": "http://example.invalid"}},
        {"type": "tool", "tool": "{calculator}", "arguments": {**ADD, "a": "40"}},
        {"type": "tool", "tool": "{calculator}", "arguments": ADD},
        {"type": "final", "output": {"total": 42}},
    ]
    with Session(migrated_database) as db, db.begin():
        world = _world(db, script=script)
        run = execute_run(db, world.run_ids[0])
        assert run.status == RunStatus.COMPLETED
        assert run.error_class is None
        assert run.result is not None
        assert run.result["policy_violations"] == 2
        assert run.result["usage"]["tool_calls"] == 1

    events = _events(client, world.run_ids[0])
    denied = [e["payload"] for e in events if e["type"] == "tool.denied"]
    assert [d["reason_codes"] for d in denied] == [
        ["not_in_scenario"],
        ["unknown_tool"],
        ["schema_invalid"],
    ]
    assert denied[2]["schema_result"] == "invalid"
    assert denied[2]["error_class"] == "invalid_arguments"
    violations = [e["payload"] for e in events if e["type"] == "policy.violation"]
    assert [v["attempted"] for v in violations] == [world.shell, "curl"]
    assert all(v["executed"] is False for v in violations)
    requested = [e["payload"]["tool"] for e in events if e["type"] == "tool.requested"]
    assert requested == [world.shell, "curl", world.calculator, world.calculator]
    assert sum(1 for e in events if e["type"] == "tool.completed") == 1


def test_fixture_state_is_not_shared_between_runs(
    migrated_database: Engine, client: TestClient
) -> None:
    script = [
        {"type": "tool", "tool": "{ledger}", "arguments": {"entry": "x"}},
        {"type": "final", "output": {"ok": True}},
    ]
    with Session(migrated_database) as db, db.begin():
        world = _world(db, script=script, runs=2)
        fixture_before = db.get(m.Fixture, world.ledger_fixture_hash)
        assert fixture_before is not None
        payload_before = json.loads(json.dumps(fixture_before.payload))
        for run_id in world.run_ids:
            assert execute_run(db, run_id).status == RunStatus.COMPLETED

    digests = []
    for run_id in world.run_ids:
        started = _events(client, run_id)
        completed = next(e for e in started if e["type"] == "tool.completed")
        digests.append(completed["payload"]["state_digest"])
    after_one_append = canonical_digest({**INITIAL_STATE, "entries": 1})
    assert digests == [after_one_append, after_one_append]

    with Session(migrated_database) as db:
        fixture = db.get(m.Fixture, world.ledger_fixture_hash)
        assert fixture is not None
        assert fixture.payload == payload_before
        scenario_env = db.scalar(
            select(m.Scenario.environment)
            .join(m.Run, m.Run.scenario_id == m.Scenario.id)
            .where(m.Run.id == world.run_ids[0])
        )
        assert scenario_env is not None
        assert scenario_env["initial_state"] == INITIAL_STATE


def test_queued_run_has_no_trace_until_execution(
    migrated_database: Engine, client: TestClient
) -> None:
    with Session(migrated_database) as db, db.begin():
        world = _world(db)
    response = client.get(f"/runs/{world.run_ids[0]}/trace")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_react_is_not_reported_as_implemented(
    migrated_database: Engine, client: TestClient
) -> None:
    with Session(migrated_database) as db, db.begin():
        world = _world(db, pattern="react")
        run = execute_run(db, world.run_ids[0])
        assert run.status == RunStatus.FAILED
        assert run.error_class == "infrastructure_error"
        assert run.result is not None
        assert run.result["label"] == "react"
        assert run.result["attribution"] == "unimplemented"
    body = client.get(f"/runs/{world.run_ids[0]}").json()
    assert body["result"]["usage"]["model_calls"] == 0


def test_worker_claims_queued_run(empty_database: Engine) -> None:
    with empty_database.begin() as conn:
        upgrade_head(conn)
    with Session(empty_database) as db, db.begin():
        world = _world(db)
        claimed = claim_queued_run(db)
        assert claimed is not None
        assert claimed.id == world.run_ids[0]
        assert execute_claimed_run(db, claimed).status == RunStatus.COMPLETED
