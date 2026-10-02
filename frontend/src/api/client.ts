import type {
  CellsResponse,
  ComparisonSummary,
  Evaluation,
  Experiment,
  ExperimentReport,
  Run,
  RunMode,
  Snapshot,
  Trace,
  TraceEvent,
} from "./types";

// El dashboard es de sólo lectura: este cliente no expone ningún método de escritura.
export const API_BASE = "/api";
export const TRACE_PAGE_SIZE = 50;

/**
 * Modo estático (GitHub Pages): sin backend, lee la instantánea JSON que genera
 * `evallab-export-static` con las reglas de la demo (sólo lecturas públicas, sin oráculos).
 */
export const STATIC_MODE = import.meta.env["VITE_EVALLAB_STATIC"] === "1";
export const DATA_BASE = `${import.meta.env.BASE_URL}data`;

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(status: number, code: string | null, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, {
    headers: { Accept: "application/json" },
    ...(signal ? { signal } : {}),
  });
  if (!response.ok) {
    let code: string | null = null;
    let message = `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { error?: { code?: string; message?: string } };
      code = body.error?.code ?? null;
      message = body.error?.message ?? message;
    } catch {
      // Cuerpo no JSON (p. ej. proxy sin API o fichero ausente): se conserva el código HTTP.
    }
    throw new ApiError(response.status, code, message);
  }
  return (await response.json()) as T;
}

const getJson = <T>(path: string, signal?: AbortSignal) =>
  fetchJson<T>(`${API_BASE}${path}`, signal);
const getStatic = <T>(path: string, signal?: AbortSignal) =>
  fetchJson<T>(`${DATA_BASE}/${path}`, signal);

export function traceQuery(afterSequence: number | null, limit: number): string {
  const params = new URLSearchParams({ limit: String(limit) });
  if (afterSequence !== null) {
    params.set("after_sequence", String(afterSequence));
  }
  return params.toString();
}

/** Página keyset sobre una traza completa, con la misma semántica que la API. */
export function pageTrace(full: Trace, afterSequence: number | null, limit: number): Trace {
  const remaining = full.events.filter(
    (event) => afterSequence === null || event.sequence > afterSequence,
  );
  const events = remaining.slice(0, limit);
  const hasMore = remaining.length > limit;
  return {
    ...full,
    events,
    page: {
      after_sequence: afterSequence,
      limit,
      returned: events.length,
      next_after_sequence: hasMore ? (events.at(-1)?.sequence ?? null) : null,
    },
  };
}

async function staticEvent(runId: string, eventId: string, signal?: AbortSignal) {
  const trace = await getStatic<Trace>(`runs/${runId}/trace.json`, signal);
  const event = trace.events.find((candidate) => candidate.event_id === eventId);
  if (!event) throw new ApiError(404, "not_found", "evento inexistente en la traza");
  return event;
}

const liveApi = {
  experiments: (signal?: AbortSignal) => getJson<Experiment[]>("/experiments", signal),
  experiment: (id: string, signal?: AbortSignal) =>
    getJson<Experiment>(`/experiments/${id}`, signal),
  report: (id: string, mode: RunMode, signal?: AbortSignal) =>
    getJson<ExperimentReport>(`/experiments/${id}/report?mode=${mode}`, signal),
  cells: (id: string, mode: RunMode, signal?: AbortSignal) =>
    getJson<CellsResponse>(`/experiments/${id}/cells?mode=${mode}`, signal),
  run: (id: string, signal?: AbortSignal) => getJson<Run>(`/runs/${id}`, signal),
  evaluations: (runId: string, signal?: AbortSignal) =>
    getJson<Evaluation[]>(`/runs/${runId}/evaluations`, signal),
  tracePage: (runId: string, afterSequence: number | null, limit = TRACE_PAGE_SIZE) =>
    getJson<Trace>(`/runs/${runId}/trace?${traceQuery(afterSequence, limit)}`),
  traceEvent: (runId: string, eventId: string, signal?: AbortSignal) =>
    getJson<TraceEvent>(`/runs/${runId}/trace/events/${eventId}`, signal),
  // La API calcula comparaciones bajo demanda (`GET /comparisons`); no hay índice.
  comparisons: async (): Promise<ComparisonSummary[]> => [],
  snapshot: async (): Promise<Snapshot | null> => null,
};

const staticApi: typeof liveApi = {
  experiments: (signal) => getStatic<Experiment[]>("experiments.json", signal),
  experiment: (id, signal) => getStatic<Experiment>(`experiments/${id}.json`, signal),
  report: (id, mode, signal) =>
    getStatic<ExperimentReport>(`experiments/${id}/report-${mode}.json`, signal),
  cells: (id, mode, signal) =>
    getStatic<CellsResponse>(`experiments/${id}/cells-${mode}.json`, signal),
  run: (id, signal) => getStatic<Run>(`runs/${id}.json`, signal),
  evaluations: (runId, signal) => getStatic<Evaluation[]>(`runs/${runId}/evaluations.json`, signal),
  tracePage: async (runId, afterSequence, limit = TRACE_PAGE_SIZE) =>
    pageTrace(await getStatic<Trace>(`runs/${runId}/trace.json`), afterSequence, limit),
  traceEvent: staticEvent,
  comparisons: () => getStatic<ComparisonSummary[]>("comparisons.json"),
  snapshot: () => getStatic<Snapshot>("snapshot.json"),
};

export const api = STATIC_MODE ? staticApi : liveApi;

/** Enlaces a los documentos versionados crudos (vista pública; nunca el oráculo). */
export const rawLinks = STATIC_MODE
  ? {
      manifest: (experimentId: string) => `${DATA_BASE}/experiments/${experimentId}/manifest.json`,
      scenario: (id: string, version: string) =>
        `${DATA_BASE}/scenarios/${id}/versions/${version}.json`,
      agent: (id: string, version: string) =>
        `${DATA_BASE}/agent-configurations/${id}/versions/${version}.json`,
      benchmark: (id: string, version: string) =>
        `${DATA_BASE}/benchmarks/${id}/versions/${version}.json`,
      traceExport: (runId: string) => `${DATA_BASE}/runs/${runId}/trace.json`,
      comparison: (id: string) => `${DATA_BASE}/comparisons/${id}.json`,
    }
  : {
      manifest: (experimentId: string) => `${API_BASE}/experiments/${experimentId}/manifest`,
      scenario: (id: string, version: string) => `${API_BASE}/scenarios/${id}/versions/${version}`,
      agent: (id: string, version: string) =>
        `${API_BASE}/agent-configurations/${id}/versions/${version}`,
      benchmark: (id: string, version: string) =>
        `${API_BASE}/benchmarks/${id}/versions/${version}`,
      traceExport: (runId: string) => `${API_BASE}/runs/${runId}/trace/export`,
      comparison: (id: string) => {
        const [baseline, candidate] = id.split("_");
        return `${API_BASE}/comparisons?baseline=${baseline}&candidate=${candidate}`;
      },
    };
