from typing import Literal

from fastapi import FastAPI, Response, status
from pydantic import BaseModel

from evallab import __version__
from evallab.health import DatabaseCheck, check_database
from evallab.settings import DatabaseSettings


class Liveness(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["api"] = "api"
    version: str = __version__


class Readiness(BaseModel):
    status: Literal["ready", "not_ready"]
    database: DatabaseCheck


def create_app(db_settings: DatabaseSettings | None = None) -> FastAPI:
    db = db_settings or DatabaseSettings()
    app = FastAPI(title="evallab control plane", version=__version__)

    @app.get("/health")
    def health() -> Liveness:
        return Liveness()

    @app.get("/health/ready")
    def ready(response: Response) -> Readiness:
        check = check_database(db)
        if check.status != "ok":
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return Readiness(status="not_ready", database=check)
        return Readiness(status="ready", database=check)

    return app
