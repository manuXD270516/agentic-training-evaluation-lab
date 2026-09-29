from fastapi.testclient import TestClient
from pydantic import SecretStr

from evallab.api.app import create_app
from evallab.settings import DatabaseSettings

UNREACHABLE_DB = DatabaseSettings(
    host="127.0.0.1", port=1, password=SecretStr("not-a-real-secret"), connect_timeout_s=1
)


def test_liveness_does_not_require_database() -> None:
    client = TestClient(create_app(UNREACHABLE_DB))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "api", "version": "0.1.0"}


def test_readiness_reports_unreachable_database_without_leaking_secrets() -> None:
    client = TestClient(create_app(UNREACHABLE_DB))
    response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["database"]["status"] == "error"
    assert body["database"]["error_class"] in {"OperationalError", "ConnectionTimeout"}
    assert "not-a-real-secret" not in response.text
