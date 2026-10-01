import uuid

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from evallab.api.catalog import _create
from evallab.api.http import IdempotencyHeader, dump, sessions
from evallab.schemas import (
    AgentConfigurationCreate,
    AgentConfigurationOut,
    ModelConfigurationCreate,
    ModelConfigurationOut,
)
from evallab.services import agents as svc

router = APIRouter()


@router.post(
    "/model-configurations",
    status_code=status.HTTP_201_CREATED,
    response_model=ModelConfigurationOut,
)
def create_model(
    request: Request, body: ModelConfigurationCreate, idempotency_key: IdempotencyHeader = None
) -> JSONResponse:
    return _create(
        request,
        "POST /model-configurations",
        idempotency_key,
        body.model_dump(mode="json"),
        lambda db: (status.HTTP_201_CREATED, dump(svc.model_to_out(svc.publish_model(db, body)))),
    )


@router.get("/model-configurations/{model_id}/versions/{version}")
def read_model(request: Request, model_id: uuid.UUID, version: str) -> ModelConfigurationOut:
    with sessions(request).begin() as db:
        return svc.model_to_out(svc.get_model(db, model_id, version))


@router.post(
    "/agent-configurations",
    status_code=status.HTTP_201_CREATED,
    response_model=AgentConfigurationOut,
)
def create_agent(
    request: Request, body: AgentConfigurationCreate, idempotency_key: IdempotencyHeader = None
) -> JSONResponse:
    return _create(
        request,
        "POST /agent-configurations",
        idempotency_key,
        body.model_dump(mode="json"),
        lambda db: (
            status.HTTP_201_CREATED,
            dump(svc.agent_to_out(db, svc.publish_agent(db, body))),
        ),
    )


@router.get("/agent-configurations/{agent_id}/versions/{version}")
def read_agent(request: Request, agent_id: uuid.UUID, version: str) -> AgentConfigurationOut:
    with sessions(request).begin() as db:
        return svc.agent_to_out(db, svc.get_agent(db, agent_id, version))
