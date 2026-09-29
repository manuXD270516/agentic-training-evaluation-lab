# Trace format v1

Traza de evaluación append-only, distinta del sampling de OpenTelemetry. Un run tiene una traza lógica; sus intentos operativos llevan attempt_id y fencing token. Los eventos incluyen `schema_version`, `event_id`, `run_id`, `attempt_id`, `sequence`, `timestamp_utc`, `elapsed_ms`, `type`, `actor_role`, `parent_event_id`, `otel_trace_id`, `otel_span_id`, `payload`, `payload_digest`, `redaction_metadata`.

`sequence` es entero creciente asignado por el sink dentro del run; concurrencia entre spans se representa con parent/call ids y tiempos monotónicos. Orden de recepción no inventa causalidad. `event_id` duplicado con mismo digest es idempotente; con otro digest es error de integridad. Referencias de evidencia apuntan a event_id y JSON pointer, o artifact hash + rango. Nunca confiar sólo en texto libre de una explicación.

## Event types

| Evento | Payload requerido |
|---|---|
| run.started | manifest_hash, scenario_ref, agent_ref, mode, limits |
| step.started / step.completed | step_id, role, status; todo rol comparte presupuesto |
| model.requested | request_digest, model_config_ref, input redactado/ref, tools disponibles |
| model.completed / model.failed | request_event_id, output/ref, usage con fuente, resolved_model, finish_reason/error |
| plan.created | plan público estructurado, dependencias; nunca razonamiento interno oculto |
| tool.requested | call_id, tool/version, argumentos redactados/ref |
| tool.validated / tool.denied | call_id, schema_result, policy_result, reason_codes |
| tool.completed / tool.failed | call_id, resultado/ref, evidence_ids, state_digest, error_class, retriable |
| retrieval.completed | query_id, corpus_ref, embedding/retriever refs, ranked chunk_ids, scores, top_k |
| retry.scheduled | origin_call_id, attempt_number, reason, delay_ms |
| policy.violation | rule/version, severity, attempted/executed, evidence_refs |
| run.completed / run.failed / run.timed_out / run.budget_exceeded / run.cancelled | output/ref si existe, usage acumulado, error, completeness y último sequence |

Eventos de evaluación se guardan bajo evaluation_id con referencia a digest sellado del run; no se agregan retroactivamente a la traza del agente. Tool requests rechazadas se conservan y cuentan en exactitud. Toda llamada iniciada tendrá resultado/error o marca explícita de interrupción; worker caído no puede fabricar un resultado correcto.

## Privacy and integrity

Redactar credenciales/PII antes de persistir/exportar y antes de enviar a OTel. En v1 sólo datos sintéticos y credenciales fuera de payloads; no almacenar secretos con la excusa de replay. Guardar metadata de campos redactados; usar HMAC o marcador en datos sensibles si un hash ordinario permite adivinarlos. Si la redacción impide reconstruir una llamada, replay se declara no disponible para ella. El digest público se calcula sobre contenido redactado canónico, sin incluir el propio digest; no prometer verificar bytes secretos ausentes.

Al cerrar: digest sobre lista ordenada de eventos y refs, contador, estado de completitud. Eventos faltantes, conflicto de id o referencia huérfana producen `incomplete`/`invalid`; reevaluación estricta no emite éxito verificado. Exportar JSONL de eventos y manifest JSON con schema/hash, sin sampling. Una exportación truncada falla verificación.

## Minimal trace example

Secuencia conceptual de un caso válido: run.started → step.started → model.requested → model.completed (solicita calculator) → tool.requested → tool.validated → tool.completed (total 42, evidence e1) → step.completed → step.started → model.requested → model.completed (output con e1) → step.completed → run.completed. La evaluación posterior verifica valor, tool, argumentos y origen de e1; no infiere éxito sólo del evento terminal.
