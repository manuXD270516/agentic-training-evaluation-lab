"""Redacción de secretos antes de calcular digest y persistir (trace-format.md, Privacy)."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from evallab.canonical import canonical_digest
from evallab.runner.redaction import MARKER, is_sensitive_key, redact, redaction_metadata
from evallab.runner.sink import MemoryTraceSink

API_KEY = "sk-live-0123456789abcdefXYZ"


@pytest.mark.parametrize(
    "key",
    ["password", "api_key", "API-Key", "Authorization", "client_secret", "db_password", "token"],
)
def test_sensitive_keys(key: str) -> None:
    assert is_sensitive_key(key)


@pytest.mark.parametrize(
    "key", ["max_tokens", "tokens", "fencing_token_count", "total", "secretary", "call_id"]
)
def test_ordinary_keys_are_kept(key: str) -> None:
    assert not is_sensitive_key(key)


def test_nested_keys_and_values_are_replaced_with_pointers() -> None:
    value = {
        "result": {"total": 42, "api_key": API_KEY, "items": [{"password": "hunter2"}]},
        "note": f"usa Bearer {API_KEY}",
        "a/b": {"secret": "x"},
        "max_tokens": 10,
    }
    redacted, fields = redact(value)
    assert redacted["result"] == {"total": 42, "api_key": MARKER, "items": [{"password": MARKER}]}
    assert redacted["note"] == MARKER
    assert redacted["a/b"] == {"secret": MARKER}
    assert redacted["max_tokens"] == 10
    assert {(f["pointer"], f["reason"]) for f in fields} == {
        ("/result/api_key", "sensitive_key"),
        ("/result/items/0/password", "sensitive_key"),
        ("/note", "secret_pattern"),
        ("/a~1b/secret", "sensitive_key"),
    }
    assert API_KEY not in repr(redacted) and "hunter2" not in repr(fields)


def test_no_redaction_has_empty_metadata() -> None:
    redacted, fields = redact({"total": 42, "token": None})
    assert redacted == {"total": 42, "token": None}
    assert fields == []
    assert redaction_metadata(fields, replayable=False) == {}


def _sink() -> MemoryTraceSink:
    return MemoryTraceSink(
        started_at=datetime.now(UTC),
        schema_version="1.0.0",
        run_id=uuid.uuid4(),
        attempt_id=uuid.uuid4(),
    )


def test_sink_redacts_before_digest_and_marks_replay_unavailable() -> None:
    sink = _sink()
    payload: dict[str, Any] = {"call_id": "c1", "result": {"total": 42, "api_key": API_KEY}}
    event = sink.append("tool.completed", "tool", payload)
    assert event.payload["result"]["api_key"] == MARKER
    assert event.payload_digest == canonical_digest(event.payload)
    metadata: dict[str, Any] = event.redaction_metadata
    assert metadata["replay"] == "unavailable"
    assert metadata["fields"] == [{"pointer": "/result/api_key", "reason": "sensitive_key"}]
    assert payload["result"]["api_key"] == API_KEY


def test_redacted_output_keeps_replay_of_calls_available() -> None:
    event = _sink().append("run.completed", "harness", {"output": {"token": "abc"}})
    assert event.redaction_metadata["replay"] == "available"


def test_resent_event_with_same_secret_is_idempotent() -> None:
    sink = _sink()
    event_id = uuid.uuid4()
    payload = {"call_id": "c1", "result": {"api_key": API_KEY}}
    first = sink.append("tool.completed", "tool", payload, event_id=event_id)
    again = sink.append("tool.completed", "tool", payload, event_id=event_id)
    assert again is first
    assert len(sink.events) == 1
