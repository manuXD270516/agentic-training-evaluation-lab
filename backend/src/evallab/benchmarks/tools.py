"""Tools sintéticas compartidas por los benchmarks; sus fixtures son tablas de lookup.

Ninguna tool toca sistema de archivos, procesos ni red: el gateway resuelve cada llamada contra
la fixture publicada. Los valores son inventados y no describen ningún sistema real.
"""

from __future__ import annotations

from typing import Any

from evallab.benchmarks.suite import ToolDef

SKU = {"type": "string", "pattern": "^[A-Z]-[0-9]{3}$"}
EVIDENCE_IDS = {"type": "array", "items": {"type": "string"}, "minItems": 1}


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required if required is not None else sorted(properties),
        "additionalProperties": False,
    }


def _case(arguments: dict[str, Any], result: Any, **extra: Any) -> dict[str, Any]:
    return {"arguments": arguments, "result": result, **extra}


STOCK = {"A-100": 37, "B-200": 5, "C-300": 1, "D-400": 0, "E-500": 12}
PRICES = {"A-100": 12.5, "B-200": 40.0, "C-300": 99.9, "D-400": 7.25, "E-500": 3.0}
WAREHOUSE = {"A-100": "MAD", "B-200": "BCN", "C-300": "MAD", "D-400": "VLC", "E-500": "BCN"}


def inventory_lookup(extra_cases: list[dict[str, Any]] | None = None) -> ToolDef:
    return ToolDef(
        name="inventory-lookup",
        input_schema=_obj({"sku": SKU}),
        output_schema=_obj(
            {
                "sku": SKU,
                "stock": {"type": "integer", "minimum": 0},
                "warehouse": {"type": "string"},
            }
        ),
        effect_class="read_only",
        fixture={
            "kind": "lookup",
            "cases": [
                _case({"sku": sku}, {"sku": sku, "stock": stock, "warehouse": WAREHOUSE[sku]})
                for sku, stock in STOCK.items()
            ]
            + (extra_cases or []),
        },
    )


def price_lookup() -> ToolDef:
    return ToolDef(
        name="price-lookup",
        input_schema=_obj({"sku": SKU}),
        output_schema=_obj({"sku": SKU, "unit_price_eur": {"type": "number", "minimum": 0}}),
        effect_class="read_only",
        fixture={
            "kind": "lookup",
            "cases": [
                _case({"sku": sku}, {"sku": sku, "unit_price_eur": price})
                for sku, price in PRICES.items()
            ],
        },
    )


CALCULATIONS: list[tuple[str, float, float, float]] = [
    ("add", 17, 25, 42),
    ("mul", 42, 2, 84),
    ("mul", 25, 2, 50),
    ("add", 17, 50, 67),
    ("add", 6.21371, 3.10686, 9.32057),
    ("add", 10, 5, 15),
    ("mul", 12.5, 3, 37.5),
    ("mul", 40, 2, 80),
    ("sub", 37, 2, 35),
    ("sub", 100, 37, 63),
    ("div", 84, 4, 21),
    ("add", 37, 5, 42),
    ("mul", 7.25, 4, 29),
    ("sub", 50, 12, 38),
    ("div", 90, 3, 30),
    ("mul", 3, 12, 36),
    ("add", 36, 37.5, 73.5),
    ("div", 73.5, 3, 24.5),
    ("sub", 42, 17, 25),
    ("mul", 99.9, 2, 199.8),
]


def calculator() -> ToolDef:
    number = {"type": "number"}
    return ToolDef(
        name="calculator",
        input_schema=_obj(
            {
                "op": {"type": "string", "enum": ["add", "sub", "mul", "div"]},
                "a": number,
                "b": number,
            }
        ),
        output_schema=_obj({"result": number}),
        effect_class="read_only",
        fixture={
            "kind": "lookup",
            "cases": [
                _case({"op": op, "a": a, "b": b}, {"result": r}) for op, a, b, r in CALCULATIONS
            ],
        },
    )


CONVERSIONS: list[tuple[float, str, str, float]] = [
    (10, "km", "mi", 6.21371),
    (5, "km", "mi", 3.10686),
    (10, "mi", "km", 16.0934),
    (15, "km", "mi", 9.32057),
    (3, "kg", "lb", 6.61387),
    (3, "lb", "kg", 1.36078),
    (42, "km", "mi", 26.0976),
    (2.5, "kg", "lb", 5.51156),
    (100, "c", "f", 212),
    (100, "f", "c", 37.7778),
    (20, "c", "f", 68),
]


def unit_converter() -> ToolDef:
    unit = {"type": "string", "enum": ["km", "mi", "kg", "lb", "c", "f"]}
    return ToolDef(
        name="unit-converter",
        input_schema=_obj({"value": {"type": "number"}, "from": unit, "to": unit}),
        output_schema=_obj({"value": {"type": "number"}, "unit": unit}),
        effect_class="read_only",
        fixture={
            "kind": "lookup",
            "cases": [
                _case({"value": v, "from": f, "to": t}, {"value": r, "unit": t})
                for v, f, t, r in CONVERSIONS
            ],
        },
    )


ORDERS: list[tuple[str, int, str]] = [
    ("B-200", 3, "ORD-1001"),
    ("A-100", 2, "ORD-1002"),
    ("C-300", 1, "ORD-1003"),
    ("E-500", 4, "ORD-1004"),
    ("A-100", 5, "ORD-1005"),
    ("B-200", 1, "ORD-1006"),
]


def order_create() -> ToolDef:
    return ToolDef(
        name="order-create",
        input_schema=_obj(
            {"sku": SKU, "quantity": {"type": "integer", "minimum": 1, "maximum": 10}}
        ),
        output_schema=_obj({"order_id": {"type": "string"}, "status": {"type": "string"}}),
        effect_class="side_effect",
        fixture={
            "kind": "lookup",
            "cases": [
                _case(
                    {"sku": sku, "quantity": qty},
                    {"order_id": order, "status": "created"},
                    state_patch={f"order:{order}": {"sku": sku, "quantity": qty}},
                )
                for sku, qty, order in ORDERS
            ],
        },
    )


def refund_issue() -> ToolDef:
    return ToolDef(
        name="refund-issue",
        input_schema=_obj({"order_id": {"type": "string"}, "amount_eur": {"type": "number"}}),
        output_schema=_obj({"refund_id": {"type": "string"}}),
        effect_class="side_effect",
        fixture={
            "kind": "lookup",
            "cases": [],
            "default": {"result": {"refund_id": "RF-0001"}, "state_patch": {"refunded": True}},
        },
    )


def email_send() -> ToolDef:
    return ToolDef(
        name="email-send",
        input_schema=_obj({"to": {"type": "string"}, "body": {"type": "string"}}),
        output_schema=_obj({"sent": {"type": "boolean"}}),
        effect_class="side_effect",
        fixture={
            "kind": "lookup",
            "cases": [],
            "default": {"result": {"sent": True}, "state_patch": {"emails_sent": 1}},
        },
    )


def kb_search(entries: dict[str, list[dict[str, str]]]) -> ToolDef:
    """Búsqueda por lookup exacto de la consulta (M5); el retriever versionado llega en M9."""
    chunk = _obj({"chunk_id": {"type": "string"}, "text": {"type": "string"}})
    return ToolDef(
        name="kb-search",
        input_schema=_obj({"query": {"type": "string", "minLength": 1}}),
        output_schema=_obj({"results": {"type": "array", "items": chunk}}),
        effect_class="read_only",
        fixture={
            "kind": "lookup",
            "cases": [_case({"query": q}, {"results": r}) for q, r in sorted(entries.items())],
            "default": {"result": {"results": []}},
        },
    )
