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


def _rate(value: Any) -> str:
    return "—" if value is None else f"{value:.3f}"


def render_controlled(document: dict[str, Any], *, title: str, names: dict[str, str]) -> str:
    """Markdown del export controlado (M11): gates y decisión tal como están en el JSON."""
    protocol = document["protocol"]
    gate = document["comparability"]
    base, cand = document["baseline"], document["candidate"]

    def agent(side: dict[str, Any]) -> str:
        agents = side["manifest"].get("agents") or [{}]
        agent_id = str(agents[0].get("id"))
        fallback = f"{agents[0].get('pattern')}@{agents[0].get('version')} {agent_id[:8]}"
        return names.get(agent_id, fallback)

    def attribution(side: dict[str, Any]) -> str:
        agents = side["report"].get("agents") or [{}]
        return str(agents[0].get("agent", {}).get("attribution", "unknown"))

    per_category = ", ".join(f"{c} {n}" for c, n in document["scenarios_per_category"].items())

    lines = [
        f"# {title}",
        "",
        f"> Protocolo `{protocol['id']}@{protocol['version']}` (hash `{protocol['hash']}`), "
        f"análisis `{document['labels']['analysis']}`, afirmaciones estadísticas "
        f"`{document['labels']['statistical_claims']}`. {document['labels']['reason']}.",
        "",
        f"- Decisión: **`{document['decision']['status']}`** "
        f"({', '.join(document['decision']['reasons'])})",
        f"- Comparabilidad: `{gate['status']}`; modos {gate['modes']['baseline']} / "
        f"{gate['modes']['candidate']}; variable `{document['independent_variable']}`",
        f"- Baseline `{agent(base)}` (manifest `{base['manifest_hash']}`), candidato "
        f"`{agent(cand)}` (manifest `{cand['manifest_hash']}`)",
        f"- Atribución: baseline `{attribution(base)}`, candidato `{attribution(cand)}` "
        "(`harness_baseline` y `fixture_model` no miden ningún LLM)",
        f"- Escenarios por categoría: {per_category}",
        f"- Digest del export: `{document['export_digest']}`",
        "",
    ]
    if document["status"] == "incompatible":
        lines += ["## Bloqueada", ""]
        lines += [f"- `{r['code']}`: {r.get('path') or r}" for r in gate["reasons"]]
        return "\n".join([*lines, ""]) + "\n"
    gates = document["gates"]
    success, policy, latency = gates["success"], gates["policy"], gates["latency"]
    interval = success.get("interval")
    counts = document["pair_counts"]
    lines += [
        "## Gates",
        "",
        "| Gate | Resultado | Detalle |",
        "|---|---|---|",
        f"| Política (violación crítica nueva) | `{policy['gate']}` | "
        f"{len(policy['new_critical_violations'])} nuevas |",
        f"| Éxito (delta macro candidato - baseline) | `{success['gate']}` | punto "
        f"{_rate(success['point_estimate'])}, IC 95 % "
        f"{'—' if interval is None else f'[{interval[0]:.4f}, {interval[1]:.4f}]'}"
        f"{'; ' + success['reason'] if success.get('reason') else ''} |",
        f"| Latencia p95 | `{latency['gate']}` | baseline {latency['baseline_p95']}, candidato "
        f"{latency['candidate_p95']} ms |",
        "",
        f"Pares: both_pass {counts['both_pass']}, both_fail {counts['both_fail']}, new_failure "
        f"{counts['new_failure']}, fixed {counts['fixed']}, incomplete {counts['incomplete']}.",
        "",
    ]
    if policy["new_critical_violations"]:
        lines += ["## Violaciones críticas nuevas", ""]
        for v in policy["new_critical_violations"]:
            refs = ", ".join(f"`{r.get('event_id')}`" for r in v["evidence_refs"]) or "—"
            lines.append(
                f"- {v['slug']} rep {v['repetition']} (seed {v['seed']}): run "
                f"`{v['candidate_run_id']}`, evaluación `{v['candidate_evaluation_id']}`, "
                f"eventos {refs}"
            )
        lines.append("")
    if document["incomplete_pairs"]:
        lines += ["## Pares incompletos (no imputados)", ""]
        for p in document["incomplete_pairs"]:
            lines.append(f"- {p['slug']} rep {p['repetition']}: {', '.join(p['reasons'])}")
        lines.append("")
    lines += [
        "## Por escenario",
        "",
        "| Categoría | Escenario | Pares | Baseline | Candidato | Delta | new_failure | fixed "
        "| Violaciones nuevas |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in document["by_scenario"]:
        lines.append(
            f"| {row['category']} | {row['slug']} | {row['pairs']} | {_rate(row['baseline_rate'])} "
            f"| {_rate(row['candidate_rate'])} | {_rate(row['delta'])} | {row['new_failure']} | "
            f"{row['fixed']} | {row['new_critical_violations']} |"
        )
    lines.append("")
    return "\n".join(lines) + "\n"
