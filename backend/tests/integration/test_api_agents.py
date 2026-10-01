"""Publicación de ModelConfiguration y AgentConfiguration: hash, idempotencia e inmutabilidad."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from evallab.api.app import create_app
from tests.integration import factories as f
from tests.integration.test_api_catalog import _fixture, _key, _post, _tool

pytestmark = pytest.mark.integration


@pytest.fixture
def client(fresh_database: Engine) -> Iterator[TestClient]:
    # Base propia: test_api_catalog cuenta las fixtures de la base compartida.
    with TestClient(create_app(engine=fresh_database)) as test_client:
        yield test_client


def _model(client: TestClient, **overrides: Any) -> dict[str, Any]:
    body = {
        "version": "1.0.0",
        "provider": "scripted",
        "requested_model": f"fake-{uuid.uuid4().hex[:8]}",
        "temperature": 0,
        "seed_support": "unsupported",
        "max_tokens": 512,
        **overrides,
    }
    response = _post(client, "/model-configurations", body)
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


def _agent_body(tool: dict[str, str], model: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "version": "1.0.0",
        "pattern": "scripted" if model is None else "react",
        "pattern_version": "1.0.0",
        "pattern_parameters": {"note": uuid.uuid4().hex},
        "tools": [tool],
    }
    if model is not None:
        body["roles"] = [
            {"role": "executor", "model": {"id": model["id"], "version": model["version"]}}
        ]
    return body


def test_model_configuration_declares_unknown_revision(client: TestClient) -> None:
    model = _model(client)
    assert model["resolved_revision"] is None
    assert model["temperature"] == "0"
    again = client.get(f"/model-configurations/{model['id']}/versions/1.0.0").json()
    assert again["content_hash"] == model["content_hash"]


def test_agent_configuration_snapshot_and_idempotency(client: TestClient) -> None:
    tool = _tool(client, _fixture(client, f"fx-{uuid.uuid4().hex[:6]}"))
    model = _model(client)
    body = _agent_body(tool, model)
    headers = _key()
    created = _post(client, "/agent-configurations", body, headers)
    assert created.status_code == 201, created.text
    agent = created.json()
    assert agent["roles"] == [
        {"role": "executor", "model": {"id": model["id"], "version": "1.0.0"}}
    ]
    assert [t["content_hash"] for t in agent["tools"]] == [tool["content_hash"]]

    replay = _post(client, "/agent-configurations", body, headers)
    assert replay.status_code == 201
    assert replay.headers["idempotent-replayed"] == "true"
    assert replay.json()["content_hash"] == agent["content_hash"]

    # Misma identidad con otro prompt/parámetros: versión publicada inmutable.
    changed = {**body, "id": agent["id"], "pattern_parameters": {"note": "otro"}}
    conflict = _post(client, "/agent-configurations", changed)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "version_exists"

    fetched = client.get(f"/agent-configurations/{agent['id']}/versions/1.0.0").json()
    assert fetched["content_hash"] == agent["content_hash"]


def test_agent_configuration_rejects_missing_roles_and_refs(client: TestClient) -> None:
    tool = _tool(client, _fixture(client, f"fx-{uuid.uuid4().hex[:6]}"))
    no_executor = {**_agent_body(tool), "pattern": "react"}
    assert _post(client, "/agent-configurations", no_executor).status_code == 422

    ghost = {"id": str(uuid.uuid4()), "version": "1.0.0"}
    body = _agent_body(tool, {**ghost})
    response = _post(client, "/agent-configurations", body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_reference"

    wrong_hash = _agent_body({**tool, "content_hash": "0" * 64})
    assert _post(client, "/agent-configurations", wrong_hash).status_code == 422


PRICE_BODY = {
    "provider": "fixture",
    "model": "fake-model",
    "input_per_mtok": "3",
    "output_per_mtok": "15.00",
    "effective_date": "2026-10-01",
    "source": "tarifa sintética de prueba, no es un precio real",
    "synthetic": True,
}


def test_price_snapshot_is_content_addressed(client: TestClient) -> None:
    first = _post(client, "/price-snapshots", PRICE_BODY)
    second = _post(client, "/price-snapshots", {**PRICE_BODY, "output_per_mtok": "15"})
    assert first.status_code == second.status_code == 201, first.text
    # 15.00 y 15 son el mismo decimal: mismo documento canónico y mismo hash.
    assert first.json()["content_hash"] == second.json()["content_hash"]
    assert first.json()["output_per_mtok"] == "15"
    assert _post(client, "/price-snapshots", {**PRICE_BODY, "currency": "usd"}).status_code == 422
    assert (
        _post(client, "/price-snapshots", {**PRICE_BODY, "input_per_mtok": "-1"}).status_code == 422
    )
    ghost = _post(
        client, "/model-configurations", {**_model_body(), "price_snapshot_ref": "0" * 64}
    )
    assert ghost.status_code == 422
    assert ghost.json()["error"]["code"] == "invalid_reference"


def _model_body(**overrides: Any) -> dict[str, Any]:
    return {
        "version": "1.0.0",
        "provider": "fixture",
        "requested_model": "f" * 64,
        "seed_support": "unsupported",
        "max_tokens": 256,
        **overrides,
    }


def test_monetary_limit_requires_price_on_every_model(
    client: TestClient, fresh_database: Engine
) -> None:
    tool = _tool(client, _fixture(client, f"fx-{uuid.uuid4().hex[:6]}"))
    price = _post(client, "/price-snapshots", PRICE_BODY).json()["content_hash"]
    unpriced = _model(client)
    priced = _model(client, price_snapshot_ref=price)
    with Session(fresh_database) as db, db.begin():
        bench = f.benchmark(db)
        benchmark = {"id": str(bench.id), "version": bench.version}

    def experiment(model: dict[str, Any], budgets: dict[str, Any]) -> str:
        agent = _post(client, "/agent-configurations", _agent_body(tool, model)).json()
        body = {
            "hypothesis": "límite monetario",
            "benchmark": benchmark,
            "agents": [{"id": agent["id"], "version": agent["version"]}],
            "budgets": budgets,
            "repetitions": 1,
            "seeds": [11],
        }
        created = _post(client, "/experiments", body)
        assert created.status_code == 201, created.text
        return str(created.json()["id"])

    rejected = client.post(f"/experiments/{experiment(unpriced, {'max_cost_usd': '0.5'})}/seal")
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "price_required_for_monetary_limit"
    # Sin límite monetario se admite: el coste quedará unknown.
    assert (
        client.post(f"/experiments/{experiment(unpriced, {'max_steps': 4})}/seal").status_code
        == 200
    )
    sealed_id = experiment(priced, {"max_cost_usd": "0.5"})
    assert client.post(f"/experiments/{sealed_id}/seal").status_code == 200
    manifest = client.get(f"/experiments/{sealed_id}/manifest").json()["manifest"]
    assert manifest["agents"][0]["roles"][0]["model"]["price_snapshot_ref"] == price
