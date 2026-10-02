"""API de lectura que consume el dashboard (M10, 11.1 y 11.2).

Lista de experimentos, celdas programadas con su estado (también ausentes), filtro por modo,
paginación keyset de la traza y resolución de una referencia de evidencia a su evento.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from evallab.api.app import create_app
from evallab.benchmarks import runner
from evallab.benchmarks.pilot import PILOT, SEEDS
from evallab.benchmarks.suite import publish_suite
from evallab.domain.vocabulary import PRIMARY_CATEGORIES

pytestmark = pytest.mark.integration

AGENTS = ["pilot-scripted-reference", "pilot-scripted-faulty"]


@pytest.fixture
def client(fresh_database: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(engine=fresh_database)) as test_client:
        yield test_client


def _pilot(engine: Engine, *, skip: int) -> tuple[uuid.UUID, list[uuid.UUID]]:
    with Session(engine, expire_on_commit=False) as db, db.begin():
        published = publish_suite(db, PILOT)
        experiment = runner.create_experiment(
            db, published, AGENTS, hypothesis="dashboard", seeds=SEEDS[:1]
        )
        agents = [published.agents[n] for n in AGENTS]
        cells = runner.plan_cells(published.scenarios, agents, 1)
        run_ids = runner.enqueue(db, experiment, cells[: len(cells) - skip])
    runner.execute_in_order(engine, run_ids)
    runner.evaluate_all(engine, run_ids)
    return experiment.id, run_ids


def test_experiment_list_and_cells_keep_missing_cells(
    fresh_database: Engine, client: TestClient
) -> None:
    experiment_id, run_ids = _pilot(fresh_database, skip=2)
    listed = client.get("/experiments").json()
    assert [e["id"] for e in listed] == [str(experiment_id)]
    assert listed[0]["manifest_hash"] is not None
    # Faltan dos celdas por ejecutar: el experimento sigue running.
    assert listed[0]["status"] == "running"

    body = client.get(f"/experiments/{experiment_id}/cells").json()
    assert body["mode"] == "live" and body["planned_cells"] == 28
    cells = body["cells"]
    assert {c["category"] for c in cells} == set(PRIMARY_CATEGORIES)
    missing = [c for c in cells if c["run_status"] == "missing"]
    # Celdas no ejecutadas: presentes, sin run, sin evaluación y con task_success desconocido.
    assert len(missing) == 2
    assert all(c["run_id"] is None and c["task_success"] is None for c in missing)
    executed = [c for c in cells if c["run_id"] is not None]
    assert {c["run_id"] for c in executed} == {str(r) for r in run_ids}
    assert all(c["trace_completeness"] == "complete" for c in executed)
    assert all(c["evaluation_status"] == "completed" for c in executed)
    assert {c["task_success"] for c in executed} == {"pass", "fail"}


def test_report_and_cells_filter_by_mode(fresh_database: Engine, client: TestClient) -> None:
    experiment_id, _ = _pilot(fresh_database, skip=0)
    # Todas las celdas live programadas son terminales: running -> completed.
    assert client.get(f"/experiments/{experiment_id}").json()["status"] == "completed"
    replay = client.get(f"/experiments/{experiment_id}/report", params={"mode": "replay"}).json()
    assert replay["labels"]["mode"] == "replay"
    # Sin replays: las 28 celdas siguen en el denominador como unknown, no desaparecen.
    assert replay["planned_cells"] == 28
    assert sum(a["summary"]["task_success"]["unknown"] for a in replay["agents"]) == 28
    cells = client.get(f"/experiments/{experiment_id}/cells", params={"mode": "replay"}).json()
    assert {c["run_status"] for c in cells["cells"]} == {"missing"}
    bad = client.get(f"/experiments/{experiment_id}/report", params={"mode": "reevaluation"})
    assert bad.status_code == 422


def _all_pages(client: TestClient, run_id: str, limit: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    after: int | None = None
    while True:
        params: dict[str, int] = {"limit": limit}
        if after is not None:
            params["after_sequence"] = after
        page = client.get(f"/runs/{run_id}/trace", params=params).json()
        assert page["page"]["returned"] == len(page["events"]) <= limit
        events.extend(page["events"])
        after = page["page"]["next_after_sequence"]
        if after is None:
            return events


def test_trace_pages_reassemble_the_full_trace(fresh_database: Engine, client: TestClient) -> None:
    _, run_ids = _pilot(fresh_database, skip=0)
    run_id = str(run_ids[0])
    full = client.get(f"/runs/{run_id}/trace").json()
    assert full["page"] is None and full["event_count"] == len(full["events"]) > 3
    for limit in (1, 2, full["event_count"], full["event_count"] + 5):
        assert _all_pages(client, run_id, limit) == full["events"]
    exact = client.get(f"/runs/{run_id}/trace", params={"limit": full["event_count"]}).json()
    # Una página que llega justo al final ya declara que no hay más.
    assert exact["page"]["next_after_sequence"] is None
    assert client.get(f"/runs/{run_id}/trace", params={"limit": 0}).status_code == 422
    assert client.get(f"/runs/{run_id}/trace", params={"limit": 501}).status_code == 422


def test_score_evidence_resolves_to_trace_event(fresh_database: Engine, client: TestClient) -> None:
    _, run_ids = _pilot(fresh_database, skip=0)
    refs = []
    for source in run_ids:
        evaluation = client.get(f"/runs/{source}/evaluations").json()[-1]
        refs += [
            (str(source), ref)
            for score in evaluation["scores"]
            for ref in score["evidence_refs"]
            if ref.get("event_id")
        ]
    assert refs
    run_id, ref = refs[0]
    event = client.get(f"/runs/{run_id}/trace/events/{ref['event_id']}")
    assert event.status_code == 200
    assert event.json()["event_id"] == ref["event_id"]
    page = client.get(
        f"/runs/{run_id}/trace",
        params={"after_sequence": event.json()["sequence"] - 1, "limit": 1},
    ).json()
    assert page["events"][0]["event_id"] == ref["event_id"]
    other = client.get(f"/runs/{run_ids[1]}/trace/events/{ref['event_id']}")
    assert other.status_code == 404
