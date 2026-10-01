"""versioned retrieval: corpora, chunks, embeddings (pgvector) and retrievers

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-01 06:00:00.000000
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from evallab.db.models import Vector

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SHA = "'^[0-9a-f]{64}$'"
TABLES = ("corpora", "corpus_chunks", "embedding_sets", "chunk_embeddings", "retrievers")


def _created_at() -> sa.Column[Any]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "corpora",
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("corpus_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("synthetic", sa.Boolean(), nullable=False),
        sa.Column("license", sa.Text(), nullable=False),
        sa.Column("chunking", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("manifest", sa.dialects.postgresql.JSONB(), nullable=False),
        _created_at(),
        sa.CheckConstraint(f"content_hash ~ {SHA}", name=op.f("ck_corpora_content_hash_sha256")),
        sa.CheckConstraint(
            "version ~ '^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$'",
            name=op.f("ck_corpora_version_semver"),
        ),
        sa.CheckConstraint("synthetic IS TRUE", name=op.f("ck_corpora_synthetic")),
        sa.PrimaryKeyConstraint("content_hash", name=op.f("pk_corpora")),
        sa.UniqueConstraint("corpus_id", "version", name=op.f("uq_corpora_corpus_id_version")),
    )
    op.create_table(
        "corpus_chunks",
        sa.Column("corpus_hash", sa.Text(), nullable=False),
        sa.Column("chunk_id", sa.Text(), nullable=False),
        sa.Column("doc_id", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.Text(), nullable=False),
        sa.CheckConstraint(f"text_sha256 ~ {SHA}", name=op.f("ck_corpus_chunks_text_sha256")),
        sa.CheckConstraint("position >= 1", name=op.f("ck_corpus_chunks_position_positive")),
        sa.ForeignKeyConstraint(
            ["corpus_hash"],
            ["corpora.content_hash"],
            name=op.f("fk_corpus_chunks_corpus_hash_corpora"),
        ),
        sa.PrimaryKeyConstraint("corpus_hash", "chunk_id", name=op.f("pk_corpus_chunks")),
    )
    op.create_table(
        "embedding_sets",
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("corpus_hash", sa.Text(), nullable=False),
        sa.Column("embedder", sa.Text(), nullable=False),
        sa.Column("embedder_version", sa.Text(), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("embeddings_sha256", sa.Text(), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            f"content_hash ~ {SHA}", name=op.f("ck_embedding_sets_content_hash_sha256")
        ),
        sa.CheckConstraint(
            f"embeddings_sha256 ~ {SHA}", name=op.f("ck_embedding_sets_embeddings_sha256")
        ),
        sa.CheckConstraint("dimension > 0", name=op.f("ck_embedding_sets_dimension_positive")),
        sa.ForeignKeyConstraint(
            ["corpus_hash"],
            ["corpora.content_hash"],
            name=op.f("fk_embedding_sets_corpus_hash_corpora"),
        ),
        sa.PrimaryKeyConstraint("content_hash", name=op.f("pk_embedding_sets")),
    )
    op.create_table(
        "chunk_embeddings",
        sa.Column("embedding_set", sa.Text(), nullable=False),
        sa.Column("chunk_id", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.CheckConstraint(
            "vector_dims(embedding) > 0", name=op.f("ck_chunk_embeddings_embedding_not_empty")
        ),
        sa.ForeignKeyConstraint(
            ["embedding_set"],
            ["embedding_sets.content_hash"],
            name=op.f("fk_chunk_embeddings_embedding_set_embedding_sets"),
        ),
        sa.PrimaryKeyConstraint("embedding_set", "chunk_id", name=op.f("pk_chunk_embeddings")),
    )
    op.create_table(
        "retrievers",
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("embedding_set", sa.Text(), nullable=False),
        sa.Column("distance", sa.Text(), nullable=False),
        sa.Column("index_kind", sa.Text(), nullable=False),
        sa.Column("tie_break", sa.Text(), nullable=False),
        sa.Column("score_decimals", sa.Integer(), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        _created_at(),
        sa.CheckConstraint(f"content_hash ~ {SHA}", name=op.f("ck_retrievers_content_hash_sha256")),
        sa.CheckConstraint("distance IN ('cosine')", name=op.f("ck_retrievers_distance")),
        sa.CheckConstraint("index_kind IN ('exact')", name=op.f("ck_retrievers_index_kind")),
        sa.CheckConstraint("top_k > 0", name=op.f("ck_retrievers_top_k_positive")),
        sa.ForeignKeyConstraint(
            ["embedding_set"],
            ["embedding_sets.content_hash"],
            name=op.f("fk_retrievers_embedding_set_embedding_sets"),
        ),
        sa.PrimaryKeyConstraint("content_hash", name=op.f("pk_retrievers")),
    )
    for table in TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table}"
            " FOR EACH ROW EXECUTE FUNCTION forbid_published_mutation()"
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TRIGGER {table}_immutable ON {table}")
        op.drop_table(table)
    op.execute("DROP EXTENSION IF EXISTS vector")
