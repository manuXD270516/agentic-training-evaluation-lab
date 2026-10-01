import uuid
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, Response

from evallab.api.http import IdempotencyHeader, dump, require_key, respond, sessions
from evallab.schemas import (
    ExperimentCreate,
    ExperimentOut,
    ExperimentUpdate,
    ManifestOut,
    RunCreate,
    RunOut,
    TraceOut,
)
from evallab.services import execution as execution_svc
from evallab.services import experiments as svc
from evallab.services import replays as replay_svc
from evallab.services import reports as report_svc
from evallab.services import traces as trace_svc
from evallab.services.errors import ExperimentNotSealedError
from evallab.services.idempotency import run_idempotent

router = APIRouter()


@router.post(
    "/experiments",
    status_code=status.HTTP_201_CREATED,
    response_model=ExperimentOut,
)
def create_experiment(
    request: Request, body: ExperimentCreate, idempotency_key: IdempotencyHeader = None
) -> JSONResponse:
    key = require_key(idempotency_key)
    with sessions(request).begin() as db:

        def handler() -> tuple[int, Any]:
            exp = svc.create_draft(db, body)
            return status.HTTP_201_CREATED, dump(svc.to_out(db, exp))

        result = run_idempotent(
            db,
            scope="POST /experiments",
            key=key,
            payload=body.model_dump(mode="json"),
            handler=handler,
        )
    return respond(result)


@router.get("/experiments/{experiment_id}")
def get_experiment(request: Request, experiment_id: uuid.UUID) -> ExperimentOut:
    with sessions(request).begin() as db:
        return svc.to_out(db, svc.get_experiment(db, experiment_id))


@router.patch("/experiments/{experiment_id}")
def update_experiment(
    request: Request, experiment_id: uuid.UUID, body: ExperimentUpdate
) -> ExperimentOut:
    with sessions(request).begin() as db:
        return svc.to_out(db, svc.update_draft(db, experiment_id, body))


@router.post("/experiments/{experiment_id}/seal")
def seal_experiment(request: Request, experiment_id: uuid.UUID) -> ExperimentOut:
    with sessions(request).begin() as db:
        return svc.to_out(db, svc.seal(db, experiment_id))


@router.get("/experiments/{experiment_id}/manifest")
def get_manifest(request: Request, experiment_id: uuid.UUID) -> ManifestOut:
    with sessions(request).begin() as db:
        exp = svc.get_experiment(db, experiment_id)
        if exp.manifest is None or exp.manifest_hash is None:
            raise ExperimentNotSealedError("el experimento no está sellado", status=exp.status)
        return ManifestOut(
            experiment_id=exp.id, manifest_hash=exp.manifest_hash, manifest=exp.manifest
        )


@router.get("/experiments/{experiment_id}/report")
def get_report(request: Request, experiment_id: uuid.UUID) -> dict[str, Any]:
    """Reporte descriptivo: N = celdas programadas, unknown y N/A explícitos, sin inferencia."""
    with sessions(request).begin() as db:
        return report_svc.experiment_report(db, experiment_id)


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
    key = require_key(idempotency_key)
    with sessions(request).begin() as db:

        def handler() -> tuple[int, Any]:
            run = svc.create_run(db, experiment_id, body)
            return status.HTTP_202_ACCEPTED, dump(svc.run_to_out(run))

        result = run_idempotent(
            db,
            scope=f"POST /experiments/{experiment_id}/runs",
            key=key,
            payload=body.model_dump(mode="json"),
            handler=handler,
        )
    return respond(result)


@router.get("/experiments/{experiment_id}/runs")
def list_runs(request: Request, experiment_id: uuid.UUID) -> list[RunOut]:
    with sessions(request).begin() as db:
        return [svc.run_to_out(r) for r in svc.list_runs(db, experiment_id)]


@router.get("/runs/{run_id}")
def get_run(request: Request, run_id: uuid.UUID) -> RunOut:
    with sessions(request).begin() as db:
        return svc.run_to_out(svc.get_run(db, run_id))


@router.post(
    "/runs/{run_id}/replays",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunOut,
)
def create_replay(
    request: Request, run_id: uuid.UUID, idempotency_key: IdempotencyHeader = None
) -> JSONResponse:
    key = require_key(idempotency_key)
    with sessions(request).begin() as db:

        def handler() -> tuple[int, Any]:
            run = replay_svc.create_replay(db, run_id)
            return status.HTTP_202_ACCEPTED, dump(svc.run_to_out(run))

        result = run_idempotent(
            db, scope=f"POST /runs/{run_id}/replays", key=key, payload={}, handler=handler
        )
    return respond(result)


@router.get("/runs/{run_id}/trace")
def get_run_trace(request: Request, run_id: uuid.UUID) -> TraceOut:
    with sessions(request).begin() as db:
        trace, events = execution_svc.get_trace(db, run_id)
        return svc.trace_to_out(trace, events)


@router.get("/runs/{run_id}/trace/manifest")
def get_trace_manifest(request: Request, run_id: uuid.UUID) -> dict[str, Any]:
    with sessions(request).begin() as db:
        return trace_svc.export_trace(db, run_id).manifest


@router.get("/runs/{run_id}/trace/export")
def export_run_trace(request: Request, run_id: uuid.UUID) -> Response:
    with sessions(request).begin() as db:
        export = trace_svc.export_trace(db, run_id)
    return Response(
        content=export.jsonl,
        media_type="application/x-ndjson",
        headers={
            "X-Trace-Digest": str(export.manifest["trace_digest"]),
            "X-Events-SHA256": str(export.manifest["events_sha256"]),
        },
    )
