"""price snapshots referenced by model configurations

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-01 03:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "price_snapshots",
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("input_per_mtok", sa.Numeric(), nullable=False),
        sa.Column("output_per_mtok", sa.Numeric(), nullable=False),
        sa.Column("cached_input_per_mtok", sa.Numeric(), nullable=True),
        sa.Column("effective_date", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("synthetic", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_price_snapshots_content_hash_sha256"),
        ),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_price_snapshots_currency_iso")),
        sa.CheckConstraint(
            "input_per_mtok >= 0 AND output_per_mtok >= 0"
            " AND (cached_input_per_mtok IS NULL OR cached_input_per_mtok >= 0)",
            name=op.f("ck_price_snapshots_prices_non_negative"),
        ),
        sa.PrimaryKeyConstraint("content_hash", name=op.f("pk_price_snapshots")),
    )
    op.create_foreign_key(
        op.f("fk_model_configurations_price_snapshot_ref_price_snapshots"),
        "model_configurations",
        "price_snapshots",
        ["price_snapshot_ref"],
        ["content_hash"],
    )
    op.execute(
        "CREATE TRIGGER price_snapshots_immutable BEFORE UPDATE OR DELETE ON price_snapshots"
        " FOR EACH ROW EXECUTE FUNCTION forbid_published_mutation()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER price_snapshots_immutable ON price_snapshots")
    op.drop_constraint(
        op.f("fk_model_configurations_price_snapshot_ref_price_snapshots"),
        "model_configurations",
        type_="foreignkey",
    )
    op.drop_table("price_snapshots")
