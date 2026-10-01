import os
import socket
from typing import Literal

from psycopg.conninfo import make_conninfo
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Conexión a PostgreSQL. Comparte nombres de variables con la imagen oficial."""

    model_config = SettingsConfigDict(env_prefix="POSTGRES_", extra="ignore")

    host: str = "127.0.0.1"
    port: int = Field(default=5432, ge=1, le=65535)
    db: str = "evallab"
    user: str = "evallab"
    password: SecretStr | None = None
    connect_timeout_s: int = Field(default=3, ge=1, le=60)

    def conninfo(self) -> str:
        return make_conninfo(
            host=self.host,
            port=self.port,
            dbname=self.db,
            user=self.user,
            password=self.password.get_secret_value() if self.password else None,
            connect_timeout=self.connect_timeout_s,
            application_name="evallab",
        )


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="API_", extra="ignore")

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)


class AccessSettings(BaseSettings):
    """Control de acceso de la API (M12, 13.2).

    Por defecto (desarrollo local) todo está abierto. `EVALLAB_READ_ONLY=1` (demo) rechaza
    cualquier escritura con 403 aunque haya token y deja el oráculo privado inaccesible salvo
    con token. Con `EVALLAB_ADMIN_TOKEN`, escrituras y oráculos exigen
    `Authorization: Bearer <token>`.
    """

    model_config = SettingsConfigDict(env_prefix="EVALLAB_", extra="ignore", frozen=True)

    read_only: bool = False
    admin_token: SecretStr | None = Field(default=None)

    @field_validator("admin_token", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("admin_token")
    @classmethod
    def _strong_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < 24:
            raise ValueError("EVALLAB_ADMIN_TOKEN debe tener al menos 24 caracteres")
        return value


class SandboxPolicy(BaseSettings):
    """Política de aislamiento de los agentes ejecutados por el worker.

    Sólo se admite `deny` y `host_tools=False` hasta que exista el sandbox (M2) y el
    gateway de modelo (M6); cualquier otro valor aborta el arranque.
    """

    model_config = SettingsConfigDict(env_prefix="WORKER_AGENT_", extra="ignore", frozen=True)

    network: Literal["deny"] = "deny"
    host_tools: bool = False

    @field_validator("host_tools")
    @classmethod
    def _host_tools_disabled(cls, value: bool) -> bool:
        if value:
            raise ValueError("el host del worker no puede exponerse como herramienta del agente")
        return value


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WORKER_", extra="ignore")

    health_host: str = "127.0.0.1"
    health_port: int = Field(default=8001, ge=1, le=65535)
    heartbeat_interval_s: float = Field(default=10.0, gt=0, le=3600)
    poll_interval_s: float = Field(default=0.5, gt=0, le=3600)
    worker_id: str = Field(
        default_factory=lambda: f"{socket.gethostname()}-{os.getpid()}", min_length=1
    )
    lease_s: float = Field(default=300.0, gt=0, le=86400)
    max_attempts: int = Field(default=2, ge=1, le=10)
