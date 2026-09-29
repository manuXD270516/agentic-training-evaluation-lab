"""run attempts with leases and fencing

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-29 04:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("fencing_token", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
    )
    op.create_table(
        "run_attempts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("worker_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attempt_number >= 1", name=op.f("ck_run_attempts_attempt_number_positive")
        ),
        sa.CheckConstraint(
            "(status = 'active') = (ended_at IS NULL)",
            name=op.f("ck_run_attempts_ended_iff_inactive"),
        ),
        sa.CheckConstraint(
            "fencing_token >= 1", name=op.f("ck_run_attempts_fencing_token_positive")
        ),
        sa.CheckConstraint(
            "status IN ('active', 'expired', 'finished', 'rejected')",
            name=op.f("ck_run_attempts_status"),
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name=op.f("fk_run_attempts_run_id_runs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_attempts")),
        sa.UniqueConstraint(
            "run_id", "attempt_number", name=op.f("uq_run_attempts_run_id_attempt_number")
        ),
        sa.UniqueConstraint(
            "run_id", "fencing_token", name=op.f("uq_run_attempts_run_id_fencing_token")
        ),
    )
    op.create_index(
        "uq_run_attempts_active_run",
        "run_attempts",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    # Intentos previos a los leases: los de runs con traza quedan terminados y los runs
    # que seguían en running reciben un intento con lease ya vencido para ser reclamados.
    op.execute(
        """
        INSERT INTO run_attempts
            (id, run_id, attempt_number, fencing_token, worker_id, status,
             lease_expires_at, ended_at)
        SELECT e.attempt_id, e.run_id,
               row_number() OVER (PARTITION BY e.run_id ORDER BY e.first_ts),
               row_number() OVER (PARTITION BY e.run_id ORDER BY e.first_ts),
               'pre-lease', 'finished', e.first_ts, e.first_ts
        FROM (
            SELECT run_id, attempt_id, min(timestamp_utc) AS first_ts
            FROM trace_events GROUP BY run_id, attempt_id
        ) AS e
        """
    )
    op.execute(
        """
        INSERT INTO run_attempts
            (run_id, attempt_number, fencing_token, worker_id, status, lease_expires_at)
        SELECT r.id, 1, 1, 'pre-lease', 'active', now()
        FROM runs r
        WHERE r.status = 'running'
          AND NOT EXISTS (SELECT 1 FROM run_attempts a WHERE a.run_id = r.id)
        """
    )
    op.execute(
        """
        UPDATE runs SET fencing_token = sub.token
        FROM (SELECT run_id, max(fencing_token) AS token FROM run_attempts GROUP BY run_id) sub
        WHERE runs.id = sub.run_id
        """
    )

    op.create_check_constraint(
        op.f("ck_runs_fencing_token_non_negative"), "runs", "fencing_token >= 0"
    )
    op.create_check_constraint(
        op.f("ck_runs_running_has_token"), "runs", "status <> 'running' OR fencing_token >= 1"
    )
    op.create_foreign_key(
        op.f("fk_trace_events_attempt_id_run_attempts"),
        "trace_events",
        "run_attempts",
        ["attempt_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_trace_events_attempt_id_run_attempts"), "trace_events", type_="foreignkey"
    )
    op.drop_constraint(op.f("ck_runs_running_has_token"), "runs", type_="check")
    op.drop_constraint(op.f("ck_runs_fencing_token_non_negative"), "runs", type_="check")
    op.drop_index("uq_run_attempts_active_run", table_name="run_attempts")
    op.drop_table("run_attempts")
    op.drop_column("runs", "fencing_token")
