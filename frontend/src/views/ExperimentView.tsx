import { useState } from "react";
import { api, rawLinks } from "../api/client";
import type {
  AgentReport,
  CellRow,
  CellsResponse,
  ExperimentReport,
  GroupSummary,
  RunMode,
} from "../api/types";
import { Hash, Legend, Loadable, StatusBadge } from "../components";
import { useAsync } from "../hooks";
import {
  ALL,
  type CellFilter,
  EMPTY_FILTER,
  SCORE_STATUSES,
  categoriesOf,
  countStatuses,
  filterCells,
  formatRange,
  formatRate,
  href,
  statusDisplay,
} from "../model";

export function agentLabel(agent: AgentReport["agent"]): string {
  return `${agent.pattern}@${agent.version} · ${agent.id.slice(0, 8)}`;
}

function summaryFor(agent: AgentReport, category: string): GroupSummary | undefined {
  return category === ALL ? agent.summary : agent.by_category[category];
}

export function ReportHeader({ report }: { report: ExperimentReport }) {
  const { labels, dataset, benchmark, experiment } = report;
  return (
    <dl className="facts">
      <dt>Hipótesis</dt>
      <dd>{experiment.hypothesis}</dd>
      <dt>Manifest</dt>
      <dd>
        <a href={rawLinks.manifest(experiment.id)}>
          <Hash value={experiment.manifest_hash} />
        </a>
      </dd>
      <dt>Benchmark</dt>
      <dd>
        <a href={rawLinks.benchmark(benchmark.id, benchmark.version)}>
          {benchmark.version} <Hash value={benchmark.content_hash} />
        </a>
      </dd>
      <dt>Dataset</dt>
      <dd>
        {dataset.name}@{dataset.version} · cobertura <strong>{dataset.coverage_class}</strong>
      </dd>
      <dt>Modo</dt>
      <dd>{labels.mode}</dd>
      <dt>Análisis</dt>
      <dd>
        <strong>{labels.analysis}</strong> · afirmaciones estadísticas:{" "}
        <strong>{labels.statistical_claims}</strong> · {labels.reason}
      </dd>
      <dt>Celdas programadas</dt>
      <dd>{report.planned_cells}</dd>
    </dl>
  );
}

export function AgentSummaryTable({
  report,
  category,
}: {
  report: ExperimentReport;
  category: string;
}) {
  return (
    <table>
      <thead>
        <tr>
          <th>Agente</th>
          <th>Atribución</th>
          <th title="Celdas en N (excluye N/A)">N</th>
          <th>pass</th>
          <th>fail</th>
          <th title="Sin evaluar, unknown o error del evaluador">unknown</th>
          <th title="Éxitos / N, con unknown como fallo">Éxito conservador</th>
          <th>Cobertura</th>
          <th title="Rango si cada unknown fuese fallo o éxito">Rango por missingness</th>
        </tr>
      </thead>
      <tbody>
        {report.agents.map((agent) => {
          const summary = summaryFor(agent, category);
          const task = summary?.task_success;
          return (
            <tr key={agent.agent.id}>
              <td>{agentLabel(agent.agent)}</td>
              <td>
                <code>{agent.agent.attribution}</code>
              </td>
              <td>{task?.cells ?? 0}</td>
              <td>{task?.successes ?? 0}</td>
              <td>{task?.failures ?? 0}</td>
              <td>{task?.unknown ?? 0}</td>
              <td>{formatRate(task?.conservative_rate)}</td>
              <td>{formatRate(task?.coverage)}</td>
              <td>{formatRange(task?.missingness_range ?? null)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function MetricsTable({ agent }: { agent: AgentReport }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Métrica</th>
          {SCORE_STATUSES.map((status) => (
            <th key={status}>
              <StatusBadge status={status} />
            </th>
          ))}
          <th title="Suma de numeradores / suma de denominadores (sólo pass/fail)">Micro</th>
          <th title="Media de valores por run (sólo pass/fail)">Macro</th>
        </tr>
      </thead>
      <tbody>
        {Object.entries(agent.metrics).map(([metric, summary]) => (
          <tr key={metric}>
            <td>
              <code>{metric}</code>
            </td>
            {SCORE_STATUSES.map((status) => (
              <td key={status} className={`count-${status}`}>
                {summary.statuses[status]}
              </td>
            ))}
            <td>
              {formatRate(summary.micro.value)}{" "}
              <span className="muted">
                ({summary.micro.numerator}/{summary.micro.denominator})
              </span>
            </td>
            <td>
              {formatRate(summary.macro.value)}{" "}
              <span className="muted">(n={summary.macro.runs})</span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ConsumptionLine({ agent }: { agent: AgentReport }) {
  const { usage, judge_usage: judge, latency_ms: latency } = agent;
  const cost = usage.estimated_cost;
  return (
    <p className="muted">
      Tokens agente: {usage.tokens.status}
      {usage.tokens.total != null ? ` (${usage.tokens.total})` : ""} · coste estimado: {cost.status}
      {cost.amount != null ? ` (${cost.amount} ${cost.currency ?? ""})` : ""}
      {cost.synthetic_price ? " · precio sintético" : ""} · judge: {judge.calls} llamadas · latencia
      p50/p95: {latency.p50 ?? "—"}/{latency.p95 ?? "—"} ms (n={latency.n})
    </p>
  );
}

export function CellsTable({
  cells,
  agentNames,
}: {
  cells: CellRow[];
  agentNames: Record<string, string>;
}) {
  if (cells.length === 0) return <p className="muted">Ninguna celda con este filtro.</p>;
  return (
    <table>
      <thead>
        <tr>
          <th>Escenario</th>
          <th>Categoría</th>
          <th>Agente</th>
          <th>Rep.</th>
          <th>Run</th>
          <th>Traza</th>
          <th>task_success</th>
          <th>raw_outcome_pass</th>
        </tr>
      </thead>
      <tbody>
        {cells.map((cell) => (
          <tr
            key={`${cell.scenario.id}:${cell.agent.id}:${cell.repetition}`}
            className={cell.run_id === null ? "row-missing" : undefined}
          >
            <td>
              {cell.slug ?? cell.scenario.id.slice(0, 8)}@{cell.scenario.version}
            </td>
            <td>{cell.category}</td>
            <td>{agentNames[cell.agent.id] ?? cell.agent.id.slice(0, 8)}</td>
            <td>{cell.repetition}</td>
            <td>
              {cell.run_id ? (
                <a href={href.run(cell.run_id)}>
                  {cell.run_status}
                  {cell.error_class ? ` (${cell.error_class})` : ""}
                </a>
              ) : (
                <span className="muted">sin run</span>
              )}
            </td>
            <td>
              {cell.trace_completeness === null ? (
                <span className="muted">—</span>
              ) : cell.trace_completeness === "complete" ? (
                "complete"
              ) : (
                <strong className="warn">{cell.trace_completeness}</strong>
              )}
            </td>
            <td>
              <StatusBadge status={cell.task_success} />
            </td>
            <td>
              <StatusBadge status={cell.raw_outcome_pass} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function select(
  label: string,
  value: string,
  options: [string, string][],
  onChange: (value: string) => void,
) {
  return (
    <label>
      {label}{" "}
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map(([key, text]) => (
          <option key={key} value={key}>
            {text}
          </option>
        ))}
      </select>
    </label>
  );
}

export function ExperimentDashboard({
  report,
  cells,
  filter,
  onFilter,
}: {
  report: ExperimentReport;
  cells: CellsResponse;
  filter: CellFilter;
  onFilter: (filter: CellFilter) => void;
}) {
  const agentNames = Object.fromEntries(
    report.agents.map((a) => [a.agent.id, agentLabel(a.agent)]),
  );
  const categories = categoriesOf(cells.cells);
  const visible = filterCells(cells.cells, filter);
  const counts = countStatuses(visible);
  const mode = report.labels.mode;
  return (
    <>
      <ReportHeader report={report} />
      <nav className="filters" aria-label="Filtros">
        <span>
          Modo:{" "}
          {(["live", "replay"] as RunMode[]).map((m) =>
            m === mode ? (
              <strong key={m}>{m}</strong>
            ) : (
              <a key={m} href={href.experiment(report.experiment.id, m)}>
                {m}
              </a>
            ),
          )}
        </span>
        {select(
          "Categoría",
          filter.category,
          [[ALL, "todas"], ...categories.map((c): [string, string] => [c, c])],
          (category) => onFilter({ ...filter, category }),
        )}
        {select("Agente", filter.agent, [[ALL, "todos"], ...Object.entries(agentNames)], (agent) =>
          onFilter({ ...filter, agent }),
        )}
        {select(
          "task_success",
          filter.status,
          [
            [ALL, "todos"],
            ...[...SCORE_STATUSES, null].map((s): [string, string] => [
              statusDisplay(s).key,
              statusDisplay(s).label,
            ]),
          ],
          (status) => onFilter({ ...filter, status: status as CellFilter["status"] }),
        )}
      </nav>
      <h3>
        task_success por agente{" "}
        <span className="muted">
          ({filter.category === ALL ? "todas las categorías" : filter.category})
        </span>
      </h3>
      <AgentSummaryTable report={report} category={filter.category} />
      <h3>Métricas de proceso por agente</h3>
      <p className="muted">
        Agregadas sobre todas las celdas del modo {mode}: el reporte no las desglosa por categoría.
      </p>
      {report.agents.map((agent) => (
        <div key={agent.agent.id}>
          <h4>{agentLabel(agent.agent)}</h4>
          <MetricsTable agent={agent} />
          <ConsumptionLine agent={agent} />
        </div>
      ))}
      <h3>
        Celdas ({visible.length} de {cells.planned_cells} programadas)
      </h3>
      <p className="counts">
        {(Object.keys(counts) as (keyof typeof counts)[]).map((key) => (
          <span key={key}>
            <StatusBadge status={key === "unevaluated" ? null : key} /> {counts[key]}
          </span>
        ))}
      </p>
      <Legend />
      <CellsTable cells={visible} agentNames={agentNames} />
    </>
  );
}

export function ExperimentView({ id, mode }: { id: string; mode: RunMode }) {
  const [filter, setFilter] = useState<CellFilter>(EMPTY_FILTER);
  const result = useAsync(
    async (signal) => {
      const [report, cells] = await Promise.all([
        api.report(id, mode, signal),
        api.cells(id, mode, signal),
      ]);
      return { report, cells };
    },
    [id, mode],
  );
  return (
    <section>
      <p>
        <a href={href.experiments()}>← Experimentos</a>
      </p>
      <h2>Experimento {id.slice(0, 8)}</h2>
      <Loadable result={result}>
        {({ report, cells }) => (
          <ExperimentDashboard report={report} cells={cells} filter={filter} onFilter={setFilter} />
        )}
      </Loadable>
    </section>
  );
}
