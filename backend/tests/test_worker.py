import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from evallab.settings import DatabaseSettings, SandboxPolicy, WorkerSettings
from evallab.worker.app import create_app

UNREACHABLE_DB = DatabaseSettings(host="127.0.0.1", port=1, connect_timeout_s=1)


def test_sandbox_policy_denies_network_and_host_by_default() -> None:
    policy = SandboxPolicy()
    assert policy.network == "deny"
    assert policy.host_tools is False


def test_sandbox_policy_accepts_explicit_safe_values_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_AGENT_NETWORK", "deny")
    monkeypatch.setenv("WORKER_AGENT_HOST_TOOLS", "false")
    policy = SandboxPolicy()
    assert (policy.network, policy.host_tools) == ("deny", False)


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("WORKER_AGENT_NETWORK", "allow"),
        ("WORKER_AGENT_HOST_TOOLS", "true"),
        ("WORKER_AGENT_HOST_TOOLS", "1"),
    ],
)
def test_sandbox_policy_rejects_relaxation(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValidationError):
        SandboxPolicy()


def test_worker_health_reports_policy_and_heartbeat() -> None:
    app = create_app(WorkerSettings(heartbeat_interval_s=0.01), UNREACHABLE_DB)
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["service"] == "worker"
    assert body["queue"] == "not_implemented"
    assert body["sandbox"] == {"network": "deny", "host_tools": False}
    assert body["last_heartbeat"] is not None


def test_worker_readiness_fails_without_database() -> None:
    with TestClient(create_app(db_settings=UNREACHABLE_DB)) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["database"]["status"] == "error"
