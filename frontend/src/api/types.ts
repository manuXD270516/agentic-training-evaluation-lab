// Tipos de las respuestas de la API que usa el dashboard. Sólo los campos que se muestran; el
// resto del JSON se conserva sin tipar (`Json`) para no inventar estructura.

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

export type ScoreStatus = "pass" | "fail" | "unknown" | "not_applicable" | "error";
export type RunMode = "live" | "replay";

export interface VersionRef {
  id: string;
  version: string;
}

export interface Experiment {
  id: string;
  status: string;
  hypothesis: string;
  benchmark: VersionRef | null;
  agents: VersionRef[];
  repetitions: number;
  seeds: number[];
  manifest_hash: string | null;
  created_at: string;
  sealed_at: string | null;
}

export interface SuccessSummary {
  cells: number;
  successes: number;
  failures: number;
  unknown: number;
  conservative_rate: number | null;
  coverage: number | null;
  evaluable_rate: number | null;
  missingness_range: [number, number] | null;
}

export interface GroupSummary {
  cells: number;
  run_statuses: Record<string, number>;
  evaluated: number;
  task_success: SuccessSummary;
  raw_outcome_pass: SuccessSummary;
}

export interface MetricSummary {
  statuses: Record<ScoreStatus, number>;
  micro: { numerator: number; denominator: number; value: number | null };
  macro: { runs: number; value: number | null };
}

export interface Consumption {
  status: string;
  total?: number | null;
  amount?: string | null;
  known_subtotal?: number | string;
  currency?: string;
  synthetic_price?: boolean;
  reason?: string | null;
}

export interface AgentReport {
  agent: {
    id: string;
    version: string;
    content_hash: string | null;
    pattern: string;
    pattern_version: string | null;
    attribution: string;
  };
  summary: GroupSummary;
  by_category: Record<string, GroupSummary>;
  metrics: Record<string, MetricSummary>;
  usage: {
    runs_with_usage: number;
    planned_cells: number;
    totals: Record<string, number>;
    tokens: Consumption;
    estimated_cost: Consumption;
  };
  judge_usage: { calls: number; tokens: Consumption; estimated_cost: Consumption };
  latency_ms: { n: number; coverage: number | null; p50: number | null; p95: number | null };
}

export interface ExperimentReport {
  report_version: string;
  experiment: { id: string; status: string; hypothesis: string; manifest_hash: string };
  benchmark: VersionRef & { content_hash: string | null };
  dataset: {
    name: string | null;
    version: string | null;
    content_hash: string | null;
    coverage_class: string | null;
  };
  labels: {
    cohort: string | null;
    mode: RunMode;
    analysis: string;
    statistical_claims: string;
    reason: string;
  };
  metric_profile: Record<string, Json>;
  planned_cells: number;
  scenarios_per_category: Record<string, number>;
  agents: AgentReport[];
}

export interface CellRow {
  scenario: VersionRef;
  slug: string | null;
  category: string;
  agent: VersionRef;
  repetition: number;
  seed: number;
  run_id: string | null;
  run_status: string;
  error_class: string | null;
  trace_completeness: string | null;
  evaluation_id: string | null;
  evaluation_status: string | null;
  task_success: ScoreStatus | null;
  raw_outcome_pass: ScoreStatus | null;
  latency_ms: number | null;
}

export interface CellsResponse {
  experiment_id: string;
  manifest_hash: string;
  mode: RunMode;
  planned_cells: number;
  cells: CellRow[];
}

export interface Run {
  id: string;
  experiment_id: string;
  scenario: VersionRef;
  agent: VersionRef;
  repetition: number;
  seed: number;
  mode: RunMode;
  source_run_id: string | null;
  status: string;
  error_class: string | null;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  result: { [key: string]: Json } | null;
}

export interface TraceEvent {
  event_id: string;
  sequence: number;
  timestamp_utc: string;
  elapsed_ms: number;
  type: string;
  actor_role: string;
  parent_event_id: string | null;
  payload: { [key: string]: Json };
  payload_digest: string;
  redaction_metadata: { [key: string]: Json };
}

export interface TracePage {
  after_sequence: number | null;
  limit: number;
  returned: number;
  next_after_sequence: number | null;
}

export interface Trace {
  run_id: string;
  schema_version: string;
  event_count: number;
  digest: string | null;
  completeness: string | null;
  sealed_at: string | null;
  events: TraceEvent[];
  page: TracePage | null;
}

export interface EvidenceRef {
  event_id?: string;
  pointer?: string;
  [key: string]: Json | undefined;
}

export interface Score {
  metric_id: string;
  metric_version: string;
  scope: string;
  status: ScoreStatus;
  unit: string;
  value: number | null;
  numerator: number | null;
  denominator: number | null;
  evidence_refs: EvidenceRef[];
}

export interface Evaluation {
  id: string;
  run_id: string;
  trace_digest: string;
  evaluator_suite_hash: string;
  evaluator_suite_version: string | null;
  metric_profile_version: string | null;
  metric_profile_hash: string | null;
  parent_evaluation_id: string | null;
  status: string;
  error: string | null;
  scores: Score[];
  created_at: string;
  completed_at: string | null;
}

export interface ComparisonSummary {
  id: string;
  baseline: string;
  candidate: string;
  status: string;
  decision: { status: string; reasons: string[] };
  export_digest: string;
}

export interface Snapshot {
  generated_at: string;
  commit: string | null;
  access: string;
  counts: Record<string, number>;
  note: string;
}
