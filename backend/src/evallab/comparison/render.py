"""Markdown de una comparación: las mismas cifras que el JSON, sin conclusiones añadidas."""

from __future__ import annotations

from typing import Any

from evallab.benchmarks.report_md import _cost, _fmt, _tokens


def _side_row(label: str, side: dict[str, Any], name: str) -> str:
    task = side["summary"]["task_success"]
    raw = side["summary"]["raw_outcome_pass"]
    usage = side["usage"]
    totals = usage["totals"]
    return (
        f"| {label} | `{name}` | {side['agent']['pattern']} | {task['successes']}/{task['cells']} "
        f"| {task['unknown']} | {_fmt(raw['conservative_rate'])} | {totals['steps']} | "
        f"{totals['model_calls']} | {totals['tool_calls']} | "
        f"{usage['tokens'].get('total', 'N/A')} | {usage['estimated_cost'].get('amount', 'N/A')} |"
    )


def render(comparison: dict[str, Any], *, title: str, names: dict[str, str]) -> str:
    check = comparison["manifest_check"]
    base, cand = comparison["baseline"], comparison["candidate"]
    lines = [
        f"# {title}",
        "",
        f"> **Análisis `{comparison['labels']['analysis']}`, afirmaciones estadísticas: "
        f"`{comparison['labels']['statistical_claims']}`.** {comparison['labels']['reason']}. "
        "Ambos agentes usan modelos de fixture: las diferencias las fijan sus guiones y no "
        "describen la calidad de ningún patrón con un LLM real.",
        "",
        f"- Estado: `{comparison['status']}`; variable independiente declarada: "
        f"`{check['independent_variable']}`",
        f"- Manifests: compatibles={check['compatible']}; campos del agente que cambian: "
        f"{check['changed_agent_fields']}; diferencias no declaradas: "
        f"{check['unexpected_differences'] or 'ninguna'}",
        f"- Baseline `{base['manifest_hash']}`, candidato `{cand['manifest_hash']}`",
        "",
        "| Lado | Agente | Patrón | Éxito | U | raw_outcome S/N | Pasos | Llamadas modelo | "
        "Tools | Tokens | Coste (sintético) |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
        _side_row("baseline", base, names.get(base["agent"]["id"], base["agent"]["id"])),
        _side_row("candidato", cand, names.get(cand["agent"]["id"], cand["agent"]["id"])),
        "",
        f"- Tokens baseline: {_tokens(base['usage']['tokens'])}",
        f"- Tokens candidato: {_tokens(cand['usage']['tokens'])}",
        f"- Coste baseline: {_cost(base['usage']['estimated_cost'])}",
        f"- Coste candidato: {_cost(cand['usage']['estimated_cost'])}",
        "",
    ]
    if comparison["status"] == "incompatible":
        return "\n".join([*lines, "Comparación bloqueada: manifests incompatibles.", ""]) + "\n"
    counts = comparison["pair_counts"]
    lines += [
        "## Pares (escenario, repetición, seed)",
        "",
        f"both_pass {counts['both_pass']}, both_fail {counts['both_fail']}, new_failure "
        f"{counts['new_failure']}, fixed {counts['fixed']}, incomplete {counts['incomplete']}.",
        "",
        "| Categoría | Escenario | Pares | both_pass | both_fail | new_failure | fixed "
        "| incomplete |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in comparison["by_scenario"]:
        lines.append(
            f"| {row['category']} | {row['slug']} | {row['pairs']} | {row['both_pass']} | "
            f"{row['both_fail']} | {row['new_failure']} | {row['fixed']} | {row['incomplete']} |"
        )
    lines += ["", "## Fallos conservados con evidencia", ""]
    for side in ("baseline", "candidate"):
        failures = comparison["failures"][side]
        lines.append(f"### {side} ({len(failures)})")
        lines.append("")
        if not failures:
            lines.append("Ninguno.")
        for failure in failures:
            evidence = failure["evidence"] or {}
            lines.append(
                f"- {failure['slug']} rep {failure['repetition']} (seed {failure['seed']}): "
                f"`{failure['status']}`, dimensiones {evidence.get('failed_dimensions')}, "
                f"run `{evidence.get('run_id')}`, evaluación `{evidence.get('evaluation_id')}`"
            )
        lines.append("")
    return "\n".join(lines) + "\n"
