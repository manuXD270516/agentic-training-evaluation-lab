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
