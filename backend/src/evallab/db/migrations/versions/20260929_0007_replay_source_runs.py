"""replay runs reference their source run

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-29 06:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CELL = ["experiment_id", "scenario_id", "scenario_version", "agent_id", "agent_version"]
OLD_ERROR_CLASSES = (
    "'denied', 'infrastructure_error', 'invalid_arguments', 'model_error', "
    "'tool_timeout', 'trace_error', 'transient_tool_error'"
)
NEW_ERROR_CLASSES = (
    "'denied', 'infrastructure_error', 'invalid_arguments', 'model_error', "
    "'replay_mismatch', 'tool_timeout', 'trace_error', 'transient_tool_error'"
)


def _error_class(values: str) -> None:
    op.drop_constraint(op.f("ck_runs_error_class"), "runs", type_="check")
    op.create_check_constraint(
        op.f("ck_runs_error_class"), "runs", f"error_class IS NULL OR error_class IN ({values})"
    )


def upgrade() -> None:
    op.add_column("runs", sa.Column("source_run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_runs_source_run_id_runs"), "runs", "runs", ["source_run_id"], ["id"]
    )
    op.create_unique_constraint(op.f("uq_runs_source_run_id"), "runs", ["source_run_id"])
    op.create_check_constraint(
        op.f("ck_runs_source_only_on_replay"), "runs", "source_run_id IS NULL OR mode = 'replay'"
    )
    op.create_check_constraint(op.f("ck_runs_source_not_self"), "runs", "source_run_id <> id")
    op.drop_constraint("uq_runs_experimental_cell", "runs", type_="unique")
    op.create_unique_constraint(
        "uq_runs_experimental_cell", "runs", [*CELL, "repetition", "seed", "mode"]
    )
    _error_class(NEW_ERROR_CLASSES)


def downgrade() -> None:
    _error_class(OLD_ERROR_CLASSES)
    op.drop_constraint("uq_runs_experimental_cell", "runs", type_="unique")
    op.create_unique_constraint("uq_runs_experimental_cell", "runs", [*CELL, "repetition", "seed"])
    op.drop_constraint(op.f("ck_runs_source_not_self"), "runs", type_="check")
    op.drop_constraint(op.f("ck_runs_source_only_on_replay"), "runs", type_="check")
    op.drop_constraint(op.f("uq_runs_source_run_id"), "runs", type_="unique")
    op.drop_constraint(op.f("fk_runs_source_run_id_runs"), "runs", type_="foreignkey")
    op.drop_column("runs", "source_run_id")
