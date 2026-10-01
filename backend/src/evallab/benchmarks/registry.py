"""Suites disponibles por nombre y su lock de hashes publicado en el repositorio."""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

from evallab.benchmarks.suite import Suite


def suites() -> dict[str, Suite]:
    from evallab.benchmarks.pilot import PILOT

    return {"pilot": PILOT}


def get_suite(name: str) -> Suite:
    available = suites()
    if name not in available:
        raise KeyError(f"suite desconocida: {name}; disponibles: {sorted(available)}")
    return available[name]


def committed_lock(name: str) -> dict[str, Any] | None:
    """Lock versionado en `evallab/benchmarks/locks/<name>.json`, si existe."""
    path = resources.files("evallab.benchmarks").joinpath("locks", f"{name}.json")
    if not path.is_file():
        return None
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded
