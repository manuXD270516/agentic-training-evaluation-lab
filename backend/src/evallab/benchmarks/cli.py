"""CLI `evallab-benchmark`: publicar suites sintéticas, comprobar su lock y ejecutarlas offline.

    evallab-benchmark publish pilot          # publica (o reutiliza) y verifica el lock
    evallab-benchmark lock pilot             # imprime el lock calculado (JSON)
    evallab-benchmark run pilot --repetitions 5 --out ../results/m5-pilot-scripted
    evallab-benchmark compare pilot-models --baseline A --candidate B --out <dir>
    evallab-benchmark compare-experiments --baseline-experiment ID --candidate-experiment ID \
        --out <dir>                          # export controlado (M11) de experimentos existentes

`run` publica la suite, crea y sella un experimento con sus agentes, encola las celdas en el
orden de metrics.md §4, las ejecuta con las fases del worker, las evalúa y escribe el reporte
descriptivo (JSON y Markdown) junto al manifest sellado. Usa la base de POSTGRES_*; no llama a
ningún modelo ni abre red externa.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from evallab import __version__
from evallab.benchmarks import runner
from evallab.benchmarks.registry import committed_lock, get_suite
from evallab.benchmarks.report_md import render
from evallab.benchmarks.suite import Published, Suite, publish_suite
from evallab.comparison.descriptive import compare as compare_experiments
from evallab.comparison.protocol import compare_controlled
from evallab.comparison.render import render as render_comparison
from evallab.comparison.render import render_controlled
from evallab.db.engine import create_db_engine
from evallab.services.reports import experiment_report
from evallab.settings import DatabaseSettings


class LockMismatchError(Exception):
    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evallab-benchmark")
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("publish", help="publica la suite y verifica el lock")
    publish.add_argument("suite")
    lock = commands.add_parser("lock", help="imprime el lock de hashes de la suite")
    lock.add_argument("suite")
    run = commands.add_parser("run", help="ejecuta la suite offline y escribe el reporte")
    run.add_argument("suite")
    run.add_argument("--repetitions", type=int, default=5)
    run.add_argument("--agents", default="", help="nombres separados por comas (todos si vacío)")
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--hypothesis", default=None)
    compare = commands.add_parser(
        "compare", help="ejecuta baseline y candidato en experimentos gemelos y los compara"
    )
    compare.add_argument("suite")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--candidate", required=True)
    compare.add_argument("--variable", default="pattern", choices=["pattern", "model", "prompt"])
    compare.add_argument("--repetitions", type=int, default=5)
    compare.add_argument("--out", type=Path, required=True)
    existing = commands.add_parser(
        "compare-experiments",
        help="exporta la comparación controlada (M11) de dos experimentos sellados ya ejecutados",
    )
    existing.add_argument("--baseline-experiment", type=uuid.UUID, required=True)
    existing.add_argument("--candidate-experiment", type=uuid.UUID, required=True)
    existing.add_argument("--variable", default="pattern", choices=["pattern", "model", "prompt"])
    existing.add_argument("--baseline-mode", default="live", choices=["live", "replay"])
    existing.add_argument("--candidate-mode", default="live", choices=["live", "replay"])
    existing.add_argument("--out", type=Path, required=True)
    return parser


def _publish(
    engine: Engine, name: str, suite: Suite, *, keep: bool
) -> tuple[Published, dict[str, Any]]:
    with Session(engine, expire_on_commit=False) as db:
        transaction = db.begin()
        published = publish_suite(db, suite)
        lock = published.lock()
        expected = committed_lock(name)
        if not keep:
            transaction.rollback()  # `lock` sólo calcula hashes; no deja filas.
        elif expected is not None and expected != lock:
            transaction.rollback()
            raise LockMismatchError("el lock publicado no coincide con el versionado")
        else:
            transaction.commit()
    return published, lock


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _run(engine: Engine, name: str, suite: Suite, args: argparse.Namespace) -> dict[str, Any]:
    published, lock = _publish(engine, name, suite, keep=True)
    names = [n for n in args.agents.split(",") if n] or sorted(published.agents)
    seeds = list(suite.benchmark.get("seed_schedule") or [])[: args.repetitions]
    if len(seeds) != args.repetitions:
        raise SystemExit(f"la suite sólo declara {len(seeds)} seeds")
    started = time.monotonic()
    with Session(engine, expire_on_commit=False) as db, db.begin():
        experiment = runner.create_experiment(
            db,
            published,
            names,
            hypothesis=args.hypothesis
            or f"Descripción offline de {suite.dataset_name} con agentes scripted (harness)",
            seeds=seeds,
            comparison_plan={"mode": "descriptive_only", "agents": names},
        )
        agents = [published.agents[n] for n in names]
        cells = runner.plan_cells(published.scenarios, agents, args.repetitions)
        run_ids = runner.enqueue(db, experiment, cells)
    executed = runner.execute_in_order(engine, run_ids)
    runner.evaluate_all(engine, run_ids)
    with Session(engine) as db:
        report = experiment_report(db, experiment.id)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "report.json", report)
    _write_json(out / "lock.json", lock)
    _write_json(
        out / "manifest.json",
        {"manifest_hash": experiment.manifest_hash, "manifest": experiment.manifest},
    )
    _write_json(
        out / "run.json",
        {
            "evallab_version": __version__,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "suite": name,
            "agents": {n: str(published.agents[n].id) for n in names},
            "repetitions": args.repetitions,
            "seeds": seeds,
            "cells_planned": len(cells),
            "cells_executed": executed,
            "wall_clock_s": round(time.monotonic() - started, 3),
            "finished_at": datetime.now(UTC).isoformat(),
            "experiment_id": str(experiment.id),
        },
    )
    (out / "report.md").write_text(
        render(
            report,
            title=f"Reporte descriptivo: {suite.dataset_name}",
            names={str(published.agents[n].id): n for n in names},
        ),
        encoding="utf-8",
        newline="\n",
    )
    return report


def _compare(engine: Engine, name: str, suite: Suite, args: argparse.Namespace) -> dict[str, Any]:
    """Dos experimentos que sólo difieren en el agente; celdas intercaladas por escenario."""
    published, lock = _publish(engine, name, suite, keep=True)
    seeds = list(suite.benchmark.get("seed_schedule") or [])[: args.repetitions]
    if len(seeds) != args.repetitions:
        raise SystemExit(f"la suite sólo declara {len(seeds)} seeds")
    plan = {
        "mode": "descriptive_only",
        "baseline": args.baseline,
        "candidate": args.candidate,
        "independent_variable": args.variable,
    }
    with Session(engine, expire_on_commit=False) as db, db.begin():
        experiments = {
            role: runner.create_experiment(
                db,
                published,
                [agent],
                hypothesis=f"{role}: {agent} en {suite.dataset_name} (comparación descriptiva)",
                seeds=seeds,
                comparison_plan=plan,
            )
            for role, agent in (("baseline", args.baseline), ("candidate", args.candidate))
        }
        agents = [published.agents[args.baseline], published.agents[args.candidate]]
        by_agent = {agents[0].id: experiments["baseline"], agents[1].id: experiments["candidate"]}
        run_ids = [
            runner.enqueue(db, by_agent[cell.agent.id], [cell])[0]
            for cell in runner.plan_cells(published.scenarios, agents, args.repetitions)
        ]
    executed = runner.execute_in_order(engine, run_ids)
    runner.evaluate_all(engine, run_ids)
    with Session(engine) as db:
        comparison = compare_experiments(
            db, experiments["baseline"].id, experiments["candidate"].id, variable=args.variable
        )
        controlled = compare_controlled(
            db, experiments["baseline"].id, experiments["candidate"].id, variable=args.variable
        )
        reports = {role: experiment_report(db, exp.id) for role, exp in experiments.items()}
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    names = {str(published.agents[n].id): n for n in (args.baseline, args.candidate)}
    _write_json(out / "comparison.json", comparison)
    _write_json(out / "lock.json", lock)
    for role, exp in experiments.items():
        _write_json(out / f"{role}-report.json", reports[role])
        _write_json(
            out / f"{role}-manifest.json",
            {"manifest_hash": exp.manifest_hash, "manifest": exp.manifest},
        )
    _write_json(
        out / "run.json",
        {
            "evallab_version": __version__,
            "python": platform.python_version(),
            "suite": name,
            "baseline": args.baseline,
            "candidate": args.candidate,
            "variable": args.variable,
            "seeds": seeds,
            "cells_executed": executed,
            "finished_at": datetime.now(UTC).isoformat(),
        },
    )
    (out / "comparison.md").write_text(
        render_comparison(
            comparison,
            title=f"Comparación descriptiva: {args.baseline} vs {args.candidate}",
            names=names,
        ),
        encoding="utf-8",
        newline="\n",
    )
    _write_controlled(out, controlled, f"{args.baseline} vs {args.candidate}", names)
    return comparison


def _write_controlled(
    out: Path, document: dict[str, Any], label: str, names: dict[str, str]
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "controlled.json", document)
    (out / "controlled.md").write_text(
        render_controlled(document, title=f"Comparación controlada: {label}", names=names),
        encoding="utf-8",
        newline="\n",
    )


def _compare_existing(engine: Engine, args: argparse.Namespace) -> dict[str, Any]:
    with Session(engine) as db:
        document = compare_controlled(
            db,
            args.baseline_experiment,
            args.candidate_experiment,
            variable=args.variable,
            baseline_mode=args.baseline_mode,
            candidate_mode=args.candidate_mode,
        )
    label = f"{args.baseline_experiment} vs {args.candidate_experiment}"
    _write_controlled(args.out, document, label, {})
    return document


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    engine = create_db_engine(DatabaseSettings())
    if args.command == "compare-experiments":
        try:
            document = _compare_existing(engine, args)
        finally:
            engine.dispose()
        print(json.dumps(document["decision"], indent=2, sort_keys=True))
        return 0 if document["status"] != "incompatible" else 1
    suite = get_suite(args.suite)
    try:
        if args.command == "compare":
            comparison = _compare(engine, args.suite, suite, args)
            print(json.dumps(comparison.get("pair_counts"), indent=2, sort_keys=True))
            return 0 if comparison["status"] != "incompatible" else 1
        if args.command == "run":
            report = _run(engine, args.suite, suite, args)
            summary = {a["agent"]["id"]: a["summary"]["task_success"] for a in report["agents"]}
            print(json.dumps(summary, indent=2, sort_keys=True))
            return 0
        _, lock = _publish(engine, args.suite, suite, keep=args.command == "publish")
    except LockMismatchError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print(json.dumps(lock, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
