import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Literal

from fastapi import FastAPI, Response, status
from pydantic import BaseModel
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from evallab import __version__, telemetry
from evallab.db.engine import create_db_engine
from evallab.health import DatabaseCheck, check_database
from evallab.services.execution import claim_next_run, finish_attempt, run_attempt
from evallab.settings import DatabaseSettings, SandboxPolicy, WorkerSettings

logger = logging.getLogger("evallab.worker")


class WorkerState:
    def __init__(self) -> None:
        self.last_heartbeat: datetime | None = None


class Liveness(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["worker"] = "worker"
    version: str = __version__
    queue: Literal["polling"] = "polling"
    sandbox: SandboxPolicy
    last_heartbeat: datetime | None


class Readiness(BaseModel):
    status: Literal["ready", "not_ready"]
    database: DatabaseCheck


def poll_once(
    sessions: sessionmaker[Session],
    cfg: WorkerSettings | None = None,
    policy: SandboxPolicy | None = None,
) -> bool:
    """Reclama, ejecuta y persiste una celda; devuelve False si no había trabajo."""
    settings = cfg or WorkerSettings()
    with sessions.begin() as db:
        claim = claim_next_run(
            db,
            worker_id=settings.worker_id,
            lease_s=settings.lease_s,
            max_attempts=settings.max_attempts,
        )
    if claim is None:
        return False
    with telemetry.span(
        "worker.attempt",
        **{"evallab.run_id": str(claim.run_id), "evallab.attempt_id": str(claim.attempt_id)},
    ):
        with sessions() as db:
            outcome = run_attempt(db, claim, policy=policy)
        with sessions.begin() as db:
            accepted = finish_attempt(db, outcome)
    if not accepted:
        logger.warning("intento %s rechazado por fencing", claim.attempt_id)
    return True


async def _heartbeat(state: WorkerState, interval_s: float) -> None:
    while True:
        state.last_heartbeat = datetime.now(UTC)
        await asyncio.sleep(interval_s)


async def _poll(
    sessions: sessionmaker[Session], cfg: WorkerSettings, policy: SandboxPolicy
) -> None:
    while True:
        try:
            await asyncio.to_thread(poll_once, sessions, cfg, policy)
        except Exception:
            logger.exception("worker poll failed")
        await asyncio.sleep(cfg.poll_interval_s)


def create_app(
    worker_settings: WorkerSettings | None = None,
    db_settings: DatabaseSettings | None = None,
    policy: SandboxPolicy | None = None,
    *,
    engine: Engine | None = None,
) -> FastAPI:
    cfg = worker_settings or WorkerSettings()
    db = db_settings or DatabaseSettings()
    sandbox = policy or SandboxPolicy()
    state = WorkerState()
    bound = engine or create_db_engine(db)
    sessions = sessionmaker(bound, expire_on_commit=False)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("worker started; sandbox=%s queue=polling", sandbox.model_dump())
        heartbeat = asyncio.create_task(_heartbeat(state, cfg.heartbeat_interval_s))
        poller = asyncio.create_task(_poll(sessions, cfg, sandbox))
        try:
            yield
        finally:
            heartbeat.cancel()
            poller.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            with contextlib.suppress(asyncio.CancelledError):
                await poller

    app = FastAPI(title="evallab worker", version=__version__, lifespan=lifespan)

    @app.get("/health")
    def health() -> Liveness:
        return Liveness(sandbox=sandbox, last_heartbeat=state.last_heartbeat)

    @app.get("/health/ready")
    def ready(response: Response) -> Readiness:
        check = check_database(db)
        if check.status != "ok":
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return Readiness(status="not_ready", database=check)
        return Readiness(status="ready", database=check)

    return app
