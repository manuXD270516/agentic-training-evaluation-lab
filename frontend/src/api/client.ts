import type {
  CellsResponse,
  Evaluation,
  Experiment,
  ExperimentReport,
  Run,
  RunMode,
  Trace,
  TraceEvent,
} from "./types";

// El dashboard es de sólo lectura: este cliente no expone ningún método de escritura.
export const API_BASE = "/api";
export const TRACE_PAGE_SIZE = 50;

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(status: number, code: string | null, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
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
      // Cuerpo no JSON (p. ej. proxy sin API): se conserva el código HTTP.
    }
    throw new ApiError(response.status, code, message);
  }
  return (await response.json()) as T;
}

export function traceQuery(afterSequence: number | null, limit: number): string {
  const params = new URLSearchParams({ limit: String(limit) });
  if (afterSequence !== null) {
    params.set("after_sequence", String(afterSequence));
  }
  return params.toString();
}

export const api = {
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
};

/** Enlaces a los documentos versionados crudos (vista pública; nunca el oráculo). */
export const rawLinks = {
  manifest: (experimentId: string) => `${API_BASE}/experiments/${experimentId}/manifest`,
  scenario: (id: string, version: string) => `${API_BASE}/scenarios/${id}/versions/${version}`,
  agent: (id: string, version: string) =>
    `${API_BASE}/agent-configurations/${id}/versions/${version}`,
  benchmark: (id: string, version: string) => `${API_BASE}/benchmarks/${id}/versions/${version}`,
  traceExport: (runId: string) => `${API_BASE}/runs/${runId}/trace/export`,
};
