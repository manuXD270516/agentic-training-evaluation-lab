"""Comparación controlada (M11, 12.1-12.3) según metrics.md, "Experiment comparisons".

Orden de decisión, todo predeclarado en `PROTOCOL` (su hash va en cada export):

1. Comparability gate. Los manifests sellados sólo pueden diferir en la variable declarada
   (`descriptive.manifest_check`); además ambos lados usan el mismo modo de ejecución (replay
   nunca compite contra live), un solo agente por experimento y, en cada par, la misma suite
   de evaluadores y el mismo perfil de métricas. Si algo falla: `incompatible`, sin pares ni
   deltas; sólo la vista descriptiva de cada lado.
2. Pares por escenario/versión, repetición y seed. Un par sin run, sin evaluación completada,
   con `task_success` fuera de pass/fail o con traza no `complete` es `incomplete`: se lista,
   nunca se imputa ni se descarta, y bloquea la aprobación automática.
3. Gate de política: una violación crítica nueva (dimensión `policy` en `fail` en el candidato
   cuando la baseline no la tiene en ese par) falla la comparación aunque el éxito agregado
   mejore y aunque haya missingness, con la evidencia del escenario.
4. Gate de éxito: delta candidato - baseline de `task_success`, macro por escenario y luego por
   categoría. Bootstrap pareado por clúster escenario (las repeticiones de ambos lados viajan
   juntas), estratificado por categoría, 10 000 remuestreos con seed 2026 y percentiles
   2.5/97.5 por nearest-rank. Regresión si el límite superior < -0.02; no inferioridad si el
   inferior >= -0.02; si no, inconcluso. Con menos de 5 escenarios por categoría no se calcula
   intervalo ni se decide: sólo descripción. Nunca se declara superioridad.
5. Gate de latencia p95 (+20 % máximo frente a una baseline positiva), sólo si el éxito es no
   inferior y la cobertura es completa; p95 de baseline 0 deja el gate N/A. El coste se
   compara sólo descriptivamente.

Los intervalos describen incertidumbre dentro de este dataset, no validez externa.
"""

from __future__ import annotations

import math
import random
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from evallab.canonical import canonical_digest
from evallab.comparison.descriptive import manifest_check
from evallab.db import models as m
from evallab.services.errors import ExperimentNotSealedError, NotFoundError
from evallab.services.reports import Cell, collect_cells, experiment_report, trace_completeness

COMPARISON_VERSION = "2.0.0"
PROTOCOL: dict[str, Any] = {
    "id": "paired-comparison",
    "version": "1.0.0",
    "source": "metrics.md#experiment-comparisons",
    "pairing": ["scenario_id", "scenario_version", "repetition", "seed"],
    "primary_metric": "task_success",
    "statistic": "macro_category_mean_of_scenario_deltas",
    "bootstrap": {
        "kind": "paired_cluster_by_scenario",
        "stratified_by": "category",
        "resamples": 10_000,
        "seed": 2026,
        "percentiles": [2.5, 97.5],
        "percentile_method": "nearest_rank",
    },
    "min_scenarios_per_category": 5,
    "success_gate": {"regression_if_upper_below": -0.02, "non_inferior_if_lower_at_least": -0.02},
    "latency_gate": {"metric": "latency_ms_p95", "max_relative_increase": 0.20},
    "policy_gate": {"critical_violation": "policy_dimension_fail", "max_new": 0},
    "cost": "descriptive_only",
    "superiority_claims": "never",
}
PROTOCOL_HASH = canonical_digest(PROTOCOL)
EVALUABLE = ("pass", "fail")


@dataclass(frozen=True)
class SideCell:
    """Lo que la comparación necesita de una celda; sin ORM para poder probarlo a mano."""

    status: str  # task_success, `missing` sin run o `unknown` sin evaluación completada
    policy: str | None = None
    suite_hash: str | None = None
    profile_hash: str | None = None
    trace: str | None = "complete"
    latency_ms: int | None = None
    run_id: str | None = None
    evaluation_id: str | None = None
    failed_dimensions: tuple[str, ...] = ()
    policy_evidence: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class Pair:
    scenario_id: str
    scenario_version: str
    slug: str
    category: str
    repetition: int
    seed: int
    baseline: SideCell
    candidate: SideCell

    @property
    def complete(self) -> bool:
        return all(
            side.status in EVALUABLE and side.trace == "complete"
            for side in (self.baseline, self.candidate)
        )

    @property
    def new_critical_violation(self) -> bool:
        return self.candidate.policy == "fail" and self.baseline.policy != "fail"

    def incomplete_reasons(self) -> list[str]:
        reasons = []
        for name, side in (("baseline", self.baseline), ("candidate", self.candidate)):
            if side.status not in EVALUABLE:
                reasons.append(f"{name}:{side.status}")
            elif side.trace != "complete":
                reasons.append(f"{name}:trace_{side.trace}")
        return reasons


# --- Estadística pura ---------------------------------------------------------------------


def nearest_rank(sorted_values: Sequence[float], percentile: float) -> float:
    rank = max(1, math.ceil(percentile / 100 * len(sorted_values)))
    return sorted_values[rank - 1]


@dataclass(frozen=True)
class ScenarioDelta:
    slug: str
    category: str
    pairs: int
    baseline_rate: float
    candidate_rate: float

    @property
    def delta(self) -> float:
        return self.candidate_rate - self.baseline_rate


def scenario_deltas(pairs: Iterable[Pair]) -> list[ScenarioDelta]:
    """Éxito por escenario de cada lado sobre sus pares completos (clúster = escenario)."""
    groups: dict[tuple[str, str, str], list[Pair]] = defaultdict(list)
    for pair in pairs:
        if pair.complete:
            groups[(pair.category, pair.slug, pair.scenario_id)].append(pair)
    deltas = []
    for (category, slug, _), group in sorted(groups.items()):
        n = len(group)
        deltas.append(
            ScenarioDelta(
                slug=slug,
                category=category,
                pairs=n,
                baseline_rate=sum(p.baseline.status == "pass" for p in group) / n,
                candidate_rate=sum(p.candidate.status == "pass" for p in group) / n,
            )
        )
    return deltas


def macro_delta(by_category: Mapping[str, Sequence[float]]) -> float:
    """Media por categoría de la media de deltas de sus escenarios (macro-macro)."""
    means = [sum(values) / len(values) for values in by_category.values() if values]
    return sum(means) / len(means)


def bootstrap_interval(
    deltas: Sequence[ScenarioDelta],
    *,
    resamples: int = PROTOCOL["bootstrap"]["resamples"],
    seed: int = PROTOCOL["bootstrap"]["seed"],
) -> tuple[float, float]:
    """Remuestrea escenarios con reemplazo dentro de cada categoría (orden fijo) y devuelve
    los percentiles 2.5 y 97.5 por nearest-rank de la delta macro."""
    strata: dict[str, list[float]] = defaultdict(list)
    for item in deltas:
        strata[item.category].append(item.delta)
    categories = sorted(strata)
    rng = random.Random(seed)
    stats: list[float] = []
    for _ in range(resamples):
        sample: dict[str, list[float]] = {}
        for category in categories:
            values = strata[category]
            sample[category] = [values[rng.randrange(len(values))] for _ in values]
        stats.append(macro_delta(sample))
    stats.sort()
    low, high = PROTOCOL["bootstrap"]["percentiles"]
    return nearest_rank(stats, low), nearest_rank(stats, high)


def success_gate(interval: tuple[float, float]) -> str:
    rules = PROTOCOL["success_gate"]
    low, high = interval
    if high < rules["regression_if_upper_below"]:
        return "regression"
    if low >= rules["non_inferior_if_lower_at_least"]:
        return "non_inferior"
    return "inconclusive"


def latency_gate(
    baseline_p95: float | None, candidate_p95: float | None, *, success: str, complete: bool
) -> dict[str, Any]:
    limit = PROTOCOL["latency_gate"]["max_relative_increase"]
    base: dict[str, Any] = {"baseline_p95": baseline_p95, "candidate_p95": candidate_p95}
    if success != "non_inferior" or not complete:
        return {
            **base,
            "gate": "not_evaluated",
            "reason": "requiere éxito no inferior y pares completos",
        }
    if baseline_p95 is None or candidate_p95 is None:
        return {**base, "gate": "not_evaluated", "reason": "latencia no observada"}
    if baseline_p95 <= 0:
        return {
            **base,
            "gate": "not_applicable",
            "reason": "p95 de baseline 0: exige límite absoluto",
        }
    ratio = candidate_p95 / baseline_p95
    return {
        **base,
        "relative_change": ratio - 1,
        "gate": "pass" if ratio <= 1 + limit else "fail",
    }


def _p95(values: Sequence[int]) -> float | None:
    return float(nearest_rank(sorted(values), 95)) if values else None


def assess(pairs: Sequence[Pair], planned_per_category: Mapping[str, int]) -> dict[str, Any]:
    """Gates y decisión sobre pares ya compatibles (pura: sin base de datos)."""
    incomplete = [p for p in pairs if not p.complete]
    violations = [p for p in pairs if p.new_critical_violation]
    unknown_policy = [
        p
        for p in pairs
        if p.candidate.policy not in ("pass", "fail") and p.candidate.status != "missing"
    ]
    policy: dict[str, Any] = {
        "gate": "fail" if violations else ("unknown" if unknown_policy or incomplete else "pass"),
        "new_critical_violations": [_violation(p) for p in violations],
    }
    deltas = scenario_deltas(pairs)
    by_category: dict[str, list[float]] = defaultdict(list)
    for item in deltas:
        by_category[item.category].append(item.delta)
    small = (
        not planned_per_category
        or min(planned_per_category.values()) < PROTOCOL["min_scenarios_per_category"]
    )
    success: dict[str, Any] = {
        "statistic": PROTOCOL["statistic"],
        "scenarios": len(deltas),
        "point_estimate": macro_delta(by_category) if by_category else None,
        "micro": _micro(pairs),
        "diagnostic_only": bool(incomplete),
    }
    if small:
        success |= {
            "interval": None,
            "gate": "not_evaluated",
            "reason": (
                f"{min(planned_per_category.values(), default=0)} escenarios por categoría (< "
                f"{PROTOCOL['min_scenarios_per_category']}): sólo descripción"
            ),
        }
    elif not deltas:
        success |= {"interval": None, "gate": "not_evaluated", "reason": "sin pares completos"}
    else:
        interval = bootstrap_interval(deltas)
        success |= {"interval": list(interval), "gate": success_gate(interval)}
    complete = not incomplete
    latency = latency_gate(
        _p95(
            [
                p.baseline.latency_ms
                for p in pairs
                if p.complete and p.baseline.latency_ms is not None
            ]
        ),
        _p95(
            [
                p.candidate.latency_ms
                for p in pairs
                if p.complete and p.candidate.latency_ms is not None
            ]
        ),
        success=success["gate"] if complete else "not_evaluated",
        complete=complete,
    )
    decision, reasons = _decide(policy["gate"], bool(incomplete), success["gate"], latency["gate"])
    return {
        "pair_counts": _counts(pairs),
        "incomplete_pairs": [
            {**_pair_ref(p), "reasons": p.incomplete_reasons()} for p in incomplete
        ],
        "by_scenario": _by_scenario(pairs, deltas),
        "gates": {"policy": policy, "success": success, "latency": latency},
        "decision": {"status": decision, "reasons": reasons},
        "statistical_claims": (
            "non_inferiority_test"
            if success["gate"] in ("non_inferior", "regression", "inconclusive")
            else "none"
        ),
    }


def _decide(policy: str, incomplete: bool, success: str, latency: str) -> tuple[str, list[str]]:
    if policy == "fail":
        return "fail", ["new_critical_violation"]
    if incomplete:
        return "incomplete", ["missing_or_unknown_pairs"]
    if success == "not_evaluated":
        return "descriptive_only", ["insufficient_sample"]
    if success == "regression":
        return "fail", ["success_regression"]
    if success == "inconclusive":
        return "inconclusive", ["success_interval_crosses_margin"]
    if latency == "fail":
        return "fail", ["latency_p95_regression"]
    return "pass", ["non_inferior"]


def _pair_ref(pair: Pair) -> dict[str, Any]:
    return {
        "slug": pair.slug,
        "category": pair.category,
        "scenario_id": pair.scenario_id,
        "scenario_version": pair.scenario_version,
        "repetition": pair.repetition,
        "seed": pair.seed,
    }


def _violation(pair: Pair) -> dict[str, Any]:
    return {
        **_pair_ref(pair),
        "baseline_policy": pair.baseline.policy,
        "candidate_run_id": pair.candidate.run_id,
        "candidate_evaluation_id": pair.candidate.evaluation_id,
        "evidence_refs": [dict(ref) for ref in pair.candidate.policy_evidence],
    }


def _outcome(pair: Pair) -> str:
    if not pair.complete:
        return "incomplete"
    return {
        ("pass", "pass"): "both_pass",
        ("fail", "fail"): "both_fail",
        ("pass", "fail"): "new_failure",
        ("fail", "pass"): "fixed",
    }[(pair.baseline.status, pair.candidate.status)]


def _counts(pairs: Iterable[Pair]) -> dict[str, int]:
    counter = Counter(_outcome(p) for p in pairs)
    keys = ("both_pass", "both_fail", "new_failure", "fixed", "incomplete")
    return {k: counter.get(k, 0) for k in keys}


def _micro(pairs: Sequence[Pair]) -> dict[str, Any]:
    complete = [p for p in pairs if p.complete]
    if not complete:
        return {"pairs": 0, "baseline_rate": None, "candidate_rate": None}
    n = len(complete)
    return {
        "pairs": n,
        "baseline_rate": sum(p.baseline.status == "pass" for p in complete) / n,
        "candidate_rate": sum(p.candidate.status == "pass" for p in complete) / n,
    }


def _by_scenario(pairs: Sequence[Pair], deltas: Sequence[ScenarioDelta]) -> list[dict[str, Any]]:
    delta_by_slug = {d.slug: d for d in deltas}
    groups: dict[tuple[str, str], list[Pair]] = defaultdict(list)
    for pair in pairs:
        groups[(pair.category, pair.slug)].append(pair)
    rows = []
    for (category, slug), group in sorted(groups.items()):
        delta = delta_by_slug.get(slug)
        rows.append(
            {
                "slug": slug,
                "category": category,
                "pairs": len(group),
                **_counts(group),
                "baseline_rate": delta.baseline_rate if delta else None,
                "candidate_rate": delta.candidate_rate if delta else None,
                "delta": delta.delta if delta else None,
                "new_critical_violations": sum(p.new_critical_violation for p in group),
                # Un nuevo fallo por escenario se informa aunque el agregado mejore.
                "evidence": [
                    {
                        "repetition": p.repetition,
                        "seed": p.seed,
                        "outcome": _outcome(p),
                        "baseline": _side_evidence(p.baseline),
                        "candidate": _side_evidence(p.candidate),
                    }
                    for p in sorted(group, key=lambda p: p.repetition)
                    if _outcome(p) != "both_pass" or p.new_critical_violation
                ],
            }
        )
    return rows


def _side_evidence(side: SideCell) -> dict[str, Any]:
    return {
        "status": side.status,
        "policy": side.policy,
        "trace": side.trace,
        "run_id": side.run_id,
        "evaluation_id": side.evaluation_id,
        "failed_dimensions": list(side.failed_dimensions),
        "policy_evidence": [dict(ref) for ref in side.policy_evidence],
    }


# --- Comparability gate y extracción desde la base ---------------------------------------


def comparability(
    baseline_manifest: Mapping[str, Any],
    candidate_manifest: Mapping[str, Any],
    *,
    variable: str,
    baseline_mode: str,
    candidate_mode: str,
    pairs: Sequence[Pair] = (),
) -> dict[str, Any]:
    check = manifest_check(baseline_manifest, candidate_manifest, variable)
    reasons: list[dict[str, Any]] = []
    for path in check["unexpected_differences"]:
        reasons.append({"code": _difference_code(path), "path": path})
    if baseline_mode != candidate_mode:
        reasons.append(
            {"code": "mixed_modes", "baseline": baseline_mode, "candidate": candidate_mode}
        )
    for name, manifest in (("baseline", baseline_manifest), ("candidate", candidate_manifest)):
        agents = manifest.get("agents") or []
        if len(agents) != 1:
            reasons.append({"code": "not_single_agent", "side": name, "agents": len(agents)})
    for code, attr in (
        ("evaluator_suite_mismatch", "suite_hash"),
        ("metric_profile_mismatch", "profile_hash"),
    ):
        mismatched = [
            _pair_ref(p)
            for p in pairs
            if getattr(p.baseline, attr) is not None
            and getattr(p.candidate, attr) is not None
            and getattr(p.baseline, attr) != getattr(p.candidate, attr)
        ]
        if mismatched:
            reasons.append({"code": code, "pairs": mismatched[:10], "count": len(mismatched)})
    return {
        "status": "incompatible" if reasons else "compatible",
        "reasons": reasons,
        "manifest_check": check,
        "modes": {"baseline": baseline_mode, "candidate": candidate_mode},
    }


def _difference_code(path: str) -> str:
    parts = path.strip("/").split("/")
    if parts[:2] == ["benchmark", "dataset"]:
        return "dataset_mismatch"
    if parts[0] == "benchmark":
        # El hash del benchmark cubre selección, oráculos, suite y perfil declarados.
        return "benchmark_mismatch"
    if parts[0] in ("seeds", "repetitions"):
        return "seed_schedule_mismatch"
    if parts[0] == "budgets":
        return "budget_mismatch"
    if parts[0] == "agents":
        return "undeclared_agent_difference"
    return "undeclared_difference"


def _sealed(db: Session, experiment_id: uuid.UUID) -> m.Experiment:
    exp = db.get(m.Experiment, experiment_id)
    if exp is None:
        raise NotFoundError("experimento inexistente", experiment_id=str(experiment_id))
    if exp.manifest is None or exp.manifest_hash is None:
        raise ExperimentNotSealedError("el experimento no está sellado", status=exp.status)
    return exp


def _side_cell(cell: Cell | None, traces: Mapping[uuid.UUID, str | None]) -> SideCell:
    if cell is None or cell.run is None:
        return SideCell(status="missing", trace=None)
    evaluation = cell.evaluation
    run_id = str(cell.run.id)
    trace = traces.get(cell.run.id)
    if evaluation is None or evaluation.status != "completed":
        return SideCell(
            status="unknown",
            trace=trace,
            run_id=run_id,
            evaluation_id=str(evaluation.id) if evaluation is not None else None,
            latency_ms=cell.latency_ms,
        )
    report = evaluation.report or {}
    dimensions = report.get("dimensions") or {}
    score = cell.scores.get("task_success")
    checks = report.get("checks") or []
    evidence = tuple(
        ref
        for check in checks
        if isinstance(check, dict)
        and check.get("dimension") == "policy"
        and check.get("status") == "fail"
        for ref in (check.get("evidence_refs") or [])
    )
    return SideCell(
        status=score.status if score is not None else "unknown",
        policy=dimensions.get("policy"),
        suite_hash=evaluation.evaluator_suite_hash,
        profile_hash=evaluation.metric_profile_hash,
        trace=trace,
        latency_ms=cell.latency_ms,
        run_id=run_id,
        evaluation_id=str(evaluation.id),
        failed_dimensions=tuple(sorted(d for d, s in dimensions.items() if s == "fail")),
        policy_evidence=evidence,
    )


def build_pairs(
    db: Session,
    baseline: m.Experiment,
    candidate: m.Experiment,
    baseline_mode: str,
    candidate_mode: str,
) -> tuple[list[Pair], dict[str, int]]:
    base_cells = collect_cells(db, baseline, baseline_mode)
    cand_cells = collect_cells(db, candidate, candidate_mode)
    run_ids = [c.run.id for c in (*base_cells, *cand_cells) if c.run is not None]
    traces = trace_completeness(db, run_ids)

    def key(c: Cell) -> tuple[str, str, int, int]:
        return (str(c.scenario_id), c.scenario_version, c.repetition, c.seed)

    base = {key(c): c for c in base_cells}
    cand = {key(c): c for c in cand_cells}
    pairs = []
    planned: dict[str, set[str]] = defaultdict(set)
    for k in sorted(set(base) | set(cand)):
        reference = base.get(k) or cand.get(k)
        assert reference is not None
        planned[reference.category].add(k[0])
        pairs.append(
            Pair(
                scenario_id=k[0],
                scenario_version=k[1],
                slug=str(reference.slug),
                category=reference.category,
                repetition=k[2],
                seed=k[3],
                baseline=_side_cell(base.get(k), traces),
                candidate=_side_cell(cand.get(k), traces),
            )
        )
    return pairs, {category: len(ids) for category, ids in sorted(planned.items())}


def _side_summary(exp: m.Experiment, report: Mapping[str, Any], mode: str) -> dict[str, Any]:
    return {
        "experiment_id": str(exp.id),
        "manifest_hash": exp.manifest_hash,
        "manifest": exp.manifest,
        "mode": mode,
        "report": report,
    }


def compare_controlled(
    db: Session,
    baseline_id: uuid.UUID,
    candidate_id: uuid.UUID,
    *,
    variable: str = "pattern",
    baseline_mode: str = "live",
    candidate_mode: str = "live",
) -> dict[str, Any]:
    """Export completo de la comparación: manifests, reportes, pares con evidencia y gates."""
    baseline = _sealed(db, baseline_id)
    candidate = _sealed(db, candidate_id)
    pairs, planned = build_pairs(db, baseline, candidate, baseline_mode, candidate_mode)
    gate = comparability(
        baseline.manifest or {},
        candidate.manifest or {},
        variable=variable,
        baseline_mode=baseline_mode,
        candidate_mode=candidate_mode,
        pairs=pairs,
    )
    document: dict[str, Any] = {
        "comparison_version": COMPARISON_VERSION,
        "protocol": {**PROTOCOL, "hash": PROTOCOL_HASH},
        "independent_variable": variable,
        "baseline": _side_summary(
            baseline, experiment_report(db, baseline.id, baseline_mode), baseline_mode
        ),
        "candidate": _side_summary(
            candidate, experiment_report(db, candidate.id, candidate_mode), candidate_mode
        ),
        "comparability": gate,
        "scenarios_per_category": planned,
    }
    if gate["status"] == "incompatible":
        document |= {
            "status": "incompatible",
            "decision": {"status": "blocked", "reasons": [r["code"] for r in gate["reasons"]]},
            "labels": {
                "analysis": "descriptive_only",
                "ranking": "blocked",
                "statistical_claims": "none",
                "reason": "Cohortes incompatibles: sólo la vista descriptiva de cada lado",
            },
            "pairs": [],
        }
    else:
        assessment = assess(pairs, planned)
        claims = assessment.pop("statistical_claims")
        status = "incomplete" if assessment["incomplete_pairs"] else "complete"
        document |= {
            "status": status,
            **assessment,
            "labels": {
                "analysis": "controlled",
                "ranking": "allowed",
                "statistical_claims": claims,
                "reason": (
                    "Intervalos sólo dentro de este dataset; sin validez externa ni superioridad"
                ),
            },
            "pairs": [
                {
                    **_pair_ref(p),
                    "outcome": _outcome(p),
                    "baseline": _side_evidence(p.baseline),
                    "candidate": _side_evidence(p.candidate),
                }
                for p in pairs
            ],
        }
    document["export_digest"] = canonical_digest(document)
    return document


def verify_export(document: Mapping[str, Any]) -> bool:
    """El digest del export cubre todo el documento salvo el propio digest."""
    body = {k: v for k, v in document.items() if k != "export_digest"}
    return canonical_digest(body) == document.get("export_digest")
