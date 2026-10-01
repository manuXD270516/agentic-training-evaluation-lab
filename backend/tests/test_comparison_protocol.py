"""Protocolo de comparación (M11, 12.1-12.3) con pares sintéticos calculables a mano."""

from collections.abc import Sequence
from typing import Any

import pytest

from evallab.canonical import canonical_digest
from evallab.comparison.protocol import (
    PROTOCOL,
    Pair,
    SideCell,
    assess,
    bootstrap_interval,
    comparability,
    latency_gate,
    scenario_deltas,
    success_gate,
    verify_export,
)

CATEGORIES = ("c1", "c2", "c3", "c4", "c5", "c6", "c7")
REPS = 5


def side(status: str, *, policy: str | None = None, latency: int = 100, **kw: Any) -> SideCell:
    if policy is None:
        policy = "pass" if status in ("pass", "fail") else None
    kw.setdefault("suite_hash", "s")
    return SideCell(status=status, policy=policy, latency_ms=latency, **kw)


def pair(category: str, slug: str, rep: int, base: SideCell, cand: SideCell) -> Pair:
    return Pair(
        scenario_id=f"id-{slug}",
        scenario_version="1.0.0",
        slug=slug,
        category=category,
        repetition=rep,
        seed=rep * 10,
        baseline=base,
        candidate=cand,
    )


def grid(
    candidate_passes: dict[str, int] | None = None,
    *,
    per_category: int = 5,
    baseline_passes: int = REPS,
) -> list[Pair]:
    """7 categorías x `per_category` escenarios x 5 repeticiones. `candidate_passes[slug]`
    fija cuántas repeticiones aprueba el candidato (por defecto, las mismas que la baseline)."""
    pairs = []
    for category in CATEGORIES:
        for s in range(per_category):
            slug = f"{category}-s{s}"
            wins = (candidate_passes or {}).get(slug, baseline_passes)
            for rep in range(1, REPS + 1):
                base = side("pass" if rep <= baseline_passes else "fail")
                cand = side("pass" if rep <= wins else "fail")
                pairs.append(pair(category, slug, rep, base, cand))
    return pairs


def planned(pairs: Sequence[Pair]) -> dict[str, int]:
    slugs: dict[str, set[str]] = {}
    for p in pairs:
        slugs.setdefault(p.category, set()).add(p.slug)
    return {c: len(s) for c, s in slugs.items()}


def run(pairs: list[Pair]) -> dict[str, Any]:
    return assess(pairs, planned(pairs))


def test_protocol_is_the_predeclared_one() -> None:
    assert PROTOCOL["bootstrap"]["resamples"] == 10_000
    assert PROTOCOL["bootstrap"]["seed"] == 2026
    assert PROTOCOL["bootstrap"]["percentiles"] == [2.5, 97.5]
    assert PROTOCOL["success_gate"] == {
        "regression_if_upper_below": -0.02,
        "non_inferior_if_lower_at_least": -0.02,
    }
    assert PROTOCOL["superiority_claims"] == "never"


def test_identical_variants_are_non_inferior_with_degenerate_interval() -> None:
    result = run(grid())
    success = result["gates"]["success"]
    assert success["point_estimate"] == 0.0 and success["interval"] == [0.0, 0.0]
    assert success["gate"] == "non_inferior"
    assert result["gates"]["latency"]["gate"] == "pass"
    assert result["decision"] == {"status": "pass", "reasons": ["non_inferior"]}
    assert result["statistical_claims"] == "non_inferiority_test"


def test_small_loss_within_margin_is_non_inferior() -> None:
    # Un escenario de c1 pierde 1 de 5 repeticiones: delta -0.2 en 1 de 35 escenarios.
    # Punto: (-0.2 / 5) / 7. Límite inferior: el percentil 2.5 cae en 3 de 5 extracciones de
    # ese escenario (P(k>=3)=5.8 %, P(k>=4)=0.7 %), es decir (-0.6 / 5) / 7 = -0.0171 >= -0.02.
    result = run(grid({"c1-s0": 4}))
    success = result["gates"]["success"]
    assert success["point_estimate"] == pytest.approx(-0.2 / 5 / 7)
    low, high = success["interval"]
    assert low == pytest.approx(-0.6 / 5 / 7) and high == 0.0
    assert success["gate"] == "non_inferior"
    assert result["decision"]["status"] == "pass"
    # El nuevo fallo individual se conserva aunque el gate pase.
    row = next(r for r in result["by_scenario"] if r["slug"] == "c1-s0")
    assert row["new_failure"] == 1 and row["evidence"][0]["outcome"] == "new_failure"


def test_broad_loss_is_a_confirmed_regression() -> None:
    # El candidato falla todas las repeticiones de 3 de 5 escenarios en cada categoría.
    losses = {f"{c}-s{s}": 0 for c in CATEGORIES for s in range(3)}
    result = run(grid(losses))
    success = result["gates"]["success"]
    assert success["point_estimate"] == pytest.approx(-0.6)
    assert success["interval"][1] < -0.02
    assert success["gate"] == "regression"
    assert result["decision"] == {"status": "fail", "reasons": ["success_regression"]}
    assert result["gates"]["latency"]["gate"] == "not_evaluated"


def test_mixed_changes_are_inconclusive() -> None:
    # En cada categoría un escenario gana todo y otro lo pierde todo: delta medio 0, varianza alta.
    changes = {f"{c}-s0": 0 for c in CATEGORIES} | {f"{c}-s1": REPS for c in CATEGORIES}
    result = run(grid(changes, baseline_passes=2))
    success = result["gates"]["success"]
    low, high = success["interval"]
    assert low < -0.02 < high
    assert success["gate"] == "inconclusive"
    assert result["decision"]["status"] == "inconclusive"


def test_bootstrap_is_reproducible_with_seed_2026() -> None:
    changes = {f"{c}-s0": 0 for c in CATEGORIES} | {f"{c}-s1": REPS for c in CATEGORIES}
    deltas = scenario_deltas(grid(changes, baseline_passes=2))
    first = bootstrap_interval(deltas)
    assert bootstrap_interval(deltas) == first
    assert bootstrap_interval(deltas, seed=2026) == first


def test_success_gate_boundaries() -> None:
    assert success_gate((-0.05, -0.021)) == "regression"
    assert success_gate((-0.02, 0.3)) == "non_inferior"
    assert success_gate((-0.021, 0.3)) == "inconclusive"


def test_pilot_sized_sample_is_descriptive_only() -> None:
    result = run(grid(per_category=2))
    success = result["gates"]["success"]
    assert success["interval"] is None and success["gate"] == "not_evaluated"
    assert "2 escenarios por categoría" in success["reason"]
    assert result["decision"]["status"] == "descriptive_only"
    assert result["statistical_claims"] == "none"


def test_new_critical_violation_fails_even_if_success_improves() -> None:
    pairs = grid(
        baseline_passes=0,
        candidate_passes={f"{c}-s{s}": REPS for c in CATEGORIES for s in range(5)},
    )
    target = next(p for p in pairs if p.slug == "c3-s2" and p.repetition == 4)
    refs = ({"event_id": "e-1", "pointer": "/tool"},)
    violating = side(
        "fail", policy="fail", run_id="run-x", evaluation_id="eval-x", policy_evidence=refs
    )
    pairs[pairs.index(target)] = pair("c3", "c3-s2", 4, target.baseline, violating)
    result = run(pairs)
    success = result["gates"]["success"]
    assert success["point_estimate"] > 0.9 and success["gate"] == "non_inferior"
    policy = result["gates"]["policy"]
    assert policy["gate"] == "fail"
    (violation,) = policy["new_critical_violations"]
    assert violation["slug"] == "c3-s2" and violation["repetition"] == 4
    assert violation["candidate_run_id"] == "run-x"
    assert violation["evidence_refs"] == [{"event_id": "e-1", "pointer": "/tool"}]
    assert result["decision"] == {"status": "fail", "reasons": ["new_critical_violation"]}
    row = next(r for r in result["by_scenario"] if r["slug"] == "c3-s2")
    assert row["new_critical_violations"] == 1


def test_existing_violation_is_not_new() -> None:
    pairs = grid()
    target = pairs[0]
    pairs[0] = pair(
        target.category, target.slug, 1, side("pass", policy="fail"), side("pass", policy="fail")
    )
    assert run(pairs)["gates"]["policy"]["gate"] == "pass"


def test_missing_or_unknown_cells_block_approval_without_imputation() -> None:
    pairs = grid()
    pairs[0] = pair(
        pairs[0].category, pairs[0].slug, 1, pairs[0].baseline, side("missing", trace=None)
    )
    pairs[1] = pair(
        pairs[1].category, pairs[1].slug, 2, pairs[1].baseline, side("pass", trace="incomplete")
    )
    pairs[2] = pair(pairs[2].category, pairs[2].slug, 3, side("unknown"), pairs[2].candidate)
    result = run(pairs)
    assert result["decision"] == {"status": "incomplete", "reasons": ["missing_or_unknown_pairs"]}
    assert [r["reasons"] for r in result["incomplete_pairs"]] == [
        ["candidate:missing"],
        ["candidate:trace_incomplete"],
        ["baseline:unknown"],
    ]
    assert result["pair_counts"]["incomplete"] == 3
    # Diagnóstico: el intervalo se calcula con los pares completos pero no aprueba nada.
    assert result["gates"]["success"]["diagnostic_only"] is True
    assert result["gates"]["latency"]["gate"] == "not_evaluated"


def test_latency_gate() -> None:
    assert latency_gate(100, 120, success="non_inferior", complete=True)["gate"] == "pass"
    assert latency_gate(100, 121, success="non_inferior", complete=True)["gate"] == "fail"
    assert latency_gate(0, 50, success="non_inferior", complete=True)["gate"] == "not_applicable"
    assert latency_gate(100, 500, success="inconclusive", complete=True)["gate"] == "not_evaluated"
    assert latency_gate(100, 500, success="non_inferior", complete=False)["gate"] == "not_evaluated"


def test_latency_regression_fails_a_non_inferior_candidate() -> None:
    pairs = [
        pair(p.category, p.slug, p.repetition, p.baseline, side(p.candidate.status, latency=200))
        for p in grid()
    ]
    result = run(pairs)
    assert result["gates"]["latency"]["gate"] == "fail"
    assert result["decision"] == {"status": "fail", "reasons": ["latency_p95_regression"]}


MANIFEST: dict[str, Any] = {
    "hypothesis": "h",
    "benchmark": {"id": "b", "content_hash": "b1", "dataset": {"id": "d", "content_hash": "d1"}},
    "agents": [{"id": "a", "pattern": "react", "tools": []}],
    "budgets": {"max_steps": 8},
    "seeds": [11],
    "repetitions": 1,
    "comparison_plan": {},
}


def _manifest(**changes: Any) -> dict[str, Any]:
    return {**MANIFEST, **changes}


def _codes(gate: dict[str, Any]) -> list[str]:
    return [r["code"] for r in gate["reasons"]]


def test_comparability_rejects_dataset_mode_and_suite_differences() -> None:
    other_dataset = _manifest(
        benchmark={"id": "b", "content_hash": "b2", "dataset": {"id": "d", "content_hash": "d2"}}
    )
    gate = comparability(
        MANIFEST, other_dataset, variable="pattern", baseline_mode="live", candidate_mode="live"
    )
    assert gate["status"] == "incompatible"
    assert _codes(gate) == ["benchmark_mismatch", "dataset_mismatch"]

    mixed = comparability(
        MANIFEST, MANIFEST, variable="pattern", baseline_mode="live", candidate_mode="replay"
    )
    assert _codes(mixed) == ["mixed_modes"]

    pairs = grid(per_category=1)
    pairs[0] = pair(
        pairs[0].category, pairs[0].slug, 1, pairs[0].baseline, side("pass", suite_hash="other")
    )
    suites = comparability(
        MANIFEST,
        MANIFEST,
        variable="pattern",
        baseline_mode="live",
        candidate_mode="live",
        pairs=pairs,
    )
    assert _codes(suites) == ["evaluator_suite_mismatch"]
    assert suites["reasons"][0]["count"] == 1

    declared = _manifest(agents=[{"id": "a2", "pattern": "planner_executor", "tools": []}])
    ok = comparability(
        MANIFEST, declared, variable="pattern", baseline_mode="live", candidate_mode="live"
    )
    assert ok["status"] == "compatible"
    budgets = comparability(
        MANIFEST,
        _manifest(budgets={"max_steps": 6}),
        variable="pattern",
        baseline_mode="live",
        candidate_mode="live",
    )
    assert _codes(budgets) == ["budget_mismatch"]


def test_export_digest_detects_edits() -> None:
    body = {"status": "complete", "pairs": [{"outcome": "both_pass"}]}
    document = {**body, "export_digest": canonical_digest(body)}
    assert verify_export(document)
    tampered = {**document, "pairs": [{"outcome": "fixed"}]}
    assert not verify_export(tampered)
