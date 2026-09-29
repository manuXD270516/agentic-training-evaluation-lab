import logging
from importlib.resources import files

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection

from evallab.settings import DatabaseSettings


def alembic_config(connection: Connection | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(files("evallab.db") / "migrations"))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def upgrade_head(connection: Connection | None = None) -> None:
    command.upgrade(alembic_config(connection), "head")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    DatabaseSettings()  # falla pronto si la configuración es inválida
    upgrade_head()


if __name__ == "__main__":
    main()
