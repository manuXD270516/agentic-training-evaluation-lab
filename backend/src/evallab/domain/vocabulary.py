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
)

TRACE_COMPLETENESS = ("complete", "incomplete", "invalid")

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
