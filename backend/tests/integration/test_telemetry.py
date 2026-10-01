"""OpenTelemetry (5.2): correlación run/tool/evaluador y evidencia completa con collector caído."""

import json
import socket
import uuid
from collections.abc import Iterator, Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from evallab import telemetry
from evallab.api.app import create_app
from evallab.db import models as m
from evallab.domain.lifecycle import RunStatus
from evallab.runner.sink import trace_digest
from evallab.services.evaluations import evaluate_run
from evallab.services.execution import execute_run
from evallab.services.traces import export_trace, verify_export
from tests.integration.test_evaluations import ORACLE, REQUIRED
from tests.integration.test_execution import _world
from tests.integration.test_traces import API_KEY, SECRET_RESULT

pytestmark = pytest.mark.integration


class RecordingExporter(SpanExporter):
    """Delega en otro exportador y registra el resultado de cada exportación."""

    def __init__(self, inner: SpanExporter) -> None:
        self.inner = inner
        self.results: list[SpanExportResult] = []

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        result = self.inner.export(spans)
        self.results.append(result)
        return result

    def shutdown(self) -> None:
        self.inner.shutdown()


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


@pytest.fixture
def memory() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    previous = telemetry.configure(provider)
    try:
        yield exporter
    finally:
        telemetry.configure(previous)
        provider.shutdown()


@pytest.fixture
def collector_down() -> Iterator[RecordingExporter]:
    endpoint = f"http://127.0.0.1:{_closed_port()}/v1/traces"
    exporter = RecordingExporter(OTLPSpanExporter(endpoint=endpoint, timeout=0.5))
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    previous = telemetry.configure(provider)
    try:
        yield exporter
    finally:
        telemetry.configure(previous)
        provider.shutdown()


def _execute(engine: Engine, **world: Any) -> uuid.UUID:
    with Session(engine) as db, db.begin():
        built = _world(db, oracle=ORACLE, evaluation=REQUIRED, **world)
        run = execute_run(db, built.run_ids[0])
        assert run.status == RunStatus.COMPLETED
        return built.run_ids[0]


def _events(engine: Engine, run_id: uuid.UUID) -> list[m.TraceEvent]:
    with Session(engine) as db:
        return list(
            db.scalars(
                select(m.TraceEvent)
                .where(m.TraceEvent.run_id == run_id)
                .order_by(m.TraceEvent.sequence)
            )
        )


def _event_id(events: list[m.TraceEvent], kind: str) -> uuid.UUID:
    return next(e.event_id for e in events if e.type == kind)


def _span_id(span: ReadableSpan) -> str:
    assert span.context is not None
    return format(span.context.span_id, "016x")


def _trace_id(span: ReadableSpan) -> str:
    assert span.context is not None
    return format(span.context.trace_id, "032x")


def test_spans_correlate_run_tool_and_evaluator(
    migrated_database: Engine, memory: InMemorySpanExporter
) -> None:
    run_id = _execute(migrated_database, calculator_result=SECRET_RESULT)
    with Session(migrated_database) as db, db.begin():
        evaluation = evaluate_run(db, run_id)
        evaluation_id = str(evaluation.id)

    spans = {span.name: span for span in memory.get_finished_spans()}
    assert {"worker.execute", "agent.execute", "tool.invoke", "worker.persist"} <= set(spans)
    assert "evaluation.evaluate" in spans
    for name in ("worker.execute", "agent.execute", "tool.invoke", "worker.persist"):
        attributes = spans[name].attributes or {}
        assert attributes["evallab.run_id"] == str(run_id)
    tool = spans["tool.invoke"]
    agent = spans["agent.execute"]
    assert tool.parent is not None and tool.parent.span_id == agent.context.span_id
    assert (tool.attributes or {})["evallab.tool.status"] == "completed"
    evaluator = spans["evaluation.evaluate"].attributes or {}
    assert evaluator["evallab.evaluation_id"] == evaluation_id
    assert evaluator["evallab.run_id"] == str(run_id)

    events = _events(migrated_database, run_id)
    assert all(e.otel_trace_id == _trace_id(agent) for e in events)
    by_type = {e.type: e for e in events}
    for kind in ("tool.requested", "tool.validated", "tool.completed"):
        assert by_type[kind].otel_span_id == _span_id(tool)
    for kind in ("run.started", "step.started", "run.completed"):
        assert by_type[kind].otel_span_id == _span_id(agent)

    # Los spans sólo llevan ids y nombres: ni argumentos, ni resultados, ni secretos.
    blob = json.dumps([dict(s.attributes or {}) for s in memory.get_finished_spans()])
    assert API_KEY not in blob
    assert "total" not in blob


def test_trace_ids_do_not_enter_the_sealed_digest(
    migrated_database: Engine, memory: InMemorySpanExporter
) -> None:
    run_id = _execute(migrated_database)
    events = _events(migrated_database, run_id)
    assert all(e.otel_span_id is not None for e in events)
    recomputed = trace_digest((e.event_id, e.sequence, e.type, e.payload_digest) for e in events)
    with Session(migrated_database) as db:
        trace = db.scalars(select(m.Trace).where(m.Trace.run_id == run_id)).one()
        assert trace.digest == recomputed
        export = export_trace(db, run_id)
    lines = [json.loads(line) for line in export.jsonl.splitlines()]
    assert [line["otel_span_id"] for line in lines] == [e.otel_span_id for e in events]
    assert verify_export(export.manifest, export.jsonl) == []


def test_collector_down_keeps_complete_evidence(
    migrated_database: Engine, collector_down: RecordingExporter
) -> None:
    run_id = _execute(migrated_database)
    with Session(migrated_database) as db, db.begin():
        evaluation = evaluate_run(db, run_id)
        assert evaluation.status == "completed"
        assert evaluation.report is not None
        assert evaluation.report["task_success"] == "pass"

    # El collector no aceptó ningún span: cada exportación falló sin interrumpir el run.
    assert collector_down.results
    assert set(collector_down.results) == {SpanExportResult.FAILURE}

    events = _events(migrated_database, run_id)
    with Session(migrated_database) as db:
        trace = db.scalars(select(m.Trace).where(m.Trace.run_id == run_id)).one()
        run = db.get(m.Run, run_id)
        assert run is not None and run.result is not None
        export = export_trace(db, run_id)
    assert trace.completeness == "complete"
    assert trace.event_count == len(events) == 9
    assert [e.type for e in events] == [
        "run.started",
        "step.started",
        "tool.requested",
        "tool.validated",
        "tool.completed",
        "step.completed",
        "step.started",
        "step.completed",
        "run.completed",
    ]
    assert run.result["evidence_refs"][0]["event_id"] == str(_event_id(events, "tool.completed"))
    assert verify_export(export.manifest, export.jsonl) == []


def test_api_spans_carry_route_and_ids(
    migrated_database: Engine, memory: InMemorySpanExporter
) -> None:
    run_id = _execute(migrated_database)
    memory.clear()
    with TestClient(create_app(engine=migrated_database)) as client:
        assert client.get(f"/runs/{run_id}/trace").status_code == 200
        assert client.get(f"/runs/{uuid.uuid4()}").status_code == 404
        assert client.get("/health").status_code == 200
    assert all("/health" not in s.name for s in memory.get_finished_spans())
    spans = [s for s in memory.get_finished_spans() if s.name.startswith("GET ")]
    assert [s.name for s in spans] == ["GET /runs/{run_id}/trace", "GET /runs/{run_id}"]
    first = spans[0].attributes or {}
    assert first["evallab.run_id"] == str(run_id)
    assert first["http.response.status_code"] == 200
    assert (spans[1].attributes or {})["http.response.status_code"] == 404


def test_without_endpoint_telemetry_is_noop(migrated_database: Engine) -> None:
    assert (
        telemetry.configure_from_env(
            "evallab-test", telemetry.TelemetrySettings(exporter_otlp_endpoint=None)
        )
        is False
    )
    run_id = _execute(migrated_database)
    events = _events(migrated_database, run_id)
    assert {(e.otel_trace_id, e.otel_span_id) for e in events} == {(None, None)}
