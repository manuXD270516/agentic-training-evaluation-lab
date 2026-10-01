"""Publicación de ModelConfiguration y AgentConfiguration: hash, idempotencia e inmutabilidad."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from evallab.api.app import create_app
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
