"""CLI `evallab-judge-calibration`: plantilla, votos del judge y análisis de acuerdo (9.2).

    evallab-judge-calibration template --out anotaciones.json
    evallab-judge-calibration votes --model qwen2.5:7b --out votos.json   # proveedor live
    evallab-judge-calibration analyze --annotations anotaciones.json [--votes votos.json]

No genera anotaciones: la plantilla deja las notas vacías para dos anotadores humanos.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evallab.canonical import canonical_digest
from evallab.evaluation.calibration import analyze, load_set
from evallab.evaluation.judge import JUDGE_ROLE, RUBRICS, run_judge
from evallab.runner.live import LIVE_PROVIDER, ModelGatewaySettings, live_providers
from evallab.runner.models import ModelSnapshot, ProviderModelGateway


def _template() -> dict[str, object]:
    data = load_set()
    return {
        "set_id": data["set_id"],
        "set_version": data["version"],
        "rubric": data["rubric"],
        "human": False,
        "annotators": ["anotador_a", "anotador_b"],
        "instructions": (
            "Cada anotador puntúa todos los ítems con la rúbrica (1-4 o null para abstenerse) "
            "sin ver al judge ni al otro anotador. Después se adjudican los desacuerdos y se "
            "documenta el motivo. Marcar human=true sólo con anotaciones humanas reales."
        ),
        "ratings": {
            item["item_id"]: {"anotador_a": None, "anotador_b": None} for item in data["items"]
        },
        "adjudicated": {},
        "adjudication_notes": {},
    }


def _votes(model: str, revision: str | None, max_tokens: int) -> dict[str, Any]:
    """Votos del judge sobre el set con el proveedor live (paso 5 del protocolo). No mira las
    anotaciones; una respuesta inválida, una abstención o una inyección quedan `null`."""
    settings = ModelGatewaySettings()
    if not settings.live_ready():
        raise SystemExit(
            "proveedor live deshabilitado: define MODEL_GATEWAY_LIVE_ENABLED=true, "
            "MODEL_GATEWAY_BASE_URL y MODEL_GATEWAY_API_KEY (ver docs/live-run.md)"
        )
    config = {"provider": LIVE_PROVIDER, "model": model, "revision": revision}
    digest = canonical_digest(config)
    snapshot = ModelSnapshot(
        role=JUDGE_ROLE,
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"evallab:judge:{digest}"),
        version="1.0.0",
        content_hash=digest,
        provider=LIVE_PROVIDER,
        requested_model=model,
        resolved_revision=revision,
        temperature="0",
        max_tokens=max_tokens,
        seed_support="unknown",
    )
    gateway = ProviderModelGateway({JUDGE_ROLE: snapshot}, live_providers(settings))
    data = load_set()
    rubric = RUBRICS[str(data["rubric"])]
    votes: dict[str, int | None] = {}
    details: dict[str, dict[str, str]] = {}
    for item in data["items"]:
        outcome = run_judge(
            gateway,
            rubric,
            task=item["task"],
            output=item["response"],
            output_available=True,
            scenario_slug=None,
            deterministic={},
        )
        votes[item["item_id"]] = outcome.rating if outcome.status in ("pass", "fail") else None
        details[item["item_id"]] = {"status": outcome.status, "reason": outcome.reason}
    return {
        "set_id": data["set_id"],
        "set_version": data["version"],
        "rubric": data["rubric"],
        "judge_model": config,
        "votes": votes,
        "details": details,
    }


def _dump(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evallab-judge-calibration")
    commands = parser.add_subparsers(dest="command", required=True)
    template = commands.add_parser("template")
    template.add_argument("--out", type=Path, required=True)
    votes = commands.add_parser("votes", help="vota el set con el judge (proveedor live)")
    votes.add_argument("--model", required=True)
    votes.add_argument("--revision", default=None)
    votes.add_argument("--max-tokens", type=int, default=400)
    votes.add_argument("--out", type=Path, required=True)
    run = commands.add_parser("analyze")
    run.add_argument("--annotations", type=Path, required=True)
    run.add_argument("--votes", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.command == "template":
        _dump(args.out, _template())
        return 0
    if args.command == "votes":
        _dump(args.out, _votes(args.model, args.revision, args.max_tokens))
        return 0
    data = load_set()
    annotations = json.loads(args.annotations.read_text(encoding="utf-8"))
    raw_votes = json.loads(args.votes.read_text(encoding="utf-8")) if args.votes else None
    # Acepta el fichero de `votes` completo o sólo el mapa {item_id: rating}.
    if isinstance(raw_votes, dict) and isinstance(raw_votes.get("votes"), dict):
        raw_votes = raw_votes["votes"]
    rubric = RUBRICS[str(data["rubric"])]
    result = analyze(data["items"], annotations, raw_votes, rubric.pass_threshold)
    print(json.dumps(result.document, indent=2, ensure_ascii=False))
    return 0 if result.status == "calibrated" else 3


if __name__ == "__main__":
    sys.exit(main())
