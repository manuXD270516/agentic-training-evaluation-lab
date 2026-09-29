"""trace events and run result

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-29 03:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TRACE_EVENT_TYPES = (
    "model.completed",
    "model.failed",
    "model.requested",
    "plan.created",
    "policy.violation",
    "retrieval.completed",
    "retry.scheduled",
    "run.budget_exceeded",
    "run.cancelled",
    "run.completed",
    "run.failed",
    "run.started",
    "run.timed_out",
    "step.completed",
    "step.started",
    "tool.completed",
    "tool.denied",
    "tool.failed",
    "tool.requested",
    "tool.validated",
)


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_table(
        "trace_events",
        sa.Column(
            "event_id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("timestamp_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("elapsed_ms", sa.Integer(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("actor_role", sa.Text(), nullable=False),
        sa.Column("parent_event_id", sa.Uuid(), nullable=True),
        sa.Column("otel_trace_id", sa.Text(), nullable=True),
        sa.Column("otel_span_id", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_digest", sa.Text(), nullable=False),
        sa.Column(
            "redaction_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "actor_role IN ('executor', 'harness', 'planner')",
            name=op.f("ck_trace_events_actor_role"),
        ),
        sa.CheckConstraint("elapsed_ms >= 0", name=op.f("ck_trace_events_elapsed_non_negative")),
        sa.CheckConstraint(
            "payload_digest ~ '^[0-9a-f]{64}$'", name=op.f("ck_trace_events_payload_digest_sha256")
        ),
        sa.CheckConstraint("sequence >= 1", name=op.f("ck_trace_events_sequence_positive")),
        sa.CheckConstraint(
            "type IN (" + ", ".join(repr(v) for v in TRACE_EVENT_TYPES) + ")",
            name=op.f("ck_trace_events_type"),
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name=op.f("fk_trace_events_run_id_runs")),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_trace_events")),
        sa.UniqueConstraint("run_id", "sequence", name=op.f("uq_trace_events_run_id_sequence")),
    )


def downgrade() -> None:
    op.drop_table("trace_events")
    op.drop_column("runs", "result")
