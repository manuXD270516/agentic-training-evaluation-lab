"""Instantánea estática del dashboard (GitHub Pages, M12).

    evallab-export-static --out ../frontend/public/data [--comparison controlled.json ...]

Recorre la API con las mismas reglas que la demo (`EVALLAB_READ_ONLY=1` sin token): sólo
lecturas públicas, nunca el oráculo. Cada respuesta se guarda como JSON en la ruta que el modo
estático del frontend espera. Las comparaciones (`controlled.json` de `evallab-benchmark
compare`) se copian tal cual; su digest sigue verificable.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evallab.api.app import create_app
from evallab.comparison.protocol import verify_export
from evallab.db.engine import create_db_engine
from evallab.settings import AccessSettings, DatabaseSettings

MODES = ("live", "replay")
# Nunca se exportan: oráculos, vistas privadas ni endpoints de escritura.
FORBIDDEN_FRAGMENTS = ("/oracle",)


# Claves del oráculo privado: si aparecen en cualquier respuesta, la exportación aborta.
PRIVATE_KEYS = frozenset({"expected", "oracle_ref", "qrels", "relevant", "family_id", "split"})


class StaticExportError(Exception):
    pass


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def _write(out: Path, relative: str, value: Any) -> None:
    leaked = _keys(value) & PRIVATE_KEYS
    if leaked:
        raise StaticExportError(f"{relative} contiene claves privadas: {sorted(leaked)}")
    path = out / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except OSError, subprocess.CalledProcessError:
        return None


def export(out: Path, comparisons: Sequence[Path] = ()) -> dict[str, int]:
    from fastapi.testclient import TestClient

    engine = create_db_engine(DatabaseSettings())
    app = create_app(engine=engine, access=AccessSettings(read_only=True))
    counts = {"experiments": 0, "runs": 0, "comparisons": 0}
    written: set[str] = set()

    with TestClient(app) as client:

        def get(path: str) -> Any:
            if any(fragment in path for fragment in FORBIDDEN_FRAGMENTS):
                raise StaticExportError(f"ruta privada: {path}")
            response = client.get(path)
            if response.status_code != 200:
                raise StaticExportError(f"GET {path}: {response.status_code} {response.text}")
            return response.json()

        def save(relative: str, path: str) -> Any:
            value = get(path)
            _write(out, relative, value)
            written.add(relative)
            return value

        experiments = [e for e in get("/experiments") if e.get("manifest_hash")]
        _write(out, "experiments.json", experiments)
        for exp in experiments:
            exp_id = exp["id"]
            base = f"experiments/{exp_id}"
            save(f"{base}.json", f"/experiments/{exp_id}")
            save(f"{base}/manifest.json", f"/experiments/{exp_id}/manifest")
            for mode in MODES:
                save(f"{base}/report-{mode}.json", f"/experiments/{exp_id}/report?mode={mode}")
                save(f"{base}/cells-{mode}.json", f"/experiments/{exp_id}/cells?mode={mode}")
            benchmark = exp.get("benchmark")
            if benchmark:
                ref = f"{benchmark['id']}/versions/{benchmark['version']}"
                if f"benchmarks/{ref}.json" not in written:
                    save(f"benchmarks/{ref}.json", f"/benchmarks/{ref}")
            for run in get(f"/experiments/{exp_id}/runs"):
                run_id = run["id"]
                _write(out, f"runs/{run_id}.json", run)
                save(f"runs/{run_id}/evaluations.json", f"/runs/{run_id}/evaluations")
                if run["status"] not in ("queued", "running"):
                    save(f"runs/{run_id}/trace.json", f"/runs/{run_id}/trace")
                for kind, ref in (
                    ("scenarios", run["scenario"]),
                    ("agent-configurations", run["agent"]),
                ):
                    relative = f"{kind}/{ref['id']}/versions/{ref['version']}.json"
                    if relative not in written:
                        save(relative, f"/{kind}/{ref['id']}/versions/{ref['version']}")
                counts["runs"] += 1
            counts["experiments"] += 1

    index: list[dict[str, Any]] = []
    for source in comparisons:
        document = json.loads(source.read_text(encoding="utf-8"))
        if not verify_export(document):
            raise StaticExportError(f"export de comparación alterado: {source}")
        name = f"{document['baseline']['experiment_id']}_{document['candidate']['experiment_id']}"
        _write(out, f"comparisons/{name}.json", document)
        index.append(
            {
                "id": name,
                "baseline": document["baseline"]["experiment_id"],
                "candidate": document["candidate"]["experiment_id"],
                "status": document["status"],
                "decision": document["decision"],
                "export_digest": document["export_digest"],
            }
        )
        counts["comparisons"] += 1
    _write(out, "comparisons.json", index)
    _write(
        out,
        "snapshot.json",
        {
            "generated_at": datetime.now(UTC).isoformat(),
            "commit": _commit(),
            "access": "read_only",
            "counts": counts,
            "note": "Instantánea estática de experimentos scripted y de modelos de fixture: "
            "prueba el harness, no mide ningún LLM.",
        },
    )
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evallab-export-static")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--comparison", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    counts = export(args.out, args.comparison)
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
