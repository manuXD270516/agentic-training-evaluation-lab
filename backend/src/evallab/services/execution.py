"""Ejecución de celdas queued: runner scripted, traza en memoria y sellado."""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from evallab.db import models as m
from evallab.domain.lifecycle import (
    EXPERIMENT_LIFECYCLE,
    RUN_LIFECYCLE,
    ExperimentStatus,
    RunStatus,
)
from evallab.domain.vocabulary import TRACE_SCHEMA_VERSION
from evallab.runner.agent import execute_agent
from evallab.runner.contracts import AgentSnapshot, AllowedTool, RunContext, RunResult, Usage
from evallab.runner.errors import RunnerError
from evallab.runner.gateways import DeniedModelGateway, DeniedToolGateway
from evallab.runner.sink import MemoryTraceSink, mark_incomplete
from evallab.services.catalog import scenario_public_view
from evallab.services.errors import NotFoundError
from evallab.settings import SandboxPolicy

TERMINAL_EVENT_TYPES = frozenset(
    {
        "run.budget_exceeded",
        "run.cancelled",
        "run.completed",
        "run.failed",
        "run.timed_out",
    }
)


def _agent_snapshot(db: Session, cfg: m.AgentConfiguration) -> AgentSnapshot:
    roles = tuple(
        db.scalars(
            select(m.AgentRole.role)
            .where(m.AgentRole.agent_id == cfg.id, m.AgentRole.agent_version == cfg.version)
            .order_by(m.AgentRole.role)
        )
    )
    params = cfg.pattern_parameters if isinstance(cfg.pattern_parameters, dict) else {}
    return AgentSnapshot(
        id=cfg.id,
        version=cfg.version,
        pattern=cfg.pattern,
        pattern_version=cfg.pattern_version,
        content_hash=cfg.content_hash,
        pattern_parameters=params,
        prompt_hash=cfg.prompt_hash,
        roles=roles or ("executor",),
    )


def _allowed_tools(
    db: Session, scenario: m.Scenario, agent: m.AgentConfiguration
) -> list[AllowedTool]:
    linked = {
        (row.tool_id, row.tool_version)
        for row in db.scalars(
            select(m.AgentTool).where(
                m.AgentTool.agent_id == agent.id, m.AgentTool.agent_version == agent.version
            )
        )
    }
    allowed: list[AllowedTool] = []
    raw = scenario.tools if isinstance(scenario.tools, list) else []
    for item in raw:
        if not isinstance(item, dict) or "id" not in item or "version" not in item:
            continue
        tool = db.get(m.ToolDefinition, (uuid.UUID(str(item["id"])), str(item["version"])))
        if tool is None or (tool.id, tool.version) not in linked:
            continue
        allowed.append(
            AllowedTool(
                id=tool.id, version=tool.version, name=tool.name, content_hash=tool.content_hash
            )
        )
    return allowed


def _result_document(result: RunResult) -> dict[str, Any]:
    label = "scripted" if result.pattern == "scripted" else result.pattern
    attribution = "harness_baseline" if result.pattern == "scripted" else "unimplemented"
    document: dict[str, Any] = {
        "pattern": result.pattern,
        "pattern_version": result.pattern_version,
        "label": label,
        "attribution": attribution,
        "output": result.output,
        "evidence_refs": [ref.as_json() for ref in result.evidence_refs],
        "usage": result.usage.as_json(),
    }
    if result.error is not None:
        document["error"] = result.error
    return document


def _flush_events(
    db: Session, run: m.Run, trace: m.Trace, sink: MemoryTraceSink, sealed_at: datetime
) -> None:
    for event in sink.events:
        db.add(
            m.TraceEvent(
                event_id=event.event_id,
                run_id=run.id,
                attempt_id=sink.attempt_id,
                sequence=event.sequence,
                timestamp_utc=event.timestamp_utc,
                elapsed_ms=event.elapsed_ms,
                type=event.type,
                actor_role=event.actor_role,
                parent_event_id=event.parent_event_id,
                payload=event.payload,
                payload_digest=event.payload_digest,
                redaction_metadata=event.redaction_metadata,
            )
        )
    trace.event_count = len(sink.events)
    trace.digest = sink.digest()
    trace.completeness = sink.completeness
    trace.sealed_at = sealed_at


def _limits(exp: m.Experiment | None, scenario: m.Scenario) -> dict[str, Any]:
    limits: dict[str, Any] = {}
    if exp is not None and isinstance(exp.budgets, dict):
        limits.update(exp.budgets)
    if isinstance(scenario.limits, dict):
        limits.update(scenario.limits)
    return limits


def _mode(value: str) -> Literal["live", "replay"]:
    return "replay" if value == "replay" else "live"


def _ensure_failed_event(sink: MemoryTraceSink, error_class: str, error: str) -> None:
    if any(event.type in TERMINAL_EVENT_TYPES for event in sink.events):
        return
    with contextlib.suppress(RunnerError):
        sink.append(
            "run.failed",
            "harness",
            {"error": error, "error_class": error_class, "completeness": sink.completeness},
        )


def claim_queued_run(db: Session) -> m.Run | None:
    return db.scalar(
        select(m.Run)
        .where(m.Run.status == RunStatus.QUEUED)
        .order_by(m.Run.created_at, m.Run.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def execute_run(db: Session, run_id: uuid.UUID, *, policy: SandboxPolicy | None = None) -> m.Run:
    run = db.get(m.Run, run_id, with_for_update=True)
    if run is None:
        raise NotFoundError("run inexistente", run_id=str(run_id))
    return execute_claimed_run(db, run, policy=policy)


def execute_claimed_run(db: Session, run: m.Run, *, policy: SandboxPolicy | None = None) -> m.Run:
    if run.status != RunStatus.QUEUED:
        return run
    sandbox = policy or SandboxPolicy()
    started_at = datetime.now(UTC)
    RUN_LIFECYCLE.check(RunStatus.QUEUED, RunStatus.RUNNING)
    run.status = RunStatus.RUNNING
    run.started_at = started_at

    exp = db.get(m.Experiment, run.experiment_id, with_for_update=True)
    if exp is not None and exp.status == ExperimentStatus.SEALED:
        EXPERIMENT_LIFECYCLE.check(ExperimentStatus.SEALED, ExperimentStatus.RUNNING)
        exp.status = ExperimentStatus.RUNNING

    scenario = db.get(m.Scenario, (run.scenario_id, run.scenario_version))
    agent = db.get(m.AgentConfiguration, (run.agent_id, run.agent_version))
    if scenario is None or agent is None:
        run.status = RunStatus.FAILED
        run.error_class = "infrastructure_error"
        run.ended_at = datetime.now(UTC)
        run.result = {
            "pattern": agent.pattern if agent is not None else "unknown",
            "label": "scripted",
            "attribution": "harness_baseline",
            "error": "agente o escenario irresoluble",
            "usage": Usage(model_calls=0, tool_calls=0).as_json(),
        }
        return run

    public = scenario_public_view(scenario)
    snapshot = _agent_snapshot(db, agent)
    allowed = _allowed_tools(db, scenario, agent)
    attempt_id = uuid.uuid4()
    budgets = exp.budgets if exp is not None and isinstance(exp.budgets, dict) else {}

    trace = m.Trace(run_id=run.id, schema_version=TRACE_SCHEMA_VERSION)
    db.add(trace)
    db.flush()

    sink = MemoryTraceSink(
        started_at=started_at,
        schema_version=TRACE_SCHEMA_VERSION,
        run_id=run.id,
        attempt_id=attempt_id,
    )
    context = RunContext(
        run_id=run.id,
        attempt_id=attempt_id,
        experiment_id=run.experiment_id,
        mode=_mode(run.mode),
        seed=run.seed,
        started_at=started_at,
        limits=_limits(exp, scenario),
        sandbox=sandbox,
        manifest_hash=exp.manifest_hash if exp is not None else None,
        experiment_budgets=cast(dict[str, Any], budgets),
    )
    try:
        result = execute_agent(
            context,
            snapshot,
            public,
            allowed,
            DeniedModelGateway(),
            DeniedToolGateway(),
            sink,
        )
    except RunnerError as exc:
        mark_incomplete(sink)
        _ensure_failed_event(sink, exc.error_class, exc.message)
        result = RunResult(
            status=RunStatus.FAILED,
            pattern=snapshot.pattern,
            pattern_version=snapshot.pattern_version,
            output=None,
            evidence_refs=(),
            usage=Usage(model_calls=0, tool_calls=0),
            error_class=exc.error_class,
            error=exc.message,
            completeness=sink.completeness,
        )
    except Exception as exc:
        mark_incomplete(sink)
        _ensure_failed_event(sink, "infrastructure_error", type(exc).__name__)
        result = RunResult(
            status=RunStatus.FAILED,
            pattern=snapshot.pattern,
            pattern_version=snapshot.pattern_version,
            output=None,
            evidence_refs=(),
            usage=Usage(model_calls=0, tool_calls=0),
            error_class="infrastructure_error",
            error=type(exc).__name__,
            completeness=sink.completeness,
        )

    ended_at = datetime.now(UTC)
    _flush_events(db, run, trace, sink, ended_at)
    RUN_LIFECYCLE.check(RunStatus.RUNNING, result.status)
    run.status = result.status
    run.error_class = result.error_class
    run.ended_at = ended_at
    run.result = _result_document(result)
    db.flush()
    return run


def get_trace(db: Session, run_id: uuid.UUID) -> tuple[m.Trace, list[m.TraceEvent]]:
    run = db.get(m.Run, run_id)
    if run is None:
        raise NotFoundError("run inexistente", run_id=str(run_id))
    trace = db.scalar(select(m.Trace).where(m.Trace.run_id == run_id))
    if trace is None:
        raise NotFoundError("traza inexistente", run_id=str(run_id))
    events = list(
        db.scalars(
            select(m.TraceEvent)
            .where(m.TraceEvent.run_id == run_id)
            .order_by(m.TraceEvent.sequence, m.TraceEvent.event_id)
        )
    )
    return trace, events
