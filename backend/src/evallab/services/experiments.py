"""Ciclo de vida de experimentos: draft editable, sellado con manifest y creación de celdas."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from evallab.canonical import canonical_digest
from evallab.db import models as m
from evallab.domain.lifecycle import (
    EXPERIMENT_LIFECYCLE,
    ExperimentStatus,
    InvalidTransitionError,
)
from evallab.schemas import (
    ExperimentCreate,
    ExperimentOut,
    ExperimentUpdate,
    RunCreate,
    RunOut,
    TraceEventOut,
    TraceOut,
    VersionRef,
)
from evallab.services.errors import (
    ExperimentNotAcceptingRunsError,
    ExperimentSealedError,
    InvalidReferenceError,
    InvalidRequestError,
    InvalidTransitionConflictError,
    NotFoundError,
    RunCellExistsError,
)

MANIFEST_SCHEMA_VERSION = "1.0.0"
ACCEPTS_RUNS = {ExperimentStatus.SEALED, ExperimentStatus.RUNNING}


def _ref(id_: uuid.UUID | None, version: str | None) -> VersionRef | None:
    return None if id_ is None or version is None else VersionRef(id=id_, version=version)


def _agent_refs(db: Session, experiment_id: uuid.UUID) -> list[VersionRef]:
    rows = db.scalars(
        select(m.ExperimentAgent)
        .where(m.ExperimentAgent.experiment_id == experiment_id)
        .order_by(m.ExperimentAgent.agent_id, m.ExperimentAgent.agent_version)
    )
    return [VersionRef(id=r.agent_id, version=r.agent_version) for r in rows]


def to_out(db: Session, exp: m.Experiment) -> ExperimentOut:
    return ExperimentOut(
        id=exp.id,
        status=exp.status,
        hypothesis=exp.hypothesis,
        benchmark=_ref(exp.benchmark_id, exp.benchmark_version),
        agents=_agent_refs(db, exp.id),
        budgets=exp.budgets,
        repetitions=exp.repetitions,
        seeds=exp.seeds,
        comparison_plan=exp.comparison_plan,
        manifest_hash=exp.manifest_hash,
        created_at=exp.created_at,
        sealed_at=exp.sealed_at,
    )


def run_to_out(run: m.Run) -> RunOut:
    return RunOut(
        id=run.id,
        experiment_id=run.experiment_id,
        scenario=VersionRef(id=run.scenario_id, version=run.scenario_version),
        agent=VersionRef(id=run.agent_id, version=run.agent_version),
        repetition=run.repetition,
        seed=run.seed,
        mode=run.mode,
        status=run.status,
        error_class=run.error_class,
        created_at=run.created_at,
        started_at=run.started_at,
        ended_at=run.ended_at,
        result=run.result if isinstance(run.result, dict) else None,
    )


def _require_benchmark(db: Session, ref: VersionRef) -> m.Benchmark:
    bench = db.get(m.Benchmark, (ref.id, ref.version))
    if bench is None:
        raise InvalidReferenceError("benchmark inexistente", benchmark=ref.model_dump(mode="json"))
    return bench


def _require_agents(db: Session, refs: list[VersionRef]) -> None:
    missing = [r for r in refs if db.get(m.AgentConfiguration, (r.id, r.version)) is None]
    if missing:
        raise InvalidReferenceError(
            "configuraciones de agente inexistentes",
            agents=[r.model_dump(mode="json") for r in missing],
        )


def _get_for_update(db: Session, experiment_id: uuid.UUID) -> m.Experiment:
    exp = db.get(m.Experiment, experiment_id, with_for_update=True)
    if exp is None:
        raise NotFoundError("experimento inexistente", experiment_id=str(experiment_id))
    return exp


def get_experiment(db: Session, experiment_id: uuid.UUID) -> m.Experiment:
    exp = db.get(m.Experiment, experiment_id)
    if exp is None:
        raise NotFoundError("experimento inexistente", experiment_id=str(experiment_id))
    return exp


def create_draft(db: Session, data: ExperimentCreate) -> m.Experiment:
    if data.benchmark is not None:
        _require_benchmark(db, data.benchmark)
    _require_agents(db, data.agents)
    exp = m.Experiment(
        hypothesis=data.hypothesis,
        benchmark_id=data.benchmark.id if data.benchmark else None,
        benchmark_version=data.benchmark.version if data.benchmark else None,
        budgets=data.budgets,
        repetitions=data.repetitions,
        seeds=data.seeds,
        comparison_plan=data.comparison_plan,
    )
    db.add(exp)
    db.flush()
    db.add_all(
        m.ExperimentAgent(experiment_id=exp.id, agent_id=r.id, agent_version=r.version)
        for r in data.agents
    )
    db.flush()
    db.refresh(exp)
    return exp


def update_draft(db: Session, experiment_id: uuid.UUID, data: ExperimentUpdate) -> m.Experiment:
    exp = _get_for_update(db, experiment_id)
    if exp.status != ExperimentStatus.DRAFT:
        raise ExperimentSealedError(
            "el experimento está sellado; crea una configuración y un experimento nuevos",
            experiment_id=str(exp.id),
            status=exp.status,
        )
    fields = data.model_fields_set
    if "benchmark" in fields:
        if data.benchmark is not None:
            _require_benchmark(db, data.benchmark)
        exp.benchmark_id = data.benchmark.id if data.benchmark else None
        exp.benchmark_version = data.benchmark.version if data.benchmark else None
    for name in ("hypothesis", "budgets", "repetitions", "seeds", "comparison_plan"):
        if name in fields:
            value = getattr(data, name)
            if value is None:
                raise InvalidRequestError(f"{name} no admite null")
            setattr(exp, name, value)
    if len(exp.seeds) != exp.repetitions:
        raise InvalidRequestError("seeds debe tener una seed por repetición")
    if "agents" in fields:
        refs = data.agents or []
        _require_agents(db, refs)
        db.execute(delete(m.ExperimentAgent).where(m.ExperimentAgent.experiment_id == exp.id))
        db.add_all(
            m.ExperimentAgent(experiment_id=exp.id, agent_id=r.id, agent_version=r.version)
            for r in refs
        )
    db.flush()
    return exp


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def build_manifest(db: Session, exp: m.Experiment) -> dict[str, Any]:
    if exp.benchmark_id is None or exp.benchmark_version is None:
        raise InvalidReferenceError("sellar exige un benchmark", experiment_id=str(exp.id))
    agent_refs = _agent_refs(db, exp.id)
    if not agent_refs:
        raise InvalidReferenceError(
            "sellar exige al menos una configuración de agente", experiment_id=str(exp.id)
        )
    bench = _require_benchmark(db, VersionRef(id=exp.benchmark_id, version=exp.benchmark_version))
    dataset = db.get(m.Dataset, (bench.dataset_id, bench.dataset_version))
    if dataset is None:  # garantizado por FK; se comprueba para no sellar refs rotas
        raise InvalidReferenceError("dataset del benchmark inexistente")

    agents = []
    for ref in agent_refs:
        cfg = db.get(m.AgentConfiguration, (ref.id, ref.version))
        if cfg is None:
            raise InvalidReferenceError("configuración de agente inexistente")
        roles = []
        for role in db.scalars(
            select(m.AgentRole)
            .where(m.AgentRole.agent_id == cfg.id, m.AgentRole.agent_version == cfg.version)
            .order_by(m.AgentRole.role)
        ):
            model = db.get(m.ModelConfiguration, (role.model_id, role.model_version))
            if model is None:
                raise InvalidReferenceError("configuración de modelo inexistente")
            roles.append(
                {
                    "role": role.role,
                    "model": {
                        "id": str(model.id),
                        "version": model.version,
                        "content_hash": model.content_hash,
                        "provider": model.provider,
                        "requested_model": model.requested_model,
                        "resolved_revision": model.resolved_revision,
                        "temperature": _decimal(model.temperature),
                        "seed_support": model.seed_support,
                        "max_tokens": model.max_tokens,
                        "price_snapshot_ref": model.price_snapshot_ref,
                    },
                }
            )
        tools = []
        for link in db.scalars(
            select(m.AgentTool)
            .where(m.AgentTool.agent_id == cfg.id, m.AgentTool.agent_version == cfg.version)
            .order_by(m.AgentTool.tool_id, m.AgentTool.tool_version)
        ):
            tool = db.get(m.ToolDefinition, (link.tool_id, link.tool_version))
            if tool is None:
                raise InvalidReferenceError("definición de tool inexistente")
            tools.append(
                {
                    "id": str(tool.id),
                    "version": tool.version,
                    "name": tool.name,
                    "content_hash": tool.content_hash,
                    "effect_class": tool.effect_class,
                    "timeout_ms": tool.timeout_ms,
                    "fixture_hash": tool.fixture_hash,
                }
            )
        agents.append(
            {
                "id": str(cfg.id),
                "version": cfg.version,
                "content_hash": cfg.content_hash,
                "pattern": cfg.pattern,
                "pattern_version": cfg.pattern_version,
                "prompt_hash": cfg.prompt_hash,
                "pattern_parameters": cfg.pattern_parameters,
                "roles": roles,
                "tools": tools,
            }
        )

    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "hypothesis": exp.hypothesis,
        "benchmark": {
            "id": str(bench.id),
            "version": bench.version,
            "content_hash": bench.content_hash,
            "dataset": {
                "id": str(dataset.id),
                "version": dataset.version,
                "content_hash": dataset.content_hash,
            },
        },
        "agents": agents,
        "budgets": exp.budgets,
        "repetitions": exp.repetitions,
        "seeds": list(exp.seeds),
        "comparison_plan": exp.comparison_plan,
    }


def seal(db: Session, experiment_id: uuid.UUID) -> m.Experiment:
    exp = _get_for_update(db, experiment_id)
    try:
        EXPERIMENT_LIFECYCLE.check(ExperimentStatus(exp.status), ExperimentStatus.SEALED)
    except InvalidTransitionError as exc:
        raise InvalidTransitionConflictError(str(exc), status=exp.status) from exc
    manifest = build_manifest(db, exp)
    exp.manifest = manifest
    exp.manifest_hash = canonical_digest(manifest)
    exp.sealed_at = datetime.now(UTC)
    exp.status = ExperimentStatus.SEALED
    db.flush()
    return exp


def create_run(db: Session, experiment_id: uuid.UUID, data: RunCreate) -> m.Run:
    exp = get_experiment(db, experiment_id)
    if exp.status not in ACCEPTS_RUNS:
        raise ExperimentNotAcceptingRunsError(
            "sólo se crean runs en experimentos sellados o en ejecución", status=exp.status
        )
    if db.get(m.ExperimentAgent, (exp.id, data.agent.id, data.agent.version)) is None:
        raise InvalidReferenceError(
            "la configuración de agente no pertenece al experimento",
            agent=data.agent.model_dump(mode="json"),
        )
    bench = db.get(m.Benchmark, (exp.benchmark_id, exp.benchmark_version))
    if bench is None:
        raise InvalidReferenceError("el experimento no tiene benchmark resoluble")
    member = db.get(m.DatasetScenario, (bench.dataset_id, bench.dataset_version, data.scenario.id))
    if member is None or member.scenario_version != data.scenario.version:
        raise InvalidReferenceError(
            "el escenario no pertenece al dataset del benchmark",
            scenario=data.scenario.model_dump(mode="json"),
        )
    if data.repetition > exp.repetitions:
        raise InvalidReferenceError(
            "repetición fuera del plan del experimento", repetitions=exp.repetitions
        )
    seed = exp.seeds[data.repetition - 1]
    existing = db.scalar(
        select(m.Run).where(
            m.Run.experiment_id == exp.id,
            m.Run.scenario_id == data.scenario.id,
            m.Run.scenario_version == data.scenario.version,
            m.Run.agent_id == data.agent.id,
            m.Run.agent_version == data.agent.version,
            m.Run.repetition == data.repetition,
            m.Run.seed == seed,
        )
    )
    if existing is not None:
        raise RunCellExistsError("la celda experimental ya existe", run_id=str(existing.id))
    run = m.Run(
        experiment_id=exp.id,
        scenario_id=data.scenario.id,
        scenario_version=data.scenario.version,
        agent_id=data.agent.id,
        agent_version=data.agent.version,
        repetition=data.repetition,
        seed=seed,
        mode=data.mode,
    )
    db.add(run)
    db.flush()
    db.refresh(run)
    return run


def get_run(db: Session, run_id: uuid.UUID) -> m.Run:
    run = db.get(m.Run, run_id)
    if run is None:
        raise NotFoundError("run inexistente", run_id=str(run_id))
    return run


def list_runs(db: Session, experiment_id: uuid.UUID) -> list[m.Run]:
    get_experiment(db, experiment_id)
    return list(
        db.scalars(
            select(m.Run)
            .where(m.Run.experiment_id == experiment_id)
            .order_by(m.Run.created_at, m.Run.id)
        )
    )


def trace_to_out(trace: m.Trace, events: list[m.TraceEvent]) -> TraceOut:
    return TraceOut(
        run_id=trace.run_id,
        schema_version=trace.schema_version,
        event_count=trace.event_count,
        digest=trace.digest,
        completeness=trace.completeness,
        sealed_at=trace.sealed_at,
        events=[
            TraceEventOut(
                event_id=event.event_id,
                sequence=event.sequence,
                timestamp_utc=event.timestamp_utc,
                elapsed_ms=event.elapsed_ms,
                type=event.type,
                actor_role=event.actor_role,
                parent_event_id=event.parent_event_id,
                payload=event.payload if isinstance(event.payload, dict) else {},
                payload_digest=event.payload_digest,
                redaction_metadata=(
                    event.redaction_metadata if isinstance(event.redaction_metadata, dict) else {}
                ),
            )
            for event in events
        ],
    )
