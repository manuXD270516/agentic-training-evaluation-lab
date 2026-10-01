"""Publicación atómica de fixtures, tools, escenarios, datasets y benchmarks."""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from evallab.canonical import canonical_digest
from evallab.db import models as m
from evallab.domain import vocabulary as vocab
from evallab.schemas import (
    BenchmarkCreate,
    BenchmarkOut,
    DatasetCreate,
    DatasetOut,
    DigestRef,
    FixtureCreate,
    FixtureOut,
    ScenarioCreate,
    ScenarioOracleOut,
    ScenarioPublicOut,
    ToolCreate,
    ToolOut,
    VersionRef,
)
from evallab.services.errors import (
    ContentDuplicateError,
    HashMismatchError,
    InvalidReferenceError,
    InvalidRequestError,
    NotFoundError,
    VersionExistsError,
)


class CategorySplit(Protocol):
    primary_category: str
    split: str | None
    family_id: str | None


HASH_EXCLUDE = frozenset({"id", "content_hash", "created_at", "digest"})
PILOT_PER_CATEGORY = 2
COMPLETE_PER_CATEGORY = 10
COMPLETE_DEV = 42
COMPLETE_HELD_OUT = 28


def _digest(document: dict[str, Any]) -> str:
    return canonical_digest({k: v for k, v in document.items() if k not in HASH_EXCLUDE})


def _check_client_hash(computed: str, provided: str | None) -> None:
    if provided is not None and provided != computed:
        raise HashMismatchError(
            "el content_hash no coincide con el documento canónico",
            computed=computed,
            provided=provided,
        )


def _version_exists(kind: str, ref: VersionRef, content_hash: str, existing_hash: str) -> None:
    raise VersionExistsError(
        f"ya existe una versión publicada de {kind}",
        id=str(ref.id),
        version=ref.version,
        content_hash=existing_hash,
        same_content=existing_hash == content_hash,
    )


def _content_duplicate(kind: str, row: Any) -> None:
    raise ContentDuplicateError(
        f"el mismo contenido ya está publicado como {kind}",
        id=str(row.id),
        version=row.version,
        content_hash=row.content_hash,
    )


def _public_environment(environment: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in environment.items() if k != "fault_schedule"}


# --- Fixtures ---------------------------------------------------------------------------


def fixture_document(data: FixtureCreate) -> dict[str, Any]:
    return {"name": data.name, "payload": data.payload}


def publish_fixture(db: Session, data: FixtureCreate) -> m.Fixture:
    document = fixture_document(data)
    content_hash = _digest(document)
    _check_client_hash(content_hash, data.content_hash)
    existing = db.get(m.Fixture, content_hash)
    if existing is not None:
        if existing.name != data.name or existing.payload != data.payload:
            raise ContentDuplicateError("hash de fixture en conflicto", content_hash=content_hash)
        return existing
    row = m.Fixture(content_hash=content_hash, name=data.name, payload=data.payload)
    db.add(row)
    db.flush()
    return row


def get_fixture(db: Session, content_hash: str) -> m.Fixture:
    row = db.get(m.Fixture, content_hash)
    if row is None:
        raise NotFoundError("fixture inexistente", content_hash=content_hash)
    return row


def fixture_to_out(row: m.Fixture) -> FixtureOut:
    return FixtureOut(
        content_hash=row.content_hash, name=row.name, payload=row.payload, created_at=row.created_at
    )


# --- Tools ------------------------------------------------------------------------------


def tool_document(data: ToolCreate, tool_id: uuid.UUID) -> dict[str, Any]:
    return {
        "id": str(tool_id),
        "version": data.version,
        "name": data.name,
        "input_schema": data.input_schema,
        "output_schema": data.output_schema,
        "effect_class": data.effect_class,
        "allowed_scope": data.allowed_scope,
        "timeout_ms": data.timeout_ms,
        "fixture_hash": data.fixture_hash,
    }


def publish_tool(db: Session, data: ToolCreate) -> m.ToolDefinition:
    if data.fixture_hash is not None and db.get(m.Fixture, data.fixture_hash) is None:
        raise InvalidReferenceError(
            "fixture de la tool inexistente", fixture_hash=data.fixture_hash
        )
    tool_id = data.id or uuid.uuid4()
    document = tool_document(data, tool_id)
    content_hash = _digest(document)
    _check_client_hash(content_hash, data.content_hash)
    existing = db.get(m.ToolDefinition, (tool_id, data.version))
    if existing is not None:
        _version_exists(
            "tool",
            VersionRef(id=tool_id, version=data.version),
            content_hash,
            existing.content_hash,
        )
    twin = db.scalar(select(m.ToolDefinition).where(m.ToolDefinition.content_hash == content_hash))
    if twin is not None:
        _content_duplicate("tool", twin)
    row = m.ToolDefinition(
        id=tool_id,
        version=data.version,
        name=data.name,
        input_schema=data.input_schema,
        output_schema=data.output_schema,
        effect_class=data.effect_class,
        allowed_scope=data.allowed_scope,
        timeout_ms=data.timeout_ms,
        fixture_hash=data.fixture_hash,
        content_hash=content_hash,
    )
    db.add(row)
    db.flush()
    return row


def get_tool(db: Session, tool_id: uuid.UUID, version: str) -> m.ToolDefinition:
    row = db.get(m.ToolDefinition, (tool_id, version))
    if row is None:
        raise NotFoundError("tool inexistente", id=str(tool_id), version=version)
    return row


def tool_to_out(row: m.ToolDefinition) -> ToolOut:
    return ToolOut(
        id=row.id,
        version=row.version,
        name=row.name,
        input_schema=row.input_schema,
        output_schema=row.output_schema,
        effect_class=row.effect_class,
        allowed_scope=row.allowed_scope,
        timeout_ms=row.timeout_ms,
        fixture_hash=row.fixture_hash,
        content_hash=row.content_hash,
        created_at=row.created_at,
    )


# --- Scenarios --------------------------------------------------------------------------


def _resolve_tools(db: Session, refs: list[DigestRef]) -> list[m.ToolDefinition]:
    tools: list[m.ToolDefinition] = []
    for ref in refs:
        tool = db.get(m.ToolDefinition, (ref.id, ref.version))
        if tool is None:
            raise InvalidReferenceError("tool inexistente", tool=ref.model_dump(mode="json"))
        if tool.content_hash != ref.content_hash:
            raise InvalidReferenceError(
                "digest de tool no coincide",
                tool=ref.model_dump(mode="json"),
                published=tool.content_hash,
            )
        tools.append(tool)
    return tools


def _resolve_fixtures(db: Session, hashes: list[str]) -> None:
    missing = [h for h in hashes if db.get(m.Fixture, h) is None]
    if missing:
        raise InvalidReferenceError("fixture inexistente", fixture_refs=missing)


def _validate_oracle(data: ScenarioCreate, tools: list[m.ToolDefinition]) -> None:
    names = {tool.name for tool in tools}
    for check in data.expected.checks:
        if check.operator in {"required_tool", "arguments_equal"} and check.tool not in names:
            raise InvalidReferenceError(
                "el oráculo referencia una tool que no está en el escenario", tool=check.tool
            )
        if check.operator == "abstention_required" and (
            data.retrieval is None or not data.retrieval.unanswerable
        ):
            raise InvalidRequestError("abstention_required exige retrieval.unanswerable")
    if data.primary_category == "retrieval" and data.retrieval is None:
        raise InvalidRequestError("un escenario retrieval exige el bloque retrieval")
    uses_citations = any(c.operator == "citation_supported" for c in data.expected.checks)
    if uses_citations and (data.retrieval is None or data.retrieval.retriever_ref is None):
        raise InvalidRequestError("citation_supported exige un retriever versionado")


def _validate_retrieval(db: Session, data: ScenarioCreate) -> None:
    """Refs de retrieval resolubles y coherentes: corpus → embeddings → retriever y qrels."""
    spec = data.retrieval
    if spec is None:
        return
    corpus = db.get(m.Corpus, spec.corpus_ref)
    if corpus is None and db.get(m.Fixture, spec.corpus_ref) is None:
        raise InvalidReferenceError("corpus inexistente", corpus_ref=spec.corpus_ref)
    if spec.retriever_ref is None:
        return
    retriever = db.get(m.RetrieverConfig, spec.retriever_ref)
    if retriever is None or corpus is None:
        raise InvalidReferenceError("retriever o corpus versionado inexistente")
    embedding_set = db.get(m.EmbeddingSet, retriever.embedding_set)
    if embedding_set is None or embedding_set.corpus_hash != corpus.content_hash:
        raise InvalidReferenceError("el retriever no pertenece al corpus declarado")
    if spec.embedding_set_ref is not None and spec.embedding_set_ref != embedding_set.content_hash:
        raise InvalidReferenceError("embedding_set_ref no coincide con el del retriever")
    if spec.top_k != retriever.top_k:
        raise InvalidRequestError("top_k distinto del fijado por el retriever")
    relevant = spec.qrels.get("relevant") if spec.qrels else None
    if not spec.unanswerable:
        if not isinstance(relevant, list) or not relevant:
            raise InvalidRequestError("qrels de un retriever versionado exige `relevant`")
        known = set(
            db.scalars(
                select(m.CorpusChunk.chunk_id).where(
                    m.CorpusChunk.corpus_hash == corpus.content_hash
                )
            )
        )
        missing = [c for c in relevant if c not in known]
        if missing:
            raise InvalidReferenceError("qrels con chunks inexistentes", chunks=missing)


def scenario_document(data: ScenarioCreate, scenario_id: uuid.UUID) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "schema_version": data.schema_version,
        "id": str(scenario_id),
        "version": data.version,
        "slug": data.slug,
        "primary_category": data.primary_category,
        "tags": list(data.tags),
        "difficulty": data.difficulty,
        "split": data.split,
        "family_id": data.family_id,
        "task": data.task.model_dump(mode="json"),
        "tools": [r.model_dump(mode="json") for r in data.tools],
        "environment": data.environment.model_dump(mode="json", exclude_none=True),
        "limits": data.limits.model_dump(mode="json"),
        "expected": data.expected.model_dump(mode="json", exclude_none=True),
        "evaluation": data.evaluation.model_dump(mode="json", exclude_none=True),
    }
    if data.retrieval is not None:
        doc["retrieval"] = data.retrieval.model_dump(mode="json", exclude_none=True)
    if data.recovery is not None:
        doc["recovery"] = data.recovery.model_dump(mode="json")
    return doc


def publish_scenario(db: Session, data: ScenarioCreate) -> m.Scenario:
    tools = _resolve_tools(db, data.tools)
    _resolve_fixtures(db, data.environment.fixture_refs)
    _validate_oracle(data, tools)
    _validate_retrieval(db, data)
    scenario_id = data.id or uuid.uuid4()
    document = scenario_document(data, scenario_id)
    content_hash = _digest(document)
    _check_client_hash(content_hash, data.content_hash)
    existing = db.get(m.Scenario, (scenario_id, data.version))
    if existing is not None:
        _version_exists(
            "scenario",
            VersionRef(id=scenario_id, version=data.version),
            content_hash,
            existing.content_hash,
        )
    twin = db.scalar(select(m.Scenario).where(m.Scenario.content_hash == content_hash))
    if twin is not None:
        _content_duplicate("scenario", twin)
    slug_taken = db.scalar(
        select(m.Scenario).where(m.Scenario.slug == data.slug, m.Scenario.version == data.version)
    )
    if slug_taken is not None:
        raise VersionExistsError(
            "slug ya publicado en esta versión", slug=data.slug, version=data.version
        )
    row = m.Scenario(
        id=scenario_id,
        version=data.version,
        schema_version=data.schema_version,
        slug=data.slug,
        primary_category=data.primary_category,
        tags=list(data.tags),
        difficulty=data.difficulty,
        split=data.split,
        family_id=data.family_id,
        input=data.task.input,
        task=data.task.model_dump(mode="json"),
        tools=[r.model_dump(mode="json") for r in data.tools],
        environment=data.environment.model_dump(mode="json", exclude_none=True),
        fixtures=list(data.environment.fixture_refs),
        oracle_ref=data.expected.model_dump(mode="json", exclude_none=True),
        evaluation=data.evaluation.model_dump(mode="json", exclude_none=True),
        limits=data.limits.model_dump(mode="json"),
        retrieval=data.retrieval.model_dump(mode="json", exclude_none=True)
        if data.retrieval
        else None,
        recovery=data.recovery.model_dump(mode="json") if data.recovery else None,
        content_hash=content_hash,
    )
    db.add(row)
    db.flush()
    return row


def get_scenario(db: Session, scenario_id: uuid.UUID, version: str) -> m.Scenario:
    row = db.get(m.Scenario, (scenario_id, version))
    if row is None:
        raise NotFoundError("escenario inexistente", id=str(scenario_id), version=version)
    return row


def scenario_public_view(row: m.Scenario) -> ScenarioPublicOut:
    environment = row.environment if isinstance(row.environment, dict) else {}
    task = row.task if isinstance(row.task, dict) else {"input": row.input}
    return ScenarioPublicOut(
        id=row.id,
        version=row.version,
        schema_version=row.schema_version,
        slug=row.slug,
        content_hash=row.content_hash,
        primary_category=row.primary_category,
        tags=list(row.tags),
        difficulty=row.difficulty,
        task=task,
        tools=list(row.tools) if isinstance(row.tools, list) else [],
        environment=_public_environment(environment),
        limits=row.limits if isinstance(row.limits, dict) else {},
        created_at=row.created_at,
    )


def scenario_oracle_view(row: m.Scenario) -> ScenarioOracleOut:
    return ScenarioOracleOut(
        id=row.id,
        version=row.version,
        content_hash=row.content_hash,
        split=row.split,
        family_id=row.family_id,
        expected=row.oracle_ref,
        evaluation=row.evaluation,
        environment=row.environment,
        retrieval=row.retrieval,
        recovery=row.recovery,
    )


# --- Datasets ---------------------------------------------------------------------------


def coverage_class(scenarios: Sequence[CategorySplit]) -> str:
    families: dict[str, set[str]] = defaultdict(set)
    for row in scenarios:
        if row.split and row.family_id:
            families[row.split].add(row.family_id)
    overlap = families.get("dev", set()) & families.get("held-out", set())
    if overlap:
        raise InvalidRequestError("family_id no puede cruzar splits", families=sorted(overlap))
    counts = Counter(row.primary_category for row in scenarios)
    splits = Counter(row.split for row in scenarios)
    n = len(scenarios)
    if (
        n == COMPLETE_PER_CATEGORY * len(vocab.PRIMARY_CATEGORIES)
        and all(counts[c] == COMPLETE_PER_CATEGORY for c in vocab.PRIMARY_CATEGORIES)
        and splits.get("dev") == COMPLETE_DEV
        and splits.get("held-out") == COMPLETE_HELD_OUT
    ):
        return "complete_v1"
    if (
        n == PILOT_PER_CATEGORY * len(vocab.PRIMARY_CATEGORIES)
        and all(counts[c] == PILOT_PER_CATEGORY for c in vocab.PRIMARY_CATEGORIES)
        and splits.get("held-out", 0) == 0
    ):
        return "pilot"
    return "incomplete"


def _load_scenario_refs(db: Session, refs: list[DigestRef]) -> list[m.Scenario]:
    rows: list[m.Scenario] = []
    for ref in refs:
        row = db.get(m.Scenario, (ref.id, ref.version))
        if row is None:
            raise InvalidReferenceError(
                "escenario inexistente", scenario=ref.model_dump(mode="json")
            )
        if row.content_hash != ref.content_hash:
            raise InvalidReferenceError(
                "digest de escenario no coincide",
                scenario=ref.model_dump(mode="json"),
                published=row.content_hash,
            )
        rows.append(row)
    return rows


def dataset_document(
    data: DatasetCreate,
    dataset_id: uuid.UUID,
    scenarios: list[m.Scenario],
    class_: str,
) -> dict[str, Any]:
    split_manifest = {
        "dev": [str(s.id) for s in scenarios if s.split == "dev"],
        "held-out": [str(s.id) for s in scenarios if s.split == "held-out"],
    }
    category_counts = {
        category: sum(1 for s in scenarios if s.primary_category == category)
        for category in vocab.PRIMARY_CATEGORIES
    }
    return {
        "schema_version": data.schema_version,
        "id": str(dataset_id),
        "version": data.version,
        "name": data.name,
        "synthetic": True,
        "license": data.license,
        "generator_version": data.generator_version,
        "scenario_refs": [r.model_dump(mode="json") for r in data.scenario_refs],
        "split_manifest": split_manifest,
        "category_counts": category_counts,
        "fixture_refs": list(data.fixture_refs),
        "corpus_refs": list(data.corpus_refs),
        "coverage_class": class_,
    }


def publish_dataset(db: Session, data: DatasetCreate) -> m.Dataset:
    _resolve_fixtures(db, data.fixture_refs)
    scenarios = _load_scenario_refs(db, data.scenario_refs)
    required_fixtures = {h for s in scenarios for h in (s.fixtures or [])}
    missing = sorted(required_fixtures - set(data.fixture_refs))
    if missing:
        raise InvalidReferenceError(
            "el dataset no declara fixtures usadas por sus escenarios", fixture_refs=missing
        )
    class_ = coverage_class(scenarios)
    dataset_id = data.id or uuid.uuid4()
    document = dataset_document(data, dataset_id, scenarios, class_)
    content_hash = _digest(document)
    _check_client_hash(content_hash, data.content_hash)
    existing = db.get(m.Dataset, (dataset_id, data.version))
    if existing is not None:
        _version_exists(
            "dataset",
            VersionRef(id=dataset_id, version=data.version),
            content_hash,
            existing.content_hash,
        )
    twin = db.scalar(select(m.Dataset).where(m.Dataset.content_hash == content_hash))
    if twin is not None:
        _content_duplicate("dataset", twin)
    row = m.Dataset(
        id=dataset_id,
        version=data.version,
        name=data.name,
        schema_version=data.schema_version,
        content_hash=content_hash,
        split_manifest=document["split_manifest"],
        synthetic=True,
        license=data.license,
        generator_version=data.generator_version,
        fixture_refs=list(data.fixture_refs),
        corpus_refs=list(data.corpus_refs),
        category_counts=document["category_counts"],
        coverage_class=class_,
        manifest=document | {"content_hash": content_hash},
    )
    db.add(row)
    db.flush()
    db.add_all(
        m.DatasetScenario(
            dataset_id=row.id,
            dataset_version=row.version,
            scenario_id=scenario.id,
            scenario_version=scenario.version,
            position=index,
        )
        for index, scenario in enumerate(scenarios)
    )
    db.flush()
    return row


def get_dataset(db: Session, dataset_id: uuid.UUID, version: str) -> m.Dataset:
    row = db.get(m.Dataset, (dataset_id, version))
    if row is None:
        raise NotFoundError("dataset inexistente", id=str(dataset_id), version=version)
    return row


def dataset_to_out(row: m.Dataset) -> DatasetOut:
    manifest = row.manifest if isinstance(row.manifest, dict) else {}
    return DatasetOut(
        id=row.id,
        version=row.version,
        name=row.name,
        schema_version=row.schema_version,
        license=row.license,
        generator_version=row.generator_version,
        synthetic=row.synthetic,
        content_hash=row.content_hash,
        coverage_class=row.coverage_class,
        category_counts=row.category_counts if isinstance(row.category_counts, dict) else {},
        split_manifest=row.split_manifest if isinstance(row.split_manifest, dict) else {},
        scenario_refs=list(manifest.get("scenario_refs", [])),
        fixture_refs=list(row.fixture_refs) if isinstance(row.fixture_refs, list) else [],
        corpus_refs=list(row.corpus_refs) if isinstance(row.corpus_refs, list) else [],
        created_at=row.created_at,
    )


# --- Benchmarks -------------------------------------------------------------------------


def publish_benchmark(db: Session, data: BenchmarkCreate) -> m.Benchmark:
    dataset = get_dataset(db, data.dataset_ref.id, data.dataset_ref.version)
    if dataset.content_hash != data.dataset_ref.content_hash:
        raise InvalidReferenceError(
            "digest de dataset no coincide",
            dataset=data.dataset_ref.model_dump(mode="json"),
            published=dataset.content_hash,
        )
    members = list(
        db.scalars(
            select(m.DatasetScenario)
            .where(
                m.DatasetScenario.dataset_id == dataset.id,
                m.DatasetScenario.dataset_version == dataset.version,
            )
            .order_by(m.DatasetScenario.position)
        )
    )
    member_keys = {(r.scenario_id, r.scenario_version) for r in members}
    selection = data.selection or [
        DigestRef(
            id=link.scenario_id,
            version=link.scenario_version,
            content_hash=get_scenario(db, link.scenario_id, link.scenario_version).content_hash,
        )
        for link in members
    ]
    for ref in selection:
        if (ref.id, ref.version) not in member_keys:
            raise InvalidReferenceError(
                "la selección incluye un escenario ajeno al dataset",
                scenario=ref.model_dump(mode="json"),
            )
        published = get_scenario(db, ref.id, ref.version)
        if published.content_hash != ref.content_hash:
            raise InvalidReferenceError(
                "digest de escenario no coincide",
                scenario=ref.model_dump(mode="json"),
            )
    bench_id = data.id or uuid.uuid4()
    document = {
        "schema_version": data.schema_version,
        "id": str(bench_id),
        "version": data.version,
        "dataset_ref": data.dataset_ref.model_dump(mode="json"),
        "selection": [r.model_dump(mode="json") for r in selection],
        "evaluator_suite": data.evaluator_suite,
        "metric_profile": data.metric_profile,
        "comparison_rules": data.comparison_rules,
        "default_repetitions": data.default_repetitions,
        "seed_schedule": list(data.seed_schedule),
        "budgets": data.budgets,
    }
    content_hash = _digest(document)
    _check_client_hash(content_hash, data.content_hash)
    existing = db.get(m.Benchmark, (bench_id, data.version))
    if existing is not None:
        _version_exists(
            "benchmark",
            VersionRef(id=bench_id, version=data.version),
            content_hash,
            existing.content_hash,
        )
    twin = db.scalar(select(m.Benchmark).where(m.Benchmark.content_hash == content_hash))
    if twin is not None:
        _content_duplicate("benchmark", twin)
    row = m.Benchmark(
        id=bench_id,
        version=data.version,
        dataset_id=dataset.id,
        dataset_version=dataset.version,
        dataset_content_hash=dataset.content_hash,
        scenario_selection=[r.model_dump(mode="json") for r in selection],
        evaluator_suite=data.evaluator_suite,
        metric_profile=data.metric_profile,
        comparison_rules=data.comparison_rules,
        default_repetitions=data.default_repetitions,
        seed_schedule=list(data.seed_schedule),
        budgets=data.budgets,
        manifest=document | {"content_hash": content_hash},
        content_hash=content_hash,
    )
    db.add(row)
    db.flush()
    return row


def get_benchmark(db: Session, benchmark_id: uuid.UUID, version: str) -> m.Benchmark:
    row = db.get(m.Benchmark, (benchmark_id, version))
    if row is None:
        raise NotFoundError("benchmark inexistente", id=str(benchmark_id), version=version)
    return row


def benchmark_to_out(row: m.Benchmark) -> BenchmarkOut:
    return BenchmarkOut(
        id=row.id,
        version=row.version,
        dataset_ref=DigestRef(
            id=row.dataset_id,
            version=row.dataset_version,
            content_hash=row.dataset_content_hash or "",
        ),
        selection=list(row.scenario_selection) if isinstance(row.scenario_selection, list) else [],
        evaluator_suite=row.evaluator_suite if isinstance(row.evaluator_suite, dict) else {},
        metric_profile=row.metric_profile if isinstance(row.metric_profile, dict) else {},
        comparison_rules=row.comparison_rules if isinstance(row.comparison_rules, dict) else {},
        default_repetitions=row.default_repetitions,
        seed_schedule=list(row.seed_schedule) if isinstance(row.seed_schedule, list) else [],
        budgets=row.budgets if isinstance(row.budgets, dict) else {},
        content_hash=row.content_hash,
        created_at=row.created_at,
    )
