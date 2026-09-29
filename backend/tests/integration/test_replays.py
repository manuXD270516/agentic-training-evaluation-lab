"""Replay offline sobre la traza sellada, mismatch sin fallback y evaluación histórica intacta."""

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from evallab.api.app import create_app
from evallab.canonical import canonical_digest
from evallab.db import models as m
from evallab.runner.sink import trace_digest
from evallab.runner.tools import FixtureToolGateway
from evallab.services.execution import execute_run
from tests.integration.test_evaluations import ORACLE, REQUIRED
from tests.integration.test_execution import _world

pytestmark = pytest.mark.integration


@pytest.fixture
def client(migrated_database: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(engine=migrated_database)) as test_client:
        yield test_client


@pytest.fixture
def no_live_tools(monkeypatch: pytest.MonkeyPatch) -> Callable[[], list[str]]:
    """Tras grabar el origen, registra cualquier ejecución de fixture (fallback a live)."""

    def forbid() -> list[str]:
        calls: list[str] = []

        def invoke(self: FixtureToolGateway, name: str, *_: Any) -> Any:
            calls.append(name)
            raise AssertionError("replay no debe ejecutar tools")

        monkeypatch.setattr(FixtureToolGateway, "invoke", invoke)
        return calls

    return forbid


def _source(engine: Engine, **world: Any) -> uuid.UUID:
    with Session(engine) as db, db.begin():
        built = _world(db, oracle=ORACLE, evaluation=REQUIRED, **world)
        execute_run(db, built.run_ids[0])
        return built.run_ids[0]


def _post_replay(client: TestClient, run_id: uuid.UUID, key: str | None = None) -> Any:
    headers = {"Idempotency-Key": key or str(uuid.uuid4())}
    return client.post(f"/runs/{run_id}/replays", headers=headers)


def _execute(engine: Engine, run_id: uuid.UUID) -> None:
    with Session(engine) as db, db.begin():
        execute_run(db, run_id)


def _rerecord(
    engine: Engine, run_id: uuid.UUID, event_type: str, mutate: Callable[[dict[str, Any]], None]
) -> None:
    """Simula una grabación hecha en otras condiciones, con digests coherentes."""
    with Session(engine) as db, db.begin():
        events = list(
            db.scalars(
                select(m.TraceEvent)
                .where(m.TraceEvent.run_id == run_id)
                .order_by(m.TraceEvent.sequence)
            )
        )
        for event in events:
            if event.type == event_type:
                payload = dict(event.payload)
                mutate(payload)
                event.payload = payload
                event.payload_digest = canonical_digest(payload)
        trace = db.scalars(select(m.Trace).where(m.Trace.run_id == run_id)).one()
        trace.digest = trace_digest(
            (e.event_id, e.sequence, e.type, e.payload_digest) for e in events
        )


def test_replay_reproduces_source_offline(
    migrated_database: Engine, client: TestClient, no_live_tools: Callable[[], list[str]]
) -> None:
    source_id = _source(migrated_database)
    live_calls = no_live_tools()
    key = str(uuid.uuid4())
    response = _post_replay(client, source_id, key)
    assert response.status_code == 202, response.text
    replay = response.json()
    assert (replay["mode"], replay["source_run_id"], replay["status"]) == (
        "replay",
        str(source_id),
        "queued",
    )
    assert _post_replay(client, source_id, key).json()["id"] == replay["id"]
    second = _post_replay(client, source_id)
    assert (second.status_code, second.json()["error"]["code"]) == (409, "run_cell_exists")

    _execute(migrated_database, uuid.UUID(replay["id"]))
    assert live_calls == []
    run = client.get(f"/runs/{replay['id']}").json()
    source = client.get(f"/runs/{source_id}").json()
    assert run["status"] == "completed"
    assert run["result"]["output"] == source["result"]["output"]
    info = run["result"]["replay"]
    source_trace = client.get(f"/runs/{source_id}/trace").json()
    assert info["source_trace_digest"] == source_trace["digest"]
    assert info["output_matches_source"] is True
    assert info["recorded_calls"] == 1

    def results(trace: dict[str, Any]) -> list[Any]:
        return [e["payload"]["result"] for e in trace["events"] if e["type"] == "tool.completed"]

    replay_trace = client.get(f"/runs/{replay['id']}/trace").json()
    assert results(replay_trace) == results(source_trace)
    assert replay_trace["events"][0]["payload"]["mode"] == "replay"


def test_divergent_recording_is_mismatch_without_fallback(
    migrated_database: Engine, client: TestClient, no_live_tools: Callable[[], list[str]]
) -> None:
    source_id = _source(migrated_database)
    live_calls = no_live_tools()
    replay_id = uuid.UUID(_post_replay(client, source_id).json()["id"])

    def other_arguments(payload: dict[str, Any]) -> None:
        payload["arguments"] = {**payload["arguments"], "b": 3}

    _rerecord(migrated_database, source_id, "tool.requested", other_arguments)
    _execute(migrated_database, replay_id)
    assert live_calls == []
    run = client.get(f"/runs/{replay_id}").json()
    assert (run["status"], run["error_class"]) == ("failed", "replay_mismatch")
    events = client.get(f"/runs/{replay_id}/trace").json()["events"]
    failed = next(e for e in events if e["type"] == "tool.failed")
    assert failed["payload"]["error_class"] == "replay_mismatch"
    assert not any(e["type"] == "tool.completed" for e in events)


def test_recording_with_other_fixture_hash_is_not_identical(
    migrated_database: Engine, client: TestClient, no_live_tools: Callable[[], list[str]]
) -> None:
    source_id = _source(migrated_database)
    live_calls = no_live_tools()
    replay_id = uuid.UUID(_post_replay(client, source_id).json()["id"])

    def other_fixture(payload: dict[str, Any]) -> None:
        payload["tools"] = [{**t, "content_hash": "f" * 64} for t in payload["tools"]]

    _rerecord(migrated_database, source_id, "run.started", other_fixture)
    _execute(migrated_database, replay_id)
    run = client.get(f"/runs/{replay_id}").json()
    assert (run["status"], run["error_class"]) == ("failed", "replay_mismatch")
    assert "tools" in run["result"]["error"]
    events = client.get(f"/runs/{replay_id}/trace").json()["events"]
    assert not any(e["type"] == "tool.requested" for e in events)
    assert live_calls == []


def test_tampered_source_trace_is_not_replayable(
    migrated_database: Engine, client: TestClient
) -> None:
    source_id = _source(migrated_database)
    with Session(migrated_database) as db, db.begin():
        event = db.scalars(
            select(m.TraceEvent).where(
                m.TraceEvent.run_id == source_id, m.TraceEvent.type == "tool.completed"
            )
        ).one()
        event.payload = {**event.payload, "result": {"total": 41}}
    response = _post_replay(client, source_id)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "replay_unavailable")


def test_replay_preconditions(migrated_database: Engine, client: TestClient) -> None:
    with Session(migrated_database) as db, db.begin():
        world = _world(db)
    queued = _post_replay(client, world.run_ids[0])
    assert (queued.status_code, queued.json()["error"]["code"]) == (409, "replay_unavailable")

    source_id = _source(migrated_database)
    replay_id = _post_replay(client, source_id).json()["id"]
    nested = _post_replay(client, uuid.UUID(replay_id))
    assert (nested.status_code, nested.json()["error"]["code"]) == (409, "replay_unavailable")
    assert _post_replay(client, uuid.uuid4()).status_code == 404
    assert client.post(f"/runs/{source_id}/replays").status_code in {400, 422}

    run = client.get(f"/runs/{source_id}").json()
    body = {
        "scenario": run["scenario"],
        "agent": run["agent"],
        "repetition": 1,
        "mode": "replay",
    }
    direct = client.post(
        f"/experiments/{run['experiment_id']}/runs",
        json=body,
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert direct.status_code == 422


def test_redacted_source_is_not_replayable(migrated_database: Engine, client: TestClient) -> None:
    source_id = _source(
        migrated_database, calculator_result={"total": 42, "api_key": "sk-" + "x" * 20}
    )
    response = _post_replay(client, source_id)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "replay_unavailable")


def test_source_evaluations_survive_replay_and_reevaluation(
    migrated_database: Engine, client: TestClient, no_live_tools: Callable[[], list[str]]
) -> None:
    source_id = _source(migrated_database)
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    original = client.post(f"/runs/{source_id}/evaluations", headers=headers).json()
    no_live_tools()

    replay_id = _post_replay(client, source_id).json()["id"]
    _execute(migrated_database, uuid.UUID(replay_id))
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    on_replay = client.post(f"/runs/{replay_id}/evaluations", headers=headers).json()
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    reevaluated = client.post(f"/runs/{source_id}/evaluations", headers=headers).json()

    history = client.get(f"/runs/{source_id}/evaluations").json()
    assert [e["id"] for e in history] == [original["id"], reevaluated["id"]]
    assert history[0] == original
    assert reevaluated["parent_evaluation_id"] == original["id"]
    assert on_replay["run_id"] == replay_id
    assert on_replay["parent_evaluation_id"] is None
    assert on_replay["trace_digest"] != original["trace_digest"]
    assert on_replay["report"]["task_success"] == original["report"]["task_success"] == "pass"
