from typing import Literal

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from evallab import __version__
from evallab.api import catalog, evaluations, experiments
from evallab.db.engine import create_db_engine
from evallab.health import DatabaseCheck, check_database
from evallab.services.errors import DomainError
from evallab.settings import DatabaseSettings

# SQLSTATE que los triggers/constraints usan como última barrera de integridad.
CONFLICT_SQLSTATES = {"23505": "conflict", "23514": "integrity_rule_violated"}


class Liveness(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["api"] = "api"
    version: str = __version__


class Readiness(BaseModel):
    status: Literal["ready", "not_ready"]
    database: DatabaseCheck


def _error(status_code: int, code: str, message: str, **details: object) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, **details}},
    )


def create_app(
    db_settings: DatabaseSettings | None = None, engine: Engine | None = None
) -> FastAPI:
    db = db_settings or DatabaseSettings()
    app = FastAPI(title="evallab control plane", version=__version__)
    app.state.sessions = sessionmaker(engine or create_db_engine(db), expire_on_commit=False)
    app.include_router(experiments.router)
    app.include_router(catalog.router)
    app.include_router(evaluations.router)

    @app.exception_handler(DomainError)
    def domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return _error(exc.status_code, exc.code, exc.message, **exc.details)

    @app.exception_handler(DBAPIError)
    def database_error(_: Request, exc: DBAPIError) -> JSONResponse:
        sqlstate = getattr(exc.orig, "sqlstate", None)
        if sqlstate in CONFLICT_SQLSTATES:
            diag = getattr(exc.orig, "diag", None)
            return _error(
                status.HTTP_409_CONFLICT,
                CONFLICT_SQLSTATES[sqlstate],
                getattr(diag, "message_primary", None) or "integrity rule violated",
                constraint=getattr(diag, "constraint_name", None),
            )
        raise exc

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
