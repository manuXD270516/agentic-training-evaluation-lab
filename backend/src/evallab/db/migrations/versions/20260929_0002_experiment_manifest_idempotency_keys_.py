"""experiment manifest, idempotency keys and immutability

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-29 02:13:51.850506
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PUBLISHED_TABLES = (
    "datasets",
    "scenarios",
    "benchmarks",
    "tool_definitions",
    "model_configurations",
    "agent_configurations",
)

# Filas hijas que forman parte del contenido publicado del padre: sólo se insertan en la
# transacción que crea al padre (now() es el instante de inicio de la transacción).
PUBLISHED_CHILDREN = {
    "agent_roles": ("agent_configurations", "agent_id", "agent_version"),
    "agent_tools": ("agent_configurations", "agent_id", "agent_version"),
    "dataset_scenarios": ("datasets", "dataset_id", "dataset_version"),
}

FORBID_MUTATION = """
CREATE FUNCTION forbid_published_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'published rows are immutable on %: % rejected', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'check_violation', HINT = 'publish a new version with its own hash';
END;
$$
"""

GUARD_PUBLISHED_CHILD = """
CREATE FUNCTION guard_published_child() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    parent_created timestamptz;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'published rows are immutable on %: % rejected', TG_TABLE_NAME, TG_OP
            USING ERRCODE = 'check_violation', HINT = 'publish a new version with its own hash';
    END IF;
    EXECUTE format('SELECT created_at FROM %I WHERE id = $1 AND version = $2', TG_ARGV[0])
        INTO parent_created
        USING (to_jsonb(NEW) ->> TG_ARGV[1])::uuid, to_jsonb(NEW) ->> TG_ARGV[2];
    IF parent_created IS NOT NULL AND parent_created <> now() THEN
        RAISE EXCEPTION 'published rows are immutable on %: cannot extend published %',
            TG_TABLE_NAME, TG_ARGV[0]
            USING ERRCODE = 'check_violation', HINT = 'publish a new version with its own hash';
    END IF;
    RETURN NEW;
END;
$$
"""

PROTECT_SEALED_EXPERIMENT = """
CREATE FUNCTION protect_sealed_experiment() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'draft' THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_OP = 'DELETE'
        OR (to_jsonb(NEW) - 'status') IS DISTINCT FROM (to_jsonb(OLD) - 'status') THEN
        RAISE EXCEPTION 'sealed experiment is immutable: % rejected', TG_OP
            USING ERRCODE = 'check_violation', HINT = 'create a new experiment';
    END IF;
    RETURN NEW;
END;
$$
"""

PROTECT_SEALED_EXPERIMENT_AGENTS = """
CREATE FUNCTION protect_sealed_experiment_agents() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    target uuid := CASE WHEN TG_OP = 'DELETE' THEN OLD.experiment_id ELSE NEW.experiment_id END;
    current_status text;
BEGIN
    SELECT status INTO current_status FROM experiments WHERE id = target;
    IF current_status IS DISTINCT FROM 'draft' THEN
        RAISE EXCEPTION 'sealed experiment is immutable: agents % rejected', TG_OP
            USING ERRCODE = 'check_violation', HINT = 'create a new experiment';
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.experiment_id <> NEW.experiment_id THEN
        SELECT status INTO current_status FROM experiments WHERE id = OLD.experiment_id;
        IF current_status IS DISTINCT FROM 'draft' THEN
            RAISE EXCEPTION 'sealed experiment is immutable: agents % rejected', TG_OP
                USING ERRCODE = 'check_violation', HINT = 'create a new experiment';
        END IF;
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$
"""


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_idempotency_keys_request_hash_sha256"),
        ),
        sa.CheckConstraint(
            "(status_code IS NULL) = (response IS NULL)",
            name=op.f("ck_idempotency_keys_response_complete"),
        ),
        sa.CheckConstraint(
            "char_length(key) BETWEEN 1 AND 255", name=op.f("ck_idempotency_keys_key_length")
        ),
        sa.PrimaryKeyConstraint("scope", "key", name=op.f("pk_idempotency_keys")),
    )
    op.add_column(
        "experiments",
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_experiments_manifest_with_hash"),
        "experiments",
        "(manifest IS NULL) = (manifest_hash IS NULL)",
    )

    for ddl in (
        FORBID_MUTATION,
        GUARD_PUBLISHED_CHILD,
        PROTECT_SEALED_EXPERIMENT,
        PROTECT_SEALED_EXPERIMENT_AGENTS,
    ):
        op.execute(ddl)
    for table in PUBLISHED_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table}"
            " FOR EACH ROW EXECUTE FUNCTION forbid_published_mutation()"
        )
    for table, (parent, id_column, version_column) in PUBLISHED_CHILDREN.items():
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE INSERT OR UPDATE OR DELETE ON {table}"
            " FOR EACH ROW EXECUTE FUNCTION"
            f" guard_published_child('{parent}', '{id_column}', '{version_column}')"
        )
    op.execute(
        "CREATE TRIGGER experiments_sealed_immutable BEFORE UPDATE OR DELETE ON experiments"
        " FOR EACH ROW EXECUTE FUNCTION protect_sealed_experiment()"
    )
    op.execute(
        "CREATE TRIGGER experiment_agents_sealed_immutable"
        " BEFORE INSERT OR UPDATE OR DELETE ON experiment_agents"
        " FOR EACH ROW EXECUTE FUNCTION protect_sealed_experiment_agents()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER experiment_agents_sealed_immutable ON experiment_agents")
    op.execute("DROP TRIGGER experiments_sealed_immutable ON experiments")
    for table in (*PUBLISHED_TABLES, *PUBLISHED_CHILDREN):
        op.execute(f"DROP TRIGGER {table}_immutable ON {table}")
    for function in (
        "protect_sealed_experiment_agents",
        "protect_sealed_experiment",
        "guard_published_child",
        "forbid_published_mutation",
    ):
        op.execute(f"DROP FUNCTION {function}()")
    op.drop_constraint(op.f("ck_experiments_manifest_with_hash"), "experiments", type_="check")
    op.drop_column("experiments", "manifest")
    op.drop_table("idempotency_keys")
