"""Bases de datos PostgreSQL vacías y temporales para tests de integración.

Requiere POSTGRES_* apuntando a un servidor con permiso CREATEDB. Si no está disponible los
tests se omiten, salvo con EVALLAB_REQUIRE_DB=1 (CI), donde fallan.
"""

import contextlib
import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from evallab.db.engine import create_db_engine
from evallab.db.migrate import upgrade_head
from evallab.settings import DatabaseSettings


@contextlib.contextmanager
def temporary_database() -> Iterator[Engine]:
    settings = DatabaseSettings()
    admin = create_db_engine(settings).execution_options(isolation_level="AUTOCOMMIT")
    name = f"evallab_test_{uuid.uuid4().hex[:12]}"
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    except OperationalError as exc:
        admin.dispose()
        if os.environ.get("EVALLAB_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"PostgreSQL no disponible ({type(exc.orig).__name__})")
    engine = create_db_engine(settings, database=name)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def empty_database() -> Iterator[Engine]:
    with temporary_database() as engine:
        yield engine


@pytest.fixture
def fresh_database(empty_database: Engine) -> Engine:
    """Base migrada exclusiva del test: sin celdas `queued` de otros tests."""
    with empty_database.begin() as conn:
        upgrade_head(conn)
    return empty_database


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[Engine]:
    with temporary_database() as engine:
        with engine.begin() as conn:
            upgrade_head(conn)
        yield engine


@pytest.fixture
def session(migrated_database: Engine) -> Iterator[Session]:
    """Sesión dentro de una transacción que se revierte al terminar cada test."""
    with migrated_database.connect() as conn:
        transaction = conn.begin()
        with Session(bind=conn, join_transaction_mode="create_savepoint") as db:
            yield db
        transaction.rollback()
