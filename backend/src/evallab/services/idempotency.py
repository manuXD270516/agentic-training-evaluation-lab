from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from evallab.canonical import canonical_digest
from evallab.db.models import IdempotencyKey
from evallab.services.errors import IdempotencyKeyReusedError


@dataclass(frozen=True)
class IdempotentResult:
    status_code: int
    body: Any
    replayed: bool


def run_idempotent(
    db: Session,
    *,
    scope: str,
    key: str,
    payload: Any,
    handler: Callable[[], tuple[int, Any]],
) -> IdempotentResult:
    """Ejecuta `handler` una sola vez por (scope, key) dentro de la transacción de `db`.

    La clave se reclama antes del efecto: una solicitud concurrente con la misma clave espera
    al commit de la primera y recibe su respuesta. Si el efecto falla, la transacción se
    revierte junto con la clave.
    """
    request_hash = canonical_digest({"scope": scope, "payload": payload})
    claimed = db.execute(
        insert(IdempotencyKey)
        .values(scope=scope, key=key, request_hash=request_hash)
        .on_conflict_do_nothing()
        .returning(IdempotencyKey.key)
    ).first()
    if claimed is None:
        stored = db.execute(
            select(IdempotencyKey).where(IdempotencyKey.scope == scope, IdempotencyKey.key == key)
        ).scalar_one()
        if stored.request_hash != request_hash or stored.status_code is None:
            raise IdempotencyKeyReusedError(
                "Idempotency-Key ya utilizada con otro payload", scope=scope
            )
        return IdempotentResult(stored.status_code, stored.response, replayed=True)

    status_code, body = handler()
    db.execute(
        update(IdempotencyKey)
        .where(IdempotencyKey.scope == scope, IdempotencyKey.key == key)
        .values(status_code=status_code, response=body)
    )
    return IdempotentResult(status_code, body, replayed=False)
