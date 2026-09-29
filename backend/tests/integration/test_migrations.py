import re
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Engine, inspect, text

from evallab.db.migrate import alembic_config, upgrade_head
from evallab.db.models import Base
from evallab.domain import vocabulary as vocab
from evallab.domain.lifecycle import (
    EVALUATION_LIFECYCLE,
    EXPERIMENT_LIFECYCLE,
    RUN_LIFECYCLE,
    Lifecycle,
)

pytestmark = pytest.mark.integration

LIFECYCLE_TABLES: dict[str, Lifecycle[Any]] = {
    "experiments": EXPERIMENT_LIFECYCLE,
    "runs": RUN_LIFECYCLE,
    "evaluations": EVALUATION_LIFECYCLE,
}


IMMUTABLE_TABLES = (
    "datasets",
    "scenarios",
    "dataset_scenarios",
    "benchmarks",
    "tool_definitions",
    "model_configurations",
    "agent_configurations",
    "agent_roles",
    "agent_tools",
    "fixtures",
)

FUNCTIONS = (
    "enforce_status_transition",
    "forbid_published_mutation",
    "guard_published_child",
    "protect_sealed_experiment",
    "protect_sealed_experiment_agents",
)


def _literals(definition: str) -> set[str]:
    return set(re.findall(r"'([^']*)'", definition))


def test_upgrade_creates_full_schema_on_empty_database(empty_database: Engine) -> None:
    with empty_database.begin() as conn:
        assert inspect(conn).get_table_names() == []
        upgrade_head(conn)
    with empty_database.connect() as conn:
        tables = set(inspect(conn).get_table_names())
        triggers: set[str] = set(
            conn.execute(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")).scalars()
        )
    assert tables == set(Base.metadata.tables) | {"alembic_version"}
    assert triggers == (
        {f"{table}_status_transition" for table in LIFECYCLE_TABLES}
        | {f"{table}_immutable" for table in IMMUTABLE_TABLES}
        | {"experiments_sealed_immutable", "experiment_agents_sealed_immutable"}
    )


def test_models_and_migrations_do_not_drift(migrated_database: Engine) -> None:
    with migrated_database.connect() as conn:
        command.check(alembic_config(conn))


def test_downgrade_to_base_and_upgrade_again(empty_database: Engine) -> None:
    with empty_database.begin() as conn:
        upgrade_head(conn)
    with empty_database.begin() as conn:
        command.downgrade(alembic_config(conn), "base")
    with empty_database.connect() as conn:
        assert set(inspect(conn).get_table_names()) == {"alembic_version"}
        functions: int = conn.execute(
            text("SELECT count(*) FROM pg_proc WHERE proname = ANY(:names)"),
            {"names": list(FUNCTIONS)},
        ).scalar_one()
        assert functions == 0
    with empty_database.begin() as conn:
        upgrade_head(conn)


def test_status_triggers_encode_domain_lifecycles(migrated_database: Engine) -> None:
    with migrated_database.connect() as conn:
        for table, lifecycle in LIFECYCLE_TABLES.items():
            definition: str = conn.execute(
                text(
                    "SELECT pg_get_triggerdef(oid) FROM pg_trigger"
                    " WHERE tgname = :name AND NOT tgisinternal"
                ),
                {"name": f"{table}_status_transition"},
            ).scalar_one()
            initial, *pairs = re.findall(r"'([^']*)'", definition)
            expected = {
                f"{current}>{target}"
                for current, targets in lifecycle.transitions.items()
                for target in targets
            }
            assert initial == lifecycle.initial
            assert set(pairs) == expected


@pytest.mark.parametrize(
    ("constraint", "expected"),
    [
        ("ck_scenarios_category", set(vocab.PRIMARY_CATEGORIES)),
        ("ck_runs_mode", set(vocab.RUN_MODES)),
        ("ck_runs_error_class", set(vocab.RUN_ERROR_CLASSES)),
        ("ck_agent_configurations_pattern", set(vocab.AGENT_PATTERNS)),
        ("ck_tool_definitions_effect_class", set(vocab.TOOL_EFFECT_CLASSES)),
        ("ck_model_configurations_seed_support", set(vocab.SEED_SUPPORT)),
        ("ck_traces_completeness", set(vocab.TRACE_COMPLETENESS)),
        ("ck_scores_status", set(vocab.SCORE_STATUSES)),
        ("ck_scores_scope", set(vocab.SCORE_SCOPES)),
        ("ck_experiments_status", set(EXPERIMENT_LIFECYCLE.states)),
        ("ck_runs_status", set(RUN_LIFECYCLE.states)),
        ("ck_evaluations_status", set(EVALUATION_LIFECYCLE.states)),
        ("ck_scenarios_split", set(vocab.SPLITS)),
        ("ck_scenarios_difficulty", set(vocab.DIFFICULTIES)),
        ("ck_datasets_coverage_class", set(vocab.COVERAGE_CLASSES)),
    ],
)
def test_check_constraints_match_domain_vocabulary(
    migrated_database: Engine, constraint: str, expected: set[str]
) -> None:
    with migrated_database.connect() as conn:
        definition: str = conn.execute(
            text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :name"),
            {"name": constraint},
        ).scalar_one()
    assert _literals(definition) == expected
