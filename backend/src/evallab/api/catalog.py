import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from evallab.api.http import IdempotencyHeader, dump, require_key, respond, sessions
from evallab.schemas import (
    BenchmarkCreate,
    BenchmarkOut,
    DatasetCreate,
    DatasetOut,
    FixtureCreate,
    FixtureOut,
    ScenarioCreate,
    ScenarioOracleOut,
    ScenarioPublicOut,
    ToolCreate,
    ToolOut,
)
from evallab.services import catalog as svc
from evallab.services.idempotency import run_idempotent

router = APIRouter()


def _create(
    request: Request,
    scope: str,
    key: str | None,
    payload: Any,
    handler: Callable[[Session], tuple[int, Any]],
) -> JSONResponse:
    token = require_key(key)
    with sessions(request).begin() as db:

        def run() -> tuple[int, Any]:
            return handler(db)

        result = run_idempotent(db, scope=scope, key=token, payload=payload, handler=run)
    return respond(result)


@router.post("/fixtures", status_code=status.HTTP_201_CREATED, response_model=FixtureOut)
def create_fixture(
    request: Request, body: FixtureCreate, idempotency_key: IdempotencyHeader = None
) -> JSONResponse:
    return _create(
        request,
        "POST /fixtures",
        idempotency_key,
        body.model_dump(mode="json"),
        lambda db: (
            status.HTTP_201_CREATED,
            dump(svc.fixture_to_out(svc.publish_fixture(db, body))),
        ),
    )


@router.get("/fixtures/{content_hash}")
def read_fixture(request: Request, content_hash: str) -> FixtureOut:
    with sessions(request).begin() as db:
        return svc.fixture_to_out(svc.get_fixture(db, content_hash))


@router.post("/tool-definitions", status_code=status.HTTP_201_CREATED, response_model=ToolOut)
def create_tool(
    request: Request, body: ToolCreate, idempotency_key: IdempotencyHeader = None
) -> JSONResponse:
    return _create(
        request,
        "POST /tool-definitions",
        idempotency_key,
        body.model_dump(mode="json"),
        lambda db: (status.HTTP_201_CREATED, dump(svc.tool_to_out(svc.publish_tool(db, body)))),
    )


@router.get("/tool-definitions/{tool_id}/versions/{version}")
def read_tool(request: Request, tool_id: uuid.UUID, version: str) -> ToolOut:
    with sessions(request).begin() as db:
        return svc.tool_to_out(svc.get_tool(db, tool_id, version))


@router.post("/scenarios", status_code=status.HTTP_201_CREATED, response_model=ScenarioPublicOut)
def create_scenario(
    request: Request, body: ScenarioCreate, idempotency_key: IdempotencyHeader = None
) -> JSONResponse:
    return _create(
        request,
        "POST /scenarios",
        idempotency_key,
        body.model_dump(mode="json"),
        lambda db: (
            status.HTTP_201_CREATED,
            dump(svc.scenario_public_view(svc.publish_scenario(db, body))),
        ),
    )


@router.get("/scenarios/{scenario_id}/versions/{version}")
def read_scenario(request: Request, scenario_id: uuid.UUID, version: str) -> ScenarioPublicOut:
    with sessions(request).begin() as db:
        return svc.scenario_public_view(svc.get_scenario(db, scenario_id, version))


@router.get("/scenarios/{scenario_id}/versions/{version}/oracle")
def read_scenario_oracle(
    request: Request, scenario_id: uuid.UUID, version: str
) -> ScenarioOracleOut:
    with sessions(request).begin() as db:
        return svc.scenario_oracle_view(svc.get_scenario(db, scenario_id, version))


@router.post("/datasets", status_code=status.HTTP_201_CREATED, response_model=DatasetOut)
def create_dataset(
    request: Request, body: DatasetCreate, idempotency_key: IdempotencyHeader = None
) -> JSONResponse:
    return _create(
        request,
        "POST /datasets",
        idempotency_key,
        body.model_dump(mode="json"),
        lambda db: (
            status.HTTP_201_CREATED,
            dump(svc.dataset_to_out(svc.publish_dataset(db, body))),
        ),
    )


@router.get("/datasets/{dataset_id}/versions/{version}")
def read_dataset(request: Request, dataset_id: uuid.UUID, version: str) -> DatasetOut:
    with sessions(request).begin() as db:
        return svc.dataset_to_out(svc.get_dataset(db, dataset_id, version))


@router.post("/benchmarks", status_code=status.HTTP_201_CREATED, response_model=BenchmarkOut)
def create_benchmark(
    request: Request, body: BenchmarkCreate, idempotency_key: IdempotencyHeader = None
) -> JSONResponse:
    return _create(
        request,
        "POST /benchmarks",
        idempotency_key,
        body.model_dump(mode="json"),
        lambda db: (
            status.HTTP_201_CREATED,
            dump(svc.benchmark_to_out(svc.publish_benchmark(db, body))),
        ),
    )


@router.get("/benchmarks/{benchmark_id}/versions/{version}")
def read_benchmark(request: Request, benchmark_id: uuid.UUID, version: str) -> BenchmarkOut:
    with sessions(request).begin() as db:
        return svc.benchmark_to_out(svc.get_benchmark(db, benchmark_id, version))
