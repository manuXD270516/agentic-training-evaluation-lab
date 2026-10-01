// Datos de prueba con la forma de las respuestas reales de la API (sólo para tests).
import type {
  AgentReport,
  CellRow,
  Evaluation,
  ExperimentReport,
  GroupSummary,
  MetricSummary,
  ScoreStatus,
  Trace,
} from "../api/types";

export const RUN = "11111111-1111-4111-8111-111111111111";
export const EVENT = "22222222-2222-4222-8222-222222222222";
export const EXPERIMENT = "33333333-3333-4333-8333-333333333333";

export function cell(
  category: string,
  agent: string,
  task: ScoreStatus | null,
  extra: Partial<CellRow> = {},
): CellRow {
  return {
    scenario: { id: `${category}-${agent}`, version: "1.0.0" },
    slug: `${category}-case`,
    category,
    agent: { id: agent, version: "1.0.0" },
    repetition: 1,
    seed: 11,
    run_id: task === null ? null : RUN,
    run_status: task === null ? "missing" : "completed",
    error_class: null,
    trace_completeness: task === null ? null : "complete",
    evaluation_id: null,
    evaluation_status: task === null ? null : "completed",
    task_success: task,
    raw_outcome_pass: task,
    latency_ms: null,
    ...extra,
  };
}

function group(successes: number, failures: number, unknown: number): GroupSummary {
  const cells = successes + failures + unknown;
  const summary = {
    cells,
    successes,
    failures,
    unknown,
    conservative_rate: cells ? successes / cells : null,
    coverage: cells ? (cells - unknown) / cells : null,
    evaluable_rate: cells - unknown ? successes / (cells - unknown) : null,
    missingness_range: cells
      ? ([successes / cells, (successes + unknown) / cells] as [number, number])
      : null,
  };
  return {
    cells,
    run_statuses: { completed: cells - unknown, missing: unknown },
    evaluated: cells - unknown,
    task_success: summary,
    raw_outcome_pass: summary,
  };
}

const metric: MetricSummary = {
  statuses: { pass: 4, fail: 1, unknown: 2, not_applicable: 3, error: 1 },
  micro: { numerator: 8, denominator: 10, value: 0.8 },
  macro: { runs: 5, value: 0.75 },
};

export const agentReport: AgentReport = {
  agent: {
    id: "agent-a",
    version: "1.0.0",
    content_hash: "a".repeat(64),
    pattern: "scripted",
    pattern_version: "1.0.0",
    attribution: "harness_baseline",
  },
  summary: group(7, 2, 1),
  by_category: { reasoning: group(4, 0, 1), retrieval: group(3, 2, 0) },
  metrics: { tool_accuracy: metric },
  usage: {
    runs_with_usage: 9,
    planned_cells: 10,
    totals: { steps: 20, tool_calls: 12, model_calls: 0, retries: 1 },
    tokens: { status: "not_applicable", reason: "sin llamadas a modelo" },
    estimated_cost: { status: "not_applicable", currency: "USD" },
  },
  judge_usage: {
    calls: 0,
    tokens: { status: "not_applicable" },
    estimated_cost: { status: "not_applicable" },
  },
  latency_ms: { n: 9, coverage: 0.9, p50: 12, p95: 30 },
};

export const report: ExperimentReport = {
  report_version: "1.0.0",
  experiment: {
    id: EXPERIMENT,
    status: "completed",
    hypothesis: "piloto",
    manifest_hash: "b".repeat(64),
  },
  benchmark: { id: "bench", version: "1.0.0", content_hash: "c".repeat(64) },
  dataset: {
    name: "pilot",
    version: "1.0.0",
    content_hash: "d".repeat(64),
    coverage_class: "pilot",
  },
  labels: {
    cohort: "pilot",
    mode: "live",
    analysis: "descriptive_only",
    statistical_claims: "none",
    reason: "2 escenarios por categoría (< 5): sin intervalos ni superioridad",
  },
  metric_profile: {},
  planned_cells: 10,
  scenarios_per_category: { reasoning: 1, retrieval: 1 },
  agents: [agentReport],
};

export const evaluation: Evaluation = {
  id: "44444444-4444-4444-8444-444444444444",
  run_id: RUN,
  trace_digest: "e".repeat(64),
  evaluator_suite_hash: "f".repeat(64),
  evaluator_suite_version: "1.0.0",
  metric_profile_version: "1.0.0",
  metric_profile_hash: "0".repeat(64),
  parent_evaluation_id: null,
  status: "completed",
  error: null,
  created_at: "2026-10-01T00:00:00Z",
  completed_at: "2026-10-01T00:00:01Z",
  scores: [
    {
      metric_id: "tool_accuracy",
      metric_version: "1.0.0",
      scope: "agent",
      status: "pass",
      unit: "ratio",
      value: 1,
      numerator: 2,
      denominator: 2,
      evidence_refs: [{ event_id: EVENT, pointer: "/tool" }],
    },
    {
      metric_id: "evidence_coverage",
      metric_version: "1.0.0",
      scope: "agent",
      status: "not_applicable",
      unit: "ratio",
      value: null,
      numerator: 0,
      denominator: 0,
      evidence_refs: [],
    },
  ],
};

export function trace(completeness: string): Trace {
  return {
    run_id: RUN,
    schema_version: "1.0.0",
    event_count: 3,
    digest: completeness === "complete" ? "9".repeat(64) : null,
    completeness,
    sealed_at: null,
    page: { after_sequence: null, limit: 2, returned: 2, next_after_sequence: 2 },
    events: [
      {
        event_id: EVENT,
        sequence: 1,
        timestamp_utc: "2026-10-01T00:00:00Z",
        elapsed_ms: 0,
        type: "run.started",
        actor_role: "harness",
        parent_event_id: null,
        payload: {},
        payload_digest: "1".repeat(64),
        redaction_metadata: {},
      },
    ],
  };
}
