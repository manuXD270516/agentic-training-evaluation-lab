"""evaluation reports and metric profile

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-29 05:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECKS = {
    "ck_evaluations_metric_profile_hash_sha256": (
        "metric_profile_hash IS NULL OR metric_profile_hash ~ '^[0-9a-f]{64}$'"
    ),
    "ck_evaluations_completed_has_report": "status <> 'completed' OR report IS NOT NULL",
    "ck_evaluations_error_has_cause": "status <> 'error' OR error IS NOT NULL",
    "ck_evaluations_completed_at_iff_terminal": (
        "(status IN ('completed', 'error')) = (completed_at IS NOT NULL)"
    ),
}


def upgrade() -> None:
    op.add_column("evaluations", sa.Column("evaluator_suite_version", sa.Text(), nullable=True))
    op.add_column("evaluations", sa.Column("metric_profile_version", sa.Text(), nullable=True))
    op.add_column("evaluations", sa.Column("metric_profile_hash", sa.Text(), nullable=True))
    op.add_column(
        "evaluations",
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("evaluations", sa.Column("error", sa.Text(), nullable=True))
    op.add_column(
        "evaluations", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True)
    )
    for name, condition in CHECKS.items():
        op.create_check_constraint(op.f(name), "evaluations", condition)


def downgrade() -> None:
    for name in CHECKS:
        op.drop_constraint(op.f(name), "evaluations", type_="check")
    for column in (
        "completed_at",
        "error",
        "report",
        "metric_profile_hash",
        "metric_profile_version",
        "evaluator_suite_version",
    ):
        op.drop_column("evaluations", column)
