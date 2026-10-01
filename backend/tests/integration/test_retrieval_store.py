"""Retrieval versionado sobre PGVector (10.1): hashes, qrels privados y orden con desempate."""

import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from evallab.api.app import create_app
from evallab.benchmarks import runner
from evallab.benchmarks.registry import committed_lock
from evallab.benchmarks.retrieval_v1 import CORPUS, RETRIEVAL_V1
from evallab.benchmarks.suite import Published, publish_suite
from evallab.db import models as m
from evallab.retrieval.embedding import embed
from evallab.retrieval.store import PgVectorRetriever, publish_corpus, verify_corpus
from evallab.services.replays import create_replay

pytestmark = pytest.mark.integration


def _publish(engine: Engine) -> Published:
    with Session(engine, expire_on_commit=False) as db, db.begin():
        return publish_suite(db, RETRIEVAL_V1)


@pytest.fixture
def client(fresh_database: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(engine=fresh_database)) as test_client:
        yield test_client


def test_corpus_embeddings_and_retriever_hashes_verify(fresh_database: Engine) -> None:
    published = _publish(fresh_database)
    lock = published.lock()
    assert lock == committed_lock("retrieval-v1")
    refs = lock["vector_corpora"]["acme-support-kb"]
    with Session(fresh_database) as db:
        assert verify_corpus(db, refs["retriever_ref"]) == []
        extension = db.scalar(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
        assert extension is not None
        retriever = db.get(m.RetrieverConfig, refs["retriever_ref"])
        assert retriever is not None
        assert (retriever.distance, retriever.index_kind, retriever.tie_break) == (
            "cosine",
            "exact",
            "chunk_id_asc",
        )
        chunks = db.scalar(select(func.count()).select_from(m.CorpusChunk))
        vectors = db.scalar(select(func.count()).select_from(m.ChunkEmbedding))
        dims = db.scalar(text("SELECT DISTINCT vector_dims(embedding) FROM chunk_embeddings"))
    assert chunks == vectors == 18 and dims == 256

    # Republicar no duplica filas ni cambia hashes.
    again = _publish(fresh_database)
    assert again.lock() == lock
    with Session(fresh_database) as db:
        assert db.scalar(select(func.count()).select_from(m.ChunkEmbedding)) == 18


def test_published_chunks_and_vectors_are_immutable(fresh_database: Engine) -> None:
    with Session(fresh_database) as db, db.begin():
        corpus = publish_corpus(db, CORPUS)
    with Session(fresh_database) as db, pytest.raises(DBAPIError), db.begin():
        db.execute(
            text("UPDATE corpus_chunks SET text = 'manipulado' WHERE corpus_hash = :c"),
            {"c": corpus.corpus_hash},
        )
    with Session(fresh_database) as db, pytest.raises(DBAPIError), db.begin():
        db.execute(
            text("DELETE FROM chunk_embeddings WHERE embedding_set = :s"),
            {"s": corpus.embedding_set},
        )


def test_embedding_is_deterministic_and_lexical() -> None:
    first = embed("Plazo de devolución vigente")
    assert first == embed("plazo de devolucion VIGENTE")  # tildes y mayúsculas normalizadas
    assert len(first) == 256 and abs(sum(v * v for v in first) - 1) < 1e-5
    assert embed("de la el") == [0.0] * 256  # sólo stopwords: vector nulo


def test_ranking_order_and_tie_break_are_recorded(fresh_database: Engine) -> None:
    published = _publish(fresh_database)
    refs = published.vector_corpora["acme-support-kb"]
    with Session(fresh_database) as db:
        _, ranked = PgVectorRetriever(db).search(refs.retriever, "duracion bateria externa horas")
        _, empty = PgVectorRetriever(db).search(refs.retriever, "de la el")
    # Dos chunks empatan con score 0: el desempate por chunk_id es determinista.
    assert [(r.chunk_id, r.score) for r in ranked] == [
        ("support#c2", 0.075593),
        ("company#c1", 0.0),
        ("exchanges#c1", 0.0),
    ]
    assert empty == []

    scenario = next(s for s in published.scenarios if s.slug == "rt-v1-battery-life-unanswerable")
    agent = published.agents["retrieval-scripted-reference"]
    with Session(fresh_database, expire_on_commit=False) as db, db.begin():
        experiment = runner.create_experiment(
            db, published, ["retrieval-scripted-reference"], hypothesis="orden", seeds=[11]
        )
        (run_id,) = runner.enqueue(db, experiment, runner.plan_cells([scenario], [agent], 1))
    runner.execute_in_order(fresh_database, [run_id])
    with Session(fresh_database) as db:
        event = db.scalars(
            select(m.TraceEvent).where(
                m.TraceEvent.run_id == run_id, m.TraceEvent.type == "retrieval.completed"
            )
        ).one()
    payload: dict[str, Any] = event.payload
    assert payload["ranked_chunk_ids"] == ["support#c2", "company#c1", "exchanges#c1"]
    assert payload["scores"] == [0.075593, 0.0, 0.0]
    assert payload["tie_break"] == "chunk_id_asc" and payload["top_k"] == 3
    assert payload["retriever_ref"] == refs.retriever
    assert payload["embedding_set_ref"] == refs.embedding_set
    assert payload["index_kind"] == "exact" and payload["distance"] == "cosine"

    # El replay sirve el ranking grabado sin consultar PGVector y lo vuelve a registrar.
    with Session(fresh_database, expire_on_commit=False) as db, db.begin():
        replay = create_replay(db, run_id)
    runner.execute_in_order(fresh_database, [replay.id])
    with Session(fresh_database) as db:
        replayed = db.scalars(
            select(m.TraceEvent).where(
                m.TraceEvent.run_id == replay.id, m.TraceEvent.type == "retrieval.completed"
            )
        ).one()
        replay_run = db.get(m.Run, replay.id)
    assert replay_run is not None and replay_run.status == "completed"
    assert replayed.payload["ranked_chunk_ids"] == payload["ranked_chunk_ids"]
    assert replayed.payload["scores"] == payload["scores"]


def test_qrels_stay_private(fresh_database: Engine, client: TestClient) -> None:
    published = _publish(fresh_database)
    for scenario in published.scenarios:
        path = f"/scenarios/{scenario.id}/versions/{scenario.version}"
        public = json.dumps(client.get(path).json())
        assert "qrels" not in public and "retriever_ref" not in public
        assert "#c" not in public  # ningún chunk_id relevante aparece en la vista pública
        oracle = client.get(f"{path}/oracle").json()
        assert oracle["retrieval"]["retriever_ref"]


def test_publication_rejects_inconsistent_retrieval_refs(fresh_database: Engine) -> None:
    from evallab.schemas import ScenarioCreate
    from evallab.services.catalog import publish_scenario
    from evallab.services.errors import InvalidReferenceError, InvalidRequestError

    published = _publish(fresh_database)
    base = next(s for s in published.scenarios if s.slug == "rt-v1-warranty-electronics")
    with Session(fresh_database) as db:
        row = db.get(m.Scenario, (base.id, base.version))
        assert row is not None
        retrieval: dict[str, Any] = dict(row.retrieval or {})
        document: dict[str, Any] = {
            "version": "2.0.0",
            "id": str(uuid.uuid4()),
            "slug": "rt-v1-warranty-broken",
            "primary_category": "retrieval",
            "difficulty": "easy",
            "split": "dev",
            "family_id": "family-broken",
            "task": row.task,
            "tools": row.tools,
            "environment": row.environment,
            "limits": row.limits,
            "expected": row.oracle_ref,
            "evaluation": row.evaluation,
            "retrieval": {**retrieval, "qrels": {"relevant": ["warranty#c99"]}},
        }
        with pytest.raises(InvalidReferenceError, match="qrels"):
            publish_scenario(db, ScenarioCreate.model_validate(document))
        wrong_k = {**document, "retrieval": {**retrieval, "top_k": 5}}
        with pytest.raises(InvalidRequestError, match="top_k"):
            publish_scenario(db, ScenarioCreate.model_validate(wrong_k))
        no_retriever = {
            **document,
            "retrieval": {
                k: v
                for k, v in retrieval.items()
                if k not in ("retriever_ref", "embedding_set_ref")
            },
        }
        with pytest.raises(InvalidRequestError, match="citation_supported"):
            publish_scenario(db, ScenarioCreate.model_validate(no_retriever))
