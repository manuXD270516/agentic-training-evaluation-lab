import re
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from evallab.schemas import (
    ExperimentCreate,
    ExperimentOut,
    ExperimentUpdate,
    ManifestOut,
    RunCreate,
    RunOut,
)
from evallab.services import experiments as svc
from evallab.services.errors import DomainError, ExperimentNotSealedError
from evallab.services.idempotency import IdempotentResult, run_idempotent

router = APIRouter()

IDEMPOTENCY_KEY = re.compile(r"^[\x21-\x7e]{1,255}$")


class IdempotencyKeyError(DomainError):
    status_code = 400
    code = "idempotency_key_required"


def _sessions(request: Request) -> sessionmaker[Session]:
    sessions: sessionmaker[Session] = request.app.state.sessions
    return sessions


def _require_key(key: str | None) -> str:
    if key is None or not IDEMPOTENCY_KEY.fullmatch(key):
        raise IdempotencyKeyError(
            "las creaciones exigen Idempotency-Key de 1 a 255 caracteres ASCII visibles"
        )
    return key


def _respond(result: IdempotentResult) -> JSONResponse:
    headers = {"Idempotent-Replayed": "true"} if result.replayed else {}
    return JSONResponse(status_code=result.status_code, content=result.body, headers=headers)


IdempotencyHeader = Annotated[str | None, Header(alias="Idempotency-Key")]


@router.post(
    "/experiments",
    status_code=status.HTTP_201_CREATED,
    response_model=ExperimentOut,
)
def create_experiment(
    request: Request, body: ExperimentCreate, idempotency_key: IdempotencyHeader = None
) -> JSONResponse:
    key = _require_key(idempotency_key)
    with _sessions(request).begin() as db:

        def handler() -> tuple[int, Any]:
            exp = svc.create_draft(db, body)
            return status.HTTP_201_CREATED, svc.to_out(db, exp).model_dump(mode="json")

        result = run_idempotent(
            db,
            scope="POST /experiments",
            key=key,
            payload=body.model_dump(mode="json"),
            handler=handler,
        )
    return _respond(result)


@router.get("/experiments/{experiment_id}")
def get_experiment(request: Request, experiment_id: uuid.UUID) -> ExperimentOut:
    with _sessions(request).begin() as db:
        return svc.to_out(db, svc.get_experiment(db, experiment_id))


@router.patch("/experiments/{experiment_id}")
def update_experiment(
    request: Request, experiment_id: uuid.UUID, body: ExperimentUpdate
) -> ExperimentOut:
    with _sessions(request).begin() as db:
        return svc.to_out(db, svc.update_draft(db, experiment_id, body))


@router.post("/experiments/{experiment_id}/seal")
def seal_experiment(request: Request, experiment_id: uuid.UUID) -> ExperimentOut:
    with _sessions(request).begin() as db:
        return svc.to_out(db, svc.seal(db, experiment_id))


@router.get("/experiments/{experiment_id}/manifest")
def get_manifest(request: Request, experiment_id: uuid.UUID) -> ManifestOut:
    with _sessions(request).begin() as db:
        exp = svc.get_experiment(db, experiment_id)
        if exp.manifest is None or exp.manifest_hash is None:
            raise ExperimentNotSealedError("el experimento no está sellado", status=exp.status)
        return ManifestOut(
            experiment_id=exp.id, manifest_hash=exp.manifest_hash, manifest=exp.manifest
        )


@router.post(
    "/experiments/{experiment_id}/runs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunOut,
)
def create_run(
    request: Request,
    experiment_id: uuid.UUID,
    body: RunCreate,
    idempotency_key: IdempotencyHeader = None,
) -> JSONResponse:
    key = _require_key(idempotency_key)
    with _sessions(request).begin() as db:

        def handler() -> tuple[int, Any]:
            run = svc.create_run(db, experiment_id, body)
            return status.HTTP_202_ACCEPTED, svc.run_to_out(run).model_dump(mode="json")

        result = run_idempotent(
            db,
            scope=f"POST /experiments/{experiment_id}/runs",
            key=key,
            payload=body.model_dump(mode="json"),
            handler=handler,
        )
    return _respond(result)


@router.get("/experiments/{experiment_id}/runs")
def list_runs(request: Request, experiment_id: uuid.UUID) -> list[RunOut]:
    with _sessions(request).begin() as db:
        return [svc.run_to_out(r) for r in svc.list_runs(db, experiment_id)]


@router.get("/runs/{run_id}")
def get_run(request: Request, run_id: uuid.UUID) -> RunOut:
    with _sessions(request).begin() as db:
        return svc.run_to_out(svc.get_run(db, run_id))
