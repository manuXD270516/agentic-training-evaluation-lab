"""Comparación pareada descriptiva entre dos experimentos sellados (M7, 8.2).

Antes de comparar se diferencian los manifests sellados: sólo pueden cambiar la hipótesis, el
plan de comparación y, del agente, las variables declaradas (para `pattern`: identidad y hash
de la configuración, patrón, versión, prompt, parámetros y modelos por rol). Cualquier otra
diferencia (benchmark, dataset, tools, budgets, seeds, repeticiones) deja la comparación
`incompatible`: sólo vista descriptiva etiquetada, sin pares ni deltas.

Las celdas se emparejan por escenario/versión, repetición y seed. Cada fallo de cualquiera de
los dos lados se conserva con su evidencia (run, evaluación y dimensiones que fallan); una
celda ausente o sin evaluación deja el par `incomplete`, nunca se imputa ni se descarta. No hay
intervalos ni afirmaciones de superioridad: el protocolo estadístico llega en M11.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sqlalchemy.orm import Session

from evallab.db import models as m
from evallab.services.errors import ExperimentNotSealedError, NotFoundError
from evallab.services.reports import Cell, collect_cells, experiment_report

COMPARISON_VERSION = "1.0.0"
AGENT_VARIABLES: Mapping[str, frozenset[str]] = {
    "pattern": frozenset(
        {
            "id",
            "version",
            "content_hash",
            "pattern",
            "pattern_version",
            "prompt_hash",
            "pattern_parameters",
            "roles",
        }
    ),
    "model": frozenset({"id", "version", "content_hash", "roles"}),
    "prompt": frozenset({"id", "version", "content_hash", "prompt_hash", "pattern_parameters"}),
}
ALWAYS_ALLOWED = frozenset({"hypothesis", "comparison_plan"})


def diff_paths(left: Any, right: Any, prefix: str = "") -> list[str]:
    """JSON pointers donde dos documentos difieren (claves y elementos de lista)."""
    if isinstance(left, dict) and isinstance(right, dict):
        paths: list[str] = []
        for key in sorted(set(left) | set(right)):
            child = f"{prefix}/{key}"
            if key not in left or key not in right:
                paths.append(child)
            else:
                paths.extend(diff_paths(left[key], right[key], child))
        return paths
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return [prefix or "/"]
        paths = []
        for index, (a, b) in enumerate(zip(left, right, strict=True)):
            paths.extend(diff_paths(a, b, f"{prefix}/{index}"))
        return paths
    return [] if left == right else [prefix or "/"]


def _allowed(path: str, variable: str) -> bool:
    parts = path.strip("/").split("/")
    if parts[0] in ALWAYS_ALLOWED:
        return True
    if parts[0] == "agents" and len(parts) >= 3:
        return parts[2] in AGENT_VARIABLES.get(variable, frozenset())
    return False


def manifest_check(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any], variable: str
) -> dict[str, Any]:
    paths = diff_paths(dict(baseline), dict(candidate))
    unexpected = [p for p in paths if not _allowed(p, variable)]
    declared = sorted({p.strip("/").split("/")[2] for p in paths if p.startswith("/agents/")})
    return {
        "independent_variable": variable,
        "compatible": not unexpected,
        "differences": paths,
        "unexpected_differences": unexpected,
        "changed_agent_fields": declared,
    }


def _status(cell: Cell | None) -> str:
    if cell is None or cell.run is None:
        return "missing"
    if cell.evaluation is None or cell.evaluation.status != "completed":
        return "unknown"
    score = cell.scores.get("task_success")
    return score.status if score is not None else "unknown"


def _evidence(cell: Cell | None) -> dict[str, Any] | None:
    if cell is None or cell.run is None:
        return None
    report = cell.evaluation.report if cell.evaluation is not None else None
    dimensions = (report or {}).get("dimensions") or {}
    return {
        "run_id": str(cell.run.id),
        "run_status": cell.run.status,
        "evaluation_id": str(cell.evaluation.id) if cell.evaluation is not None else None,
        "failed_dimensions": sorted(d for d, s in dimensions.items() if s == "fail"),
        "task_success_reasons": list((report or {}).get("task_success_reasons") or []),
    }


def _pair_outcome(base: str, cand: str) -> str:
    if "missing" in (base, cand) or base not in ("pass", "fail") or cand not in ("pass", "fail"):
        return "incomplete"
    return {
        ("pass", "pass"): "both_pass",
        ("fail", "fail"): "both_fail",
        ("pass", "fail"): "new_failure",
        ("fail", "pass"): "fixed",
    }[(base, cand)]


def _key(cell: Cell) -> tuple[uuid.UUID, str, int, int]:
    return (cell.scenario_id, cell.scenario_version, cell.repetition, cell.seed)


def _sealed(db: Session, experiment_id: uuid.UUID) -> m.Experiment:
    exp = db.get(m.Experiment, experiment_id)
    if exp is None:
        raise NotFoundError("experimento inexistente", experiment_id=str(experiment_id))
    if exp.manifest is None:
        raise ExperimentNotSealedError("el experimento no está sellado", status=exp.status)
    return exp


def _side(report: Mapping[str, Any]) -> dict[str, Any]:
    (agent,) = report["agents"]
    return {
        "agent": agent["agent"],
        "summary": agent["summary"],
        "usage": agent["usage"],
        "latency_ms": agent["latency_ms"],
        "manifest_hash": report["experiment"]["manifest_hash"],
    }


def _counts(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counter = Counter(row["outcome"] for row in rows)
    return {
        k: counter.get(k, 0)
        for k in ("both_pass", "both_fail", "new_failure", "fixed", "incomplete")
    }


def compare(
    db: Session,
    baseline_id: uuid.UUID,
    candidate_id: uuid.UUID,
    *,
    variable: str = "pattern",
) -> dict[str, Any]:
    baseline = _sealed(db, baseline_id)
    candidate = _sealed(db, candidate_id)
    check = manifest_check(baseline.manifest or {}, candidate.manifest or {}, variable)
    base_report = experiment_report(db, baseline.id)
    cand_report = experiment_report(db, candidate.id)
    result: dict[str, Any] = {
        "comparison_version": COMPARISON_VERSION,
        "baseline": {"experiment_id": str(baseline.id), **_side(base_report)},
        "candidate": {"experiment_id": str(candidate.id), **_side(cand_report)},
        "manifest_check": check,
        "labels": {
            "analysis": "descriptive_only",
            "statistical_claims": "none",
            "cohort": base_report["labels"]["cohort"],
            "reason": "comparación descriptiva: sin intervalos ni superioridad (protocolo M11)",
        },
    }
    if not check["compatible"]:
        result["status"] = "incompatible"
        result["pairs"] = []
        return result

    base_cells = {_key(c): c for c in collect_cells(db, baseline)}
    cand_cells = {_key(c): c for c in collect_cells(db, candidate)}
    rows: list[dict[str, Any]] = []
    for key in sorted(set(base_cells) | set(cand_cells), key=lambda k: (str(k[0]), k[1], k[2])):
        base, cand = base_cells.get(key), cand_cells.get(key)
        reference = base or cand
        assert reference is not None
        base_status, cand_status = _status(base), _status(cand)
        rows.append(
            {
                "scenario_id": str(key[0]),
                "scenario_version": key[1],
                "slug": reference.slug,
                "category": reference.category,
                "repetition": key[2],
                "seed": key[3],
                "baseline": base_status,
                "candidate": cand_status,
                "outcome": _pair_outcome(base_status, cand_status),
                "baseline_evidence": _evidence(base) if base_status != "pass" else None,
                "candidate_evidence": _evidence(cand) if cand_status != "pass" else None,
            }
        )
    by_scenario: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_scenario.setdefault(str(row["slug"]), []).append(row)
    counts = _counts(rows)
    result["status"] = "incomplete" if counts["incomplete"] else "complete"
    result["pairs"] = rows
    result["pair_counts"] = counts
    result["by_scenario"] = [
        {
            "slug": slug,
            "category": group[0]["category"],
            "pairs": len(group),
            **_counts(group),
        }
        for slug, group in sorted(by_scenario.items(), key=lambda kv: (kv[1][0]["category"], kv[0]))
    ]
    result["failures"] = {
        "baseline": _failures(rows, "baseline"),
        "candidate": _failures(rows, "candidate"),
    }
    return result


def _failures(rows: Sequence[Mapping[str, Any]], side: str) -> list[dict[str, Any]]:
    """Todo fallo o celda no evaluable de un lado, con su evidencia: nada se descarta."""
    return [
        {
            "slug": row["slug"],
            "repetition": row["repetition"],
            "seed": row["seed"],
            "status": row[side],
            "evidence": row[f"{side}_evidence"],
        }
        for row in rows
        if row[side] != "pass"
    ]
