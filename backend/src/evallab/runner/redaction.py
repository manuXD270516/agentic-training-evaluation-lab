"""Redacción de secretos antes de persistir o exportar evidencia (trace-format.md, Privacy).

Se sustituye el valor completo por un marcador fijo, sin hash: un hash ordinario de un secreto
corto permitiría adivinarlo. La metadata registra el JSON pointer y el motivo, nunca el valor.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTION_VERSION = "1.0.0"
MARKER = "[REDACTED]"

_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth_token",
        "authorization",
        "bearer",
        "client_secret",
        "cookie",
        "credential",
        "credentials",
        "passwd",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "session_token",
        "set_cookie",
        "token",
    }
)
_SENSITIVE_SUFFIXES = (
    "_access_token",
    "_api_key",
    "_auth_token",
    "_password",
    "_private_key",
    "_refresh_token",
    "_secret",
)
_SECRET_VALUE = re.compile(
    r"(?:\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|\bsk-[A-Za-z0-9_-]{16,}"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)


def _normalize(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


def is_sensitive_key(key: str) -> bool:
    normalized = _normalize(key)
    return normalized in _SENSITIVE_KEYS or normalized.endswith(_SENSITIVE_SUFFIXES)


def _escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _walk(value: Any, pointer: str, fields: list[dict[str, str]]) -> Any:
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            child = f"{pointer}/{_escape(str(key))}"
            if is_sensitive_key(str(key)) and item is not None:
                fields.append({"pointer": child, "reason": "sensitive_key"})
                out[key] = MARKER
            else:
                out[key] = _walk(item, child, fields)
        return out
    if isinstance(value, list):
        return [_walk(item, f"{pointer}/{index}", fields) for index, item in enumerate(value)]
    if isinstance(value, str) and value != MARKER and _SECRET_VALUE.search(value):
        fields.append({"pointer": pointer, "reason": "secret_pattern"})
        return MARKER
    return value


def redact(value: Any) -> tuple[Any, list[dict[str, str]]]:
    """Copia redactada de `value` y la lista de campos sustituidos (pointer + motivo)."""
    fields: list[dict[str, str]] = []
    return _walk(value, "", fields), fields


def redaction_metadata(fields: list[dict[str, str]], *, replayable: bool) -> dict[str, Any]:
    """Metadata del evento; vacía si no se redactó nada."""
    if not fields:
        return {}
    return {
        "version": REDACTION_VERSION,
        "method": "marker",
        "fields": fields,
        "replay": "available" if replayable else "unavailable",
    }
