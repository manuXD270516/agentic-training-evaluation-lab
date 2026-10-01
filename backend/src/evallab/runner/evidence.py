"""Normalización de salidas para comparar runs: los event_id de evidencia son nuevos en cada
run, así que se sustituyen por su posición antes de comparar (replay, determinismo)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def normalize_evidence(output: Any, evidence_refs: Iterable[Mapping[str, Any]]) -> Any:
    positions = {str(ref.get("event_id")): f"evidence:{i}" for i, ref in enumerate(evidence_refs)}

    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, str):
            return positions.get(value, value)
        return value

    return walk(output)
