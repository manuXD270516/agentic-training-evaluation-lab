import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from evallab.api.app import create_app
from evallab.canonical import canonical_digest
from evallab.db import models as m
from evallab.domain.lifecycle import ExperimentStatus, RunStatus
from evallab.schemas import FixtureCreate
from evallab.services.catalog import publish_fixture
from evallab.services.execution import (
    AttemptOutcome,
    Claim,
    claim_next_run,
    execute_run,
    finish_attempt,
    run_attempt,
)
from evallab.services.experiments import seal
from evallab.worker.app import poll_once
from tests.integration import factories as f
from tests.test_tools import ADD, CALCULATOR_INPUT, TOTAL_OUTPUT

pytestmark = pytest.mark.integration

ORACLE_SECRET = "SECRET_ORACLE_42"
HIDDEN_FAULT = "fault-secret-99"
INITIAL_STATE = {"entries": 0}
LEDGER_SCRIPT = [
    {"type": "tool", "tool": "{ledger}", "arguments": {"entry": "x"}},
    {"type": "final", "output": {"ok": True}},
]


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
    limits: dict[str, Any] | None = None,
    faults: list[dict[str, Any]] | None = None,
    oracle: dict[str, Any] | None = None,
    evaluation: dict[str, Any] | None = None,
    calculator_result: dict[str, Any] | None = None,
) -> World:
    bench = f.benchmark(db)
    result = calculator_result or {"total": 42}
    calculator = _tool(
        db,
        "calculator",
        effect_class="read_only",
        input_schema=CALCULATOR_INPUT,
        output_schema=TOTAL_OUTPUT,
        fixture={"kind": "lookup", "cases": [{"arguments": ADD, "result": result}]},
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
    names = {"calculator": calculator.name, "ledger": ledger.name, "shell": shell.name}
    hidden_fault = {
        "fault_id": HIDDEN_FAULT,
        "tool": calculator.name,
        "call_index": 99,
        "kind": "transient",
    }
    schedule = [{**fault, "tool": fault["tool"].format(**names)} for fault in faults or []]
    if oracle is not None:
        oracle = {
            **oracle,
            "checks": [
                {**c, "tool": c["tool"].format(**names)} if "tool" in c else c
                for c in oracle.get("checks", [])
            ],
        }
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
        environment={"initial_state": INITIAL_STATE, "fault_schedule": [*schedule, hidden_fault]},
        oracle_ref=oracle or {"checks": [{"value": ORACLE_SECRET}]},
        evaluation=evaluation or {},
        limits=limits or {"max_steps": 6},
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
        budgets={"max_steps": 8},
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
    assert HIDDEN_FAULT not in blob


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


def test_model_pattern_without_models_is_not_reported_as_executed(
    migrated_database: Engine, client: TestClient
) -> None:
    with Session(migrated_database) as db, db.begin():
        # Sin roles→modelo ni prompt_hash del patrón: fallo tipado antes de cualquier llamada.
        world = _world(db, pattern="planner_executor")
        run = execute_run(db, world.run_ids[0])
        assert run.status == RunStatus.FAILED
        assert run.error_class == "invalid_arguments"
        assert run.result is not None
        assert run.result["label"] == "planner_executor"
        assert run.result["attribution"] == "no_model_configured"
    body = client.get(f"/runs/{world.run_ids[0]}").json()
    assert body["result"]["usage"]["model_calls"] == 0


def test_failed_run_trace_is_exposed_as_incomplete(
    migrated_database: Engine, client: TestClient
) -> None:
    """El dashboard (11.2) muestra la traza incompleta tal cual: completeness y run.failed."""
    with Session(migrated_database) as db, db.begin():
        faults = [{"fault_id": "x", "tool": "ghost", "call_index": 1, "kind": "transient"}]
        world = _world(db, faults=faults)
        execute_run(db, world.run_ids[0])
    trace = client.get(f"/runs/{world.run_ids[0]}/trace", params={"limit": 1}).json()
    assert trace["completeness"] == "incomplete"
    assert trace["page"]["returned"] == 1
    full = client.get(f"/runs/{world.run_ids[0]}/trace").json()
    assert full["events"][-1]["type"] == "run.failed"


def _run_and_events(
    engine: Engine, client: TestClient, **world: Any
) -> tuple[m.Run, list[dict[str, Any]]]:
    with Session(engine, expire_on_commit=False) as db, db.begin():
        built = _world(db, **world)
        run = execute_run(db, built.run_ids[0])
    return run, _events(client, built.run_ids[0])


def test_step_budget_stops_before_next_call(migrated_database: Engine, client: TestClient) -> None:
    script = [
        {"type": "tool", "tool": "{calculator}", "arguments": ADD},
        {"type": "tool", "tool": "{calculator}", "arguments": ADD},
        {"type": "final", "output": {"total": 42}},
    ]
    run, events = _run_and_events(migrated_database, client, script=script, limits={"max_steps": 2})
    assert run.status == RunStatus.BUDGET_EXCEEDED
    assert run.error_class is None
    assert run.result is not None
    assert run.result["termination"] == {"limit": "max_steps", "limit_value": 2, "used": 2}
    assert run.result["usage"]["steps"] == 2
    types = [e["type"] for e in events]
    assert types[-1] == "run.budget_exceeded"
    assert types.count("step.started") == 2
    assert types.count("tool.requested") == 2
    assert "run.completed" not in types


def test_tool_call_budget_stops_before_request(
    migrated_database: Engine, client: TestClient
) -> None:
    script = [
        {"type": "tool", "tool": "{calculator}", "arguments": ADD},
        {"type": "tool", "tool": "{calculator}", "arguments": ADD},
        {"type": "final", "output": {"total": 42}},
    ]
    run, events = _run_and_events(
        migrated_database, client, script=script, limits={"max_steps": 6, "max_tool_calls": 1}
    )
    assert run.status == RunStatus.BUDGET_EXCEEDED
    assert run.result is not None
    assert run.result["termination"]["limit"] == "max_tool_calls"
    assert run.result["usage"]["tool_calls"] == 1
    started = next(e for e in events if e["type"] == "run.started")
    assert started["payload"]["limits"] == {"max_steps": 6, "max_tool_calls": 1}
    assert [e["type"] for e in events].count("tool.requested") == 1
    interrupted = [e["payload"]["status"] for e in events if e["type"] == "step.completed"]
    assert interrupted == ["completed", "interrupted"]


def test_transient_fault_is_retried_and_traced(
    migrated_database: Engine, client: TestClient
) -> None:
    faults = [{"fault_id": "t1", "tool": "{calculator}", "call_index": 1, "kind": "transient"}]
    run, events = _run_and_events(
        migrated_database, client, faults=faults, limits={"max_steps": 4, "max_retries": 1}
    )
    assert run.status == RunStatus.COMPLETED
    assert run.result is not None
    assert run.result["usage"]["tool_calls"] == 2
    assert run.result["usage"]["retries"] == 1
    types = [e["type"] for e in events]
    assert types.count("tool.validated") == 1
    failed = next(e["payload"] for e in events if e["type"] == "tool.failed")
    assert failed["error_class"] == "transient_tool_error"
    assert failed["retriable"] is True and failed["attempt"] == 1
    retry = next(e["payload"] for e in events if e["type"] == "retry.scheduled")
    assert retry["origin_call_id"] == failed["call_id"]
    assert retry["attempt_number"] == 2
    completed = next(e["payload"] for e in events if e["type"] == "tool.completed")
    assert completed["attempt"] == 2 and completed["call_id"] == failed["call_id"]


def test_retries_stop_at_scenario_maximum(migrated_database: Engine, client: TestClient) -> None:
    faults = [
        {"fault_id": "t1", "tool": "{calculator}", "call_index": 1, "kind": "transient"},
        {"fault_id": "t2", "tool": "{calculator}", "call_index": 2, "kind": "timeout"},
    ]
    run, events = _run_and_events(
        migrated_database, client, faults=faults, limits={"max_steps": 4, "max_retries": 1}
    )
    assert run.status == RunStatus.COMPLETED
    assert run.result is not None
    assert run.result["usage"]["retries"] == 1
    failed = [e["payload"] for e in events if e["type"] == "tool.failed"]
    assert [f["error_class"] for f in failed] == ["transient_tool_error", "tool_timeout"]
    assert failed[1]["retries_exhausted"] is True
    assert failed[1]["timeout_ms"] == 1000
    assert "tool.completed" not in [e["type"] for e in events]


def test_ambiguous_side_effect_is_not_retried(
    migrated_database: Engine, client: TestClient
) -> None:
    faults = [
        {
            "fault_id": "amb",
            "tool": "{ledger}",
            "call_index": 1,
            "kind": "timeout",
            "effect_applied": True,
        }
    ]
    run, events = _run_and_events(
        migrated_database,
        client,
        script=LEDGER_SCRIPT,
        faults=faults,
        limits={"max_steps": 4, "max_retries": 3},
    )
    assert run.status == RunStatus.COMPLETED
    assert run.result is not None
    assert run.result["usage"]["retries"] == 0
    assert run.result["usage"]["tool_calls"] == 1
    types = [e["type"] for e in events]
    assert "retry.scheduled" not in types
    failed = next(e["payload"] for e in events if e["type"] == "tool.failed")
    assert failed["ambiguous_effect"] is True
    assert failed["reconciliation"] == "required"
    assert failed["retriable"] is False


def test_invalid_fault_schedule_fails_as_infrastructure(
    migrated_database: Engine, client: TestClient
) -> None:
    faults = [{"fault_id": "x", "tool": "ghost", "call_index": 1, "kind": "transient"}]
    run, events = _run_and_events(migrated_database, client, faults=faults)
    assert run.status == RunStatus.FAILED
    assert run.error_class == "infrastructure_error"
    assert events[-1]["type"] == "run.failed"
    assert "ghost" not in json.dumps(events)


def _claim(engine: Engine, worker: str, now: datetime, **kwargs: Any) -> Claim | None:
    with Session(engine, expire_on_commit=False) as db, db.begin():
        return claim_next_run(db, worker_id=worker, lease_s=10, now=now, **kwargs)


def _execute(engine: Engine, claim: Claim) -> AttemptOutcome:
    with Session(engine, expire_on_commit=False) as db:
        return run_attempt(db, claim)


def _finish(engine: Engine, outcome: AttemptOutcome) -> bool:
    with Session(engine, expire_on_commit=False) as db, db.begin():
        return finish_attempt(db, outcome)


def test_expired_worker_cannot_persist_duplicate_effects(fresh_database: Engine) -> None:
    with Session(fresh_database, expire_on_commit=False) as db, db.begin():
        world = _world(db, script=LEDGER_SCRIPT)
    t0 = datetime.now(UTC)

    claim_a = _claim(fresh_database, "worker-a", t0)
    assert claim_a is not None and claim_a.fencing_token == 1
    outcome_a = _execute(fresh_database, claim_a)
    assert _claim(fresh_database, "worker-b", t0 + timedelta(seconds=5)) is None

    claim_b = _claim(fresh_database, "worker-b", t0 + timedelta(seconds=11))
    assert claim_b is not None
    assert claim_b.run_id == claim_a.run_id == world.run_ids[0]
    assert (claim_b.attempt_number, claim_b.fencing_token) == (2, 2)
    outcome_b = _execute(fresh_database, claim_b)

    assert _finish(fresh_database, outcome_a) is False
    assert _finish(fresh_database, outcome_b) is True
    assert _finish(fresh_database, outcome_a) is False

    with Session(fresh_database) as db:
        run = db.get(m.Run, world.run_ids[0])
        assert run is not None and run.status == RunStatus.COMPLETED
        assert run.result is not None
        assert run.result["attempt"] == {"number": 2, "fencing_token": 2}
        attempts = {
            a.worker_id: a.status
            for a in db.scalars(select(m.RunAttempt).where(m.RunAttempt.run_id == run.id))
        }
        assert attempts == {"worker-a": "rejected", "worker-b": "finished"}
        events = list(db.scalars(select(m.TraceEvent).where(m.TraceEvent.run_id == run.id)))
        assert {e.attempt_id for e in events} == {claim_b.attempt_id}
        assert sum(1 for e in events if e.type == "tool.completed") == 1
        assert db.scalar(select(func.count()).select_from(m.Trace)) == 1


def test_expired_lease_without_reclaim_still_finishes(fresh_database: Engine) -> None:
    with Session(fresh_database, expire_on_commit=False) as db, db.begin():
        world = _world(db)
    claim = _claim(fresh_database, "slow", datetime.now(UTC) - timedelta(minutes=5))
    assert claim is not None
    assert _finish(fresh_database, _execute(fresh_database, claim)) is True
    with Session(fresh_database) as db:
        run = db.get(m.Run, world.run_ids[0])
        assert run is not None and run.status == RunStatus.COMPLETED


def test_lost_worker_fails_explicitly_after_max_attempts(fresh_database: Engine) -> None:
    with Session(fresh_database, expire_on_commit=False) as db, db.begin():
        world = _world(db)
    t0 = datetime.now(UTC)
    claim = _claim(fresh_database, "lost", t0, max_attempts=1)
    assert claim is not None
    assert _claim(fresh_database, "other", t0 + timedelta(seconds=11), max_attempts=1) is None
    assert _finish(fresh_database, _execute(fresh_database, claim)) is False
    with Session(fresh_database) as db:
        run = db.get(m.Run, world.run_ids[0])
        assert run is not None
        assert run.status == RunStatus.FAILED
        assert run.error_class == "infrastructure_error"
        assert run.result is not None
        assert "lease" in run.result["error"]
        assert db.scalar(select(m.Trace).where(m.Trace.run_id == run.id)) is None


def test_worker_poll_claims_executes_and_persists(fresh_database: Engine) -> None:
    with Session(fresh_database, expire_on_commit=False) as db, db.begin():
        world = _world(db)
    sessions = sessionmaker(fresh_database, expire_on_commit=False)
    assert poll_once(sessions) is True
    assert poll_once(sessions) is False
    with Session(fresh_database) as db:
        run = db.get(m.Run, world.run_ids[0])
        assert run is not None and run.status == RunStatus.COMPLETED
        attempt = db.scalar(select(m.RunAttempt).where(m.RunAttempt.run_id == run.id))
        assert attempt is not None and attempt.status == "finished"
