import hmac
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from evallab import __version__, telemetry
from evallab.api import agents, catalog, comparisons, evaluations, experiments
from evallab.db.engine import create_db_engine
from evallab.health import DatabaseCheck, check_database
from evallab.services.errors import DomainError
from evallab.settings import AccessSettings, DatabaseSettings

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


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _is_private_read(path: str) -> bool:
    """Lecturas privadas: el oráculo de un escenario (evaluador, nunca runner ni demo)."""
    return path.rstrip("/").endswith("/oracle")


def _bearer_matches(header: str | None, token: str) -> bool:
    if header is None or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header.removeprefix("Bearer ").encode(), token.encode())


def access_decision(
    access: AccessSettings, method: str, path: str, authorization: str | None
) -> tuple[int, str, str] | None:
    """None si se permite; si no, (status, code, mensaje). Nunca revela el token."""
    write = method.upper() not in SAFE_METHODS
    private = write or _is_private_read(path)
    if not private:
        return None
    if write and access.read_only:
        return (status.HTTP_403_FORBIDDEN, "read_only", "esta instancia es de sólo lectura")
    if access.admin_token is None:
        if access.read_only:
            return (
                status.HTTP_403_FORBIDDEN,
                "private_operation",
                "operación privada no disponible en la demo",
            )
        return None
    if _bearer_matches(authorization, access.admin_token.get_secret_value()):
        return None
    return (status.HTTP_401_UNAUTHORIZED, "unauthorized", "operación privada: requiere token")


def create_app(
    db_settings: DatabaseSettings | None = None,
    engine: Engine | None = None,
    access: AccessSettings | None = None,
) -> FastAPI:
    db = db_settings or DatabaseSettings()
    policy = access or AccessSettings()
    app = FastAPI(title="evallab control plane", version=__version__)
    app.state.sessions = sessionmaker(engine or create_db_engine(db), expire_on_commit=False)
    app.include_router(experiments.router)
    app.include_router(catalog.router)
    app.include_router(evaluations.router)
    app.include_router(agents.router)
    app.include_router(comparisons.router)

    @app.middleware("http")
    async def http_span(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Sólo método, ruta, estado e ids de path: nunca cuerpos ni cabeceras. Los health checks
        # no generan spans (Compose los consulta cada pocos segundos).
        if request.url.path.startswith("/health"):
            return await call_next(request)
        with telemetry.span(
            f"HTTP {request.method}", **{"http.request.method": request.method}
        ) as current:
            response = await call_next(request)
            route = request.scope.get("route")
            template = getattr(route, "path", None)
            params = request.path_params
            telemetry.set_attributes(
                current,
                **{
                    "http.route": template,
                    "http.response.status_code": response.status_code,
                    "evallab.run_id": params.get("run_id"),
                    "evallab.experiment_id": params.get("experiment_id"),
                    "evallab.evaluation_id": params.get("evaluation_id"),
                },
            )
            if template is not None:
                current.update_name(f"{request.method} {template}")
            if response.status_code >= 500:
                telemetry.mark_error(current, str(response.status_code))
            return response

    # Registrado después del span: se ejecuta antes y un rechazo también queda trazado.
    @app.middleware("http")
    async def access_control(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        denied = access_decision(
            policy, request.method, request.url.path, request.headers.get("authorization")
        )
        if denied is not None:
            code, error, message = denied
            response = _error(code, error, message)
            if code == status.HTTP_401_UNAUTHORIZED:
                response.headers["WWW-Authenticate"] = "Bearer"
            return response
        return await call_next(request)

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
