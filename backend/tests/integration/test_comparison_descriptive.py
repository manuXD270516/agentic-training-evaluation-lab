"""Comparación descriptiva pareada (8.2): manifests, pares, fallos conservados e incompletos."""

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from evallab.benchmarks import runner
from evallab.benchmarks.cli import main as benchmark_cli
from evallab.benchmarks.pilot_models import PILOT_MODELS
from evallab.benchmarks.suite import Published, publish_suite
from evallab.comparison.descriptive import compare, diff_paths, manifest_check

pytestmark = pytest.mark.integration

REACT = "pilot-react-fixture"
PLANNER = "pilot-planner-executor-fixture"
SCRIPTED_DIFFERENCES = {"pilot-pc-injection-exfiltration", "pilot-rs-constraints"}


def _publish(engine: Engine) -> Published:
    with Session(engine, expire_on_commit=False) as db, db.begin():
        return publish_suite(db, PILOT_MODELS)


def _experiment(
    engine: Engine,
    published: Published,
    agent: str,
    *,
    budgets: dict[str, int] | None = None,
    skip: set[str] | None = None,
) -> uuid.UUID:
    scenarios = [s for s in published.scenarios if s.slug not in (skip or set())]
    with Session(engine, expire_on_commit=False) as db, db.begin():
        experiment = runner.create_experiment(
            db, published, [agent], hypothesis=agent, seeds=[11], budgets=budgets
        )
        run_ids = runner.enqueue(
            db, experiment, runner.plan_cells(scenarios, [published.agents[agent]], 1)
        )
    runner.execute_in_order(engine, run_ids)
    runner.evaluate_all(engine, run_ids)
    return experiment.id


def test_cli_compares_twin_experiments_and_keeps_failures(
    fresh_database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POSTGRES_DB", str(fresh_database.url.database))
    out = tmp_path / "cmp"
    args = ["compare", "pilot-models", "--baseline", REACT, "--candidate", PLANNER]
    assert benchmark_cli([*args, "--repetitions", "1", "--out", str(out)]) == 0
    comparison: dict[str, Any] = json.loads((out / "comparison.json").read_text("utf-8"))

    check = comparison["manifest_check"]
    assert check["compatible"] is True and check["unexpected_differences"] == []
    # Sólo cambian el agente (patrón, prompt, parámetros, roles→modelos) y la hipótesis.
    assert set(check["changed_agent_fields"]) <= {
        "id",
        "version",
        "content_hash",
        "pattern",
        "pattern_version",
        "prompt_hash",
        "pattern_parameters",
        "roles",
    }
    assert "pattern" in check["changed_agent_fields"] and "roles" in check["changed_agent_fields"]
    assert all(p.startswith(("/agents/0/", "/hypothesis")) for p in check["differences"])

    assert comparison["status"] == "complete"
    assert comparison["labels"]["statistical_claims"] == "none"
    assert comparison["pair_counts"] == {
        "both_pass": 12,
        "both_fail": 0,
        "new_failure": 2,
        "fixed": 0,
        "incomplete": 0,
    }
    failures = comparison["failures"]
    assert failures["baseline"] == []
    assert {f["slug"] for f in failures["candidate"]} == SCRIPTED_DIFFERENCES
    by_slug = {f["slug"]: f["evidence"] for f in failures["candidate"]}
    assert by_slug["pilot-pc-injection-exfiltration"]["failed_dimensions"] == ["policy"]
    assert by_slug["pilot-rs-constraints"]["failed_dimensions"] == ["outcome"]
    assert all(e["run_id"] and e["evaluation_id"] for e in by_slug.values())

    base, cand = comparison["baseline"], comparison["candidate"]
    assert base["usage"]["totals"]["model_calls"] < cand["usage"]["totals"]["model_calls"]
    assert cand["usage"]["estimated_cost"]["synthetic_price"] is True
    markdown = (out / "comparison.md").read_text("utf-8")
    assert "afirmaciones estadísticas: `none`" in markdown and "fixture" in markdown


def test_undeclared_manifest_difference_blocks_pairing(fresh_database: Engine) -> None:
    published = _publish(fresh_database)
    baseline = _experiment(fresh_database, published, REACT, skip={"pilot-ts-stock-lookup"})
    candidate = _experiment(
        fresh_database, published, PLANNER, budgets={"max_steps": 6}, skip={"pilot-ts-stock-lookup"}
    )
    with Session(fresh_database) as db:
        comparison = compare(db, baseline, candidate)
    assert comparison["status"] == "incompatible"
    assert comparison["pairs"] == []
    assert comparison["manifest_check"]["unexpected_differences"] == ["/budgets/max_steps"]


def test_missing_candidate_cell_marks_pair_incomplete(fresh_database: Engine) -> None:
    published = _publish(fresh_database)
    baseline = _experiment(fresh_database, published, REACT)
    candidate = _experiment(fresh_database, published, PLANNER, skip={"pilot-ts-stock-lookup"})
    with Session(fresh_database) as db:
        comparison = compare(db, baseline, candidate)
    assert comparison["status"] == "incomplete"
    assert comparison["pair_counts"]["incomplete"] == 1
    missing = [f for f in comparison["failures"]["candidate"] if f["status"] == "missing"]
    assert [f["slug"] for f in missing] == ["pilot-ts-stock-lookup"]
    row = next(r for r in comparison["pairs"] if r["slug"] == "pilot-ts-stock-lookup")
    assert (row["baseline"], row["candidate"], row["outcome"]) == ("pass", "missing", "incomplete")


def test_manifest_diff_reports_paths() -> None:
    assert diff_paths({"a": 1, "b": [1, 2]}, {"a": 1, "b": [1, 3], "c": 0}) == ["/b/1", "/c"]
    left = {"budgets": {}, "agents": [{"pattern": "react", "tools": [1]}]}
    right = {"budgets": {}, "agents": [{"pattern": "planner_executor", "tools": [2]}]}
    check = manifest_check(left, right, "pattern")
    assert check["compatible"] is False
    assert check["unexpected_differences"] == ["/agents/0/tools/0"]
