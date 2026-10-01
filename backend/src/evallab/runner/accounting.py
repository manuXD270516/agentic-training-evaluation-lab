"""Contabilidad de tokens y coste por rol durante un run (M6, 7.1).

Reglas de metrics.md: categorías no solapadas, uso no informado = `unknown` (el total no se
presenta como cero y el subtotal conocido sigue visible), coste estimado sólo con precio, y
llamadas fallidas cuentan como llamadas. Con límite monetario, una llamada sin uso informado
consume su reserva máxima (estimación conservadora, etiquetada).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from evallab.runner.models import ModelResult


@dataclass
class RoleUsage:
    model_calls: int = 0
    failed_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    unknown_usage_calls: int = 0
    cost: Decimal = Decimal(0)
    unknown_cost_calls: int = 0

    def as_json(self) -> dict[str, Any]:
        return {
            "model_calls": self.model_calls,
            "failed_calls": self.failed_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "unknown_usage_calls": self.unknown_usage_calls,
            "estimated_cost_known": format(self.cost, "f"),
            "unknown_cost_calls": self.unknown_cost_calls,
        }


@dataclass
class ModelAccounting:
    roles: dict[str, RoleUsage] = field(default_factory=dict)
    currency: str = "USD"
    synthetic_price: bool = False
    reserved_unknown: Decimal = Decimal(0)
    overrun: bool = False

    def role(self, name: str) -> RoleUsage:
        return self.roles.setdefault(name, RoleUsage())

    @property
    def model_calls(self) -> int:
        return sum(r.model_calls for r in self.roles.values())

    def record_failure(self, role: str) -> None:
        usage = self.role(role)
        usage.model_calls += 1
        usage.failed_calls += 1
        # Un fallo del proveedor no informa uso: sus tokens y coste son desconocidos.
        usage.unknown_usage_calls += 1
        usage.unknown_cost_calls += 1

    def record_result(self, result: ModelResult, reservation: Decimal | None) -> None:
        usage = self.role(result.model.role)
        usage.model_calls += 1
        tokens = result.response.usage
        if tokens.known:
            assert tokens.input_tokens is not None and tokens.output_tokens is not None
            usage.input_tokens += tokens.input_tokens
            usage.output_tokens += tokens.output_tokens
        else:
            usage.unknown_usage_calls += 1
        if result.cost.status == "estimated" and result.cost.amount is not None:
            usage.cost += result.cost.amount
            self.currency = result.cost.currency
            self.synthetic_price = self.synthetic_price or result.cost.synthetic_price
        else:
            usage.unknown_cost_calls += 1
            if reservation is not None:
                self.reserved_unknown += reservation

    @property
    def known_tokens(self) -> int:
        return sum(r.input_tokens + r.output_tokens for r in self.roles.values())

    @property
    def known_cost(self) -> Decimal:
        return sum((r.cost for r in self.roles.values()), Decimal(0))

    def committed_cost(self) -> Decimal:
        """Coste para límites: conocido más reservas de llamadas sin uso informado."""
        return self.known_cost + self.reserved_unknown

    def tokens_json(self) -> dict[str, Any] | None:
        if self.model_calls == 0:
            return None
        unknown = sum(r.unknown_usage_calls for r in self.roles.values())
        inputs = sum(r.input_tokens for r in self.roles.values())
        outputs = sum(r.output_tokens for r in self.roles.values())
        return {
            "status": "unknown" if unknown else "observed",
            "input_tokens": None if unknown else inputs,
            "output_tokens": None if unknown else outputs,
            "total_tokens": None if unknown else inputs + outputs,
            "known_subtotal": inputs + outputs,
            "unknown_usage_calls": unknown,
        }

    def cost_json(self) -> dict[str, Any] | None:
        if self.model_calls == 0:
            return None
        unknown = sum(r.unknown_cost_calls for r in self.roles.values())
        known = self.known_cost
        return {
            "status": "unknown" if unknown else "estimated",
            "amount": None if unknown else format(known, "f"),
            "known_subtotal": format(known, "f"),
            "currency": self.currency,
            "unknown_cost_calls": unknown,
            "synthetic_price": self.synthetic_price,
            "overrun": self.overrun,
        }

    def by_role_json(self) -> dict[str, Any] | None:
        if not self.roles:
            return None
        return {name: usage.as_json() for name, usage in sorted(self.roles.items())}
