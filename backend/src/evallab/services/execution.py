"""Ejecución de celdas: reclamar con lease, ejecutar en memoria y persistir con fencing.

1. `claim_next_run` (transacción corta): toma una celda `queued` o un run cuyo lease venció,
   registra un `RunAttempt` con un fencing token nuevo y lo confirma antes de cualquier efecto.
2. `run_attempt` (sin escrituras): ejecuta el runner sobre fixtures en memoria.
3. `finish_attempt` (transacción corta): persiste traza y resultado sólo si el token del
   intento sigue siendo el del run; un worker vencido no puede escribir.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evallab import telemetry
from evallab.canonical import canonical_digest
from evallab.db import models as m
from evallab.domain.lifecycle import (
    EXPERIMENT_LIFECYCLE,
    RUN_LIFECYCLE,
    ExperimentStatus,
    RunStatus,
)
from evallab.domain.vocabulary import TRACE_SCHEMA_VERSION
from evallab.retrieval.store import PgVectorRetriever
from evallab.runner.agent import execute_agent
from evallab.runner.contracts import (
    AgentSnapshot,
    AllowedTool,
    ModelGateway,
    RunContext,
    RunResult,
    ToolGateway,
    Usage,
)
from evallab.runner.errors import InvalidFaultScheduleError, RunnerError
from evallab.runner.evidence import normalize_evidence
from evallab.runner.gateways import DeniedModelGateway
from evallab.runner.limits import Limits
from evallab.runner.models import ModelSnapshot, PriceInfo, ProviderModelGateway
from evallab.runner.providers import FIXTURE_PROVIDER, default_providers
from evallab.runner.redaction import redact, redaction_metadata
from evallab.runner.replay import ReplayMismatchError, ReplayModelGateway, ReplayToolGateway
from evallab.runner.sink import MemoryTraceSink, mark_incomplete
from evallab.runner.tools import FixtureToolGateway, ToolBinding, parse_fault_schedule
from evallab.services.catalog import scenario_public_view
from evallab.services.errors import NotFoundError
from evallab.services.replays import ReplayUnavailableError, load_recording
from evallab.services.traces import persist_events, seal_trace
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
INLINE_WORKER = "inline"
DEFAULT_LEASE_S = 300.0


@dataclass(frozen=True)
class Claim:
    run_id: uuid.UUID
    attempt_id: uuid.UUID
    attempt_number: int
    fencing_token: int
    worker_id: str
    started_at: datetime


@dataclass(frozen=True)
class AttemptOutcome:
    claim: Claim
    result: RunResult
    sink: MemoryTraceSink | None
    replay: dict[str, Any] | None = None


def _price(db: Session, ref: str | None) -> PriceInfo | None:
    row = db.get(m.PriceSnapshot, ref) if ref is not None else None
    if row is None:
        return None
    return PriceInfo(
        ref=row.content_hash,
        currency=row.currency,
        input_per_mtok=row.input_per_mtok,
        output_per_mtok=row.output_per_mtok,
        cached_input_per_mtok=row.cached_input_per_mtok,
        synthetic=row.synthetic,
    )


def _model_snapshot(db: Session, role: m.AgentRole) -> ModelSnapshot | None:
    model = db.get(m.ModelConfiguration, (role.model_id, role.model_version))
    if model is None:
        return None
    return model_snapshot_for(db, model, role.role)


def model_snapshot_for(db: Session, model: m.ModelConfiguration, role: str) -> ModelSnapshot:
    return ModelSnapshot(
        role=role,
        id=model.id,
        version=model.version,
        content_hash=model.content_hash,
        provider=model.provider,
        requested_model=model.requested_model,
        resolved_revision=model.resolved_revision,
        temperature=None if model.temperature is None else format(model.temperature, "f"),
        max_tokens=model.max_tokens,
        seed_support=model.seed_support,
        price=_price(db, model.price_snapshot_ref),
    )


def _agent_snapshot(db: Session, cfg: m.AgentConfiguration) -> AgentSnapshot:
    rows = list(
        db.scalars(
            select(m.AgentRole)
            .where(m.AgentRole.agent_id == cfg.id, m.AgentRole.agent_version == cfg.version)
            .order_by(m.AgentRole.role)
        )
    )
    models = {row.role: snap for row in rows if (snap := _model_snapshot(db, row)) is not None}
    params = cfg.pattern_parameters if isinstance(cfg.pattern_parameters, dict) else {}
    return AgentSnapshot(
        id=cfg.id,
        version=cfg.version,
        pattern=cfg.pattern,
        pattern_version=cfg.pattern_version,
        content_hash=cfg.content_hash,
        pattern_parameters=params,
        prompt_hash=cfg.prompt_hash,
        roles=tuple(row.role for row in rows) or ("executor",),
        models=models,
    )


def _fixture_payload(db: Session) -> Callable[[str], Any | None]:
    def load(content_hash: str) -> Any | None:
        row = db.get(m.Fixture, content_hash)
        return row.payload if row is not None else None

    return load


def provider_gateway(db: Session, models: Mapping[str, ModelSnapshot]) -> ProviderModelGateway:
    """Gateway con los proveedores habilitados (fixture siempre; live sólo si se configura)."""
    return ProviderModelGateway(models, default_providers(_fixture_payload(db)))


def model_gateway(db: Session, agent: AgentSnapshot) -> ModelGateway:
    """Scripted no tiene modelo; el resto usa los proveedores habilitados (fixture por defecto)."""
    if agent.pattern == "scripted":
        return DeniedModelGateway()
    return provider_gateway(db, agent.models)


def _scenario_tools(db: Session, scenario: m.Scenario) -> list[m.ToolDefinition]:
    tools: list[m.ToolDefinition] = []
    raw = scenario.tools if isinstance(scenario.tools, list) else []
    for item in raw:
        if not isinstance(item, dict) or "id" not in item or "version" not in item:
            continue
        tool = db.get(m.ToolDefinition, (uuid.UUID(str(item["id"])), str(item["version"])))
        if tool is not None:
            tools.append(tool)
    return tools


def _agent_tools(db: Session, agent: m.AgentConfiguration) -> list[m.ToolDefinition]:
    rows = db.scalars(
        select(m.AgentTool).where(
            m.AgentTool.agent_id == agent.id, m.AgentTool.agent_version == agent.version
        )
    )
    tools = (db.get(m.ToolDefinition, (row.tool_id, row.tool_version)) for row in rows)
    return [tool for tool in tools if tool is not None]


def _binding(db: Session, tool: m.ToolDefinition) -> ToolBinding:
    fixture = db.get(m.Fixture, tool.fixture_hash) if tool.fixture_hash else None
    return ToolBinding(
        tool=AllowedTool(
            id=tool.id, version=tool.version, name=tool.name, content_hash=tool.content_hash
        ),
        input_schema=tool.input_schema if isinstance(tool.input_schema, dict) else {},
        output_schema=tool.output_schema if isinstance(tool.output_schema, dict) else {},
        effect_class=tool.effect_class,
        fixture=fixture.payload if fixture is not None else None,
        timeout_ms=tool.timeout_ms,
    )


def _tool_gateway(
    db: Session, scenario: m.Scenario, agent: m.AgentConfiguration
) -> FixtureToolGateway:
    in_scenario = _scenario_tools(db, scenario)
    in_agent = _agent_tools(db, agent)
    scenario_keys = {(t.id, t.version) for t in in_scenario}
    agent_keys = {(t.id, t.version) for t in in_agent}
    environment = scenario.environment if isinstance(scenario.environment, dict) else {}
    initial_state = environment.get("initial_state")
    try:
        faults = parse_fault_schedule(
            environment.get("fault_schedule"), (t.name for t in in_scenario)
        )
    except ValueError as exc:
        raise InvalidFaultScheduleError("fault_schedule del escenario inválido") from exc
    return FixtureToolGateway(
        [_binding(db, t) for t in in_scenario if (t.id, t.version) in agent_keys],
        scenario_only={t.name for t in in_scenario if (t.id, t.version) not in agent_keys},
        agent_only={t.name for t in in_agent if (t.id, t.version) not in scenario_keys},
        initial_state=initial_state if isinstance(initial_state, dict) else {},
        faults=faults,
        # Lectura en PGVector para tools de recuperación; la ejecución sigue sin escribir.
        retrieval=PgVectorRetriever(db),
    )


def _labels(pattern: str, providers: Collection[str] = ()) -> dict[str, str]:
    """Atribución honesta: scripted prueba el harness; un modelo de fixture tampoco es un LLM."""
    if pattern == "scripted":
        return {"label": "scripted", "attribution": "harness_baseline"}
    if providers and set(providers) == {FIXTURE_PROVIDER}:
        return {"label": pattern, "attribution": "fixture_model"}
    if providers:
        return {"label": pattern, "attribution": "model_pattern"}
    return {"label": pattern, "attribution": "no_model_configured"}


def _result_document(result: RunResult, claim: Claim) -> dict[str, Any]:
    output, redacted = redact(result.output)
    document: dict[str, Any] = {
        "pattern": result.pattern,
        "pattern_version": result.pattern_version,
        **_labels(result.pattern, result.providers),
        "output": output,
        "evidence_refs": [ref.as_json() for ref in result.evidence_refs],
        "usage": result.usage.as_json(),
        "policy_violations": result.policy_violations,
        "attempt": {"number": claim.attempt_number, "fencing_token": claim.fencing_token},
    }
    if result.termination is not None:
        document["termination"] = result.termination
    if result.error is not None:
        document["error"] = result.error
    if redacted:
        document["redaction_metadata"] = redaction_metadata(redacted, replayable=True)
    return document


def _store_trace(db: Session, run: m.Run, sink: MemoryTraceSink, sealed_at: datetime) -> None:
    trace = db.scalar(select(m.Trace).where(m.Trace.run_id == run.id))
    if trace is None:
        trace = m.Trace(run_id=run.id, schema_version=TRACE_SCHEMA_VERSION)
        db.add(trace)
        db.flush()
    persist_events(db, run.id, sink.attempt_id, sink.events)
    seal_trace(db, trace, sink.events, sink.completeness, trace.sealed_at or sealed_at)


def _limits(exp: m.Experiment | None, scenario: m.Scenario) -> dict[str, Any]:
    """Límites efectivos: el más estricto entre presupuesto del experimento y escenario."""
    budgets = exp.budgets if exp is not None and isinstance(exp.budgets, dict) else {}
    limits = scenario.limits if isinstance(scenario.limits, dict) else {}
    try:
        return Limits.effective(budgets, limits).as_json()
    except RunnerError:
        merged: dict[str, Any] = {**budgets, **limits}
        return merged


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


def _failed_result(pattern: str, pattern_version: str, error_class: str, error: str) -> RunResult:
    return RunResult(
        status=RunStatus.FAILED,
        pattern=pattern,
        pattern_version=pattern_version,
        output=None,
        evidence_refs=(),
        usage=Usage(model_calls=0, tool_calls=0),
        error_class=error_class,
        error=error,
        completeness="incomplete",
    )


# --- Fase 1: reclamar ----------------------------------------------------------------------


def _queued_run(db: Session) -> m.Run | None:
    return db.scalar(
        select(m.Run)
        .where(m.Run.status == RunStatus.QUEUED)
        .order_by(m.Run.created_at, m.Run.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def _expired_attempt(db: Session, now: datetime) -> tuple[m.Run, m.RunAttempt] | None:
    row = db.execute(
        select(m.Run, m.RunAttempt)
        .join(m.RunAttempt, m.RunAttempt.run_id == m.Run.id)
        .where(
            m.Run.status == RunStatus.RUNNING,
            m.RunAttempt.status == "active",
            m.RunAttempt.lease_expires_at <= now,
        )
        .order_by(m.RunAttempt.lease_expires_at, m.Run.id)
        .limit(1)
        .with_for_update(skip_locked=True, of=m.Run)
    ).first()
    if row is None:
        return None
    return row[0], row[1]


def _start_run(db: Session, run: m.Run, now: datetime) -> None:
    """Pasa a running; el llamador crea el intento (y el token) antes del siguiente flush."""
    exp = db.get(m.Experiment, run.experiment_id, with_for_update=True)
    if exp is not None and exp.status == ExperimentStatus.SEALED:
        EXPERIMENT_LIFECYCLE.check(ExperimentStatus.SEALED, ExperimentStatus.RUNNING)
        exp.status = ExperimentStatus.RUNNING
    RUN_LIFECYCLE.check(RunStatus.QUEUED, RunStatus.RUNNING)
    run.status = RunStatus.RUNNING
    run.started_at = now


def _new_attempt(
    db: Session, run: m.Run, *, number: int, worker_id: str, lease_s: float, now: datetime
) -> Claim:
    token = run.fencing_token + 1
    run.fencing_token = token
    attempt = m.RunAttempt(
        id=uuid.uuid4(),
        run_id=run.id,
        attempt_number=number,
        fencing_token=token,
        worker_id=worker_id,
        status="active",
        lease_expires_at=now + timedelta(seconds=lease_s),
    )
    db.add(attempt)
    db.flush()
    return Claim(
        run_id=run.id,
        attempt_id=attempt.id,
        attempt_number=number,
        fencing_token=token,
        worker_id=worker_id,
        started_at=now,
    )


def _planned_cells(db: Session, exp: m.Experiment) -> int:
    benchmark = db.get(m.Benchmark, (exp.benchmark_id, exp.benchmark_version))
    selection = benchmark.scenario_selection if benchmark is not None else None
    scenarios = len(selection) if isinstance(selection, list) else 0
    agents = db.scalar(
        select(func.count())
        .select_from(m.ExperimentAgent)
        .where(m.ExperimentAgent.experiment_id == exp.id)
    )
    return scenarios * int(agents or 0) * exp.repetitions


def complete_experiment_if_done(db: Session, experiment_id: uuid.UUID) -> bool:
    """`running -> completed` cuando todas las celdas live programadas tienen un run terminal
    y no queda ningún run activo (design: completed = celdas terminales, no éxitos)."""
    exp = db.get(m.Experiment, experiment_id, with_for_update=True)
    if exp is None or exp.status != ExperimentStatus.RUNNING:
        return False
    active = db.scalar(
        select(func.count())
        .select_from(m.Run)
        .where(m.Run.experiment_id == exp.id, m.Run.status.in_(m.RUN_ACTIVE))
    )
    terminal_cells = db.scalar(
        select(func.count())
        .select_from(m.Run)
        .where(
            m.Run.experiment_id == exp.id,
            m.Run.mode == "live",
            m.Run.status.not_in(m.RUN_ACTIVE),
        )
    )
    planned = _planned_cells(db, exp)
    if active or planned == 0 or int(terminal_cells or 0) < planned:
        return False
    EXPERIMENT_LIFECYCLE.check(ExperimentStatus.RUNNING, ExperimentStatus.COMPLETED)
    exp.status = ExperimentStatus.COMPLETED
    db.flush()
    return True


LOST_WORKER_ERROR = "worker perdido: lease vencido sin resultado"


def _seal_lost_trace(db: Session, run: m.Run, attempt: m.RunAttempt, now: datetime) -> None:
    """La captura en memoria del worker caído se perdió: se sella una traza `incomplete` con
    el único hecho verificable (el harness declaró el run perdido), sin fabricar eventos."""
    sink = MemoryTraceSink(
        started_at=run.started_at or now,
        schema_version=TRACE_SCHEMA_VERSION,
        run_id=run.id,
        attempt_id=attempt.id,
    )
    mark_incomplete(sink)
    sink.append(
        "run.failed",
        "harness",
        {
            "error": LOST_WORKER_ERROR,
            "error_class": "infrastructure_error",
            "completeness": "incomplete",
            "attempt_number": attempt.attempt_number,
            "lost_events": "unknown",
        },
    )
    _store_trace(db, run, sink, now)


def _fail_lost_run(
    db: Session, run: m.Run, attempt: m.RunAttempt, attempts: int, now: datetime
) -> None:
    agent = db.get(m.AgentConfiguration, (run.agent_id, run.agent_version))
    pattern = agent.pattern if agent is not None else "unknown"
    if db.scalar(select(m.Trace).where(m.Trace.run_id == run.id)) is None:
        _seal_lost_trace(db, run, attempt, max(now, run.started_at or now))
    RUN_LIFECYCLE.check(RunStatus.RUNNING, RunStatus.FAILED)
    run.status = RunStatus.FAILED
    run.error_class = "infrastructure_error"
    run.ended_at = now
    run.result = {
        "pattern": pattern,
        **_labels(pattern),
        "output": None,
        "evidence_refs": [],
        "usage": Usage(model_calls=0, tool_calls=0).as_json(),
        "policy_violations": 0,
        "error": LOST_WORKER_ERROR,
        "attempt": {"number": attempts, "fencing_token": run.fencing_token},
    }


def claim_next_run(
    db: Session,
    *,
    worker_id: str,
    lease_s: float = DEFAULT_LEASE_S,
    max_attempts: int = 2,
    now: datetime | None = None,
) -> Claim | None:
    """Reclama la siguiente celda. Los leases vencidos se reintentan hasta `max_attempts`;
    agotados, el run termina `failed` con `infrastructure_error` explícito."""
    now = now or datetime.now(UTC)
    while (expired := _expired_attempt(db, now)) is not None:
        run, attempt = expired
        attempt.status = "expired"
        attempt.ended_at = now
        db.flush()
        if attempt.attempt_number >= max_attempts:
            _fail_lost_run(db, run, attempt, attempt.attempt_number, now)
            db.flush()
            complete_experiment_if_done(db, run.experiment_id)
            continue
        return _new_attempt(
            db,
            run,
            number=attempt.attempt_number + 1,
            worker_id=worker_id,
            lease_s=lease_s,
            now=now,
        )
    queued = _queued_run(db)
    if queued is None:
        return None
    _start_run(db, queued, now)
    return _new_attempt(db, queued, number=1, worker_id=worker_id, lease_s=lease_s, now=now)


def claim_run(
    db: Session,
    run_id: uuid.UUID,
    *,
    worker_id: str,
    lease_s: float = DEFAULT_LEASE_S,
    now: datetime | None = None,
) -> Claim | None:
    """Reclama una celda `queued` concreta (para ejecutar en el orden planificado)."""
    now = now or datetime.now(UTC)
    run = db.get(m.Run, run_id, with_for_update=True)
    if run is None or run.status != RunStatus.QUEUED:
        return None
    _start_run(db, run, now)
    return _new_attempt(db, run, number=1, worker_id=worker_id, lease_s=lease_s, now=now)


# --- Fase 2: ejecutar ----------------------------------------------------------------------


def _claim_attributes(claim: Claim) -> dict[str, Any]:
    return {
        "evallab.run_id": str(claim.run_id),
        "evallab.attempt_id": str(claim.attempt_id),
        "evallab.attempt_number": claim.attempt_number,
        "evallab.fencing_token": claim.fencing_token,
        "evallab.worker_id": claim.worker_id,
    }


def run_attempt(
    db: Session, claim: Claim, *, policy: SandboxPolicy | None = None
) -> AttemptOutcome:
    """Ejecuta el intento sin escribir en la base; todo efecto queda en memoria del intento."""
    with telemetry.span("worker.execute", **_claim_attributes(claim)) as current:
        outcome = _run_attempt(db, claim, policy=policy)
        telemetry.set_attributes(
            current,
            **{
                "evallab.run.status": str(outcome.result.status),
                "evallab.run.error_class": outcome.result.error_class,
            },
        )
        return outcome


def _run_attempt(db: Session, claim: Claim, *, policy: SandboxPolicy | None) -> AttemptOutcome:
    run = db.get(m.Run, claim.run_id)
    if run is None:
        raise NotFoundError("run inexistente", run_id=str(claim.run_id))
    scenario = db.get(m.Scenario, (run.scenario_id, run.scenario_version))
    agent = db.get(m.AgentConfiguration, (run.agent_id, run.agent_version))
    if scenario is None or agent is None:
        pattern = agent.pattern if agent is not None else "unknown"
        version = agent.pattern_version if agent is not None else "0.0.0"
        result = _failed_result(
            pattern, version, "infrastructure_error", "agente o escenario irresoluble"
        )
        return AttemptOutcome(claim=claim, result=result, sink=None)

    exp = db.get(m.Experiment, run.experiment_id)
    snapshot = _agent_snapshot(db, agent)
    budgets = exp.budgets if exp is not None and isinstance(exp.budgets, dict) else {}
    sink = MemoryTraceSink(
        started_at=claim.started_at,
        schema_version=TRACE_SCHEMA_VERSION,
        run_id=run.id,
        attempt_id=claim.attempt_id,
    )
    context = RunContext(
        run_id=run.id,
        attempt_id=claim.attempt_id,
        experiment_id=run.experiment_id,
        mode=_mode(run.mode),
        seed=run.seed,
        started_at=claim.started_at,
        limits=_limits(exp, scenario),
        sandbox=policy or SandboxPolicy(),
        manifest_hash=exp.manifest_hash if exp is not None else None,
        experiment_budgets=cast(dict[str, Any], budgets),
    )
    replay: dict[str, Any] | None = None
    source_output: str | None = None
    try:
        tools: ToolGateway = _tool_gateway(db, scenario, agent)
        model = model_gateway(db, snapshot)
        if run.mode == "replay" and run.source_run_id is not None:
            replay_tools, replay, source_output = _replay_gateway(db, run.source_run_id, tools)
            tools = replay_tools
            if snapshot.pattern != "scripted":
                # El modelo también se sirve de la grabación: nunca se llama al proveedor.
                model = ReplayModelGateway(model.models(), replay_tools.recording)
        result = execute_agent(
            context, snapshot, scenario_public_view(scenario), model, tools, sink
        )
        if replay is not None:
            replay["output_matches_source"] = (
                result.status == RunStatus.COMPLETED
                and canonical_digest(
                    normalize_evidence(
                        redact(result.output)[0], [r.as_json() for r in result.evidence_refs]
                    )
                )
                == source_output
            )
    except RunnerError as exc:
        mark_incomplete(sink)
        _ensure_failed_event(sink, exc.error_class, exc.message)
        result = _failed_result(
            snapshot.pattern, snapshot.pattern_version, exc.error_class, exc.message
        )
    except Exception as exc:
        mark_incomplete(sink)
        _ensure_failed_event(sink, "infrastructure_error", type(exc).__name__)
        result = _failed_result(
            snapshot.pattern, snapshot.pattern_version, "infrastructure_error", type(exc).__name__
        )
    return AttemptOutcome(claim=claim, result=result, sink=sink, replay=replay)


def _replay_gateway(
    db: Session, source_run_id: uuid.UUID, live: ToolGateway
) -> tuple[ReplayToolGateway, dict[str, Any], str]:
    """Gateway que sirve la grabación de origen; el gateway live sólo aporta la allowlist."""
    try:
        recording, trace = load_recording(db, source_run_id)
    except ReplayUnavailableError as exc:
        raise ReplayMismatchError(exc.message) from exc
    source = db.get(m.Run, source_run_id)
    result = source.result if source is not None and isinstance(source.result, dict) else {}
    info: dict[str, Any] = {
        "source_run_id": str(source_run_id),
        "source_trace_digest": trace.digest,
        "recorded_calls": len(recording.calls),
    }
    gateway = ReplayToolGateway(live.allowed_tools(), recording)
    # Los ids de evidencia del replay son nuevos: se comparan por posición, no por valor.
    source_output = normalize_evidence(result.get("output"), result.get("evidence_refs") or [])
    return gateway, info, canonical_digest(source_output)


# --- Fase 3: persistir con fencing ---------------------------------------------------------


def finish_attempt(db: Session, outcome: AttemptOutcome, *, now: datetime | None = None) -> bool:
    """Persiste el intento si su fencing token sigue vigente; si no, lo marca `rejected`."""
    with telemetry.span("worker.persist", **_claim_attributes(outcome.claim)) as current:
        accepted = _finish_attempt(db, outcome, now=now)
        telemetry.set_attributes(current, **{"evallab.attempt.accepted": accepted})
        if not accepted:
            telemetry.mark_error(current, "fencing_rejected")
        return accepted


def _finish_attempt(db: Session, outcome: AttemptOutcome, *, now: datetime | None) -> bool:
    now = now or datetime.now(UTC)
    claim = outcome.claim
    run = db.get(m.Run, claim.run_id, with_for_update=True)
    attempt = db.get(m.RunAttempt, claim.attempt_id, with_for_update=True)
    if run is None or attempt is None:
        return False
    if attempt.status == "finished" and run.fencing_token == claim.fencing_token:
        # Confirmación repetida del mismo intento: idempotente si la evidencia coincide.
        if outcome.sink is not None:
            _store_trace(db, run, outcome.sink, attempt.ended_at or now)
        return True
    if run.status != RunStatus.RUNNING or run.fencing_token != claim.fencing_token:
        attempt.status = "rejected"
        attempt.ended_at = attempt.ended_at or now
        db.flush()
        return False

    ended_at = max(now, run.started_at or now)
    if outcome.sink is not None:
        _store_trace(db, run, outcome.sink, ended_at)
    result = outcome.result
    RUN_LIFECYCLE.check(RunStatus.RUNNING, result.status)
    run.status = result.status
    run.error_class = result.error_class
    run.ended_at = ended_at
    run.result = _result_document(result, claim)
    if outcome.replay is not None:
        run.result = {**run.result, "replay": outcome.replay}
    attempt.status = "finished"
    attempt.ended_at = ended_at
    db.flush()
    complete_experiment_if_done(db, run.experiment_id)
    return True


def execute_run(
    db: Session,
    run_id: uuid.UUID,
    *,
    policy: SandboxPolicy | None = None,
    worker_id: str = INLINE_WORKER,
) -> m.Run:
    """Ejecuta una celda `queued` concreta en la sesión actual (útil para tests y CLI)."""
    run = db.get(m.Run, run_id, with_for_update=True)
    if run is None:
        raise NotFoundError("run inexistente", run_id=str(run_id))
    if run.status != RunStatus.QUEUED:
        return run
    now = datetime.now(UTC)
    _start_run(db, run, now)
    claim = _new_attempt(db, run, number=1, worker_id=worker_id, lease_s=DEFAULT_LEASE_S, now=now)
    finish_attempt(db, run_attempt(db, claim, policy=policy))
    return run


def get_trace(
    db: Session,
    run_id: uuid.UUID,
    *,
    after_sequence: int | None = None,
    limit: int | None = None,
) -> tuple[m.Trace, list[m.TraceEvent]]:
    """Eventos ordenados por `sequence`; con `after_sequence`/`limit` devuelve una página
    (keyset: estable aunque la traza tenga huecos de secuencia por incompleta)."""
    run = db.get(m.Run, run_id)
    if run is None:
        raise NotFoundError("run inexistente", run_id=str(run_id))
    trace = db.scalar(select(m.Trace).where(m.Trace.run_id == run_id))
    if trace is None:
        raise NotFoundError("traza inexistente", run_id=str(run_id))
    query = select(m.TraceEvent).where(m.TraceEvent.run_id == run_id)
    if after_sequence is not None:
        query = query.where(m.TraceEvent.sequence > after_sequence)
    query = query.order_by(m.TraceEvent.sequence, m.TraceEvent.event_id)
    if limit is not None:
        query = query.limit(limit)
    return trace, list(db.scalars(query))


def get_trace_event(db: Session, run_id: uuid.UUID, event_id: uuid.UUID) -> m.TraceEvent:
    """Un evento concreto: destino de las referencias de evidencia de un score."""
    event = db.scalar(
        select(m.TraceEvent).where(m.TraceEvent.run_id == run_id, m.TraceEvent.event_id == event_id)
    )
    if event is None:
        raise NotFoundError(
            "evento inexistente en la traza", run_id=str(run_id), event_id=str(event_id)
        )
    return event
