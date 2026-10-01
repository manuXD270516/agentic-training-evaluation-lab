"""CLI `evallab-judge-calibration`: plantilla de anotación y análisis de acuerdo (9.2).

    evallab-judge-calibration template --out anotaciones.json
    evallab-judge-calibration analyze --annotations anotaciones.json [--votes votos.json]

No genera anotaciones: la plantilla deja las notas vacías para dos anotadores humanos.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from evallab.evaluation.calibration import analyze, load_set
from evallab.evaluation.judge import RUBRICS


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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evallab-judge-calibration")
    commands = parser.add_subparsers(dest="command", required=True)
    template = commands.add_parser("template")
    template.add_argument("--out", type=Path, required=True)
    run = commands.add_parser("analyze")
    run.add_argument("--annotations", type=Path, required=True)
    run.add_argument("--votes", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.command == "template":
        args.out.write_text(
            json.dumps(_template(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return 0
    data = load_set()
    annotations = json.loads(args.annotations.read_text(encoding="utf-8"))
    votes = json.loads(args.votes.read_text(encoding="utf-8")) if args.votes else None
    rubric = RUBRICS[str(data["rubric"])]
    result = analyze(data["items"], annotations, votes, rubric.pass_threshold)
    print(json.dumps(result.document, indent=2, ensure_ascii=False))
    return 0 if result.status == "calibrated" else 3


if __name__ == "__main__":
    sys.exit(main())
