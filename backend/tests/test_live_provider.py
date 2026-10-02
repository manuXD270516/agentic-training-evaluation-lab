"""Adaptador live opcional contra un servidor local que imita chat completions (sin red externa)."""

import json
import threading
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from pydantic import SecretStr

from evallab.runner.live import (
    LIVE_PROVIDER,
    ModelGatewaySettings,
    OpenAICompatibleProvider,
    live_providers,
)
from evallab.runner.models import (
    ModelProviderError,
    ModelRequest,
    ModelSnapshot,
    ToolSpec,
)
from evallab.runner.providers import FIXTURE_PROVIDER, default_providers

API_KEY = "sk-test-local-stub-not-a-real-key"


class Stub:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self.status = 200
        self.body: Any = {}


@pytest.fixture
def stub() -> Iterator[tuple[Stub, str]]:
    state = Stub()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            state.requests.append(json.loads(self.rfile.read(length)))
            state.headers.append(dict(self.headers.items()))
            raw = state.body if isinstance(state.body, bytes) else json.dumps(state.body).encode()
            self.send_response(state.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args: Any) -> None:
            del args

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()


def _model(seed_support: str = "supported") -> ModelSnapshot:
    return ModelSnapshot(
        role="executor",
        id=uuid.uuid4(),
        version="1.0.0",
        content_hash="a" * 64,
        provider=LIVE_PROVIDER,
        requested_model="stub-model",
        resolved_revision=None,
        temperature="0",
        max_tokens=128,
        seed_support=seed_support,
    )


REQUEST = ModelRequest(
    role="executor",
    messages=(
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c1", "name": "calculator", "arguments": {"a": 1}}],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "calculator", "content": {"r": 2}},
    ),
    tools=(ToolSpec(name="calculator", input_schema={"type": "object"}),),
    seed=11,
)


def test_request_mapping_and_response_parsing(stub: tuple[Stub, str]) -> None:
    state, url = stub
    state.body = {
        "id": "chatcmpl-1",
        "model": "stub-model-2026-10-01",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c2",
                            "type": "function",
                            "function": {"name": "calculator", "arguments": '{"a": 2}'},
                        }
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "prompt_tokens_details": {"cached_tokens": 100},
        },
    }
    response = OpenAICompatibleProvider(url, API_KEY).complete(REQUEST, _model())
    sent = state.requests[0]
    assert sent["model"] == "stub-model" and sent["seed"] == 11 and sent["max_tokens"] == 128
    assert sent["tools"][0]["function"]["name"] == "calculator"
    assistant = sent["messages"][2]
    assert assistant["tool_calls"][0]["function"]["arguments"] == '{"a": 1}'
    assert sent["messages"][3]["role"] == "tool" and sent["messages"][3]["tool_call_id"] == "c1"
    assert state.headers[0]["Authorization"] == f"Bearer {API_KEY}"
    assert response.tool_calls[0].arguments == {"a": 2}
    assert (response.usage.input_tokens, response.usage.cached_input_tokens) == (120, 100)
    assert response.usage.source == "observed"
    assert response.resolved_model == "stub-model-2026-10-01"
    assert response.provider_request_id == "chatcmpl-1"

    OpenAICompatibleProvider(url, API_KEY).complete(REQUEST, _model("unsupported"))
    assert "seed" not in state.requests[1]


def test_missing_usage_is_unknown(stub: tuple[Stub, str]) -> None:
    state, url = stub
    state.body = {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}
    response = OpenAICompatibleProvider(url, API_KEY).complete(REQUEST, _model())
    assert response.usage.source == "unknown" and response.usage.total is None
    assert response.resolved_model is None


@pytest.mark.parametrize(
    ("status", "body", "kind", "retriable"),
    [
        (429, {}, "rate_limited", True),
        (504, {}, "timeout", True),
        (503, {}, "provider_error", True),
        (400, {"error": {"code": "context_length_exceeded"}}, "context_length", False),
        (401, {}, "provider_error", False),
        (200, b"no-json", "invalid_response", False),
    ],
)
def test_http_errors_are_typed(
    stub: tuple[Stub, str], status: int, body: Any, kind: str, retriable: bool
) -> None:
    state, url = stub
    state.status, state.body = status, body
    with pytest.raises(ModelProviderError) as error:
        OpenAICompatibleProvider(url, API_KEY).complete(REQUEST, _model())
    assert (error.value.kind, error.value.retriable) == (kind, retriable)


def test_live_provider_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LIVE_ENABLED", "BASE_URL", "API_KEY"):
        monkeypatch.delenv(f"MODEL_GATEWAY_{name}", raising=False)
    assert live_providers() == {}
    assert set(default_providers(lambda _: None)) == {FIXTURE_PROVIDER}
    half = ModelGatewaySettings(live_enabled=True, base_url="http://127.0.0.1:9")
    assert live_providers(half) == {}
    ready = ModelGatewaySettings(
        live_enabled=True, base_url="http://127.0.0.1:9", api_key=SecretStr(API_KEY)
    )
    assert set(live_providers(ready)) == {LIVE_PROVIDER}


def test_live_commands_refuse_without_enabled_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from evallab.benchmarks.cli import main as benchmark_cli
    from evallab.evaluation.calibration_cli import main as calibration_cli

    for name in ("MODEL_GATEWAY_LIVE_ENABLED", "MODEL_GATEWAY_BASE_URL", "MODEL_GATEWAY_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit, match="proveedor live deshabilitado"):
        calibration_cli(["votes", "--model", "m", "--out", "unused.json"])
    with pytest.raises(SystemExit, match="proveedor live deshabilitado"):
        benchmark_cli(["run-live", "pilot", "--model", "m", "--out", "unused"])


def test_judge_votes_use_the_live_provider_and_never_the_annotations(
    stub: tuple[Stub, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from evallab.evaluation.calibration_cli import main as calibration_cli

    state, url = stub
    state.body = {
        "id": "r",
        "model": "m",
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {"abstain": True, "rating": None, "evidence": [], "rationale": "x"}
                    )
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    monkeypatch.setenv("MODEL_GATEWAY_LIVE_ENABLED", "true")
    monkeypatch.setenv("MODEL_GATEWAY_BASE_URL", url)
    monkeypatch.setenv("MODEL_GATEWAY_API_KEY", API_KEY)
    out = tmp_path / "votes.json"
    assert calibration_cli(["votes", "--model", "m", "--out", str(out)]) == 0
    document = json.loads(out.read_text("utf-8"))
    assert len(document["votes"]) == 32 and set(document["votes"].values()) == {None}
    assert len(state.requests) == 32
    assert API_KEY not in out.read_text("utf-8")
    assert all("anotador" not in json.dumps(r) for r in state.requests)
