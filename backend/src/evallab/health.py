from typing import Literal

import psycopg
from pydantic import BaseModel

from evallab.settings import DatabaseSettings


class DatabaseCheck(BaseModel):
    status: Literal["ok", "error"]
    server_version_num: int | None = None
    error_class: str | None = None


def check_database(settings: DatabaseSettings) -> DatabaseCheck:
    """Comprueba conectividad; no expone mensajes del driver porque pueden incluir el DSN."""
    try:
        with psycopg.connect(settings.conninfo(), autocommit=True) as conn:
            row = conn.execute("SELECT current_setting('server_version_num')::int").fetchone()
    except psycopg.Error as exc:
        return DatabaseCheck(status="error", error_class=type(exc).__name__)
    return DatabaseCheck(status="ok", server_version_num=row[0] if row else None)
