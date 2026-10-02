"""Variabilidad de un proveedor remoto (reproducibility, "Remote variability").

Un servidor local imita un proveedor compatible con OpenAI que responde distinto a peticiones
idénticas (seed no soportada, temperatura > 0). Ambos runs se conservan, el reporte expone la
variabilidad y los snapshots del experimento no cambian.
"""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from evallab.benchmarks import runner
from evallab.benchmarks.live import live_model, live_react_suite
from evallab.benchmarks.pilot import AGENT_TOOLS, PILOT
from evallab.benchmarks.suite import publish_suite
from evallab.db import models as m
from evallab.services.reports import experiment_report

pytestmark = pytest.mark.integration
SLUG = "pilot-rs-constraints"
ANSWERS = ['{"slot": "B"}', '{"slot": "C"}']


@pytest.fixture
def varying_provider() -> Iterator[tuple[list[dict[str, Any]], str]]:
    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            requests.append(json.loads(self.rfile.read(length)))
            content = ANSWERS[(len(requests) - 1) % len(ANSWERS)]
            body = {
                "id": f"resp-{len(requests)}",
                "model": "remote-model-2026",
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 8},
            }
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args: Any) -> None:
            del args

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield requests, f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()


def test_identical_requests_with_different_answers_are_kept_and_reported(
    fresh_database: Engine,
    varying_provider: tuple[list[dict[str, Any]], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests, url = varying_provider
    monkeypatch.setenv("MODEL_GATEWAY_LIVE_ENABLED", "true")
    monkeypatch.setenv("MODEL_GATEWAY_BASE_URL", url)
    monkeypatch.setenv("MODEL_GATEWAY_API_KEY", "sk-test-local-stub-not-a-real-key")
    model = live_model(
        "remote-model",
        revision=None,
        seed_support="unsupported",
        temperature=0.7,
        max_tokens=64,
        price_input=None,
        price_output=None,
        currency="USD",
        price_source=None,
    )
    suite, agent = live_react_suite(PILOT, model, AGENT_TOOLS)
    with Session(fresh_database, expire_on_commit=False) as db, db.begin():
        published = publish_suite(db, suite)
        experiment = runner.create_experiment(
            db, published, [agent], hypothesis="variabilidad remota", seeds=[11, 23]
        )
        scenarios = [s for s in published.scenarios if s.slug == SLUG]
        run_ids = runner.enqueue(
            db, experiment, runner.plan_cells(scenarios, [published.agents[agent]], 2)
        )
        manifest_before = experiment.manifest_hash
    runner.execute_in_order(fresh_database, run_ids)
    runner.evaluate_all(fresh_database, run_ids)

    # Peticiones idénticas (sin seed: el modelo no la soporta) y respuestas distintas.
    assert len(requests) == 2 and requests[0] == requests[1] and "seed" not in requests[0]
    with Session(fresh_database) as db:
        runs = [db.get(m.Run, run_id) for run_id in run_ids]
        assert all(r is not None and r.status == "completed" for r in runs)
        outputs = [r.result["output"] for r in runs if r is not None and r.result]
        assert outputs == [{"slot": "B"}, {"slot": "C"}]
        exp = db.get(m.Experiment, experiment.id)
        assert exp is not None and exp.manifest_hash == manifest_before
        configs = {
            e.content_hash
            for e in db.scalars(
                select(m.ModelConfiguration).where(
                    m.ModelConfiguration.requested_model == "remote-model"
                )
            )
        }
        assert len(configs) == 1
        report = experiment_report(db, experiment.id)
    (row,) = [r for r in report["agents"][0]["by_scenario"] if r["slug"] == SLUG]
    # Ambos runs cuentan y la variabilidad queda visible: dos salidas distintas, 1 pass, 1 fail.
    assert row["distinct_outputs"] == 2
    assert row["task_success"] == {"pass": 1, "fail": 1}
    assert report["agents"][0]["agent"]["attribution"] == "model_pattern"
