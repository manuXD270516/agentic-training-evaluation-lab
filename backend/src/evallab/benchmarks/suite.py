"""Definición declarativa de un benchmark y su publicación idempotente en el catálogo.

Las identidades se derivan con UUIDv5 del nombre y la versión, de modo que el mismo contenido
produce los mismos `content_hash` en cualquier entorno y una publicación repetida reutiliza las
versiones existentes en vez de duplicarlas. Un contenido distinto bajo la misma identidad falla
como en la API (versión publicada inmutable).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from sqlalchemy.orm import Session

from evallab.canonical import canonical_digest
from evallab.db import models as m
from evallab.retrieval.store import CorpusDef, PublishedCorpus, publish_corpus
from evallab.schemas import (
    AgentConfigurationCreate,
    BenchmarkCreate,
    DatasetCreate,
    DigestRef,
    FixtureCreate,
    ModelConfigurationCreate,
    PriceSnapshotCreate,
    ScenarioCreate,
    ToolCreate,
)
from evallab.services import agents as agent_svc
from evallab.services import catalog as svc
from evallab.services.errors import VersionExistsError

NAMESPACE = uuid.UUID("6f1c2d0e-6a51-5b7e-9c43-0d7c3b1e9a10")
VERSION = "1.0.0"


def stable_id(kind: str, name: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"{kind}:{name}")


@dataclass(frozen=True)
class ToolDef:
    name: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    effect_class: str
    fixture: dict[str, Any] | None
    timeout_ms: int = 1000
    version: str = VERSION


@dataclass(frozen=True)
class AgentDef:
    name: str
    pattern: str
    pattern_version: str
    pattern_parameters: dict[str, Any]
    tools: tuple[str, ...]
    # (rol, nombre de un ModelDef de la suite)
    roles: tuple[tuple[str, str], ...] = ()
    prompt_hash: str | None = None
    version: str = VERSION


@dataclass(frozen=True)
class ModelDef:
    """ModelConfiguration de la suite. Con `script`, el proveedor `fixture` responde con ese
    guion (publicado como Fixture y fijado por hash en `requested_model`); con `price`, el
    modelo referencia ese PriceSnapshot."""

    name: str
    script: dict[str, Any] | None = None
    price: dict[str, Any] | None = None
    provider: str = "fixture"
    requested_model: str | None = None
    resolved_revision: str | None = None
    temperature: float | None = 0.0
    max_tokens: int | None = 512
    seed_support: str = "unsupported"
    version: str = VERSION


@dataclass(frozen=True)
class Suite:
    """Contenido completo de un dataset y su benchmark.

    `scenarios` usa la forma de `ScenarioCreate` con nombres simbólicos: `tools` es una lista de
    nombres de `ToolDef`, `environment.fixture_refs` se rellena con las fixtures de sus tools y
    `retrieval.corpus_ref` nombra un corpus de `corpora`.
    """

    dataset_name: str
    dataset_version: str
    license: str
    generator_version: str
    tools: tuple[ToolDef, ...]
    scenarios: tuple[dict[str, Any], ...]
    corpora: Mapping[str, Any] = field(default_factory=dict)
    agents: tuple[AgentDef, ...] = ()
    benchmark: Mapping[str, Any] = field(default_factory=dict)
    benchmark_version: str = VERSION
    models: tuple[ModelDef, ...] = ()
    # Corpus versionados en PGVector (M9). Una tool con fixture
    # `{"kind": "retriever", "corpus": <corpus_id>}` y un escenario con
    # `retrieval.vector_corpus = <corpus_id>` se resuelven a sus hashes al publicar.
    vector_corpora: tuple[CorpusDef, ...] = ()


@dataclass
class Published:
    vector_corpora: dict[str, PublishedCorpus] = field(default_factory=dict)
    fixtures: dict[str, str] = field(default_factory=dict)
    tools: dict[str, m.ToolDefinition] = field(default_factory=dict)
    scenarios: list[m.Scenario] = field(default_factory=list)
    agents: dict[str, m.AgentConfiguration] = field(default_factory=dict)
    models: dict[str, m.ModelConfiguration] = field(default_factory=dict)
    prices: dict[str, str] = field(default_factory=dict)
    dataset: m.Dataset | None = None
    benchmark: m.Benchmark | None = None

    def lock(self) -> dict[str, Any]:
        """Hashes publicados: cualquier cambio de contenido cambia este documento."""
        assert self.dataset is not None and self.benchmark is not None
        extra: dict[str, Any] = {}
        if self.models:
            extra["models"] = {n: r.content_hash for n, r in sorted(self.models.items())}
            extra["prices"] = dict(sorted(self.prices.items()))
        if self.vector_corpora:
            extra["vector_corpora"] = {
                name: corpus.refs() for name, corpus in sorted(self.vector_corpora.items())
            }
        return {
            **extra,
            "fixtures": dict(sorted(self.fixtures.items())),
            "tools": {n: t.content_hash for n, t in sorted(self.tools.items())},
            "scenarios": {str(s.slug): s.content_hash for s in self.scenarios},
            "agents": {n: a.content_hash for n, a in sorted(self.agents.items())},
            "dataset": {
                "id": str(self.dataset.id),
                "version": self.dataset.version,
                "content_hash": self.dataset.content_hash,
                "coverage_class": self.dataset.coverage_class,
            },
            "benchmark": {
                "id": str(self.benchmark.id),
                "version": self.benchmark.version,
                "content_hash": self.benchmark.content_hash,
            },
        }


def _existing[Row](
    publish: Callable[[], Row], load: Callable[[], Row | None], hash_of: Callable[[Row], str]
) -> Row:
    """Publica o reutiliza una versión ya publicada con idéntico contenido."""
    try:
        return publish()
    except VersionExistsError as exc:
        row = load()
        if row is None or not exc.details.get("same_content"):
            raise
        assert hash_of(row) == exc.details.get("content_hash")
        return row


def _content_hash(row: Any) -> str:
    return str(row.content_hash)


def _ref(row: m.ToolDefinition | m.Scenario | m.Dataset) -> DigestRef:
    return DigestRef(id=row.id, version=row.version, content_hash=row.content_hash)


def _scenario_payload(
    raw: Mapping[str, Any], published: Published, corpora: Mapping[str, str]
) -> ScenarioCreate:
    data = dict(raw)
    tools = [published.tools[name] for name in data.get("tools", [])]
    environment = dict(data.get("environment") or {})
    fixtures = sorted({t.fixture_hash for t in tools if t.fixture_hash is not None})
    environment["fixture_refs"] = fixtures
    data["environment"] = environment
    data["tools"] = [_ref(t).model_dump(mode="json") for t in tools]
    if data.get("retrieval") is not None:
        retrieval = dict(data["retrieval"])
        vector = retrieval.pop("vector_corpus", None)
        if vector is not None:
            corpus = published.vector_corpora[str(vector)]
            retrieval["corpus_ref"] = corpus.corpus_hash
            retrieval["embedding_set_ref"] = corpus.embedding_set
            retrieval["retriever_ref"] = corpus.retriever
        else:
            retrieval["corpus_ref"] = corpora[str(retrieval["corpus_ref"])]
        data["retrieval"] = retrieval
    data["id"] = str(stable_id("scenario", str(data["slug"])))
    data.setdefault("version", VERSION)
    return ScenarioCreate.model_validate(data)


def publish_suite(db: Session, suite: Suite) -> Published:
    published = Published()
    corpora: dict[str, str] = {}
    for name, payload in sorted(suite.corpora.items()):
        corpus = svc.publish_fixture(db, FixtureCreate(name=f"corpus-{name}", payload=payload))
        corpora[name] = corpus.content_hash
        published.fixtures[f"corpus:{name}"] = corpus.content_hash
    for vector_corpus in suite.vector_corpora:
        published.vector_corpora[vector_corpus.corpus_id] = publish_corpus(db, vector_corpus)

    for tool in suite.tools:
        fixture_hash = None
        if tool.fixture is not None:
            payload = dict(tool.fixture)
            if payload.get("kind") == "retriever" and "corpus" in payload:
                retriever = published.vector_corpora[str(payload.pop("corpus"))].retriever
                payload["retriever_ref"] = retriever
            fixture = svc.publish_fixture(
                db, FixtureCreate(name=f"{tool.name}-fixture", payload=payload)
            )
            fixture_hash = fixture.content_hash
            published.fixtures[f"tool:{tool.name}"] = fixture_hash
        data = ToolCreate(
            id=stable_id("tool", tool.name),
            version=tool.version,
            name=tool.name,
            input_schema=tool.input_schema,
            output_schema=tool.output_schema,
            effect_class=tool.effect_class,
            timeout_ms=tool.timeout_ms,
            fixture_hash=fixture_hash,
        )
        published.tools[tool.name] = _existing(
            partial(svc.publish_tool, db, data),
            partial(db.get, m.ToolDefinition, (data.id, data.version)),
            _content_hash,
        )

    for raw in suite.scenarios:
        scenario = _scenario_payload(raw, published, corpora)
        published.scenarios.append(
            _existing(
                partial(svc.publish_scenario, db, scenario),
                partial(db.get, m.Scenario, (scenario.id, scenario.version)),
                _content_hash,
            )
        )

    dataset = DatasetCreate(
        id=stable_id("dataset", suite.dataset_name),
        version=suite.dataset_version,
        name=suite.dataset_name,
        license=suite.license,
        generator_version=suite.generator_version,
        scenario_refs=[_ref(s) for s in published.scenarios],
        fixture_refs=sorted({h for k, h in published.fixtures.items() if k.startswith("tool:")}),
        corpus_refs=sorted(
            {*corpora.values(), *(c.corpus_hash for c in published.vector_corpora.values())}
        ),
    )
    published.dataset = _existing(
        lambda: svc.publish_dataset(db, dataset),
        lambda: db.get(m.Dataset, (dataset.id, dataset.version)),
        _content_hash,
    )

    bench = BenchmarkCreate(
        id=stable_id("benchmark", suite.dataset_name),
        version=suite.benchmark_version,
        dataset_ref=_ref(published.dataset),
        **dict(suite.benchmark),
    )
    published.benchmark = _existing(
        lambda: svc.publish_benchmark(db, bench),
        lambda: db.get(m.Benchmark, (bench.id, bench.version)),
        _content_hash,
    )

    for model in suite.models:
        published.models[model.name] = publish_model_def(db, model, published)
    for agent in suite.agents:
        published.agents[agent.name] = publish_agent_def(
            db, agent, published.tools, published.models
        )
    return published


def publish_model_def(db: Session, model: ModelDef, published: Published) -> m.ModelConfiguration:
    requested = model.requested_model
    if model.script is not None:
        fixture = svc.publish_fixture(
            db, FixtureCreate(name=f"model-script-{model.name}", payload=model.script)
        )
        published.fixtures[f"model:{model.name}"] = fixture.content_hash
        requested = fixture.content_hash
    if requested is None:
        raise ValueError(f"el modelo {model.name} no declara requested_model ni guion")
    price_ref = None
    if model.price is not None:
        price = agent_svc.publish_price(db, PriceSnapshotCreate.model_validate(model.price))
        price_ref = price.content_hash
        published.prices[model.name] = price_ref
    data = ModelConfigurationCreate.model_validate(
        {
            "id": str(stable_id("model", model.name)),
            "version": model.version,
            "provider": model.provider,
            "requested_model": requested,
            "resolved_revision": model.resolved_revision,
            "temperature": model.temperature,
            "seed_support": model.seed_support,
            "max_tokens": model.max_tokens,
            "price_snapshot_ref": price_ref,
        }
    )
    row: m.ModelConfiguration = _existing(
        partial(agent_svc.publish_model, db, data),
        partial(db.get, m.ModelConfiguration, (data.id, data.version)),
        _content_hash,
    )
    return row


def publish_agent_def(
    db: Session,
    agent: AgentDef,
    tools: Mapping[str, m.ToolDefinition],
    models: Mapping[str, m.ModelConfiguration] | None = None,
) -> m.AgentConfiguration:
    resolved = models or {}
    data = AgentConfigurationCreate.model_validate(
        {
            "id": str(stable_id("agent", agent.name)),
            "version": agent.version,
            "pattern": agent.pattern,
            "pattern_version": agent.pattern_version,
            "prompt_hash": agent.prompt_hash,
            "pattern_parameters": agent.pattern_parameters,
            "roles": [
                {
                    "role": role,
                    "model": {"id": str(resolved[name].id), "version": resolved[name].version},
                }
                for role, name in agent.roles
            ],
            "tools": [_ref(tools[name]).model_dump(mode="json") for name in agent.tools],
        }
    )
    row: m.AgentConfiguration = _existing(
        partial(agent_svc.publish_agent, db, data),
        partial(db.get, m.AgentConfiguration, (data.id, data.version)),
        _content_hash,
    )
    return row


def lock_digest(lock: Mapping[str, Any]) -> str:
    return canonical_digest(dict(lock))


def scenario_slugs(suite: Suite) -> Sequence[str]:
    return [str(s["slug"]) for s in suite.scenarios]
