"""Filas mínimas y válidas para tests. Son datos efímeros de test, no un benchmark."""

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from evallab.db import models as m


def digest() -> str:
    return hashlib.sha256(uuid.uuid4().bytes).hexdigest()


def now() -> datetime:
    return datetime.now(UTC)


def benchmark(db: Session) -> m.Benchmark:
    dataset = m.Dataset(
        id=uuid.uuid4(),
        version="1.0.0",
        name=f"test-dataset-{uuid.uuid4().hex[:8]}",
        schema_version="1.0",
        content_hash=digest(),
        split_manifest={},
    )
    db.add(dataset)
    db.flush()
    bench = m.Benchmark(
        id=uuid.uuid4(),
        version="1.0.0",
        dataset_id=dataset.id,
        dataset_version=dataset.version,
        scenario_selection={},
        evaluator_suite={},
        metric_profile={},
        comparison_rules={},
        content_hash=digest(),
    )
    db.add(bench)
    db.flush()
    return bench


def scenario(db: Session) -> m.Scenario:
    row = m.Scenario(
        id=uuid.uuid4(),
        version="1.0.0",
        primary_category="reasoning",
        input={},
        oracle_ref={},
        limits={},
        content_hash=digest(),
    )
    db.add(row)
    db.flush()
    return row


def agent(
    db: Session, *, pattern: str = "scripted", pattern_parameters: dict[str, Any] | None = None
) -> m.AgentConfiguration:
    row = m.AgentConfiguration(
        id=uuid.uuid4(),
        version="1.0.0",
        pattern=pattern,
        pattern_version="1.0.0",
        pattern_parameters=pattern_parameters or {},
        content_hash=digest(),
    )
    db.add(row)
    db.flush()
    return row


def experiment(db: Session, *, repetitions: int = 1) -> m.Experiment:
    bench = benchmark(db)
    row = m.Experiment(
        hypothesis="test",
        benchmark_id=bench.id,
        benchmark_version=bench.version,
        repetitions=repetitions,
        seeds=list(range(11, 11 + repetitions)),
    )
    db.add(row)
    db.flush()
    return row


def run(db: Session, exp: m.Experiment | None = None) -> m.Run:
    exp = exp or experiment(db)
    cfg = agent(db)
    db.add(m.ExperimentAgent(experiment_id=exp.id, agent_id=cfg.id, agent_version=cfg.version))
    sc = scenario(db)
    row = m.Run(
        experiment_id=exp.id,
        scenario_id=sc.id,
        scenario_version=sc.version,
        agent_id=cfg.id,
        agent_version=cfg.version,
        repetition=1,
        seed=11,
        mode="live",
    )
    db.add(row)
    db.flush()
    return row


def sealed_trace(db: Session, run_row: m.Run | None = None) -> m.Trace:
    row = m.Trace(
        run_id=(run_row or run(db)).id,
        schema_version="1.0",
        digest=digest(),
        completeness="complete",
        sealed_at=now(),
    )
    db.add(row)
    db.flush()
    return row


def evaluation(db: Session) -> m.Evaluation:
    trace = sealed_trace(db)
    assert trace.digest is not None
    row = m.Evaluation(
        run_id=trace.run_id, trace_digest=trace.digest, evaluator_suite_hash=digest()
    )
    db.add(row)
    db.flush()
    return row
