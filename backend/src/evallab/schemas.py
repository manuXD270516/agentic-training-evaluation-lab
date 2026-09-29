import uuid
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import JsonValue as JsonValue

from evallab.db.models import SEMVER, SHA256

Version = Annotated[str, Field(pattern=SEMVER)]
Digest = Annotated[str, Field(pattern=SHA256)]
Slug = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=1, max_length=128)]
Seed = Annotated[int, Field(ge=0, le=2**63 - 1)]
JsonObject = dict[str, JsonValue]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VersionRef(StrictModel):
    id: uuid.UUID
    version: Version


class DigestRef(VersionRef):
    content_hash: Digest


def _unique_refs(refs: list[VersionRef] | None) -> None:
    if refs is not None and len({(r.id, r.version) for r in refs}) != len(refs):
        raise ValueError("agents contiene referencias duplicadas")


class ExperimentCreate(StrictModel):
    hypothesis: str = Field(min_length=1)
    benchmark: VersionRef | None = None
    agents: list[VersionRef] = Field(default_factory=list)
    budgets: JsonObject = Field(default_factory=dict)
    repetitions: int = Field(ge=1, le=1000)
    seeds: list[Seed]
    comparison_plan: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if len(self.seeds) != self.repetitions:
            raise ValueError("seeds debe tener una seed por repetición")
        _unique_refs(self.agents)
        return self


class ExperimentUpdate(StrictModel):
    """Campos editables de un draft; los ausentes no cambian."""

    hypothesis: str | None = Field(default=None, min_length=1)
    benchmark: VersionRef | None = None
    agents: list[VersionRef] | None = None
    budgets: JsonObject | None = None
    repetitions: int | None = Field(default=None, ge=1, le=1000)
    seeds: list[Seed] | None = None
    comparison_plan: JsonObject | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _unique_refs(self.agents)
        return self


class ExperimentOut(BaseModel):
    id: uuid.UUID
    status: str
    hypothesis: str
    benchmark: VersionRef | None
    agents: list[VersionRef]
    budgets: JsonObject
    repetitions: int
    seeds: list[int]
    comparison_plan: JsonObject
    manifest_hash: str | None
    created_at: datetime
    sealed_at: datetime | None


class ManifestOut(BaseModel):
    experiment_id: uuid.UUID
    manifest_hash: str
    manifest: JsonObject


class RunCreate(StrictModel):
    scenario: VersionRef
    agent: VersionRef
    repetition: int = Field(ge=1)
    mode: Literal["live", "replay"]


class RunOut(BaseModel):
    id: uuid.UUID
    experiment_id: uuid.UUID
    scenario: VersionRef
    agent: VersionRef
    repetition: int
    seed: int
    mode: str
    source_run_id: uuid.UUID | None = None
    status: str
    error_class: str | None
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    result: JsonObject | None = None


class TraceEventOut(BaseModel):
    event_id: uuid.UUID
    sequence: int
    timestamp_utc: datetime
    elapsed_ms: int
    type: str
    actor_role: str
    parent_event_id: uuid.UUID | None
    payload: JsonObject
    payload_digest: str
    redaction_metadata: JsonObject


class TraceOut(BaseModel):
    run_id: uuid.UUID
    schema_version: str
    event_count: int
    digest: str | None
    completeness: str | None
    sealed_at: datetime | None
    events: list[TraceEventOut]


class ScoreOut(BaseModel):
    metric_id: str
    metric_version: str
    scope: str
    status: str
    unit: str
    value: float | None
    numerator: int | None
    denominator: int | None
    evidence_refs: list[JsonObject]


class EvaluationOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    trace_digest: str
    evaluator_suite_hash: str
    evaluator_suite_version: str | None
    metric_profile_version: str | None
    metric_profile_hash: str | None
    parent_evaluation_id: uuid.UUID | None
    status: str
    error: str | None
    report: JsonObject | None
    scores: list[ScoreOut]
    created_at: datetime
    completed_at: datetime | None


# --- Catálogo versionado (M1, 2.3) --------------------------------------------------------


class FixtureCreate(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    payload: JsonValue
    content_hash: Digest | None = None


class FixtureOut(BaseModel):
    content_hash: str
    name: str
    payload: JsonValue
    created_at: datetime


class ToolCreate(StrictModel):
    id: uuid.UUID | None = None
    version: Version
    name: Slug
    input_schema: JsonObject
    output_schema: JsonObject
    effect_class: Literal["read_only", "side_effect"]
    allowed_scope: JsonObject = Field(default_factory=dict)
    timeout_ms: int = Field(ge=1)
    fixture_hash: Digest | None = None
    content_hash: Digest | None = None


class ToolOut(BaseModel):
    id: uuid.UUID
    version: str
    name: str
    input_schema: JsonObject
    output_schema: JsonObject
    effect_class: str
    allowed_scope: JsonObject
    timeout_ms: int
    fixture_hash: str | None
    content_hash: str
    created_at: datetime


class TaskSpec(StrictModel):
    instruction: str = Field(min_length=1)
    input: JsonValue


class LimitsSpec(StrictModel):
    max_steps: int = Field(ge=1)
    max_model_calls: int = Field(ge=1)
    max_tool_calls: int = Field(ge=1)
    max_tokens: int = Field(ge=1)
    deadline_ms: int = Field(ge=1)
    max_retries: int = Field(ge=0)


class EnvironmentSpec(StrictModel):
    fixture_refs: list[Digest] = Field(default_factory=list)
    clock: JsonObject | None = None
    initial_state: JsonObject | None = None
    fault_schedule: JsonValue | None = None


class OracleCheck(StrictModel):
    operator: Literal[
        "json_value_equals",
        "json_value_numeric_equals",
        "required_tool",
        "forbidden_tool",
        "arguments_equal",
        "evidence_from_successful_call",
        "output_schema_valid",
        "abstention_required",
    ]
    path: str | None = None
    value: JsonValue | None = None
    tool: str | None = None
    min_calls: int | None = Field(default=None, ge=1)
    abs_tolerance: float | None = Field(default=None, ge=0)
    rel_tolerance: float | None = Field(default=None, ge=0)
    set_equality: bool | None = None

    @model_validator(mode="after")
    def _operator_fields(self) -> Self:
        op = self.operator
        if op in {"json_value_equals", "json_value_numeric_equals"} and (
            self.path is None or not self.path.startswith("/")
        ):
            raise ValueError("path debe ser un JSON pointer que empieza por /")
        if op == "json_value_numeric_equals" and not isinstance(self.value, int | float):
            raise ValueError("json_value_numeric_equals exige un value numérico")
        if op in {"required_tool", "forbidden_tool", "arguments_equal"} and not self.tool:
            raise ValueError(f"{op} exige tool")
        if op == "required_tool" and self.min_calls is None:
            raise ValueError("required_tool exige min_calls")
        if op == "arguments_equal" and self.value is None:
            raise ValueError("arguments_equal exige value")
        if op == "evidence_from_successful_call" and (
            self.path is None or not self.path.startswith("/")
        ):
            raise ValueError("evidence_from_successful_call exige path JSON pointer")
        return self


class ExpectedSpec(StrictModel):
    output_schema: JsonObject
    checks: list[OracleCheck] = Field(min_length=1)

    @model_validator(mode="after")
    def _output_schema(self) -> Self:
        if "type" not in self.output_schema:
            raise ValueError("output_schema debe ser un JSON Schema con type")
        return self


class EvaluationSpec(StrictModel):
    required_checks: list[
        Literal[
            "outcome",
            "output_structure",
            "required_tool",
            "semantic_arguments",
            "evidence",
            "policy",
            "retrieval",
            "recovery",
        ]
    ] = Field(min_length=1)
    applicable_metrics: list[str] = Field(min_length=1)
    suite_ref: DigestRef | None = None

    @model_validator(mode="after")
    def _metrics(self) -> Self:
        from evallab.domain.vocabulary import APPLICABLE_METRICS

        unknown = [m for m in self.applicable_metrics if m not in APPLICABLE_METRICS]
        if unknown:
            raise ValueError(f"métricas no declaradas: {unknown}")
        return self


class RetrievalSpec(StrictModel):
    corpus_ref: Digest
    top_k: int = Field(ge=1)
    unanswerable: bool = False
    qrels: JsonObject = Field(default_factory=dict)
    citation_rules: JsonObject | None = None

    @model_validator(mode="after")
    def _qrels(self) -> Self:
        if self.unanswerable and self.qrels:
            raise ValueError("una consulta sin respuesta no admite qrels")
        if not self.unanswerable and not self.qrels:
            raise ValueError("retrieval con respuesta exige qrels privados")
        return self


class RecoverySpec(StrictModel):
    trigger: str = Field(min_length=1)
    fault_id: str = Field(min_length=1)
    retry_allowed: bool
    expected_final_status: str = Field(min_length=1)


class ScenarioCreate(StrictModel):
    id: uuid.UUID | None = None
    version: Version
    schema_version: Literal["1.0"] = "1.0"
    slug: Slug
    primary_category: Literal[
        "tool_selection",
        "tool_arguments",
        "retrieval",
        "reasoning",
        "multi_step_execution",
        "error_recovery",
        "policy_compliance",
    ]
    tags: list[str] = Field(default_factory=list)
    difficulty: Literal["easy", "medium", "hard"]
    split: Literal["dev", "held-out"]
    family_id: str = Field(min_length=1, max_length=128)
    task: TaskSpec
    tools: list[DigestRef]
    environment: EnvironmentSpec = Field(default_factory=EnvironmentSpec)
    limits: LimitsSpec
    expected: ExpectedSpec
    evaluation: EvaluationSpec
    retrieval: RetrievalSpec | None = None
    recovery: RecoverySpec | None = None
    content_hash: Digest | None = None

    @model_validator(mode="after")
    def _unique_tools(self) -> Self:
        if len({(t.id, t.version) for t in self.tools}) != len(self.tools):
            raise ValueError("tools contiene referencias duplicadas")
        if "outcome" not in self.evaluation.required_checks:
            raise ValueError("required_checks debe incluir outcome")
        return self


class ScenarioPublicOut(BaseModel):
    id: uuid.UUID
    version: str
    schema_version: str
    slug: str | None
    content_hash: str
    primary_category: str
    tags: list[str]
    difficulty: str | None
    task: JsonObject
    tools: list[JsonObject]
    environment: JsonObject
    limits: JsonObject
    created_at: datetime


class ScenarioOracleOut(BaseModel):
    id: uuid.UUID
    version: str
    content_hash: str
    split: str | None
    family_id: str | None
    expected: JsonValue
    evaluation: JsonValue
    environment: JsonValue
    retrieval: JsonValue | None
    recovery: JsonValue | None


class DatasetCreate(StrictModel):
    id: uuid.UUID | None = None
    version: Version
    schema_version: Literal["1.0"] = "1.0"
    name: Slug
    license: str = Field(min_length=1)
    generator_version: Version
    scenario_refs: list[DigestRef] = Field(min_length=1)
    fixture_refs: list[Digest] = Field(default_factory=list)
    corpus_refs: list[Digest] = Field(default_factory=list)
    content_hash: Digest | None = None
    synthetic: Literal[True] = True

    @model_validator(mode="after")
    def _unique_scenarios(self) -> Self:
        if len({(r.id, r.version) for r in self.scenario_refs}) != len(self.scenario_refs):
            raise ValueError("scenario_refs contiene duplicados")
        return self


class DatasetOut(BaseModel):
    id: uuid.UUID
    version: str
    name: str
    schema_version: str
    license: str | None
    generator_version: str | None
    synthetic: bool
    content_hash: str
    coverage_class: str | None
    category_counts: JsonObject
    split_manifest: JsonObject
    scenario_refs: list[JsonObject]
    fixture_refs: list[str]
    corpus_refs: list[str]
    created_at: datetime


class BenchmarkCreate(StrictModel):
    id: uuid.UUID | None = None
    version: Version
    schema_version: Literal["1.0"] = "1.0"
    dataset_ref: DigestRef
    selection: list[DigestRef] = Field(default_factory=list)
    evaluator_suite: JsonObject
    metric_profile: JsonObject
    comparison_rules: JsonObject
    default_repetitions: int = Field(default=5, ge=1, le=1000)
    seed_schedule: list[Seed] = Field(default_factory=list)
    budgets: JsonObject = Field(default_factory=dict)
    content_hash: Digest | None = None


class BenchmarkOut(BaseModel):
    id: uuid.UUID
    version: str
    dataset_ref: DigestRef
    selection: list[JsonObject]
    evaluator_suite: JsonObject
    metric_profile: JsonObject
    comparison_rules: JsonObject
    default_repetitions: int
    seed_schedule: list[int]
    budgets: JsonObject
    content_hash: str
    created_at: datetime
