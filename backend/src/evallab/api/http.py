import re
from typing import Annotated, Any

from fastapi import Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from evallab.services.errors import DomainError
from evallab.services.idempotency import IdempotentResult

IDEMPOTENCY_KEY = re.compile(r"^[\x21-\x7e]{1,255}$")
IdempotencyHeader = Annotated[str | None, Header(alias="Idempotency-Key")]


class IdempotencyKeyError(DomainError):
    status_code = 400
    code = "idempotency_key_required"


def sessions(request: Request) -> sessionmaker[Session]:
    bound: sessionmaker[Session] = request.app.state.sessions
    return bound


def require_key(key: str | None) -> str:
    if key is None or not IDEMPOTENCY_KEY.fullmatch(key):
        raise IdempotencyKeyError(
            "las creaciones exigen Idempotency-Key de 1 a 255 caracteres ASCII visibles"
        )
    return key


def respond(result: IdempotentResult) -> JSONResponse:
    headers = {"Idempotent-Replayed": "true"} if result.replayed else {}
    return JSONResponse(status_code=result.status_code, content=result.body, headers=headers)


def dump(model: Any) -> Any:
    return model.model_dump(mode="json")
