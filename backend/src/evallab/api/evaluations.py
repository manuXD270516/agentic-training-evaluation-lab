import uuid
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from evallab.api.http import IdempotencyHeader, dump, require_key, respond, sessions
from evallab.schemas import EvaluationOut
from evallab.services import evaluations as svc
from evallab.services.idempotency import run_idempotent

router = APIRouter()


@router.post(
    "/runs/{run_id}/evaluations",
    status_code=status.HTTP_201_CREATED,
    response_model=EvaluationOut,
)
def create_evaluation(
    request: Request, run_id: uuid.UUID, idempotency_key: IdempotencyHeader = None
) -> JSONResponse:
    key = require_key(idempotency_key)
    with sessions(request).begin() as db:

        def handler() -> tuple[int, Any]:
            evaluation = svc.evaluate_run(db, run_id)
            return status.HTTP_201_CREATED, dump(svc.to_out(db, evaluation))

        result = run_idempotent(
            db,
            scope=f"POST /runs/{run_id}/evaluations",
            key=key,
            payload={},
            handler=handler,
        )
    return respond(result)


@router.get("/runs/{run_id}/evaluations")
def list_evaluations(request: Request, run_id: uuid.UUID) -> list[EvaluationOut]:
    with sessions(request).begin() as db:
        return [svc.to_out(db, e) for e in svc.list_evaluations(db, run_id)]


@router.get("/evaluations/{evaluation_id}")
def get_evaluation(request: Request, evaluation_id: uuid.UUID) -> EvaluationOut:
    with sessions(request).begin() as db:
        return svc.to_out(db, svc.get_evaluation(db, evaluation_id))
