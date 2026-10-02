"""Instantánea estática para GitHub Pages: sólo lecturas públicas, sin oráculos."""

import json
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from evallab.benchmarks import runner
from evallab.benchmarks.pilot import PILOT, SEEDS
from evallab.benchmarks.suite import publish_suite
from evallab.static_export import export

pytestmark = pytest.mark.integration
AGENTS = ["pilot-scripted-reference", "pilot-scripted-faulty"]
PRIVATE_KEYS = {"expected", "oracle_ref", "qrels", "relevant", "family_id", "split"}


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def test_static_snapshot_mirrors_public_api_without_oracles(
    fresh_database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with Session(fresh_database, expire_on_commit=False) as db, db.begin():
        published = publish_suite(db, PILOT)
        experiment = runner.create_experiment(
            db, published, AGENTS, hypothesis="pages", seeds=SEEDS[:1]
        )
        agents = [published.agents[n] for n in AGENTS]
        run_ids = runner.enqueue(db, experiment, runner.plan_cells(published.scenarios, agents, 1))
    runner.execute_in_order(fresh_database, run_ids)
    runner.evaluate_all(fresh_database, run_ids)
    monkeypatch.setenv("POSTGRES_DB", str(fresh_database.url.database))
    out = tmp_path / "data"

    counts = export(out)

    assert counts == {"experiments": 1, "runs": 28, "comparisons": 0}
    listed = json.loads((out / "experiments.json").read_text("utf-8"))
    assert [e["id"] for e in listed] == [str(experiment.id)]
    assert listed[0]["status"] == "completed"
    base = out / "experiments" / str(experiment.id)
    for name in ("manifest", "report-live", "report-replay", "cells-live", "cells-replay"):
        assert (base / f"{name}.json").is_file()
    for run_id in run_ids:
        trace = json.loads((out / "runs" / str(run_id) / "trace.json").read_text("utf-8"))
        assert trace["completeness"] == "complete" and trace["events"]
        assert (out / "runs" / str(run_id) / "evaluations.json").is_file()
    files = list(out.rglob("*.json"))
    assert not [p for p in files if "oracle" in p.as_posix()]
    for path in files:
        keys = _keys(json.loads(path.read_text("utf-8")))
        assert not keys & PRIVATE_KEYS, path
    snapshot = json.loads((out / "snapshot.json").read_text("utf-8"))
    assert snapshot["access"] == "read_only" and "no mide ningún LLM" in snapshot["note"]
