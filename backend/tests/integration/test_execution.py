import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from evallab.api.app import create_app
from evallab.db import models as m
from evallab.db.migrate import upgrade_head
from evallab.domain.lifecycle import ExperimentStatus, RunStatus
from evallab.services.execution import claim_queued_run, execute_claimed_run, execute_run
from evallab.services.experiments import seal
from tests.integration import factories as f

pytestmark = pytest.mark.integration

ORACLE_SECRET = "SECRET_ORACLE_42"


def _ref(row: Any) -> dict[str, str]:
    return {"id": str(row.id), "version": row.version}


def _scripted_world(db: Session, *, pattern: str = "scripted") -> dict[str, Any]:
    bench = f.benchmark(db)
    tool_name = f"calculator-{uuid.uuid4().hex[:8]}"
    tool = m.ToolDefinition(
        id=uuid.uuid4(),
        version="1.0.0",
        name=tool_name,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        effect_class="read_only",
        timeout_ms=1000,
        content_hash=f.digest(),
    )
    db.add(tool)
    db.flush()
    tool_ref = {"id": str(tool.id), "version": tool.version, "content_hash": tool.content_hash}
    scenario = m.Scenario(
        id=uuid.uuid4(),
        version="1.0.0",
        primary_category="tool_selection",
        split="dev",
        family_id="family-secret",
        input={"a": 40, "b": 2},
        task={"input": {"a": 40, "b": 2}},
        tools=[tool_ref],
        environment={"fault_schedule": [{"fault": "hidden"}]},
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
    params = (
        {
            "script": [
                {
                    "type": "tool",
                    "tool": tool_name,
                    "arguments": {"a": 40, "b": 2},
                    "result": {"total": 42},
                    "evidence_id": "e1",
                },
                {"type": "final", "output": {"total": 42, "evidence_ids": ["e1"]}},
            ]
        }
        if pattern == "scripted"
        else {}
    )
    agent = f.agent(db, pattern=pattern, pattern_parameters=params)
    db.add(
        m.AgentTool(
            agent_id=agent.id,
            agent_version=agent.version,
            tool_id=tool.id,
            tool_version=tool.version,
        )
    )
    exp = m.Experiment(
        hypothesis="scripted harness",
        status="draft",
        benchmark_id=bench.id,
        benchmark_version=bench.version,
        budgets={"max_steps": 4},
        repetitions=1,
        seeds=[11],
    )
    db.add(exp)
    db.flush()
    db.add(m.ExperimentAgent(experiment_id=exp.id, agent_id=agent.id, agent_version=agent.version))
    sealed = seal(db, exp.id)
    run = m.Run(
        experiment_id=sealed.id,
        scenario_id=scenario.id,
        scenario_version=scenario.version,
        agent_id=agent.id,
        agent_version=agent.version,
        repetition=1,
        seed=11,
        mode="live",
    )
    db.add(run)
    db.flush()
    return {"experiment_id": sealed.id, "run_id": run.id, "agent": _ref(agent)}


@pytest.fixture
def client(migrated_database: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(engine=migrated_database)) as test_client:
        yield test_client


def test_scripted_run_reaches_terminal_state_with_evidence(
    migrated_database: Engine, client: TestClient
) -> None:
    with Session(migrated_database) as db, db.begin():
        world = _scripted_world(db)
        run = execute_run(db, world["run_id"])
        run_id = run.id
        experiment_id = world["experiment_id"]
        assert run.status == RunStatus.COMPLETED
        assert run.result is not None
        assert run.result["label"] == "scripted"
        assert run.result["attribution"] == "harness_baseline"
        assert run.result["usage"]["model_calls"] == 0
        assert run.result["output"]["total"] == 42
        assert run.result["evidence_refs"]

    with Session(migrated_database) as db:
        exp = db.get(m.Experiment, experiment_id)
        assert exp is not None
        assert exp.status == ExperimentStatus.RUNNING

    body = client.get(f"/runs/{run_id}").json()
    assert body["status"] == "completed"
    assert body["result"]["usage"]["model_calls"] == 0

    trace = client.get(f"/runs/{run_id}/trace")
    assert trace.status_code == 200
    payload = trace.json()
    assert payload["completeness"] == "complete"
    assert payload["event_count"] == len(payload["events"])
    types = [event["type"] for event in payload["events"]]
    assert types[0] == "run.started"
    assert types[-1] == "run.completed"
    assert "tool.completed" in types
    assert "model.requested" not in types
    blob = json.dumps(payload)
    assert ORACLE_SECRET not in blob
    assert "family-secret" not in blob
    assert "fault_schedule" not in blob


def test_queued_run_has_no_trace_until_execution(
    migrated_database: Engine, client: TestClient
) -> None:
    with Session(migrated_database) as db, db.begin():
        world = _scripted_world(db)
        run_id = world["run_id"]
    response = client.get(f"/runs/{run_id}/trace")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_react_is_not_reported_as_implemented(
    migrated_database: Engine, client: TestClient
) -> None:
    with Session(migrated_database) as db, db.begin():
        world = _scripted_world(db, pattern="react")
        run = execute_run(db, world["run_id"])
        run_id = run.id
        assert run.status == RunStatus.FAILED
        assert run.error_class == "infrastructure_error"
        assert run.result is not None
        assert run.result["label"] == "react"
        assert run.result["attribution"] == "unimplemented"
    body = client.get(f"/runs/{run_id}").json()
    assert body["result"]["usage"]["model_calls"] == 0


def test_worker_claims_queued_run(empty_database: Engine) -> None:
    with empty_database.begin() as conn:
        upgrade_head(conn)
    with Session(empty_database) as db, db.begin():
        world = _scripted_world(db)
        claimed = claim_queued_run(db)
        assert claimed is not None
        assert claimed.id == world["run_id"]
        finished = execute_claimed_run(db, claimed)
        assert finished.status == RunStatus.COMPLETED
