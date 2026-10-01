"""Ejecución offline de una suite: experimento sellado, celdas, worker y evaluación.

El orden de celdas sigue metrics.md §4: por escenario (orden del manifest) y repetición, con el
orden de variantes aleatorizado con `Random(101)` e intercalado por escenario. Cada celda se
ejecuta con las mismas tres fases que el worker (reclamar con lease, ejecutar en memoria,
persistir con fencing) y se evalúa con la suite determinística vigente.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from evallab import telemetry
from evallab.benchmarks.suite import Published
from evallab.db import models as m
from evallab.schemas import ExperimentCreate, RunCreate, VersionRef
from evallab.services import evaluations as eval_svc
from evallab.services import experiments as exp_svc
from evallab.services.execution import claim_run, finish_attempt, run_attempt

VARIANT_ORDER_SEED = 101


@dataclass(frozen=True)
class ScheduledCell:
    scenario: VersionRef
    agent: VersionRef
    repetition: int


def plan_cells(
    scenarios: Sequence[m.Scenario], agents: Sequence[m.AgentConfiguration], repetitions: int
) -> list[ScheduledCell]:
    rng = random.Random(VARIANT_ORDER_SEED)
    cells: list[ScheduledCell] = []
    for scenario in scenarios:
        for repetition in range(1, repetitions + 1):
            order = list(agents)
            rng.shuffle(order)
            cells.extend(
                ScheduledCell(
                    scenario=VersionRef(id=scenario.id, version=scenario.version),
                    agent=VersionRef(id=agent.id, version=agent.version),
                    repetition=repetition,
                )
                for agent in order
            )
    return cells


def create_experiment(
    db: Session,
    published: Published,
    agent_names: Sequence[str],
    *,
    hypothesis: str,
    seeds: Sequence[int],
    budgets: dict[str, int] | None = None,
    comparison_plan: dict[str, object] | None = None,
) -> m.Experiment:
    assert published.benchmark is not None
    agents = [published.agents[name] for name in agent_names]
    draft = exp_svc.create_draft(
        db,
        ExperimentCreate.model_validate(
            {
                "hypothesis": hypothesis,
                "benchmark": {
                    "id": str(published.benchmark.id),
                    "version": published.benchmark.version,
                },
                "agents": [{"id": str(a.id), "version": a.version} for a in agents],
                "budgets": budgets or {},
                "repetitions": len(seeds),
                "seeds": list(seeds),
                "comparison_plan": comparison_plan or {},
            }
        ),
    )
    return exp_svc.seal(db, draft.id)


def enqueue(
    db: Session, experiment: m.Experiment, cells: Sequence[ScheduledCell]
) -> list[uuid.UUID]:
    runs = [
        exp_svc.create_run(
            db,
            experiment.id,
            RunCreate(
                scenario=cell.scenario, agent=cell.agent, repetition=cell.repetition, mode="live"
            ),
        )
        for cell in cells
    ]
    return [run.id for run in runs]


def execute_in_order(
    engine: Engine, run_ids: Sequence[uuid.UUID], *, worker_id: str = "benchmark-runner"
) -> int:
    """Ejecuta las celdas en el orden planificado con las tres fases del worker."""
    sessions = sessionmaker(engine, expire_on_commit=False)
    executed = 0
    for run_id in run_ids:
        with sessions.begin() as db:
            claim = claim_run(db, run_id, worker_id=worker_id)
        if claim is None:
            continue
        with telemetry.span("worker.attempt", **{"evallab.run_id": str(run_id)}):
            with sessions() as db:
                outcome = run_attempt(db, claim)
            with sessions.begin() as db:
                finish_attempt(db, outcome)
        executed += 1
    return executed


def evaluate_all(engine: Engine, run_ids: Sequence[uuid.UUID]) -> list[uuid.UUID]:
    evaluations: list[uuid.UUID] = []
    for run_id in run_ids:
        with Session(engine) as db, db.begin():
            evaluations.append(eval_svc.evaluate_run(db, run_id).id)
    return evaluations


def experiment_runs(db: Session, experiment_id: uuid.UUID) -> list[m.Run]:
    return list(
        db.scalars(
            select(m.Run)
            .where(m.Run.experiment_id == experiment_id)
            .order_by(m.Run.created_at, m.Run.id)
        )
    )
