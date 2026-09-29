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

from evallab import __version__
from evallab.db.engine import create_db_engine
from evallab.health import DatabaseCheck, check_database
from evallab.services.execution import claim_queued_run, execute_claimed_run
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


def poll_once(sessions: sessionmaker[Session]) -> None:
    with sessions.begin() as db:
        run = claim_queued_run(db)
        if run is None:
            return
        execute_claimed_run(db, run)


async def _heartbeat(state: WorkerState, interval_s: float) -> None:
    while True:
        state.last_heartbeat = datetime.now(UTC)
        await asyncio.sleep(interval_s)


async def _poll(sessions: sessionmaker[Session], interval_s: float) -> None:
    while True:
        try:
            await asyncio.to_thread(poll_once, sessions)
        except Exception:
            logger.exception("worker poll failed")
        await asyncio.sleep(interval_s)


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
        poller = asyncio.create_task(_poll(sessions, cfg.poll_interval_s))
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
