from alembic import context
from sqlalchemy import Connection

from evallab.db.engine import create_db_engine
from evallab.db.models import Base
from evallab.settings import DatabaseSettings


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
        compare_server_default=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = create_db_engine(DatabaseSettings())
    try:
        with engine.connect() as conn:
            _run(conn)
            conn.commit()
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Las migraciones offline no están soportadas; usar una conexión real.")
run_migrations_online()
