"""Esquema relacional de las entidades de design.md §2 (M1, tarea 2.1).

Las transiciones de estado se validan además con triggers definidos en la migración.
"""

import uuid
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from evallab.domain import vocabulary as vocab
from evallab.domain.lifecycle import EVALUATION_LIFECYCLE, EXPERIMENT_LIFECYCLE, RUN_LIFECYCLE

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

SEMVER = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
SHA256 = r"^[0-9a-f]{64}$"


def one_of(column: str, values: Iterable[str]) -> str:
    return f"{column} IN ({', '.join(repr(str(v)) for v in sorted(values))})"


def semver(column: str = "version") -> str:
    return f"{column} ~ '{SEMVER}'"


def sha256(column: str, *, nullable: bool = False) -> str:
    check = f"{column} ~ '{SHA256}'"
    return f"{column} IS NULL OR {check}" if nullable else check


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, server_default=func.gen_random_uuid())


def created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def jsonb(default: str | None = None) -> Mapped[Any]:
    server_default = None if default is None else text(f"'{default}'::jsonb")
    return mapped_column(JSONB, nullable=False, server_default=server_default)


# --- Entidades versionadas: PK (id, version) --------------------------------------------


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (
        UniqueConstraint("name", "version"),
        UniqueConstraint("content_hash"),
        CheckConstraint(semver(), name="version_semver"),
        CheckConstraint(sha256("content_hash"), name="content_hash_sha256"),
        CheckConstraint("synthetic IS TRUE", name="synthetic"),
        CheckConstraint(
            f"coverage_class IS NULL OR {one_of('coverage_class', vocab.COVERAGE_CLASSES)}",
            name="coverage_class",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    version: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    schema_version: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text)
    split_manifest: Mapped[Any] = jsonb()
    synthetic: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    license: Mapped[str | None] = mapped_column(Text)
    generator_version: Mapped[str | None] = mapped_column(Text)
    fixture_refs: Mapped[Any] = jsonb("[]")
    corpus_refs: Mapped[Any] = jsonb("[]")
    category_counts: Mapped[Any] = jsonb("{}")
    coverage_class: Mapped[str | None] = mapped_column(Text)
    manifest: Mapped[Any | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at()


class Scenario(Base):
    __tablename__ = "scenarios"
    __table_args__ = (
        UniqueConstraint("content_hash"),
        UniqueConstraint("slug", "version"),
        CheckConstraint(semver(), name="version_semver"),
        CheckConstraint(sha256("content_hash"), name="content_hash_sha256"),
        CheckConstraint(one_of("primary_category", vocab.PRIMARY_CATEGORIES), name="category"),
        CheckConstraint(f"split IS NULL OR {one_of('split', vocab.SPLITS)}", name="split"),
        CheckConstraint(
            f"difficulty IS NULL OR {one_of('difficulty', vocab.DIFFICULTIES)}",
            name="difficulty",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    version: Mapped[str] = mapped_column(Text, primary_key=True)
    schema_version: Mapped[str] = mapped_column(Text, server_default=text("'1.0'"))
    slug: Mapped[str | None] = mapped_column(Text)
    primary_category: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    difficulty: Mapped[str | None] = mapped_column(Text)
    split: Mapped[str | None] = mapped_column(Text)
    family_id: Mapped[str | None] = mapped_column(Text)
    input: Mapped[Any] = jsonb()
    task: Mapped[Any] = jsonb("{}")
    tools: Mapped[Any] = jsonb("[]")
    environment: Mapped[Any] = jsonb("{}")
    fixtures: Mapped[Any] = jsonb("[]")
    oracle_ref: Mapped[Any] = jsonb()
    evaluation: Mapped[Any] = jsonb("{}")
    limits: Mapped[Any] = jsonb()
    retrieval: Mapped[Any | None] = mapped_column(JSONB)
    recovery: Mapped[Any | None] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


class Fixture(Base):
    """Fixture sintética identificada por hash de contenido (Artifact llega en M4)."""

    __tablename__ = "fixtures"
    __table_args__ = (CheckConstraint(sha256("content_hash"), name="content_hash_sha256"),)

    content_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    payload: Mapped[Any] = jsonb()
    created_at: Mapped[datetime] = created_at()


class DatasetScenario(Base):
    """Pertenencia N:M con el orden explícito del manifest."""

    __tablename__ = "dataset_scenarios"
    __table_args__ = (
        ForeignKeyConstraint(
            ["dataset_id", "dataset_version"], ["datasets.id", "datasets.version"]
        ),
        ForeignKeyConstraint(
            ["scenario_id", "scenario_version"], ["scenarios.id", "scenarios.version"]
        ),
        UniqueConstraint("dataset_id", "dataset_version", "position"),
        CheckConstraint("position >= 0", name="position_non_negative"),
    )

    dataset_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    dataset_version: Mapped[str] = mapped_column(Text, primary_key=True)
    scenario_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    scenario_version: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)


class Benchmark(Base):
    __tablename__ = "benchmarks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["dataset_id", "dataset_version"], ["datasets.id", "datasets.version"]
        ),
        UniqueConstraint("content_hash"),
        CheckConstraint(semver(), name="version_semver"),
        CheckConstraint(sha256("content_hash"), name="content_hash_sha256"),
        CheckConstraint(
            sha256("dataset_content_hash", nullable=True), name="dataset_content_hash_sha256"
        ),
        CheckConstraint("default_repetitions > 0", name="default_repetitions_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    version: Mapped[str] = mapped_column(Text, primary_key=True)
    dataset_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    dataset_version: Mapped[str] = mapped_column(Text)
    dataset_content_hash: Mapped[str | None] = mapped_column(Text)
    scenario_selection: Mapped[Any] = jsonb()
    evaluator_suite: Mapped[Any] = jsonb()
    metric_profile: Mapped[Any] = jsonb()
    comparison_rules: Mapped[Any] = jsonb()
    default_repetitions: Mapped[int] = mapped_column(Integer, server_default=text("5"))
    seed_schedule: Mapped[Any] = jsonb("[]")
    budgets: Mapped[Any] = jsonb("{}")
    manifest: Mapped[Any | None] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


class ToolDefinition(Base):
    __tablename__ = "tool_definitions"
    __table_args__ = (
        UniqueConstraint("name", "version"),
        UniqueConstraint("content_hash"),
        CheckConstraint(semver(), name="version_semver"),
        CheckConstraint(sha256("content_hash"), name="content_hash_sha256"),
        CheckConstraint(sha256("fixture_hash", nullable=True), name="fixture_hash_sha256"),
        CheckConstraint(one_of("effect_class", vocab.TOOL_EFFECT_CLASSES), name="effect_class"),
        CheckConstraint("timeout_ms > 0", name="timeout_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    version: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    input_schema: Mapped[Any] = jsonb()
    output_schema: Mapped[Any] = jsonb()
    effect_class: Mapped[str] = mapped_column(Text)
    allowed_scope: Mapped[Any] = jsonb("{}")
    timeout_ms: Mapped[int] = mapped_column(Integer)
    fixture_hash: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


class ModelConfiguration(Base):
    __tablename__ = "model_configurations"
    __table_args__ = (
        UniqueConstraint("content_hash"),
        CheckConstraint(semver(), name="version_semver"),
        CheckConstraint(sha256("content_hash"), name="content_hash_sha256"),
        CheckConstraint(
            sha256("price_snapshot_ref", nullable=True), name="price_snapshot_ref_sha256"
        ),
        CheckConstraint(one_of("seed_support", vocab.SEED_SUPPORT), name="seed_support"),
        CheckConstraint("temperature IS NULL OR temperature >= 0", name="temperature_range"),
        CheckConstraint("max_tokens IS NULL OR max_tokens > 0", name="max_tokens_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    version: Mapped[str] = mapped_column(Text, primary_key=True)
    provider: Mapped[str] = mapped_column(Text)
    requested_model: Mapped[str] = mapped_column(Text)
    # NULL = revisión desconocida, declarada explícitamente.
    resolved_revision: Mapped[str | None] = mapped_column(Text)
    temperature: Mapped[Decimal | None] = mapped_column(Numeric)
    seed_support: Mapped[str] = mapped_column(Text)
    max_tokens: Mapped[int | None] = mapped_column(Integer)
    price_snapshot_ref: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


class AgentConfiguration(Base):
    __tablename__ = "agent_configurations"
    __table_args__ = (
        UniqueConstraint("content_hash"),
        CheckConstraint(semver(), name="version_semver"),
        CheckConstraint(sha256("content_hash"), name="content_hash_sha256"),
        CheckConstraint(sha256("prompt_hash", nullable=True), name="prompt_hash_sha256"),
        CheckConstraint(one_of("pattern", vocab.AGENT_PATTERNS), name="pattern"),
        CheckConstraint(semver("pattern_version"), name="pattern_version_semver"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    version: Mapped[str] = mapped_column(Text, primary_key=True)
    pattern: Mapped[str] = mapped_column(Text)
    pattern_version: Mapped[str] = mapped_column(Text)
    prompt_hash: Mapped[str | None] = mapped_column(Text)
    pattern_parameters: Mapped[Any] = jsonb("{}")
    content_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


class AgentRole(Base):
    """role_model_map de design.md: una ModelConfiguration por rol."""

    __tablename__ = "agent_roles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["agent_id", "agent_version"],
            ["agent_configurations.id", "agent_configurations.version"],
        ),
        ForeignKeyConstraint(
            ["model_id", "model_version"],
            ["model_configurations.id", "model_configurations.version"],
        ),
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    agent_version: Mapped[str] = mapped_column(Text, primary_key=True)
    role: Mapped[str] = mapped_column(Text, primary_key=True)
    model_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    model_version: Mapped[str] = mapped_column(Text)


class AgentTool(Base):
    """tool_refs de design.md: AgentConfiguration N:M ToolDefinition."""

    __tablename__ = "agent_tools"
    __table_args__ = (
        ForeignKeyConstraint(
            ["agent_id", "agent_version"],
            ["agent_configurations.id", "agent_configurations.version"],
        ),
        ForeignKeyConstraint(
            ["tool_id", "tool_version"], ["tool_definitions.id", "tool_definitions.version"]
        ),
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    agent_version: Mapped[str] = mapped_column(Text, primary_key=True)
    tool_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    tool_version: Mapped[str] = mapped_column(Text)


# --- Ejecución y evaluación ---------------------------------------------------------------


class Experiment(Base):
    __tablename__ = "experiments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["benchmark_id", "benchmark_version"], ["benchmarks.id", "benchmarks.version"]
        ),
        CheckConstraint(one_of("status", EXPERIMENT_LIFECYCLE.states), name="status"),
        CheckConstraint(sha256("manifest_hash", nullable=True), name="manifest_hash_sha256"),
        CheckConstraint("repetitions > 0", name="repetitions_positive"),
        CheckConstraint("cardinality(seeds) = repetitions", name="one_seed_per_repetition"),
        CheckConstraint(
            "(status = 'draft') = (manifest_hash IS NULL AND sealed_at IS NULL)",
            name="sealed_has_manifest",
        ),
        CheckConstraint(
            "status = 'draft' OR benchmark_id IS NOT NULL", name="sealed_has_benchmark"
        ),
        CheckConstraint("(manifest IS NULL) = (manifest_hash IS NULL)", name="manifest_with_hash"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    hypothesis: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'draft'"))
    manifest: Mapped[Any | None] = mapped_column(JSONB)
    manifest_hash: Mapped[str | None] = mapped_column(Text)
    benchmark_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    benchmark_version: Mapped[str | None] = mapped_column(Text)
    budgets: Mapped[Any] = jsonb("{}")
    repetitions: Mapped[int] = mapped_column(Integer)
    seeds: Mapped[list[int]] = mapped_column(ARRAY(BigInteger))
    comparison_plan: Mapped[Any] = jsonb("{}")
    created_at: Mapped[datetime] = created_at()
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExperimentAgent(Base):
    __tablename__ = "experiment_agents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["agent_id", "agent_version"],
            ["agent_configurations.id", "agent_configurations.version"],
        ),
    )

    experiment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("experiments.id"), primary_key=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    agent_version: Mapped[str] = mapped_column(Text, primary_key=True)


RUN_ACTIVE = ("queued", "running")


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["experiment_id", "agent_id", "agent_version"],
            [
                "experiment_agents.experiment_id",
                "experiment_agents.agent_id",
                "experiment_agents.agent_version",
            ],
        ),
        ForeignKeyConstraint(
            ["scenario_id", "scenario_version"], ["scenarios.id", "scenarios.version"]
        ),
        UniqueConstraint(
            "experiment_id",
            "scenario_id",
            "scenario_version",
            "agent_id",
            "agent_version",
            "repetition",
            "seed",
            name="uq_runs_experimental_cell",
        ),
        CheckConstraint(one_of("status", RUN_LIFECYCLE.states), name="status"),
        CheckConstraint(one_of("mode", vocab.RUN_MODES), name="mode"),
        CheckConstraint(
            f"error_class IS NULL OR {one_of('error_class', vocab.RUN_ERROR_CLASSES)}",
            name="error_class",
        ),
        CheckConstraint("repetition >= 1", name="repetition_positive"),
        CheckConstraint(
            f"({one_of('status', RUN_ACTIVE)}) = (ended_at IS NULL)", name="ended_iff_terminal"
        ),
        CheckConstraint("status <> 'queued' OR started_at IS NULL", name="queued_not_started"),
        CheckConstraint("status <> 'running' OR started_at IS NOT NULL", name="running_started"),
        CheckConstraint(
            "started_at IS NULL OR ended_at IS NULL OR ended_at >= started_at",
            name="ended_after_started",
        ),
        CheckConstraint("status <> 'failed' OR error_class IS NOT NULL", name="failed_has_error"),
        CheckConstraint(
            "error_class IS NULL OR status NOT IN ('queued', 'running', 'completed')",
            name="error_only_when_unsuccessful",
        ),
        CheckConstraint("fencing_token >= 0", name="fencing_token_non_negative"),
        CheckConstraint("status <> 'running' OR fencing_token >= 1", name="running_has_token"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    experiment_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    scenario_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    scenario_version: Mapped[str] = mapped_column(Text)
    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    agent_version: Mapped[str] = mapped_column(Text)
    repetition: Mapped[int] = mapped_column(Integer)
    seed: Mapped[int] = mapped_column(BigInteger)
    mode: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))
    error_class: Mapped[str | None] = mapped_column(Text)
    result: Mapped[Any | None] = mapped_column(JSONB)
    fencing_token: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    created_at: Mapped[datetime] = created_at()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunAttempt(Base):
    """Intento operativo de un run: lease con vencimiento y fencing token monotónico.

    Sólo el intento cuyo token coincide con `runs.fencing_token` puede persistir resultados.
    """

    __tablename__ = "run_attempts"
    __table_args__ = (
        UniqueConstraint("run_id", "attempt_number"),
        UniqueConstraint("run_id", "fencing_token"),
        CheckConstraint(one_of("status", vocab.RUN_ATTEMPT_STATUSES), name="status"),
        CheckConstraint("attempt_number >= 1", name="attempt_number_positive"),
        CheckConstraint("fencing_token >= 1", name="fencing_token_positive"),
        CheckConstraint("(status = 'active') = (ended_at IS NULL)", name="ended_iff_inactive"),
        Index(
            "uq_run_attempts_active_run",
            "run_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("runs.id"))
    attempt_number: Mapped[int] = mapped_column(Integer)
    fencing_token: Mapped[int] = mapped_column(BigInteger)
    worker_id: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Trace(Base):
    __tablename__ = "traces"
    __table_args__ = (
        UniqueConstraint("run_id"),
        UniqueConstraint("run_id", "digest"),
        CheckConstraint(sha256("digest", nullable=True), name="digest_sha256"),
        CheckConstraint(
            f"completeness IS NULL OR {one_of('completeness', vocab.TRACE_COMPLETENESS)}",
            name="completeness",
        ),
        CheckConstraint("event_count >= 0", name="event_count_non_negative"),
        CheckConstraint(
            "(sealed_at IS NULL) = (digest IS NULL)"
            " AND (sealed_at IS NULL) = (completeness IS NULL)",
            name="sealed_has_digest",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("runs.id"))
    schema_version: Mapped[str] = mapped_column(Text)
    event_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    digest: Mapped[str | None] = mapped_column(Text)
    completeness: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TraceEvent(Base):
    """Evento de evidencia; el sink lo acumula en memoria y el worker lo persiste al sellar."""

    __tablename__ = "trace_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence"),
        CheckConstraint(one_of("type", vocab.TRACE_EVENT_TYPES), name="type"),
        CheckConstraint(one_of("actor_role", vocab.TRACE_ACTOR_ROLES), name="actor_role"),
        CheckConstraint(sha256("payload_digest"), name="payload_digest_sha256"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint("elapsed_ms >= 0", name="elapsed_non_negative"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=func.gen_random_uuid()
    )
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("runs.id"))
    attempt_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("run_attempts.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    elapsed_ms: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(Text)
    actor_role: Mapped[str] = mapped_column(Text)
    parent_event_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    otel_trace_id: Mapped[str | None] = mapped_column(Text)
    otel_span_id: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[Any] = jsonb()
    payload_digest: Mapped[str] = mapped_column(Text)
    redaction_metadata: Mapped[Any] = jsonb("{}")


class Evaluation(Base):
    """Cada (re)evaluación es una fila nueva sobre una Trace sellada; no modifica el Run."""

    __tablename__ = "evaluations"
    __table_args__ = (
        ForeignKeyConstraint(["run_id", "trace_digest"], ["traces.run_id", "traces.digest"]),
        CheckConstraint(one_of("status", EVALUATION_LIFECYCLE.states), name="status"),
        CheckConstraint(sha256("trace_digest"), name="trace_digest_sha256"),
        CheckConstraint(sha256("evaluator_suite_hash"), name="evaluator_suite_hash_sha256"),
        CheckConstraint("parent_evaluation_id <> id", name="parent_not_self"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    trace_digest: Mapped[str] = mapped_column(Text)
    evaluator_suite_hash: Mapped[str] = mapped_column(Text)
    parent_evaluation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("evaluations.id")
    )
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    created_at: Mapped[datetime] = created_at()


class Score(Base):
    __tablename__ = "scores"
    __table_args__ = (
        UniqueConstraint("evaluation_id", "metric_id", "metric_version", "scope"),
        CheckConstraint(one_of("status", vocab.SCORE_STATUSES), name="status"),
        CheckConstraint(one_of("scope", vocab.SCORE_SCOPES), name="scope"),
        CheckConstraint(semver("metric_version"), name="metric_version_semver"),
        CheckConstraint(
            "status NOT IN ('unknown', 'not_applicable', 'error') OR value IS NULL",
            name="missing_has_no_value",
        ),
        CheckConstraint(
            "value IS NULL OR value NOT IN ('NaN', 'Infinity', '-Infinity')",
            name="value_finite",
        ),
        CheckConstraint("(numerator IS NULL) = (denominator IS NULL)", name="ratio_complete"),
        CheckConstraint(
            "denominator IS NULL OR (numerator >= 0 AND numerator <= denominator)",
            name="ratio_bounds",
        ),
        CheckConstraint(
            "denominator IS NULL OR denominator <> 0 OR value IS NULL",
            name="zero_denominator_no_value",
        ),
        CheckConstraint("jsonb_typeof(evidence_refs) = 'array'", name="evidence_refs_array"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    evaluation_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("evaluations.id"))
    metric_id: Mapped[str] = mapped_column(Text)
    metric_version: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(Text)
    value: Mapped[float | None] = mapped_column(Double)
    unit: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    numerator: Mapped[Decimal | None] = mapped_column(Numeric)
    denominator: Mapped[Decimal | None] = mapped_column(Numeric)
    evidence_refs: Mapped[Any] = jsonb("[]")


class IdempotencyKey(Base):
    """Clave registrada en la misma transacción que el efecto de la solicitud."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        CheckConstraint(sha256("request_hash"), name="request_hash_sha256"),
        CheckConstraint("char_length(key) BETWEEN 1 AND 255", name="key_length"),
        CheckConstraint("(status_code IS NULL) = (response IS NULL)", name="response_complete"),
    )

    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text)
    status_code: Mapped[int | None] = mapped_column(Integer)
    response: Mapped[Any | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at()
