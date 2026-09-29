"""JSON canónico (RFC 8785) y digests SHA-256 para manifests y payloads."""

import hashlib
from typing import Any

import rfc8785


def canonical_json(value: Any) -> bytes:
    return rfc8785.dumps(value)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_digest(value: Any) -> str:
    return sha256_hex(canonical_json(value))
