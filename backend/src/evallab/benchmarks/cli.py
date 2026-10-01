"""CLI `evallab-benchmark`: publicar suites sintéticas y comprobar su lock de hashes.

    evallab-benchmark publish pilot          # publica (o reutiliza) y verifica el lock
    evallab-benchmark lock pilot             # imprime el lock calculado (JSON canónico)

Usa la base configurada en POSTGRES_*. No llama a ningún modelo ni abre red externa.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from sqlalchemy.orm import Session

from evallab.benchmarks.registry import committed_lock, get_suite
from evallab.benchmarks.suite import publish_suite
from evallab.db.engine import create_db_engine
from evallab.settings import DatabaseSettings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evallab-benchmark")
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("publish", help="publica la suite y verifica el lock")
    publish.add_argument("suite")
    lock = commands.add_parser("lock", help="imprime el lock de hashes de la suite")
    lock.add_argument("suite")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    suite = get_suite(args.suite)
    engine = create_db_engine(DatabaseSettings())
    try:
        with Session(engine) as db:
            transaction = db.begin()
            lock = publish_suite(db, suite).lock()
            expected = committed_lock(args.suite)
            if args.command == "lock":
                transaction.rollback()  # `lock` sólo calcula hashes; no deja filas.
            elif expected is not None and expected != lock:
                transaction.rollback()
                print("el lock publicado no coincide con el versionado", file=sys.stderr)
                return 1
            else:
                transaction.commit()
    finally:
        engine.dispose()
    print(json.dumps(lock, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
