# Auditoría de aceptación (tarea 13.4)

Cada escenario de las ocho capabilities de `define-evaluation-lab-foundation`, con la prueba que lo verifica. Todos los tests citados están en `backend/tests/` (`integration/` usa PostgreSQL real) o en `frontend/src/`. Estado al 2026-10-02: 414 tests de backend y 18 de frontend en verde; `openspec validate --all --strict` sin errores.

Estados: **verificado** (un test lo comprueba tal como lo describe la spec), **parcial** (verificado con una diferencia o un alcance menor, explicado), **pendiente** (no se puede verificar sin una decisión o una ejecución que no se ha hecho).

## agent-execution

| Escenario | Evidencia | Estado |
|---|---|---|
| Pattern substitution | `integration/test_planner_executor_pilot.py::test_plan_partial_order_roles_and_summed_consumption`, `test_planner_executor.py::test_global_model_call_budget_spans_both_roles`, `integration/test_comparison_descriptive.py` | verificado (modelos de fixture) |
| Failed plan dependency | `test_planner_executor.py::test_failed_dependency_skips_the_dependent_step` | verificado |
| Invalid arguments | `test_tools.py::test_invalid_arguments_are_not_executed`, `test_runner.py::test_invalid_arguments_are_kept_in_trace_and_not_executed` | verificado |
| Forbidden tool | `test_tools.py::test_forbidden_tool_is_denied_without_execution`, `integration/test_execution.py::test_forbidden_tool_and_invalid_arguments_are_traced_not_executed` | verificado |
| Isolated fixture state | `test_tools.py::test_side_effect_state_is_isolated_per_gateway`, `integration/test_execution.py::test_fixture_state_is_not_shared_between_runs` | verificado |
| Budget exhausted | `integration/test_execution.py::test_step_budget_stops_before_next_call`, `::test_tool_call_budget_stops_before_request`, `test_model_gateway.py::test_token_and_cost_limits_stop_before_calling` | verificado |
| Monetary limit without price | `integration/test_api_agents.py::test_monetary_limit_requires_price_on_every_model` | verificado |
| Ambiguous side effect | `integration/test_execution.py::test_ambiguous_side_effect_is_not_retried` | verificado |
| Transient failure retried within limit | `integration/test_execution.py::test_transient_fault_is_retried_and_traced`, `::test_retries_stop_at_scenario_maximum` | verificado |
| Expired worker without duplicate effects | `integration/test_execution.py::test_expired_worker_cannot_persist_duplicate_effects` | verificado |
| Scripted pilot | `integration/test_pilot.py::test_reference_passes_and_faulty_mutations_are_detected` | verificado |
| Scripted live without model | `test_runner.py::test_scripted_run_emits_tool_evidence_without_model_calls` | verificado |
| Deferred patterns are not presented as implemented | `test_runner.py::test_deferred_pattern_is_not_presented_as_implemented`, `integration/test_execution.py::test_model_pattern_without_models_is_not_reported_as_executed` | verificado |

## evaluation-engine

| Escenario | Evidencia | Estado |
|---|---|---|
| Correct answer with forbidden action | `test_evaluation.py::test_correct_answer_with_forbidden_tool_fails_task` | verificado |
| Evaluator failure | `test_evaluation.py::test_evaluator_error_is_never_a_pass`, `integration/test_evaluations.py::test_evaluator_crash_is_persisted_as_error` | verificado |
| Valid JSON wrong units | `test_evaluation.py::test_valid_json_with_wrong_units_fails_semantic_arguments` | verificado |
| Unverifiable claim | `test_metrics.py::test_unverifiable_argument_semantics_is_unknown`, `test_judge.py::test_abstention_is_unknown_without_rating` | verificado |
| Output mutation | `test_evaluation.py::test_output_mutations_must_fail` | verificado |
| Nonexistent citation | `test_evaluation.py::test_nonexistent_citation_fails` | verificado |
| Unsupported citation | `test_evaluation.py::test_citation_of_non_result_event_is_unsupported`, `::test_citation_from_another_tool_is_unsupported` | verificado |
| Judge disagrees with oracle | `test_judge.py::test_judge_never_overrides_failed_gates`, `integration/test_judge.py::test_judge_score_is_separate_and_never_overrides_gates` | verificado (judge de fixture) |
| Injected judge instructions | `test_judge.py::test_injected_instructions_are_data_and_get_no_credit` | verificado |
| Calibration below threshold | `test_calibration.py::test_without_human_annotations_the_judge_stays_experimental`, `::test_threshold_and_injection_rule_on_test_annotations` | verificado: sin anotaciones humanas adjudicadas (o con acuerdo < 0.80 o una inyección aprobada) el judge es `experimental` y no controla gates; la anotación humana real es trabajo operativo de `run-live-and-calibrate-judge` |

## experiment-comparison

| Escenario | Evidencia | Estado |
|---|---|---|
| Different dataset versions | `test_comparison_protocol.py::test_comparability_rejects_dataset_mode_and_suite_differences` | verificado |
| Mixed execution modes | `integration/test_comparison_protocol.py::test_mixed_modes_are_rejected_and_ranking_blocked` | verificado |
| Small pilot | `test_comparison_protocol.py::test_pilot_sized_sample_is_descriptive_only`, `integration/test_pilot_report.py::test_five_repetitions_cover_every_cell` | verificado |
| Aggregate masks policy regression | `integration/test_comparison_protocol.py::test_new_critical_violation_fails_although_success_improves` | verificado |
| Missing candidate run | `integration/test_comparison_protocol.py::test_missing_candidate_cell_makes_the_comparison_incomplete` | verificado |

## experiment-management

| Escenario | Evidencia | Estado |
|---|---|---|
| Configuration changed after seal | `integration/test_api_experiments.py::test_sealed_experiment_rejects_edits` | verificado |
| Published configuration mutation | `integration/test_api_experiments.py::test_published_configurations_are_immutable`, `::test_published_configuration_cannot_gain_tools_later` | verificado |
| Seal with unresolved references | `integration/test_api_experiments.py::test_seal_requires_resolved_references` | verificado |
| Reopening a terminal run | `integration/test_lifecycle_db.py::test_reopening_a_terminal_run_is_rejected` | verificado |
| Cancellation before start | `test_lifecycle.py::test_cancellation_before_start_is_allowed`, `integration/test_lifecycle_db.py::test_queued_runs_can_be_cancelled_without_starting` | verificado |
| Duplicate submission | `integration/test_api_experiments.py::test_repeated_key_and_payload_returns_same_experiment`, `::test_run_submission_is_idempotent` | verificado |
| Conflicting submission | `integration/test_api_experiments.py::test_reused_key_with_other_payload_is_rejected` | verificado |
| Missing idempotency key | `integration/test_api_experiments.py::test_creation_requires_valid_idempotency_key` | verificado |
| Same cell with another key | `integration/test_api_experiments.py::test_run_submission_is_idempotent`, `integration/test_constraints.py::test_duplicate_experimental_cell_is_rejected` | verificado |
| Reevaluation | `integration/test_evaluations.py::test_reevaluation_creates_new_evaluation_and_keeps_history` | verificado |

## metric-reporting

| Escenario | Evidencia | Estado |
|---|---|---|
| No tool calls | `test_metrics.py::test_no_tool_calls_when_required` | verificado |
| Zero denominator | `test_metrics.py::test_zero_denominator_ratio_is_not_applicable` | verificado |
| Evaluator error | `test_metrics.py::test_evaluator_error_propagates_to_metric` | verificado |
| Missing provider usage | `test_model_gateway.py::test_missing_usage_is_unknown_not_zero`, `test_live_provider.py::test_missing_usage_is_unknown` | verificado (sin llamada real) |
| Judge consumption reported apart | `integration/test_judge_cost.py::test_judge_consumption_is_reported_apart_and_in_total` | verificado |
| Partial experiment | `test_metrics.py::test_partial_experiment_seven_of_ten` | verificado |
| Fault not reached | `integration/test_pilot_report.py::test_five_repetitions_cover_every_cell` (`recovery`: el agente que no llega al fallo transitorio no está expuesto ni recibe crédito; exposure_rate 0.5 visible) | verificado; implementado en esta auditoría (antes faltaba `recovery_success`) |

## reproducibility

| Escenario | Evidencia | Estado |
|---|---|---|
| Changed fixture | `test_replay.py::test_changed_tool_hash_is_rejected`, `integration/test_replays.py::test_recording_with_other_fixture_hash_is_not_identical` | verificado |
| Replay divergence | `test_replay.py::test_divergent_request_is_mismatch_without_fallback`, `integration/test_replays.py::test_divergent_recording_is_mismatch_without_fallback` | verificado |
| Offline replay | `integration/test_replays.py::test_replay_reproduces_source_offline`, `integration/test_react.py::test_recorded_react_run_replays_without_calling_the_provider` | verificado |
| Unverifiable recording | `integration/test_replays.py::test_tampered_source_trace_is_not_replayable`, `::test_redacted_source_is_not_replayable`, `::test_replay_preconditions` | verificado |
| Remote variability | `integration/test_remote_variability.py::test_identical_requests_with_different_answers_are_kept_and_reported` | verificado con un servidor local que imita un proveedor compatible con OpenAI (peticiones idénticas, respuestas distintas): ambos runs se conservan, `distinct_outputs=2` y el manifest y la ModelConfiguration no cambian. No se ha observado con un proveedor de pago |
| Fixture contamination | `integration/test_execution.py::test_fixture_state_is_not_shared_between_runs` | verificado |

## trace-capture

| Escenario | Evidencia | Estado |
|---|---|---|
| Collector unavailable | `integration/test_telemetry.py::test_collector_down_keeps_complete_evidence` | verificado |
| Correlated telemetry without payloads | `integration/test_telemetry.py::test_spans_correlate_run_tool_and_evaluator`, `::test_trace_ids_do_not_enter_the_sealed_digest` | verificado |
| Scripted events are sealed after in-memory capture | `integration/test_execution.py::test_scripted_run_reaches_terminal_state_with_evidence`, `integration/test_traces.py::test_export_verifies_and_matches_sealed_trace` | verificado |
| Missing tool result | `integration/test_execution.py::test_lost_worker_fails_explicitly_after_max_attempts` (traza sellada `incomplete` con sólo `run.failed` del harness), `integration/test_evaluations.py::test_lost_run_is_evaluated_without_fabricating_a_result` (`task_success=unknown`), `integration/test_execution.py::test_failed_run_trace_is_exposed_as_incomplete` | verificado |
| Repeated event | `test_runner.py::test_duplicate_event_id_conflicting_digest_is_invalid`, `integration/test_traces.py::test_resent_event_with_other_digest_is_integrity_error` | verificado |
| Truncated export | `integration/test_traces.py::test_truncated_or_altered_export_fails_verification` | verificado |
| Sensitive payload | `test_redaction.py::test_sink_redacts_before_digest_and_marks_replay_unavailable`, `integration/test_traces.py::test_secrets_are_absent_from_trace_export_and_result` | verificado |

## versioned-benchmarks

| Escenario | Evidencia | Estado |
|---|---|---|
| Invalid publication | `integration/test_api_catalog.py::test_scenario_without_oracle_checks_is_rejected`, `::test_dataset_with_broken_ref_is_not_created` | verificado |
| Mutation of published version | `integration/test_api_catalog.py::test_published_scenario_is_immutable`, `::test_changing_oracle_requires_new_version` | verificado |
| Duplicate identity or content | `integration/test_api_catalog.py::test_duplicate_content_hash_is_rejected` | verificado |
| Oracle leakage prevention | `integration/test_api_catalog.py::test_public_view_hides_oracle_and_private_labels`, `integration/test_benchmark_v1.py::test_public_views_hide_oracles`, `integration/test_demo_access.py` | verificado |
| Family crosses splits | `test_catalog.py::test_family_cannot_cross_splits`, `integration/test_api_catalog.py::test_dataset_rejects_family_across_splits` | verificado |
| Incomplete dataset | `integration/test_api_catalog.py::test_small_dataset_is_incomplete_not_complete_v1`, `integration/test_benchmark_v1.py::test_publication_counts_integrity_and_declarations` | verificado |
| Unanswerable query | `integration/test_retrieval_benchmark.py::test_retrieval_benchmark_metrics_and_failure_modes` | verificado |
| Unsupported citation | `integration/test_retrieval_benchmark.py::test_retrieval_benchmark_metrics_and_failure_modes` | verificado |

## Resultado

Los 66 escenarios quedan verificados y el change se archiva (specs promovidas a `openspec/specs/`). El trabajo operativo que no es un requisito del sistema —ejecución con un proveedor de pago, doble anotación humana del judge y publicar el contrato de salida de los escenarios, detectado en la ejecución live local— sigue en el change `run-live-and-calibrate-judge`.
