"""Reporte descriptivo de un experimento (M5, 6.2) según metrics.md.

N son todas las celdas programadas (escenarios del benchmark por agentes por repeticiones), no sólo
las ejecutadas: una celda ausente, sin evaluar o con evaluación `error` cuenta como unknown y
nunca desaparece del denominador. Consumo y latencia incluyen runs fallidos. El reporte no
contiene intervalos ni afirmaciones de superioridad: con un piloto sólo es descriptivo.
"""

from __future__ import annotations

import math
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from evallab.canonical import canonical_digest
from evallab.db import models as m
from evallab.evaluation.metrics import PROFILE_DESCRIPTOR, success_summary
from evallab.runner.evidence import normalize_evidence
from evallab.services.errors import ExperimentNotSealedError, NotFoundError
from evallab.services.execution import TERMINAL_EVENT_TYPES

REPORT_VERSION = "1.0.0"
STATUSES = ("pass", "fail", "unknown", "not_applicable", "error")
RATIO_METRICS = (
    "tool_accuracy",
    "required_tool_coverage",
    "argument_accuracy",
    "schema_argument_validity",
    "evidence_coverage",
    "policy_violation_rate",
)
# Del perfil `retrieval-metrics@1.0.0`: sólo existen en escenarios con retriever versionado.
RETRIEVAL_METRICS = ("retrieval_recall_at_k", "retrieval_mrr_at_k")
USAGE_KEYS = ("steps", "tool_calls", "model_calls", "retries")
DESCRIPTIVE_MIN_SCENARIOS_PER_CATEGORY = 5


def nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    """Percentil por nearest-rank (metrics.md): el valor en la posición ceil(p·n)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile / 100 * len(ordered)))
    return ordered[rank - 1]


def distribution(values: Sequence[float], planned: int) -> dict[str, Any]:
    n = len(values)
    return {
        "n": n,
        "coverage": None if planned == 0 else float(Fraction(n, planned)),
        "mean": None if n == 0 else sum(values) / n,
        "p50": nearest_rank(values, 50),
        "p95": nearest_rank(values, 95),
        "method": "nearest_rank",
    }


@dataclass(frozen=True)
class Cell:
    scenario_id: uuid.UUID
    scenario_version: str
    category: str
    slug: str | None
    agent_id: uuid.UUID
    agent_version: str
    repetition: int
    seed: int
    run: m.Run | None
    evaluation: m.Evaluation | None
    scores: Mapping[str, m.Score]
    latency_ms: int | None


def _latest_evaluations(db: Session, run_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, m.Evaluation]:
    latest: dict[uuid.UUID, m.Evaluation] = {}
    if not run_ids:
        return latest
    rows = db.scalars(
        select(m.Evaluation)
        .where(m.Evaluation.run_id.in_(run_ids))
        .order_by(m.Evaluation.created_at, m.Evaluation.id)
    )
    for row in rows:
        latest[row.run_id] = row
    return latest


def _scores(
    db: Session, evaluation_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, dict[str, m.Score]]:
    by_eval: dict[uuid.UUID, dict[str, m.Score]] = defaultdict(dict)
    if not evaluation_ids:
        return by_eval
    for score in db.scalars(select(m.Score).where(m.Score.evaluation_id.in_(evaluation_ids))):
        if score.scope == "agent":
            by_eval[score.evaluation_id][score.metric_id] = score
    return by_eval


def _latencies(db: Session, run_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
    """Latencia del runner: `elapsed_ms` del evento terminal de la traza sellada."""
    if not run_ids:
        return {}
    rows = db.execute(
        select(m.TraceEvent.run_id, m.TraceEvent.elapsed_ms).where(
            m.TraceEvent.run_id.in_(run_ids), m.TraceEvent.type.in_(TERMINAL_EVENT_TYPES)
        )
    )
    return {run_id: elapsed for run_id, elapsed in rows}


def _planned_scenarios(db: Session, benchmark: m.Benchmark) -> list[m.Scenario]:
    selection = (
        benchmark.scenario_selection if isinstance(benchmark.scenario_selection, list) else []
    )
    scenarios: list[m.Scenario] = []
    for ref in selection:
        row = db.get(m.Scenario, (uuid.UUID(str(ref["id"])), str(ref["version"])))
        if row is not None:
            scenarios.append(row)
    return scenarios


def collect_cells(db: Session, exp: m.Experiment, mode: str = "live") -> list[Cell]:
    benchmark = db.get(m.Benchmark, (exp.benchmark_id, exp.benchmark_version))
    if benchmark is None:
        raise NotFoundError("benchmark del experimento inexistente")
    scenarios = _planned_scenarios(db, benchmark)
    agents = list(
        db.scalars(
            select(m.ExperimentAgent)
            .where(m.ExperimentAgent.experiment_id == exp.id)
            .order_by(m.ExperimentAgent.agent_id)
        )
    )
    runs = list(db.scalars(select(m.Run).where(m.Run.experiment_id == exp.id, m.Run.mode == mode)))
    by_cell = {
        (r.scenario_id, r.scenario_version, r.agent_id, r.agent_version, r.repetition): r
        for r in runs
    }
    run_ids = [r.id for r in runs]
    evaluations = _latest_evaluations(db, run_ids)
    completed = [e.id for e in evaluations.values() if e.status == "completed"]
    scores = _scores(db, completed)
    latencies = _latencies(db, run_ids)
    cells: list[Cell] = []
    for scenario in scenarios:
        for agent in agents:
            for repetition, seed in enumerate(exp.seeds, start=1):
                run = by_cell.get(
                    (scenario.id, scenario.version, agent.agent_id, agent.agent_version, repetition)
                )
                evaluation = evaluations.get(run.id) if run is not None else None
                cells.append(
                    Cell(
                        scenario_id=scenario.id,
                        scenario_version=scenario.version,
                        category=scenario.primary_category,
                        slug=scenario.slug,
                        agent_id=agent.agent_id,
                        agent_version=agent.agent_version,
                        repetition=repetition,
                        seed=seed,
                        run=run,
                        evaluation=evaluation,
                        scores=scores.get(evaluation.id, {}) if evaluation is not None else {},
                        latency_ms=latencies.get(run.id) if run is not None else None,
                    )
                )
    return cells


def _task_status(cell: Cell) -> str | None:
    """task_success de la celda; None = sin evaluación utilizable (cuenta como unknown)."""
    if cell.evaluation is None or cell.evaluation.status != "completed":
        return None
    score = cell.scores.get("task_success")
    return score.status if score is not None else None


def _raw_status(cell: Cell) -> str | None:
    if cell.evaluation is None or cell.evaluation.status != "completed":
        return None
    score = cell.scores.get("raw_outcome_pass")
    return score.status if score is not None else None


def _metric_summary(cells: Sequence[Cell], metric: str) -> dict[str, Any]:
    statuses: Counter[str] = Counter()
    numerator = denominator = 0
    values: list[float] = []
    for cell in cells:
        score = cell.scores.get(metric)
        if score is None:
            # Sin evaluación es unknown; evaluada sin esa métrica (perfil no aplicable), N/A.
            evaluated = cell.evaluation is not None and cell.evaluation.status == "completed"
            statuses["not_applicable" if evaluated else "unknown"] += 1
            continue
        statuses[score.status] += 1
        if score.status not in ("pass", "fail"):
            continue
        if score.denominator:
            numerator += int(score.numerator or 0)
            denominator += int(score.denominator)
        # Las métricas sin razón (p. ej. MRR) sólo tienen media por run (macro).
        if score.value is not None:
            values.append(score.value)
    return {
        "statuses": {s: statuses.get(s, 0) for s in STATUSES},
        "micro": {
            "numerator": numerator,
            "denominator": denominator,
            "value": None if denominator == 0 else float(Fraction(numerator, denominator)),
        },
        "macro": {"runs": len(values), "value": sum(values) / len(values) if values else None},
    }


def _run_usage(cell: Cell) -> dict[str, Any]:
    result = cell.run.result if cell.run is not None else None
    usage = result.get("usage") if isinstance(result, dict) else None
    return usage if isinstance(usage, dict) else {}


def _tokens_and_cost(usages: Sequence[dict[str, Any]], model_calls: int) -> dict[str, Any]:
    """Suma tokens y coste de los runs; cualquier parte desconocida deja el total unknown y
    conserva el subtotal conocido (metrics.md). Sin llamadas a modelo, N/A."""
    if model_calls == 0:
        return {
            "tokens": {"status": "not_applicable", "reason": "sin llamadas a modelo"},
            "estimated_cost": {
                "status": "not_applicable",
                "currency": "USD",
                "reason": "sin llamadas a modelo ni tools con coste declarado",
            },
        }
    known_tokens = unknown_token_calls = unknown_cost_calls = 0
    known_cost = Decimal(0)
    currencies: set[str] = set()
    synthetic = overrun = False
    for usage in usages:
        tokens = usage.get("tokens") or {}
        cost = usage.get("cost") or {}
        if usage.get("model_calls") and not tokens:
            unknown_token_calls += int(usage.get("model_calls") or 0)
        known_tokens += int(tokens.get("known_subtotal") or 0)
        unknown_token_calls += int(tokens.get("unknown_usage_calls") or 0)
        if usage.get("model_calls") and not cost:
            unknown_cost_calls += int(usage.get("model_calls") or 0)
        known_cost += Decimal(str(cost.get("known_subtotal") or "0"))
        unknown_cost_calls += int(cost.get("unknown_cost_calls") or 0)
        if cost.get("currency"):
            currencies.add(str(cost["currency"]))
        synthetic = synthetic or bool(cost.get("synthetic_price"))
        overrun = overrun or bool(cost.get("overrun"))
    return {
        "tokens": {
            "status": "unknown" if unknown_token_calls else "observed",
            "total": None if unknown_token_calls else known_tokens,
            "known_subtotal": known_tokens,
            "unknown_usage_calls": unknown_token_calls,
        },
        "estimated_cost": {
            "status": "unknown" if unknown_cost_calls else "estimated",
            "amount": None if unknown_cost_calls else format(known_cost, "f"),
            "known_subtotal": format(known_cost, "f"),
            "currency": ",".join(sorted(currencies)) or "USD",
            "unknown_cost_calls": unknown_cost_calls,
            "synthetic_price": synthetic,
            "overrun_runs": overrun,
            "reason": "precio sintético: no es un coste real" if synthetic else None,
        },
    }


def _usage(cells: Sequence[Cell]) -> dict[str, Any]:
    executed = [c for c in cells if c.run is not None and isinstance(c.run.result, dict)]
    usages = [_run_usage(c) for c in executed]
    totals = {key: sum(int(u.get(key) or 0) for u in usages) for key in USAGE_KEYS}
    return {
        "runs_with_usage": len(executed),
        "planned_cells": len(cells),
        "totals": totals,
        "mean_per_run": {
            key: (value / len(executed) if executed else None) for key, value in totals.items()
        },
        **_tokens_and_cost(usages, totals["model_calls"]),
    }


def _summaries(cells: Sequence[Cell]) -> dict[str, Any]:
    task = success_summary(_task_status(c) for c in cells)
    raw = success_summary(_raw_status(c) for c in cells)
    statuses = Counter(c.run.status if c.run is not None else "missing" for c in cells)
    return {
        "cells": len(cells),
        "run_statuses": dict(sorted(statuses.items())),
        "evaluated": sum(1 for c in cells if _task_status(c) is not None),
        "task_success": task.as_json(),
        "raw_outcome_pass": raw.as_json(),
    }


def _group(cells: Iterable[Cell], key: Any) -> dict[Any, list[Cell]]:
    groups: dict[Any, list[Cell]] = defaultdict(list)
    for cell in cells:
        groups[key(cell)].append(cell)
    return groups


def _agent_label(
    db: Session, agent_id: uuid.UUID, version: str, cells: Sequence[Cell]
) -> dict[str, Any]:
    cfg = db.get(m.AgentConfiguration, (agent_id, version))
    pattern = cfg.pattern if cfg is not None else "unknown"
    # La atribución viene de los runs (scripted, modelo de fixture o modelo real), no del patrón.
    attributions = sorted(
        {
            str(c.run.result.get("attribution"))
            for c in cells
            if c.run is not None and isinstance(c.run.result, dict)
        }
    )
    fallback = "harness_baseline" if pattern == "scripted" else "unknown"
    return {
        "id": str(agent_id),
        "version": version,
        "content_hash": cfg.content_hash if cfg is not None else None,
        "pattern": pattern,
        "pattern_version": cfg.pattern_version if cfg is not None else None,
        "attribution": ",".join(attributions) or fallback,
    }


def _normalized_output(result: Mapping[str, Any]) -> Any:
    """Salida con cada id de evidencia propio sustituido por su posición."""
    return normalize_evidence(result.get("output"), result.get("evidence_refs") or [])


def _outputs_by_cell(cells: Sequence[Cell]) -> int:
    digests = {
        canonical_digest(_normalized_output(c.run.result))
        for c in cells
        if c.run is not None and isinstance(c.run.result, dict)
    }
    return len(digests)


def judge_consumption(db: Session, cells: Sequence[Cell]) -> dict[str, Any]:
    """Consumo del judge en TODAS las evaluaciones de las celdas (también reevaluaciones): es
    gasto real aunque el reporte use la última evaluación. Nunca se suma al del agente."""
    run_ids = [cell.run.id for cell in cells if cell.run is not None]
    documents: list[dict[str, Any]] = []
    if run_ids:
        for evaluation in db.scalars(select(m.Evaluation).where(m.Evaluation.run_id.in_(run_ids))):
            judge = (evaluation.report or {}).get("judge") if evaluation.report else None
            if isinstance(judge, dict) and "usage" in judge:
                documents.append(judge)
    calls = len(documents)
    if calls == 0:
        return {
            "calls": 0,
            "tokens": {"status": "not_applicable", "reason": "sin llamadas al judge"},
            "estimated_cost": {
                "status": "not_applicable",
                "currency": "USD",
                "reason": "sin llamadas al judge",
            },
            "latency_ms": distribution([], 0),
        }
    usages = [
        {
            "model_calls": 1,
            "tokens": {
                "known_subtotal": doc["usage"].get("total_tokens") or 0,
                "unknown_usage_calls": 0 if doc["usage"].get("total_tokens") is not None else 1,
            },
            "cost": {
                "known_subtotal": doc["cost"].get("amount") or "0",
                "unknown_cost_calls": 0 if doc["cost"].get("status") == "estimated" else 1,
                "currency": doc["cost"].get("currency"),
                "synthetic_price": doc["cost"].get("synthetic_price"),
            },
        }
        for doc in documents
    ]
    return {
        "calls": calls,
        "statuses": dict(Counter(str(doc.get("status")) for doc in documents)),
        **_tokens_and_cost(usages, calls),
        "latency_ms": distribution(
            [float(doc["latency_ms"]) for doc in documents if "latency_ms" in doc], calls
        ),
    }


def _part_status(applicable: bool, unknown: bool, known: str) -> str:
    if not applicable:
        return "not_applicable"
    return "unknown" if unknown else known


def combine_consumption(agent: Mapping[str, Any], judge: Mapping[str, Any]) -> dict[str, Any]:
    """Total = agente + judge. Una parte aplicable desconocida deja el total `unknown` con el
    subtotal conocido visible; las partes N/A no suman."""
    tokens_parts = [agent["tokens"], judge["tokens"]]
    applicable = [p for p in tokens_parts if p.get("status") != "not_applicable"]
    known_tokens = sum(int(p.get("known_subtotal") or 0) for p in applicable)
    tokens_unknown = any(p.get("status") == "unknown" for p in applicable)
    cost_parts = [agent["estimated_cost"], judge["estimated_cost"]]
    costed = [p for p in cost_parts if p.get("status") != "not_applicable"]
    known_cost = sum((Decimal(str(p.get("known_subtotal") or "0")) for p in costed), Decimal(0))
    cost_unknown = any(p.get("status") == "unknown" for p in costed)
    tokens_status = _part_status(bool(applicable), tokens_unknown, "observed")
    cost_status = _part_status(bool(costed), cost_unknown, "estimated")
    return {
        "tokens": {
            "status": tokens_status,
            "total": known_tokens if tokens_status == "observed" else None,
            "known_subtotal": known_tokens,
            "parts": {
                "agent": agent["tokens"].get("status"),
                "judge": judge["tokens"].get("status"),
            },
        },
        "estimated_cost": {
            "status": cost_status,
            "amount": format(known_cost, "f") if cost_status == "estimated" else None,
            "known_subtotal": format(known_cost, "f"),
            "synthetic_price": any(bool(p.get("synthetic_price")) for p in costed),
            "parts": {
                "agent": agent["estimated_cost"].get("status"),
                "judge": judge["estimated_cost"].get("status"),
            },
        },
    }


def _sealed_experiment(db: Session, experiment_id: uuid.UUID) -> m.Experiment:
    exp = db.get(m.Experiment, experiment_id)
    if exp is None:
        raise NotFoundError("experimento inexistente", experiment_id=str(experiment_id))
    if exp.manifest_hash is None:
        raise ExperimentNotSealedError("el experimento no está sellado", status=exp.status)
    return exp


def trace_completeness(db: Session, run_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str | None]:
    if not run_ids:
        return {}
    rows = db.execute(
        select(m.Trace.run_id, m.Trace.completeness).where(m.Trace.run_id.in_(run_ids))
    )
    return {run_id: completeness for run_id, completeness in rows}


def experiment_cells(db: Session, experiment_id: uuid.UUID, mode: str = "live") -> dict[str, Any]:
    """Una fila por celda programada (también las ausentes) para el dashboard (M10, 11.1).

    `task_success` es el status del score o `None` si no hay evaluación completada; la UI lo
    muestra como unknown sin confundirlo con `not_applicable` ni con `error`.
    """
    exp = _sealed_experiment(db, experiment_id)
    cells = collect_cells(db, exp, mode)
    traces = trace_completeness(db, [c.run.id for c in cells if c.run is not None])
    rows = []
    for cell in cells:
        run = cell.run
        evaluation = cell.evaluation
        rows.append(
            {
                "scenario": {"id": str(cell.scenario_id), "version": cell.scenario_version},
                "slug": cell.slug,
                "category": cell.category,
                "agent": {"id": str(cell.agent_id), "version": cell.agent_version},
                "repetition": cell.repetition,
                "seed": cell.seed,
                "run_id": str(run.id) if run is not None else None,
                "run_status": run.status if run is not None else "missing",
                "error_class": run.error_class if run is not None else None,
                "trace_completeness": traces.get(run.id) if run is not None else None,
                "evaluation_id": str(evaluation.id) if evaluation is not None else None,
                "evaluation_status": evaluation.status if evaluation is not None else None,
                "task_success": _task_status(cell),
                "raw_outcome_pass": _raw_status(cell),
                "latency_ms": cell.latency_ms,
            }
        )
    return {
        "experiment_id": str(exp.id),
        "manifest_hash": exp.manifest_hash,
        "mode": mode,
        "planned_cells": len(rows),
        "cells": rows,
    }


def experiment_report(db: Session, experiment_id: uuid.UUID, mode: str = "live") -> dict[str, Any]:
    exp = _sealed_experiment(db, experiment_id)
    benchmark = db.get(m.Benchmark, (exp.benchmark_id, exp.benchmark_version))
    dataset = (
        db.get(m.Dataset, (benchmark.dataset_id, benchmark.dataset_version))
        if benchmark is not None
        else None
    )
    cells = collect_cells(db, exp, mode)
    categories = sorted({c.category for c in cells})
    per_category = Counter(
        c.category for c in {(c.scenario_id, c.category): c for c in cells}.values()
    )
    min_per_category = min(per_category.values()) if per_category else 0
    coverage_class = dataset.coverage_class if dataset is not None else None
    agents_report = []
    for (agent_id, version), agent_cells in sorted(
        _group(cells, lambda c: (c.agent_id, c.agent_version)).items(), key=lambda kv: str(kv[0])
    ):
        by_category = _group(agent_cells, lambda c: c.category)
        by_scenario = _group(agent_cells, lambda c: (c.slug, c.category))
        usage = _usage(agent_cells)
        judge = judge_consumption(db, agent_cells)
        agents_report.append(
            {
                "agent": _agent_label(db, agent_id, version, agent_cells),
                "summary": _summaries(agent_cells),
                "by_category": {cat: _summaries(by_category[cat]) for cat in categories},
                "metrics": {
                    metric: _metric_summary(agent_cells, metric)
                    for metric in (*RATIO_METRICS, *RETRIEVAL_METRICS)
                },
                "usage": usage,
                # El judge se informa aparte (scope judge) y sólo se suma en `total_consumption`.
                "judge_usage": judge,
                "total_consumption": combine_consumption(usage, judge),
                "latency_ms": distribution(
                    [float(c.latency_ms) for c in agent_cells if c.latency_ms is not None],
                    len(agent_cells),
                ),
                "by_scenario": [
                    {
                        "slug": slug,
                        "category": category,
                        "task_success": dict(Counter(_task_status(c) or "unknown" for c in group)),
                        "raw_outcome_pass": dict(
                            Counter(_raw_status(c) or "unknown" for c in group)
                        ),
                        "distinct_outputs": _outputs_by_cell(group),
                    }
                    for (slug, category), group in sorted(
                        by_scenario.items(), key=lambda kv: (kv[0][1], str(kv[0][0]))
                    )
                ],
            }
        )
    descriptive_only = min_per_category < DESCRIPTIVE_MIN_SCENARIOS_PER_CATEGORY
    return {
        "report_version": REPORT_VERSION,
        "experiment": {
            "id": str(exp.id),
            "status": exp.status,
            "hypothesis": exp.hypothesis,
            "manifest_hash": exp.manifest_hash,
            "repetitions": exp.repetitions,
            "seeds": list(exp.seeds),
        },
        "benchmark": {
            "id": str(exp.benchmark_id),
            "version": exp.benchmark_version,
            "content_hash": benchmark.content_hash if benchmark is not None else None,
        },
        "dataset": {
            "name": dataset.name if dataset is not None else None,
            "version": dataset.version if dataset is not None else None,
            "content_hash": dataset.content_hash if dataset is not None else None,
            "coverage_class": coverage_class,
        },
        "labels": {
            "cohort": "pilot" if coverage_class == "pilot" else coverage_class,
            "mode": mode,
            "analysis": "descriptive_only" if descriptive_only else "descriptive",
            "statistical_claims": "none",
            "reason": (
                f"{min_per_category} escenarios por categoría (< "
                f"{DESCRIPTIVE_MIN_SCENARIOS_PER_CATEGORY}): sin intervalos ni superioridad"
                if descriptive_only
                else "sin plan de comparación evaluado en este reporte"
            ),
        },
        "metric_profile": dict(PROFILE_DESCRIPTOR),
        "planned_cells": len(cells),
        "scenarios_per_category": dict(sorted(per_category.items())),
        "agents": agents_report,
    }
