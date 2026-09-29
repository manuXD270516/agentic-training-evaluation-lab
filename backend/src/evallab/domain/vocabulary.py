"""Vocabularios cerrados de design.md, benchmark-format.md y metrics.md.

Las migraciones copian estos valores en restricciones CHECK; un test de integración
comprueba que ambas fuentes coinciden.
"""

PRIMARY_CATEGORIES = (
    "tool_selection",
    "tool_arguments",
    "retrieval",
    "reasoning",
    "multi_step_execution",
    "error_recovery",
    "policy_compliance",
)

RUN_MODES = ("live", "replay")

AGENT_PATTERNS = ("scripted", "react", "planner_executor")

TOOL_EFFECT_CLASSES = ("read_only", "side_effect")

SEED_SUPPORT = ("supported", "unsupported", "unknown")

RUN_ERROR_CLASSES = (
    "invalid_arguments",
    "denied",
    "tool_timeout",
    "transient_tool_error",
    "model_error",
    "infrastructure_error",
    "trace_error",
    "replay_mismatch",
)

TRACE_COMPLETENESS = ("complete", "incomplete", "invalid")

RUN_ATTEMPT_STATUSES = ("active", "finished", "expired", "rejected")

TRACE_SCHEMA_VERSION = "1.0"

TRACE_ACTOR_ROLES = ("executor", "harness", "planner")

TRACE_EVENT_TYPES = (
    "model.completed",
    "model.failed",
    "model.requested",
    "plan.created",
    "policy.violation",
    "retrieval.completed",
    "retry.scheduled",
    "run.budget_exceeded",
    "run.cancelled",
    "run.completed",
    "run.failed",
    "run.started",
    "run.timed_out",
    "step.completed",
    "step.started",
    "tool.completed",
    "tool.denied",
    "tool.failed",
    "tool.requested",
    "tool.validated",
)

SCORE_STATUSES = ("pass", "fail", "unknown", "not_applicable", "error")

SCORE_SCOPES = ("agent", "judge", "harness")

SCENARIO_SCHEMA_VERSION = "1.0"

SPLITS = ("dev", "held-out")

DIFFICULTIES = ("easy", "medium", "hard")

COVERAGE_CLASSES = ("pilot", "incomplete", "complete_v1")

ORACLE_OPERATORS = (
    "json_value_equals",
    "json_value_numeric_equals",
    "required_tool",
    "forbidden_tool",
    "arguments_equal",
    "evidence_from_successful_call",
    "output_schema_valid",
    "abstention_required",
)

REQUIRED_CHECKS = (
    "outcome",
    "output_structure",
    "required_tool",
    "semantic_arguments",
    "evidence",
    "policy",
    "retrieval",
    "recovery",
)

APPLICABLE_METRICS = (
    "task_success",
    "task_success_rate",
    "raw_outcome_pass",
    "raw_outcome_pass_rate",
    "tool_accuracy",
    "required_tool_coverage",
    "argument_accuracy",
    "schema_argument_validity",
    "retrieval_recall_at_k",
    "retrieval_mrr_at_k",
    "hallucination_rate",
    "evidence_coverage",
    "latency_ms",
    "tokens",
    "estimated_cost",
    "number_of_steps",
    "recovery_success",
    "policy_violation_rate",
)
