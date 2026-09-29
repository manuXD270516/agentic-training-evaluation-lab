import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Literal

from fastapi import FastAPI, Response, status
from pydantic import BaseModel

from evallab import __version__
from evallab.health import DatabaseCheck, check_database
from evallab.settings import DatabaseSettings, SandboxPolicy, WorkerSettings

logger = logging.getLogger("evallab.worker")


class WorkerState:
    def __init__(self) -> None:
        self.last_heartbeat: datetime | None = None


class Liveness(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["worker"] = "worker"
    version: str = __version__
    queue: Literal["not_implemented"] = "not_implemented"
    sandbox: SandboxPolicy
    last_heartbeat: datetime | None


class Readiness(BaseModel):
    status: Literal["ready", "not_ready"]
    database: DatabaseCheck


async def _heartbeat(state: WorkerState, interval_s: float) -> None:
    # La cola con leases/fencing llega en M2; hasta entonces el worker no consume trabajos.
    while True:
        state.last_heartbeat = datetime.now(UTC)
        logger.debug("worker idle heartbeat")
        await asyncio.sleep(interval_s)


def create_app(
    worker_settings: WorkerSettings | None = None,
    db_settings: DatabaseSettings | None = None,
    policy: SandboxPolicy | None = None,
) -> FastAPI:
    cfg = worker_settings or WorkerSettings()
    db = db_settings or DatabaseSettings()
    sandbox = policy or SandboxPolicy()
    state = WorkerState()

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("worker started; sandbox=%s", sandbox.model_dump())
        task = asyncio.create_task(_heartbeat(state, cfg.heartbeat_interval_s))
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

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
