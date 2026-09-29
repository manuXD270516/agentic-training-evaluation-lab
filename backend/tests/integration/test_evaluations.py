import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from evallab.api.app import create_app
from evallab.db.migrate import upgrade_head
from evallab.evaluation.engine import SUITE_HASH
from evallab.services import evaluations as eval_svc
from evallab.services.execution import claim_next_run, execute_run
from tests.integration.test_execution import _world
from tests.test_tools import ADD

pytestmark = pytest.mark.integration

ORACLE = {
    "output_schema": {
        "type": "object",
        "properties": {"total": {"type": "integer"}},
        "required": ["total"],
    },
    "checks": [
        {"operator": "json_value_equals", "path": "/total", "value": 42},
        {"operator": "required_tool", "tool": "{calculator}", "min_calls": 1},
        {"operator": "arguments_equal", "tool": "{calculator}", "value": ADD},
    ],
}
REQUIRED = {
    "required_checks": ["outcome", "output_structure", "required_tool", "semantic_arguments"]
}


@pytest.fixture
def client(migrated_database: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(engine=migrated_database)) as test_client:
        yield test_client


def _executed_run(engine: Engine, **world: Any) -> uuid.UUID:
    with Session(engine) as db, db.begin():
        built = _world(db, oracle=ORACLE, evaluation=REQUIRED, **world)
        execute_run(db, built.run_ids[0])
        return built.run_ids[0]


def _post(client: TestClient, run_id: uuid.UUID, key: str | None = None) -> Any:
    headers = {"Idempotency-Key": key or str(uuid.uuid4())}
    return client.post(f"/runs/{run_id}/evaluations", headers=headers)


def _scores(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["metric_id"]: s for s in body["scores"]}


def test_completed_run_is_evaluated_and_scores_persisted(
    migrated_database: Engine, client: TestClient
) -> None:
    run_id = _executed_run(migrated_database)
    response = _post(client, run_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["evaluator_suite_hash"] == SUITE_HASH
    assert body["metric_profile_version"] == "1.0.0"
    trace = client.get(f"/runs/{run_id}/trace").json()
    assert body["trace_digest"] == trace["digest"]
    scores = _scores(body)
    assert scores["task_success"]["status"] == "pass"
    assert scores["task_success"]["value"] == 1.0
    assert scores["raw_outcome_pass"]["status"] == "pass"
    assert scores["argument_accuracy"]["value"] == 1.0
    assert all(s["metric_version"] == "1.0.0" for s in body["scores"])
    assert body["report"]["task_success"] == "pass"
    assert client.get(f"/evaluations/{body['id']}").json() == body


def test_not_applicable_metric_persists_zero_denominator_without_value(
    migrated_database: Engine, client: TestClient
) -> None:
    run_id = _executed_run(migrated_database)
    body = _post(client, run_id).json()
    evidence = _scores(body)["evidence_coverage"]
    assert evidence["status"] == "not_applicable"
    assert evidence["value"] is None
    assert (evidence["numerator"], evidence["denominator"]) == (0, 0)


def test_reevaluation_creates_new_evaluation_and_keeps_history(
    migrated_database: Engine, client: TestClient
) -> None:
    run_id = _executed_run(migrated_database)
    key = str(uuid.uuid4())
    first = _post(client, run_id, key).json()
    replay = _post(client, run_id, key).json()
    assert replay["id"] == first["id"]
    second = _post(client, run_id).json()
    assert second["id"] != first["id"]
    assert second["parent_evaluation_id"] == first["id"]
    history = client.get(f"/runs/{run_id}/evaluations").json()
    assert [e["id"] for e in history] == [first["id"], second["id"]]
    assert history[0] == client.get(f"/evaluations/{first['id']}").json()
    assert _scores(history[0]) == _scores(first)


def test_failing_answer_scores_fail(migrated_database: Engine, client: TestClient) -> None:
    script = [
        {"type": "tool", "tool": "{calculator}", "arguments": ADD},
        {"type": "final", "output": {"total": 41}},
    ]
    run_id = _executed_run(migrated_database, script=script)
    scores = _scores(_post(client, run_id).json())
    assert scores["raw_outcome_pass"]["status"] == "fail"
    assert scores["task_success"]["value"] == 0.0


def test_queued_run_is_not_evaluable(migrated_database: Engine, client: TestClient) -> None:
    with Session(migrated_database) as db, db.begin():
        world = _world(db)
    response = _post(client, world.run_ids[0])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "run_not_evaluable"


def test_run_without_sealed_trace_is_not_evaluable(empty_database: Engine) -> None:
    with empty_database.begin() as conn:
        upgrade_head(conn)
    with Session(empty_database) as db, db.begin():
        world = _world(db)
    t0 = datetime.now(UTC)
    for offset in (0, 11):
        with Session(empty_database) as db, db.begin():
            claim_next_run(
                db, worker_id="w", lease_s=10, max_attempts=1, now=t0 + timedelta(seconds=offset)
            )
    with TestClient(create_app(engine=empty_database)) as client:
        response = _post(client, world.run_ids[0])
    assert response.status_code == 409
    assert "traza" in response.json()["error"]["message"]


def test_evaluator_crash_is_persisted_as_error(
    migrated_database: Engine, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = _executed_run(migrated_database)

    def boom(*_: Any) -> Any:
        raise RuntimeError("fallo interno")

    monkeypatch.setattr(eval_svc, "evaluate", boom)
    body = _post(client, run_id).json()
    assert body["status"] == "error"
    assert body["error"] == "evaluator_error: RuntimeError"
    assert body["scores"] == []
    assert body["report"] is None
    assert body["completed_at"] is not None


def test_evaluation_requires_idempotency_key(migrated_database: Engine, client: TestClient) -> None:
    run_id = _executed_run(migrated_database)
    assert client.post(f"/runs/{run_id}/evaluations").status_code in {400, 422}
