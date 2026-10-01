"""ModelGateway neutral (7.1): uso ausente, revisión desconocida, coste, errores y límites."""

import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from evallab.domain.lifecycle import RunStatus
from evallab.runner import agent as agent_module
from evallab.runner.agent import execute_agent
from evallab.runner.contracts import (
    Action,
    AgentSnapshot,
    BudgetRemaining,
    FinalAnswer,
    ModelCall,
    ModelObservation,
    PatternObservation,
    RunContext,
    RunResult,
)
from evallab.runner.models import (
    ModelCallError,
    ModelRequest,
    ModelSnapshot,
    PriceInfo,
    ProviderModelGateway,
    TokenUsage,
    estimate_cost,
)
from evallab.runner.providers import FixtureModelProvider
from evallab.runner.sink import MemoryTraceSink
from evallab.runner.tools import FixtureToolGateway
from evallab.schemas import ScenarioPublicOut
from evallab.settings import SandboxPolicy

SCRIPT_HASH = "c" * 64
PRICE = PriceInfo(
    ref="d" * 64,
    currency="USD",
    input_per_mtok=Decimal("3"),
    output_per_mtok=Decimal("15"),
    cached_input_per_mtok=Decimal("0.3"),
    synthetic=True,
)


def _model(
    *, revision: str | None = None, price: PriceInfo | None = PRICE, max_tokens: int = 100
) -> ModelSnapshot:
    return ModelSnapshot(
        role="executor",
        id=uuid.uuid4(),
        version="1.0.0",
        content_hash="e" * 64,
        provider="fixture",
        requested_model=SCRIPT_HASH,
        resolved_revision=revision,
        temperature="0",
        max_tokens=max_tokens,
        seed_support="unsupported",
        price=price,
    )


def _gateway(responses: list[dict[str, Any]], **model: Any) -> ProviderModelGateway:
    script = {"kind": "model_script", "default": responses}
    provider = FixtureModelProvider(lambda h: script if h == SCRIPT_HASH else None)
    return ProviderModelGateway({"executor": _model(**model)}, {"fixture": provider})


class AskThenAnswer:
    """Patrón mínimo de prueba: pide `turns` respuestas al modelo y devuelve la última."""

    pattern = "react"
    pattern_version = "0.0.0-test"

    def __init__(self, turns: int = 1) -> None:
        self.turns = turns

    def next_action(
        self,
        context: RunContext,
        observations: Sequence[PatternObservation],
        remaining: BudgetRemaining,
    ) -> Action:
        answers = [o for o in observations if isinstance(o, ModelObservation)]
        if len(answers) < self.turns:
            message = {"role": "user", "content": f"turno {len(answers)}"}
            return ModelCall(ModelRequest(role="executor", messages=(message,), max_tokens=100))
        return FinalAnswer(output={"answer": answers[-1].content})


def _context(limits: dict[str, Any] | None = None) -> RunContext:
    return RunContext(
        run_id=uuid.uuid4(),
        attempt_id=uuid.uuid4(),
        experiment_id=uuid.uuid4(),
        mode="live",
        seed=11,
        started_at=datetime.now(UTC),
        limits=limits or {"max_steps": 10},
        sandbox=SandboxPolicy(),
        manifest_hash=None,
        experiment_budgets={},
        clock=time.monotonic,
    )


def _scenario() -> ScenarioPublicOut:
    return ScenarioPublicOut(
        id=uuid.uuid4(),
        version="1.0.0",
        schema_version="1.0",
        slug="model-test",
        content_hash="f" * 64,
        primary_category="reasoning",
        tags=[],
        difficulty="easy",
        task={"instruction": "responde", "input": {}},
        tools=[],
        environment={},
        limits={},
        created_at=datetime.now(UTC),
    )


def _agent() -> AgentSnapshot:
    return AgentSnapshot(
        id=uuid.uuid4(),
        version="1.0.0",
        pattern="react",
        pattern_version="0.0.0-test",
        content_hash="a" * 64,
        pattern_parameters={},
        prompt_hash=None,
        roles=("executor",),
    )


def _run(
    monkeypatch: pytest.MonkeyPatch,
    gateway: ProviderModelGateway,
    *,
    turns: int = 1,
    limits: dict[str, Any] | None = None,
) -> tuple[RunResult, MemoryTraceSink]:
    monkeypatch.setattr(agent_module, "adapter_for", lambda *_: AskThenAnswer(turns))
    context = _context(limits)
    sink = MemoryTraceSink(
        started_at=context.started_at,
        schema_version="1.0",
        run_id=context.run_id,
        attempt_id=context.attempt_id,
    )
    tools = FixtureToolGateway([])
    return execute_agent(context, _agent(), _scenario(), gateway, tools, sink), sink


def _events(sink: MemoryTraceSink, kind: str) -> list[dict[str, Any]]:
    return [e.payload for e in sink.events if e.type == kind]


USAGE: dict[str, Any] = {"input_tokens": 1000, "output_tokens": 200}


def _usage(result: RunResult) -> dict[str, Any]:
    document: dict[str, Any] = dict(result.usage.as_json())
    return document


def test_cost_is_tokens_times_snapshot_price() -> None:
    usage = TokenUsage(input_tokens=1000, output_tokens=200, source="observed")
    cost = estimate_cost(usage, PRICE)
    assert cost.status == "estimated"
    assert cost.amount == Decimal("0.006")  # 1000·3/1e6 + 200·15/1e6
    cached = TokenUsage(
        input_tokens=1000, output_tokens=0, cached_input_tokens=800, source="observed"
    )
    assert estimate_cost(cached, PRICE).amount == Decimal("0.00084")  # 200·3 + 800·0.3
    assert estimate_cost(TokenUsage(), PRICE).status == "unknown"
    assert estimate_cost(usage, None).amount is None


def test_missing_usage_is_unknown_not_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    result, sink = _run(monkeypatch, _gateway([{"content": "42"}]))
    assert result.status == RunStatus.COMPLETED
    completed = _events(sink, "model.completed")[0]
    assert completed["usage"]["source"] == "unknown"
    assert completed["usage"]["total_tokens"] is None
    assert completed["cost"]["status"] == "unknown" and completed["cost"]["amount"] is None
    usage = _usage(result)
    assert usage["model_calls"] == 1
    assert usage["tokens"]["status"] == "unknown"
    assert usage["tokens"]["total_tokens"] is None and usage["tokens"]["known_subtotal"] == 0
    assert usage["cost"]["status"] == "unknown" and usage["cost"]["amount"] is None


def test_unknown_revision_is_declared_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    result, sink = _run(monkeypatch, _gateway([{"content": "42", "usage": USAGE}]))
    started = _events(sink, "run.started")[0]
    assert started["models"][0]["resolved_revision"] is None
    assert started["models"][0]["revision_status"] == "unknown"
    completed = _events(sink, "model.completed")[0]
    assert (completed["resolved_model"], completed["revision_status"]) == ("unknown", "unknown")
    assert _usage(result)["cost"]["amount"] == "0.006"

    reported = [{"content": "42", "usage": USAGE, "resolved_model": "fake-2026-09-30"}]
    _, sink = _run(monkeypatch, _gateway(reported, revision="fake-declared"))
    completed = _events(sink, "model.completed")[0]
    assert (completed["resolved_model"], completed["revision_status"]) == (
        "fake-2026-09-30",
        "reported",
    )
    _, sink = _run(monkeypatch, _gateway([{"content": "42"}], revision="fake-declared"))
    completed = _events(sink, "model.completed")[0]
    assert (completed["resolved_model"], completed["revision_status"]) == (
        "fake-declared",
        "declared",
    )


def test_typed_errors_and_traced_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    flaky: list[dict[str, Any]] = [
        {"error": {"kind": "rate_limited"}},
        {"content": "ok", "usage": USAGE},
    ]
    result, sink = _run(monkeypatch, _gateway(flaky), limits={"max_steps": 10, "max_retries": 1})
    assert result.status == RunStatus.COMPLETED and result.output == {"answer": "ok"}
    failed = _events(sink, "model.failed")[0]
    assert (failed["kind"], failed["retriable"], failed["error_class"]) == (
        "rate_limited",
        True,
        "model_error",
    )
    assert _events(sink, "retry.scheduled")[0]["reason"] == "rate_limited"
    usage = _usage(result)
    assert (usage["model_calls"], usage["retries"]) == (2, 1)
    # La llamada fallida no informó uso: el total queda unknown con el subtotal visible.
    assert usage["tokens"]["status"] == "unknown" and usage["tokens"]["known_subtotal"] == 1200
    assert usage["by_role"]["executor"]["failed_calls"] == 1

    result, sink = _run(monkeypatch, _gateway([{"error": {"kind": "content_filter"}}]))
    assert (result.status, result.error_class) == (RunStatus.FAILED, "model_error")
    assert _events(sink, "run.failed")[0]["error_class"] == "model_error"

    result, _ = _run(monkeypatch, _gateway([{"malformed": True}]))
    assert (result.status, result.error_class) == (RunStatus.FAILED, "model_error")
    assert "vacía" in (result.error or "")


def test_gateway_rejects_unknown_role_and_disabled_provider() -> None:
    gateway = _gateway([{"content": "x"}])
    with pytest.raises(ModelCallError) as unknown_role:
        gateway.generate(ModelRequest(role="planner", messages=()))
    assert unknown_role.value.kind == "unknown_role"
    live = ProviderModelGateway({"executor": _model()}, {})
    with pytest.raises(ModelCallError) as disabled:
        live.generate(ModelRequest(role="executor", messages=()))
    assert disabled.value.kind == "provider_unavailable"


def test_token_and_cost_limits_stop_before_calling(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = [{"content": str(i), "usage": USAGE} for i in range(3)]
    result, sink = _run(
        monkeypatch, _gateway(answers), turns=3, limits={"max_steps": 10, "max_tokens": 2000}
    )
    assert result.status == RunStatus.BUDGET_EXCEEDED
    assert result.termination == {"limit": "max_tokens", "limit_value": 2000, "used": 2400}
    assert len(_events(sink, "model.requested")) == 2

    # Reserva: entrada estimada + 100 tokens de salida a 15 USD/M antes de cada llamada.
    result, sink = _run(
        monkeypatch,
        _gateway(answers),
        turns=3,
        limits={"max_steps": 10, "max_cost_usd": "0.007"},
    )
    assert result.status == RunStatus.BUDGET_EXCEEDED
    assert result.termination is not None and result.termination["limit"] == "max_cost_usd"
    assert len(_events(sink, "model.requested")) == 1


def test_late_accounting_overrun_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    # El proveedor gasta más de lo reservado (salida mayor que max_tokens declarado).
    expensive = [{"content": "x", "usage": {"input_tokens": 10, "output_tokens": 1000}}]
    result, _ = _run(
        monkeypatch, _gateway(expensive), limits={"max_steps": 10, "max_cost_usd": "0.01"}
    )
    cost = _usage(result)["cost"]
    assert result.status == RunStatus.COMPLETED
    assert cost["overrun"] is True and Decimal(cost["amount"]) > Decimal("0.01")
