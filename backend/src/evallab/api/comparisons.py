import uuid
from typing import Any, Literal

from fastapi import APIRouter, Request

from evallab.api.http import sessions
from evallab.comparison.protocol import compare_controlled

router = APIRouter()
RunMode = Literal["live", "replay"]
Variable = Literal["pattern", "model", "prompt"]


@router.get("/comparisons")
def get_comparison(
    request: Request,
    baseline: uuid.UUID,
    candidate: uuid.UUID,
    variable: Variable = "pattern",
    baseline_mode: RunMode = "live",
    candidate_mode: RunMode = "live",
) -> dict[str, Any]:
    """Export de la comparación controlada (M11): sólo lectura, calculado bajo demanda."""
    with sessions(request).begin() as db:
        return compare_controlled(
            db,
            baseline,
            candidate,
            variable=variable,
            baseline_mode=baseline_mode,
            candidate_mode=candidate_mode,
        )
