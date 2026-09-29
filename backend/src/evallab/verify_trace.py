"""`evallab-verify-trace MANIFEST.json EVENTS.jsonl`: verifica un export de traza offline."""

import json
import sys
from pathlib import Path

from evallab.services.traces import verify_export


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("uso: evallab-verify-trace MANIFEST.json EVENTS.jsonl", file=sys.stderr)
        return 2
    manifest = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    problems = verify_export(manifest, Path(args[1]).read_bytes())
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
