"""Demo de sólo lectura (13.2): sin escritura pública, oráculo con token, artefactos limpios."""

import json
import re
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from evallab.api.app import access_decision, create_app
from evallab.benchmarks.pilot import PILOT
from evallab.benchmarks.suite import publish_suite
from evallab.settings import AccessSettings

pytestmark = pytest.mark.integration

TOKEN = "demo-admin-token-0123456789abcdef"
REPO = Path(__file__).resolve().parents[3]


def _client(engine: Engine, access: AccessSettings) -> TestClient:
    return TestClient(create_app(engine=engine, access=access))


def _scenario_path(engine: Engine) -> str:
    with Session(engine, expire_on_commit=False) as db, db.begin():
        scenario = publish_suite(db, PILOT).scenarios[0]
    return f"/scenarios/{scenario.id}/versions/{scenario.version}"


def test_read_only_demo_rejects_writes_and_hides_oracles(fresh_database: Engine) -> None:
    path = _scenario_path(fresh_database)
    access = AccessSettings(read_only=True, admin_token=SecretStr(TOKEN))
    with _client(fresh_database, access) as client:
        assert client.get(path).status_code == 200
        assert "expected" not in client.get(path).json()
        assert client.get("/experiments").status_code == 200
        oracle = client.get(f"{path}/oracle")
        assert oracle.status_code == 401
        assert oracle.headers["WWW-Authenticate"] == "Bearer"
        assert TOKEN not in oracle.text
        body = {"hypothesis": "x", "repetitions": 1, "seeds": [11]}
        headers = {"Idempotency-Key": uuid.uuid4().hex, "Authorization": f"Bearer {TOKEN}"}
        # Incluso con token, la demo no escribe.
        write = client.post("/experiments", json=body, headers=headers)
        assert write.status_code == 403 and write.json()["error"]["code"] == "read_only"
        for method in ("put", "patch", "delete"):
            assert client.request(method, "/experiments").status_code == 403
        assert client.get(f"{path}/oracle", headers=headers).status_code == 200

    without_token = AccessSettings(read_only=True)
    with _client(fresh_database, without_token) as client:
        response = client.get(f"{path}/oracle")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "private_operation"


def test_admin_token_protects_private_operations(fresh_database: Engine) -> None:
    path = _scenario_path(fresh_database)
    with _client(fresh_database, AccessSettings(admin_token=SecretStr(TOKEN))) as client:
        body = {"hypothesis": "x", "repetitions": 1, "seeds": [11]}
        key = {"Idempotency-Key": uuid.uuid4().hex}
        assert client.post("/experiments", json=body, headers=key).status_code == 401
        wrong = {**key, "Authorization": "Bearer " + "x" * len(TOKEN)}
        assert client.post("/experiments", json=body, headers=wrong).status_code == 401
        right = {**key, "Authorization": f"Bearer {TOKEN}"}
        assert client.post("/experiments", json=body, headers=right).status_code == 201
        assert client.get(f"{path}/oracle").status_code == 401
        assert client.get(path).status_code == 200


def test_access_rules_and_token_strength() -> None:
    open_access = AccessSettings()
    assert access_decision(open_access, "POST", "/experiments", None) is None
    demo = AccessSettings(read_only=True)
    assert access_decision(demo, "GET", "/runs/x/trace", None) is None
    assert access_decision(demo, "GET", "/scenarios/a/versions/1/oracle/", None) is not None
    with pytest.raises(ValidationError):
        AccessSettings(admin_token=SecretStr("short"))
    assert AccessSettings(admin_token="").admin_token is None


SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"(?i)api[_-]?key\"?\s*[:=]\s*\"[^\"]{8,}"),
    re.compile(r"(?i)postgres(ql)?://[^\s\"]*:[^\s\"@]+@"),
    re.compile(r"(?i)bearer [A-Za-z0-9._-]{20,}"),
)


def _json_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _json_keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _json_keys(v)}
    return set()


def test_published_artifacts_are_sanitized() -> None:
    files = sorted((REPO / "results").rglob("*.*"))
    assert files
    for path in files:
        text = path.read_text(encoding="utf-8")
        for pattern in SECRET_PATTERNS:
            assert not pattern.search(text), (path, pattern.pattern)
        if path.suffix == ".json":
            keys = _json_keys(json.loads(text))
            # Ningún oráculo privado: ni checks esperados ni qrels ni family/split.
            assert not keys & {"expected", "qrels", "relevant", "family_id", "oracle_ref"}, path
