"""Límites efectivos de un run: el mínimo entre presupuesto del experimento y del escenario."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from evallab.runner.errors import InvalidLimitsError

POSITIVE_LIMITS = ("max_steps", "max_model_calls", "max_tool_calls", "max_tokens", "deadline_ms")
NON_NEGATIVE_LIMITS = ("max_retries",)


@dataclass(frozen=True)
class Limits:
    max_steps: int | None = None
    max_model_calls: int | None = None
    max_tool_calls: int | None = None
    max_tokens: int | None = None
    deadline_ms: int | None = None
    max_retries: int | None = None

    @classmethod
    def effective(cls, *sources: Mapping[str, Any] | None) -> Limits:
        values: dict[str, int] = {}
        for source in sources:
            for key, value in (source or {}).items():
                if key not in POSITIVE_LIMITS and key not in NON_NEGATIVE_LIMITS:
                    continue
                minimum = 1 if key in POSITIVE_LIMITS else 0
                if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                    raise InvalidLimitsError(f"límite {key} inválido: {value!r}")
                values[key] = min(values.get(key, value), value)
        return cls(**values)

    @property
    def retries_per_call(self) -> int:
        return self.max_retries or 0

    def as_json(self) -> dict[str, int]:
        return {key: value for key, value in asdict(self).items() if value is not None}
