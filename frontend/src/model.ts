// Lógica pura del dashboard (probada sin DOM): estados, filtros y rutas.
import type { CellRow, RunMode, ScoreStatus } from "./api/types";

export const SCORE_STATUSES: readonly ScoreStatus[] = [
  "pass",
  "fail",
  "unknown",
  "not_applicable",
  "error",
];

export interface StatusDisplay {
  key: ScoreStatus | "unevaluated";
  label: string;
  title: string;
}

// Cada estado tiene etiqueta y explicación propias: N/A, unknown y error nunca se funden.
const DISPLAY: Record<StatusDisplay["key"], StatusDisplay> = {
  pass: { key: "pass", label: "pass", title: "Cumple la métrica" },
  fail: { key: "fail", label: "fail", title: "No cumple la métrica" },
  unknown: {
    key: "unknown",
    label: "unknown",
    title: "No se pudo determinar (evidencia ausente o traza incompleta); cuenta en N",
  },
  not_applicable: {
    key: "not_applicable",
    label: "N/A",
    title: "No aplica a este escenario (denominador 0); no entra en N",
  },
  error: {
    key: "error",
    label: "error",
    title: "Falló el evaluador; en agregados cuenta como unknown",
  },
  unevaluated: {
    key: "unevaluated",
    label: "sin evaluar",
    title: "Celda sin run o sin evaluación completada; cuenta como unknown en N",
  },
};

export function statusDisplay(status: ScoreStatus | null): StatusDisplay {
  return DISPLAY[status ?? "unevaluated"];
}

export function formatRate(value: number | null | undefined, digits = 3): string {
  return value === null || value === undefined ? "—" : value.toFixed(digits);
}

export function formatRange(range: [number, number] | null): string {
  return range === null ? "—" : `[${formatRate(range[0])}, ${formatRate(range[1])}]`;
}

export const ALL = "all";

export interface CellFilter {
  category: string;
  agent: string;
  status: ScoreStatus | "unevaluated" | typeof ALL;
}

export const EMPTY_FILTER: CellFilter = { category: ALL, agent: ALL, status: ALL };

export function filterCells(cells: readonly CellRow[], filter: CellFilter): CellRow[] {
  return cells.filter(
    (cell) =>
      (filter.category === ALL || cell.category === filter.category) &&
      (filter.agent === ALL || cell.agent.id === filter.agent) &&
      (filter.status === ALL || statusDisplay(cell.task_success).key === filter.status),
  );
}

export function categoriesOf(cells: readonly CellRow[]): string[] {
  return [...new Set(cells.map((cell) => cell.category))].sort();
}

/** Recuento por estado; incluye ceros para que todas las columnas sean visibles. */
export function countStatuses(cells: readonly CellRow[]): Record<StatusDisplay["key"], number> {
  const counts: Record<StatusDisplay["key"], number> = {
    pass: 0,
    fail: 0,
    unknown: 0,
    not_applicable: 0,
    error: 0,
    unevaluated: 0,
  };
  for (const cell of cells) {
    counts[statusDisplay(cell.task_success).key] += 1;
  }
  return counts;
}

export type Route =
  | { view: "experiments" }
  | { view: "experiment"; id: string; mode: RunMode }
  | { view: "run"; id: string; eventId: string | null }
  | { view: "not_found"; hash: string };

const UUID = "[0-9a-fA-F-]{36}";

export function parseRoute(hash: string): Route {
  const path = hash.replace(/^#/, "") || "/";
  const [pathname = "/", query = ""] = path.split("?", 2);
  const params = new URLSearchParams(query);
  if (pathname === "/" || pathname === "/experiments") {
    return { view: "experiments" };
  }
  const experiment = new RegExp(`^/experiments/(${UUID})$`).exec(pathname);
  if (experiment?.[1]) {
    return {
      view: "experiment",
      id: experiment[1],
      mode: params.get("mode") === "replay" ? "replay" : "live",
    };
  }
  const run = new RegExp(`^/runs/(${UUID})(?:/events/(${UUID}))?$`).exec(pathname);
  if (run?.[1]) {
    return { view: "run", id: run[1], eventId: run[2] ?? null };
  }
  return { view: "not_found", hash };
}

export const href = {
  experiments: () => "#/experiments",
  experiment: (id: string, mode: RunMode = "live") =>
    mode === "live" ? `#/experiments/${id}` : `#/experiments/${id}?mode=${mode}`,
  run: (id: string) => `#/runs/${id}`,
  event: (runId: string, eventId: string) => `#/runs/${runId}/events/${eventId}`,
};
