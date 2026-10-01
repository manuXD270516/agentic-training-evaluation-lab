"""Judge auxiliar por API (9.1): score con scope judge, gates intactos y suite versionada."""

import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from evallab.api.app import create_app
from evallab.benchmarks.fixture_models import SYNTHETIC_PRICE
from evallab.evaluation.engine import SUITE_HASH
from evallab.schemas import FixtureCreate, ModelConfigurationCreate, PriceSnapshotCreate
from evallab.services import agents as agent_svc
from evallab.services.catalog import publish_fixture
from evallab.services.execution import execute_run
from tests.integration.test_evaluations import ORACLE, REQUIRED
from tests.integration.test_execution import _world

pytestmark = pytest.mark.integration

JUDGE_SPEC = {
    "dimension": "clarity",
    "rubric_ref": "answer-clarity@1.0.0",
    "deterministic_unavailable_reason": "la claridad de la respuesta no tiene oráculo exacto",
}


def _vote(rating: int) -> dict[str, Any]:
    vote = {"rating": rating, "abstain": False, "evidence": ["/total"], "rationale": "clara"}
    return {"content": json.dumps(vote), "usage": {"input_tokens": 250, "output_tokens": 30}}


@pytest.fixture
def client(migrated_database: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(engine=migrated_database)) as test_client:
        yield test_client


def _judge_model(engine: Engine, responses: list[dict[str, Any]]) -> dict[str, str]:
    with Session(engine) as db, db.begin():
        script = publish_fixture(
            db,
            FixtureCreate(
                name=f"judge-{uuid.uuid4().hex[:8]}",
                payload={"kind": "model_script", "default": responses},
            ),
        )
        price = agent_svc.publish_price(db, PriceSnapshotCreate.model_validate(SYNTHETIC_PRICE))
        model = agent_svc.publish_model(
            db,
            ModelConfigurationCreate(
                version="1.0.0",
                provider="fixture",
                requested_model=script.content_hash,
                seed_support="unsupported",
                max_tokens=400,
                price_snapshot_ref=price.content_hash,
            ),
        )
        return {"id": str(model.id), "version": model.version}


def _run(engine: Engine, *, judge: bool = True, result: dict[str, Any] | None = None) -> uuid.UUID:
    evaluation = {**REQUIRED, "judge": JUDGE_SPEC} if judge else REQUIRED
    script = None
    if result is not None:
        script = [
            {"type": "tool", "tool": "{calculator}", "arguments": {"op": "add", "a": 40, "b": 2}},
            {"type": "final", "output": result},
        ]
    with Session(engine) as db, db.begin():
        built = _world(db, oracle=ORACLE, evaluation=evaluation, script=script)
        execute_run(db, built.run_ids[0])
        return built.run_ids[0]


def _evaluate(client: TestClient, run_id: uuid.UUID, body: dict[str, Any] | None) -> Any:
    headers = {"Idempotency-Key": uuid.uuid4().hex}
    response = client.post(f"/runs/{run_id}/evaluations", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _scores(body: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(s["metric_id"], s["scope"]): s for s in body["scores"]}


def test_judge_score_is_separate_and_never_overrides_gates(
    migrated_database: Engine, client: TestClient
) -> None:
    model = _judge_model(migrated_database, [_vote(4)])
    # Respuesta con valor erróneo: el gate determinístico falla aunque el judge la apruebe.
    run_id = _run(migrated_database, result={"total": 41})
    deterministic = _evaluate(client, run_id, None)
    judged = _evaluate(client, run_id, {"judge_model": model})

    assert deterministic["evaluator_suite_hash"] == SUITE_HASH
    assert judged["evaluator_suite_hash"] != SUITE_HASH
    assert judged["evaluator_suite_version"].endswith("+judge.answer-clarity@1.0.0")
    assert judged["parent_evaluation_id"] == deterministic["id"]
    scores = _scores(judged)
    assert scores[("task_success", "agent")]["status"] == "fail"
    rating = scores[("judge_task_rating", "judge")]
    assert (rating["status"], rating["value"]) == ("pass", 4.0)
    assert rating["evidence_refs"] == [{"source": "run.result", "pointer": "/output/total"}]
    judge = judged["report"]["judge"]
    assert judge["disagrees_with_gates"] is True
    assert judge["calibration"] == "experimental"
    assert judged["report"]["task_success"] == "fail"
    # La evaluación determinística previa no se modificó.
    previous = client.get(f"/evaluations/{deterministic['id']}").json()
    assert previous["report"] == deterministic["report"]
    assert ("judge_task_rating", "judge") not in _scores(previous)


def test_scenario_without_subjective_dimension_skips_the_judge(
    migrated_database: Engine, client: TestClient
) -> None:
    model = _judge_model(migrated_database, [_vote(4)])
    run_id = _run(migrated_database, judge=False)
    judged = _evaluate(client, run_id, {"judge_model": model})
    assert judged["report"]["judge"] == {"status": "not_applicable", "reason": "no_judge_dimension"}
    assert ("judge_task_rating", "judge") not in _scores(judged)
    assert judged["evaluator_suite_hash"] == SUITE_HASH


def test_invalid_judge_output_is_error_and_unknown_model_is_rejected(
    migrated_database: Engine, client: TestClient
) -> None:
    model = _judge_model(migrated_database, [{"content": "excelente respuesta"}])
    run_id = _run(migrated_database)
    judged = _evaluate(client, run_id, {"judge_model": model})
    rating = _scores(judged)[("judge_task_rating", "judge")]
    assert (rating["status"], rating["value"]) == ("error", None)
    assert judged["report"]["judge"]["reason"] == "invalid_judge_response:invalid_json"
    assert judged["report"]["task_success"] == "pass"
    ghost = {"judge_model": {"id": str(uuid.uuid4()), "version": "1.0.0"}}
    response = client.post(
        f"/runs/{run_id}/evaluations", json=ghost, headers={"Idempotency-Key": uuid.uuid4().hex}
    )
    assert response.status_code == 422
