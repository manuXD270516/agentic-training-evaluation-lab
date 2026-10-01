"""Render Markdown del reporte descriptivo: mismas cifras que el JSON, sin interpretación extra."""

from __future__ import annotations

from typing import Any


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _range(summary: dict[str, Any]) -> str:
    bounds = summary.get("missingness_range")
    return "N/A" if bounds is None else f"[{_fmt(bounds[0])}, {_fmt(bounds[1])}]"


def _success_row(label: str, summary: dict[str, Any]) -> str:
    task = summary["task_success"]
    raw = summary["raw_outcome_pass"]
    return (
        f"| {label} | {task['cells']} | {task['successes']} | {task['failures']} | "
        f"{task['unknown']} | {_fmt(task['conservative_rate'])} | {_fmt(task['coverage'])} | "
        f"{_fmt(task['evaluable_rate'])} | {_range(task)} | {_fmt(raw['conservative_rate'])} |"
    )


def _tokens(tokens: dict[str, Any]) -> str:
    if tokens["status"] == "not_applicable":
        return f"`not_applicable` ({tokens.get('reason', 'sin partes aplicables')})"
    unknown = tokens.get("unknown_usage_calls")
    suffix = f", llamadas sin uso {unknown}" if unknown is not None else ""
    return (
        f"`{tokens['status']}`, total {_fmt(tokens.get('total'))}, subtotal conocido "
        f"{tokens['known_subtotal']}{suffix}"
    )


def _cost(cost: dict[str, Any]) -> str:
    if cost["status"] == "not_applicable":
        return f"`not_applicable` ({cost.get('reason', 'sin partes aplicables')})"
    reason = cost.get("reason") or (
        "precio sintético: no es un coste real" if cost.get("synthetic_price") else None
    )
    note = f" — {reason}" if reason else ""
    return (
        f"`{cost['status']}`, {_fmt(cost.get('amount'))} {cost.get('currency', 'USD')} "
        f"(subtotal conocido {cost['known_subtotal']}){note}"
    )


SUCCESS_HEADER = (
    "| Grupo | N | S | Fallos | U | S/N | Cobertura | S/(N-U) | Rango missingness | "
    "raw_outcome S/N |\n|---|---|---|---|---|---|---|---|---|---|"
)


def _recovery(recovery: dict[str, Any] | None) -> str:
    if not recovery or recovery.get("status") == "not_applicable":
        return "N/A (sin casos de recuperación programados)"
    success = recovery["recovery_success"]
    value = "N/A (sin exposición)" if success["value"] is None else _fmt(success["value"])
    return (
        f"expuestos {recovery['exposed']} de {recovery['programmed']} programados "
        f"(exposure_rate {_fmt(recovery['exposure_rate'])}); recovery_success {value}"
    )


def render(report: dict[str, Any], *, title: str, names: dict[str, str] | None = None) -> str:
    labels = report["labels"]
    dataset = report["dataset"]
    lines = [
        f"# {title}",
        "",
        f"> **Cohorte `{labels['cohort']}`, análisis `{labels['analysis']}`, afirmaciones "
        f"estadísticas: `{labels['statistical_claims']}`.** {labels['reason']}.",
        "",
        f"- Dataset: `{dataset['name']}@{dataset['version']}` (`{dataset['content_hash']}`), "
        f"coverage_class `{dataset['coverage_class']}`",
        f"- Benchmark: `{report['benchmark']['id']}@{report['benchmark']['version']}` "
        f"(`{report['benchmark']['content_hash']}`)",
        f"- Manifest del experimento: `{report['experiment']['manifest_hash']}`; repeticiones "
        f"{report['experiment']['repetitions']}, seeds {report['experiment']['seeds']}",
        f"- Celdas programadas: {report['planned_cells']}; escenarios por categoría: "
        f"{report['scenarios_per_category']}",
        f"- Perfil de métricas: `{report['metric_profile']['profile_id']}@"
        f"{report['metric_profile']['version']}`",
        "",
    ]
    for agent in report["agents"]:
        info = agent["agent"]
        name = (names or {}).get(info["id"], info["id"])
        lines += [
            f"## Agente `{name}@{info['version']}` ({info['pattern']}, "
            f"atribución `{info['attribution']}`)",
            "",
            f"id `{info['id']}`, content_hash `{info['content_hash']}`.",
            "",
            f"Estados de run: {agent['summary']['run_statuses']}; evaluadas "
            f"{agent['summary']['evaluated']} de {agent['summary']['task_success']['cells']}.",
            "",
            "### task_success",
            "",
            SUCCESS_HEADER,
            _success_row("total", agent["summary"]),
            *(_success_row(cat, summary) for cat, summary in agent["by_category"].items()),
            "",
            "### Métricas de proceso (micro por unidad, macro por run aplicable)",
            "",
            "| Métrica | pass | fail | unknown | N/A | error | micro (num/den) | macro (runs) |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for metric, data in agent["metrics"].items():
            s = data["statuses"]
            micro = data["micro"]
            macro = data["macro"]
            lines.append(
                f"| {metric} | {s['pass']} | {s['fail']} | {s['unknown']} | "
                f"{s['not_applicable']} | {s['error']} | {_fmt(micro['value'])} "
                f"({micro['numerator']}/{micro['denominator']}) | {_fmt(macro['value'])} "
                f"({macro['runs']}) |"
            )
        usage = agent["usage"]
        latency = agent["latency_ms"]
        lines += [
            "",
            "### Consumo y latencia (incluye runs fallidos)",
            "",
            f"- Runs con uso registrado: {usage['runs_with_usage']} de {usage['planned_cells']}",
            f"- Totales: {usage['totals']}",
            f"- Tokens: {_tokens(usage['tokens'])}; coste estimado: "
            f"{_cost(usage['estimated_cost'])}",
            f"- Judge (scope judge, no se suma al agente): {agent['judge_usage']['calls']} "
            f"llamadas; tokens {_tokens(agent['judge_usage']['tokens'])}; coste "
            f"{_cost(agent['judge_usage']['estimated_cost'])}",
            f"- Total agente + judge: tokens {_tokens(agent['total_consumption']['tokens'])}; "
            f"coste {_cost(agent['total_consumption']['estimated_cost'])}",
            f"- latency_ms del runner: n={latency['n']}, cobertura {_fmt(latency['coverage'])}, "
            f"media {_fmt(latency['mean'], 1)}, p50 {_fmt(latency['p50'], 1)}, "
            f"p95 {_fmt(latency['p95'], 1)} ({latency['method']})",
            f"- Recuperación: {_recovery(agent.get('recovery'))}",
            "",
            "### Por escenario",
            "",
            "| Categoría | Escenario | task_success | raw_outcome_pass | Salidas distintas |",
            "|---|---|---|---|---|",
        ]
        for row in agent["by_scenario"]:
            lines.append(
                f"| {row['category']} | {row['slug']} | {row['task_success']} | "
                f"{row['raw_outcome_pass']} | {row['distinct_outputs']} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"
