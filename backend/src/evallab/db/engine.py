from sqlalchemy import URL, Engine, create_engine

from evallab.settings import DatabaseSettings


def database_url(settings: DatabaseSettings, database: str | None = None) -> URL:
    return URL.create(
        "postgresql+psycopg",
        username=settings.user,
        password=settings.password.get_secret_value() if settings.password else None,
        host=settings.host,
        port=settings.port,
        database=database or settings.db,
        query={"connect_timeout": str(settings.connect_timeout_s)},
    )


def create_db_engine(settings: DatabaseSettings, database: str | None = None) -> Engine:
    return create_engine(database_url(settings, database), pool_pre_ping=True)
