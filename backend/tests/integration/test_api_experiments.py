import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from evallab.api.app import create_app
from evallab.canonical import canonical_digest
from evallab.db import models as m
from tests.integration import factories as f

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class Catalog:
    benchmark: dict[str, str]
    scenarios: list[dict[str, str]]
    agent: dict[str, str]
    other_agent: dict[str, str]
    foreign_scenario: dict[str, str]


def _ref(row: Any) -> dict[str, str]:
    return {"id": str(row.id), "version": row.version}


@pytest.fixture
def catalog(migrated_database: Engine) -> Catalog:
    """Configuraciones publicadas y confirmadas, como las verá la API."""
    with Session(migrated_database) as db, db.begin():
        bench = f.benchmark(db)
        scenarios = [f.scenario(db) for _ in range(2)]
        db.add_all(
            m.DatasetScenario(
                dataset_id=bench.dataset_id,
                dataset_version=bench.dataset_version,
                scenario_id=s.id,
                scenario_version=s.version,
                position=i,
            )
            for i, s in enumerate(scenarios)
        )
        agent = f.agent(db)
        model = m.ModelConfiguration(
            id=uuid.uuid4(),
            version="1.0.0",
            provider="none",
            requested_model="scripted",
            seed_support="unsupported",
            content_hash=f.digest(),
        )
        tool = m.ToolDefinition(
            id=uuid.uuid4(),
            version="1.0.0",
            name=f"calculator-{uuid.uuid4().hex[:8]}",
            input_schema={},
            output_schema={},
            effect_class="read_only",
            timeout_ms=1000,
            content_hash=f.digest(),
        )
        db.add_all([model, tool])
        db.flush()
        db.add_all(
            [
                m.AgentRole(
                    agent_id=agent.id,
                    agent_version=agent.version,
                    role="executor",
                    model_id=model.id,
                    model_version=model.version,
                ),
                m.AgentTool(
                    agent_id=agent.id,
                    agent_version=agent.version,
                    tool_id=tool.id,
                    tool_version=tool.version,
                ),
            ]
        )
        return Catalog(
            benchmark=_ref(bench),
            scenarios=[_ref(s) for s in scenarios],
            agent=_ref(agent),
            other_agent=_ref(f.agent(db)),
            foreign_scenario=_ref(f.scenario(db)),
        )


@pytest.fixture
def client(migrated_database: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(engine=migrated_database)) as test_client:
        yield test_client


def _draft(catalog: Catalog, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "hypothesis": f"h-{uuid.uuid4().hex}",
        "benchmark": catalog.benchmark,
        "agents": [catalog.agent],
        "budgets": {"max_steps": 4},
        "repetitions": 2,
        "seeds": [11, 23],
        "comparison_plan": {},
    }
    return body | overrides


def _key() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


def _count(engine: Engine, model: type[m.Base], *where: Any) -> int:
    with Session(engine) as db:
        return db.scalar(select(func.count()).select_from(model).where(*where)) or 0


def _sealed(client: TestClient, catalog: Catalog, **overrides: Any) -> dict[str, Any]:
    created = client.post("/experiments", json=_draft(catalog, **overrides), headers=_key())
    assert created.status_code == 201, created.text
    sealed = client.post(f"/experiments/{created.json()['id']}/seal")
    assert sealed.status_code == 200, sealed.text
    body: dict[str, Any] = sealed.json()
    return body


# --- Idempotencia de creación ------------------------------------------------------------


def test_repeated_key_and_payload_returns_same_experiment(
    client: TestClient, catalog: Catalog, migrated_database: Engine
) -> None:
    body, headers = _draft(catalog), _key()
    first = client.post("/experiments", json=body, headers=headers)
    second = client.post("/experiments", json=body, headers=headers)
    assert first.status_code == second.status_code == 201
    assert second.json() == first.json()
    assert second.headers["Idempotent-Replayed"] == "true"
    assert "Idempotent-Replayed" not in first.headers
    hypothesis = m.Experiment.hypothesis == body["hypothesis"]
    assert _count(migrated_database, m.Experiment, hypothesis) == 1


def test_reused_key_with_other_payload_is_rejected(
    client: TestClient, catalog: Catalog, migrated_database: Engine
) -> None:
    headers = _key()
    first = client.post("/experiments", json=_draft(catalog), headers=headers)
    other = _draft(catalog)
    second = client.post("/experiments", json=other, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "idempotency_key_reused"
    hypothesis = m.Experiment.hypothesis == other["hypothesis"]
    assert _count(migrated_database, m.Experiment, hypothesis) == 0


@pytest.mark.parametrize("headers", [{}, {"Idempotency-Key": ""}, {"Idempotency-Key": "a b"}])
def test_creation_requires_valid_idempotency_key(
    client: TestClient, catalog: Catalog, migrated_database: Engine, headers: dict[str, str]
) -> None:
    body = _draft(catalog)
    response = client.post("/experiments", json=body, headers=headers)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "idempotency_key_required"
    hypothesis = m.Experiment.hypothesis == body["hypothesis"]
    assert _count(migrated_database, m.Experiment, hypothesis) == 0


def test_failed_request_does_not_consume_key(client: TestClient, catalog: Catalog) -> None:
    headers = _key()
    missing = {"id": str(uuid.uuid4()), "version": "1.0.0"}
    failed = client.post("/experiments", json=_draft(catalog, benchmark=missing), headers=headers)
    assert failed.status_code == 422
    assert failed.json()["error"]["code"] == "invalid_reference"
    retried = client.post("/experiments", json=_draft(catalog), headers=headers)
    assert retried.status_code == 201


def test_concurrent_requests_with_same_key_create_one_experiment(
    client: TestClient, catalog: Catalog, migrated_database: Engine
) -> None:
    body, headers = _draft(catalog), _key()
    barrier = threading.Barrier(4)
    responses: list[Any] = []

    def submit() -> None:
        barrier.wait()
        responses.append(client.post("/experiments", json=body, headers=headers))

    threads = [threading.Thread(target=submit) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert {r.status_code for r in responses} == {201}
    assert len({r.json()["id"] for r in responses}) == 1
    hypothesis = m.Experiment.hypothesis == body["hypothesis"]
    assert _count(migrated_database, m.Experiment, hypothesis) == 1


def test_seeds_must_match_repetitions(client: TestClient, catalog: Catalog) -> None:
    response = client.post(
        "/experiments", json=_draft(catalog, repetitions=3, seeds=[11]), headers=_key()
    )
    assert response.status_code == 422


# --- Sellado y snapshot ------------------------------------------------------------------


def test_seal_produces_verifiable_canonical_manifest(client: TestClient, catalog: Catalog) -> None:
    sealed = _sealed(client, catalog)
    assert sealed["status"] == "sealed"
    assert sealed["sealed_at"] is not None
    manifest = client.get(f"/experiments/{sealed['id']}/manifest").json()
    assert manifest["manifest_hash"] == sealed["manifest_hash"]
    assert canonical_digest(manifest["manifest"]) == sealed["manifest_hash"]
    content = manifest["manifest"]
    assert sealed["id"] not in str(content)
    assert content["seeds"] == [11, 23]
    assert content["benchmark"]["id"] == catalog.benchmark["id"]
    [agent] = content["agents"]
    assert agent["id"] == catalog.agent["id"]
    assert [r["role"] for r in agent["roles"]] == ["executor"]
    assert len(agent["tools"]) == 1


def test_identical_configuration_yields_identical_manifest_hash(
    client: TestClient, catalog: Catalog
) -> None:
    first = _sealed(client, catalog, hypothesis="same")
    second = _sealed(client, catalog, hypothesis="same")
    reordered = _sealed(client, catalog, hypothesis="same", seeds=[23, 11])
    assert first["id"] != second["id"]
    assert first["manifest_hash"] == second["manifest_hash"]
    assert reordered["manifest_hash"] != first["manifest_hash"]


def test_draft_is_editable_before_seal(client: TestClient, catalog: Catalog) -> None:
    created = client.post("/experiments", json=_draft(catalog), headers=_key()).json()
    patched = client.patch(
        f"/experiments/{created['id']}",
        json={"hypothesis": "revisada", "agents": [catalog.agent, catalog.other_agent]},
    )
    assert patched.status_code == 200
    assert patched.json()["hypothesis"] == "revisada"
    assert len(patched.json()["agents"]) == 2


def test_sealed_experiment_rejects_edits(
    client: TestClient, catalog: Catalog, migrated_database: Engine
) -> None:
    sealed = _sealed(client, catalog)
    response = client.patch(f"/experiments/{sealed['id']}", json={"hypothesis": "otra"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "experiment_sealed"
    assert client.get(f"/experiments/{sealed['id']}").json() == sealed

    experiment_id = uuid.UUID(sealed["id"])
    with (
        Session(migrated_database) as db,
        pytest.raises(DBAPIError, match="sealed experiment is immutable"),
    ):
        db.execute(update(m.Experiment).where(m.Experiment.id == experiment_id).values(budgets={}))
    with (
        Session(migrated_database) as db,
        pytest.raises(DBAPIError, match="sealed experiment is immutable: agents INSERT"),
    ):
        other = catalog.other_agent
        db.add(
            m.ExperimentAgent(
                experiment_id=experiment_id,
                agent_id=uuid.UUID(other["id"]),
                agent_version=other["version"],
            )
        )
        db.flush()


def test_seal_is_not_repeatable(client: TestClient, catalog: Catalog) -> None:
    sealed = _sealed(client, catalog)
    again = client.post(f"/experiments/{sealed['id']}/seal")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "invalid_transition"


@pytest.mark.parametrize("overrides", [{"agents": []}, {"benchmark": None}])
def test_seal_requires_resolved_references(
    client: TestClient, catalog: Catalog, overrides: dict[str, Any]
) -> None:
    created = client.post("/experiments", json=_draft(catalog, **overrides), headers=_key())
    response = client.post(f"/experiments/{created.json()['id']}/seal")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_reference"
    after = client.get(f"/experiments/{created.json()['id']}").json()
    assert (after["status"], after["manifest_hash"]) == ("draft", None)


def test_manifest_of_draft_is_unavailable(client: TestClient, catalog: Catalog) -> None:
    created = client.post("/experiments", json=_draft(catalog), headers=_key()).json()
    response = client.get(f"/experiments/{created['id']}/manifest")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "experiment_not_sealed"


# --- Celdas (runs) -----------------------------------------------------------------------


def _run_body(catalog: Catalog, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "scenario": catalog.scenarios[0],
        "agent": catalog.agent,
        "repetition": 2,
        "mode": "live",
    }
    return body | overrides


def test_runs_require_sealed_experiment(client: TestClient, catalog: Catalog) -> None:
    created = client.post("/experiments", json=_draft(catalog), headers=_key()).json()
    response = client.post(
        f"/experiments/{created['id']}/runs", json=_run_body(catalog), headers=_key()
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "experiment_not_accepting_runs"


def test_run_submission_is_idempotent(
    client: TestClient, catalog: Catalog, migrated_database: Engine
) -> None:
    sealed = _sealed(client, catalog)
    url, headers = f"/experiments/{sealed['id']}/runs", _key()
    first = client.post(url, json=_run_body(catalog), headers=headers)
    replay = client.post(url, json=_run_body(catalog), headers=headers)
    assert first.status_code == replay.status_code == 202
    run = first.json()
    assert (run["status"], run["seed"], run["mode"]) == ("queued", 23, "live")
    assert replay.json() == run
    assert replay.headers["Idempotent-Replayed"] == "true"

    conflicting = client.post(url, json=_run_body(catalog, repetition=1), headers=headers)
    assert conflicting.status_code == 409
    assert conflicting.json()["error"]["code"] == "idempotency_key_reused"

    same_cell = client.post(url, json=_run_body(catalog), headers=_key())
    assert same_cell.status_code == 409
    assert same_cell.json()["error"] == {
        "code": "run_cell_exists",
        "message": "la celda experimental ya existe",
        "run_id": run["id"],
    }

    experiment = m.Run.experiment_id == uuid.UUID(sealed["id"])
    assert _count(migrated_database, m.Run, experiment) == 1
    assert [r["id"] for r in client.get(url).json()] == [run["id"]]
    assert client.get(f"/runs/{run['id']}").json() == run


@pytest.mark.parametrize(
    "field",
    ["agent_not_in_experiment", "scenario_not_in_dataset", "repetition_out_of_plan"],
)
def test_run_references_are_validated_before_enqueue(
    client: TestClient, catalog: Catalog, migrated_database: Engine, field: str
) -> None:
    sealed = _sealed(client, catalog)
    options: dict[str, dict[str, Any]] = {
        "agent_not_in_experiment": {"agent": catalog.other_agent},
        "scenario_not_in_dataset": {"scenario": catalog.foreign_scenario},
        "repetition_out_of_plan": {"repetition": 3},
    }
    overrides = options[field]
    response = client.post(
        f"/experiments/{sealed['id']}/runs", json=_run_body(catalog, **overrides), headers=_key()
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_reference"
    experiment = m.Run.experiment_id == uuid.UUID(sealed["id"])
    assert _count(migrated_database, m.Run, experiment) == 0


def test_unknown_resources_return_not_found(client: TestClient) -> None:
    missing = uuid.uuid4()
    assert client.get(f"/experiments/{missing}").status_code == 404
    assert client.get(f"/runs/{missing}").status_code == 404
    assert client.post(f"/experiments/{missing}/seal").status_code == 404


# --- Versiones publicadas ----------------------------------------------------------------


def test_published_configurations_are_immutable(
    catalog: Catalog, migrated_database: Engine
) -> None:
    agent_id = uuid.UUID(catalog.agent["id"])
    with (
        Session(migrated_database) as db,
        pytest.raises(DBAPIError, match="published rows are immutable on agent_configurations"),
    ):
        db.execute(
            update(m.AgentConfiguration)
            .where(m.AgentConfiguration.id == agent_id)
            .values(prompt_hash=f.digest())
        )
    with (
        Session(migrated_database) as db,
        pytest.raises(DBAPIError, match="published rows are immutable on agent_roles: DELETE"),
    ):
        role = db.scalars(select(m.AgentRole).where(m.AgentRole.agent_id == agent_id)).one()
        db.delete(role)
        db.flush()


def test_published_configuration_cannot_gain_tools_later(
    catalog: Catalog, migrated_database: Engine
) -> None:
    with (
        Session(migrated_database) as db,
        pytest.raises(DBAPIError, match="cannot extend published agent_configurations"),
    ):
        tool = m.ToolDefinition(
            id=uuid.uuid4(),
            version="1.0.0",
            name=f"extra-{uuid.uuid4().hex[:8]}",
            input_schema={},
            output_schema={},
            effect_class="read_only",
            timeout_ms=1000,
            content_hash=f.digest(),
        )
        db.add(tool)
        db.flush()
        db.add(
            m.AgentTool(
                agent_id=uuid.UUID(catalog.agent["id"]),
                agent_version=catalog.agent["version"],
                tool_id=tool.id,
                tool_version=tool.version,
            )
        )
        db.flush()


def test_published_dataset_cannot_gain_scenarios_later(
    catalog: Catalog, migrated_database: Engine
) -> None:
    with Session(migrated_database) as db:
        bench = db.get(m.Benchmark, (uuid.UUID(catalog.benchmark["id"]), "1.0.0"))
        assert bench is not None
        foreign = catalog.foreign_scenario
        db.add(
            m.DatasetScenario(
                dataset_id=bench.dataset_id,
                dataset_version=bench.dataset_version,
                scenario_id=uuid.UUID(foreign["id"]),
                scenario_version=foreign["version"],
                position=99,
            )
        )
        with pytest.raises(DBAPIError, match="cannot extend published datasets"):
            db.flush()
