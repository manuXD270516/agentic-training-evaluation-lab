"""Publicación y búsqueda de corpus versionados sobre PGVector (M9, 10.1).

Hashes (JSON canónico RFC 8785 + SHA-256):

- corpus: id, versión, licencia, chunking y, por documento, sus chunks con `text_sha256`;
- embedding set: corpus, embedder y versión, dimensión y `embeddings_sha256` (SHA-256 del JSON
  canónico de `[[chunk_id, vector], ...]` ordenado por chunk_id con los vectores redondeados);
- retriever: embedding set, distancia, búsqueda exacta, desempate, decimales del score y `top_k`.

La búsqueda es exacta (`ORDER BY` sobre la distancia coseno `<=>` de pgvector, sin índice
aproximado) con la distancia redondeada a `score_decimals` y desempate por `chunk_id`
ascendente, de modo que el orden no depende de la máquina. `verify_corpus` recomputa los tres
hashes desde las filas almacenadas.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from evallab.canonical import canonical_digest, canonical_json
from evallab.db import models as m
from evallab.retrieval.embedding import (
    CHUNKER,
    CHUNKER_VERSION,
    DECIMALS,
    DIMENSION,
    EMBEDDER,
    EMBEDDER_VERSION,
    Chunk,
    chunk_document,
    embed,
    vector_literal,
)

DISTANCE = "cosine"
INDEX_KIND = "exact"
TIE_BREAK = "chunk_id_asc"


@dataclass(frozen=True)
class DocumentDef:
    doc_id: str
    text: str


@dataclass(frozen=True)
class CorpusDef:
    corpus_id: str
    version: str
    license: str
    documents: tuple[DocumentDef, ...]
    top_k: int = 3


@dataclass(frozen=True)
class PublishedCorpus:
    corpus_hash: str
    embedding_set: str
    retriever: str
    chunks: tuple[Chunk, ...]

    def refs(self) -> dict[str, str]:
        return {
            "corpus_ref": self.corpus_hash,
            "embedding_set_ref": self.embedding_set,
            "retriever_ref": self.retriever,
        }


def _chunks(corpus: CorpusDef) -> list[Chunk]:
    return [c for doc in corpus.documents for c in chunk_document(doc.doc_id, doc.text)]


def corpus_manifest(corpus: CorpusDef, chunks: Sequence[Chunk]) -> dict[str, Any]:
    by_doc: dict[str, list[dict[str, Any]]] = {}
    for chunk in chunks:
        by_doc.setdefault(chunk.doc_id, []).append(
            {
                "chunk_id": chunk.chunk_id,
                "position": chunk.position,
                "text_sha256": chunk.text_sha256,
            }
        )
    return {
        "corpus_id": corpus.corpus_id,
        "version": corpus.version,
        "synthetic": True,
        "license": corpus.license,
        "chunking": {"method": CHUNKER, "version": CHUNKER_VERSION},
        "documents": [
            {"doc_id": doc.doc_id, "chunks": by_doc.get(doc.doc_id, [])} for doc in corpus.documents
        ],
    }


def embeddings_sha256(vectors: Mapping[str, Sequence[float]]) -> str:
    payload = [
        [chunk_id, [round(v, DECIMALS) for v in vectors[chunk_id]]] for chunk_id in sorted(vectors)
    ]
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def embedding_set_document(corpus_hash: str, vectors_sha: str) -> dict[str, Any]:
    return {
        "corpus_hash": corpus_hash,
        "embedder": EMBEDDER,
        "embedder_version": EMBEDDER_VERSION,
        "dimension": DIMENSION,
        "embeddings_sha256": vectors_sha,
    }


def retriever_document(embedding_set: str, top_k: int) -> dict[str, Any]:
    return {
        "embedding_set": embedding_set,
        "distance": DISTANCE,
        "index_kind": INDEX_KIND,
        "tie_break": TIE_BREAK,
        "score_decimals": DECIMALS,
        "top_k": top_k,
    }


def publish_corpus(db: Session, corpus: CorpusDef) -> PublishedCorpus:
    """Publica (o reutiliza si ya existe con el mismo hash) corpus, embeddings y retriever."""
    chunks = _chunks(corpus)
    manifest = corpus_manifest(corpus, chunks)
    corpus_hash = canonical_digest(manifest)
    if db.get(m.Corpus, corpus_hash) is None:
        db.add(
            m.Corpus(
                content_hash=corpus_hash,
                corpus_id=corpus.corpus_id,
                version=corpus.version,
                synthetic=True,
                license=corpus.license,
                chunking=manifest["chunking"],
                manifest=manifest,
            )
        )
        db.flush()
        db.add_all(
            m.CorpusChunk(
                corpus_hash=corpus_hash,
                chunk_id=c.chunk_id,
                doc_id=c.doc_id,
                position=c.position,
                text=c.text,
                text_sha256=c.text_sha256,
            )
            for c in chunks
        )
        db.flush()
    vectors = {c.chunk_id: embed(c.text) for c in chunks}
    set_hash = canonical_digest(embedding_set_document(corpus_hash, embeddings_sha256(vectors)))
    if db.get(m.EmbeddingSet, set_hash) is None:
        db.add(
            m.EmbeddingSet(
                content_hash=set_hash,
                corpus_hash=corpus_hash,
                embedder=EMBEDDER,
                embedder_version=EMBEDDER_VERSION,
                dimension=DIMENSION,
                embeddings_sha256=embeddings_sha256(vectors),
            )
        )
        db.flush()
        for chunk_id, vector in sorted(vectors.items()):
            db.execute(
                text(
                    "INSERT INTO chunk_embeddings (embedding_set, chunk_id, embedding)"
                    " VALUES (:set, :chunk, CAST(:vector AS vector))"
                ),
                {"set": set_hash, "chunk": chunk_id, "vector": vector_literal(vector)},
            )
    retriever_hash = canonical_digest(retriever_document(set_hash, corpus.top_k))
    if db.get(m.RetrieverConfig, retriever_hash) is None:
        db.add(
            m.RetrieverConfig(
                content_hash=retriever_hash,
                embedding_set=set_hash,
                distance=DISTANCE,
                index_kind=INDEX_KIND,
                tie_break=TIE_BREAK,
                score_decimals=DECIMALS,
                top_k=corpus.top_k,
            )
        )
        db.flush()
    return PublishedCorpus(corpus_hash, set_hash, retriever_hash, tuple(chunks))


def _stored_vectors(db: Session, embedding_set: str) -> dict[str, list[float]]:
    rows = db.execute(
        text(
            "SELECT chunk_id, embedding::text FROM chunk_embeddings"
            " WHERE embedding_set = :set ORDER BY chunk_id"
        ),
        {"set": embedding_set},
    )
    return {chunk_id: [float(v) for v in raw.strip("[]").split(",")] for chunk_id, raw in rows}


def verify_corpus(db: Session, retriever_hash: str) -> list[str]:
    """Recalcula los hashes desde las filas; lista vacía = verificado."""
    problems: list[str] = []
    retriever = db.get(m.RetrieverConfig, retriever_hash)
    if retriever is None:
        return ["retriever_missing"]
    if (
        canonical_digest(retriever_document(retriever.embedding_set, retriever.top_k))
        != retriever_hash
    ):
        problems.append("retriever_hash_mismatch")
    embedding_set = db.get(m.EmbeddingSet, retriever.embedding_set)
    if embedding_set is None:
        return [*problems, "embedding_set_missing"]
    vectors = _stored_vectors(db, embedding_set.content_hash)
    if embeddings_sha256(vectors) != embedding_set.embeddings_sha256:
        problems.append("embeddings_sha256_mismatch")
    document = embedding_set_document(embedding_set.corpus_hash, embedding_set.embeddings_sha256)
    if canonical_digest(document) != embedding_set.content_hash:
        problems.append("embedding_set_hash_mismatch")
    corpus = db.get(m.Corpus, embedding_set.corpus_hash)
    if corpus is None:
        return [*problems, "corpus_missing"]
    chunks = list(
        db.scalars(select(m.CorpusChunk).where(m.CorpusChunk.corpus_hash == corpus.content_hash))
    )
    for chunk in chunks:
        if hashlib.sha256(chunk.text.encode("utf-8")).hexdigest() != chunk.text_sha256:
            problems.append(f"chunk_text_mismatch:{chunk.chunk_id}")
    if canonical_digest(corpus.manifest) != corpus.content_hash:
        problems.append("corpus_hash_mismatch")
    manifest_chunks = {
        c["chunk_id"]: c["text_sha256"] for d in corpus.manifest["documents"] for c in d["chunks"]
    }
    if manifest_chunks != {c.chunk_id: c.text_sha256 for c in chunks}:
        problems.append("corpus_chunks_mismatch")
    if set(vectors) != set(manifest_chunks):
        problems.append("embeddings_cover_other_chunks")
    return problems


@dataclass(frozen=True)
class RetrievedChunk:
    rank: int
    chunk_id: str
    doc_id: str
    text: str
    score: float


class PgVectorRetriever:
    """Búsqueda exacta en PGVector para un retriever publicado; sólo lectura."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def config(self, retriever_hash: str) -> m.RetrieverConfig | None:
        return self._db.get(m.RetrieverConfig, retriever_hash)

    def search(
        self, retriever_hash: str, query: str
    ) -> tuple[m.RetrieverConfig, list[RetrievedChunk]]:
        retriever = self.config(retriever_hash)
        if retriever is None:
            raise LookupError("retriever inexistente")
        embedding_set = self._db.get(m.EmbeddingSet, retriever.embedding_set)
        assert embedding_set is not None
        query_vector = embed(query, embedding_set.dimension)
        if not any(query_vector):
            # Una consulta sin términos útiles no tiene dirección: no se inventa un ranking.
            return retriever, []
        rows = self._db.execute(
            text(
                "SELECT e.chunk_id, c.doc_id, c.text,"
                " round((e.embedding <=> CAST(:query AS vector))::numeric, :decimals) AS distance"
                " FROM chunk_embeddings e"
                " JOIN corpus_chunks c ON c.corpus_hash = :corpus AND c.chunk_id = e.chunk_id"
                " WHERE e.embedding_set = :set"
                " ORDER BY distance ASC, e.chunk_id ASC"
                " LIMIT :k"
            ),
            {
                "query": vector_literal(query_vector),
                "decimals": retriever.score_decimals,
                "corpus": embedding_set.corpus_hash,
                "set": embedding_set.content_hash,
                "k": retriever.top_k,
            },
        )
        ranked = [
            RetrievedChunk(
                rank=index,
                chunk_id=chunk_id,
                doc_id=doc_id,
                text=chunk_text,
                score=round(1 - float(distance), retriever.score_decimals),
            )
            for index, (chunk_id, doc_id, chunk_text, distance) in enumerate(rows, start=1)
        ]
        return retriever, ranked
