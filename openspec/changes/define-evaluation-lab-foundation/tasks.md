## 1. M0 — Bootstrap

- [x] 1.1 Crear estructura backend/frontend y fijar dependencias/entorno (reproducibility); verificar arranque limpio y lockfiles sin referencias flotantes.
- [x] 1.2 Configurar CI para validación OpenSpec, formato y tipos (reproducibility); verificar ejecución limpia sin credenciales live.
  - Evidencia: GitHub Actions run 36527093145 sobre 93be584 (2026-09-29), jobs openspec, backend, frontend y compose-smoke en verde, sin secretos.

## 2. M1 — Experiment model

- [x] 2.1 Implementar entidades, relaciones, migraciones y estados de design.md (experiment-management); verificar creación de DB vacía y rechazo de transiciones inválidas.
  - Evidencia: GitHub Actions run 36529075377 sobre 2163c57 (2026-09-29): 64 tests, 0 omitidos, incluidos migración sobre DB vacía, deriva modelo↔migración, downgrade/upgrade y todas las transiciones de Experiment/Run/Evaluation contra PostgreSQL 18.6; `compose-smoke` migra una DB vacía.
- [x] 2.2 Implementar snapshots/hash/sellado e idempotencia API (experiment-management, reproducibility); verificar mutación denegada y clave repetida/conflictiva.
  - Evidencia: GitHub Actions run 36530654615 sobre 5de2f45 (2026-09-29): 94 tests, 0 omitidos; clave repetida (réplica), clave con otro payload (409), clave ausente, concurrencia con igual clave, celda duplicada con otra clave, edición de experimento sellado y de versiones publicadas rechazadas, hash del manifest recalculado. Mutaciones de idempotencia y triggers detectadas. Alcance de reproducibility: manifest canónico del experimento y refs resueltas; commit/entorno por run y rechazo de árbol dirty quedan para M2/M4.
- [x] 2.3 Implementar schemas/manifests de Dataset, Scenario y Benchmark (versioned-benchmarks); verificar refs rotas, duplicados y publicación inmutable.
  - Evidencia: GitHub Actions run 36531667579 sobre dd6b284 (2026-09-29): jobs openspec, backend, frontend y compose-smoke en verde. Refs rotas, familias cruzando splits, mutación de versión publicada, hash incorrecto y cobertura piloto/incomplete verificados; GET público no expone oráculo ni qrels.

## 3. M2 — Agent Runner

- [x] 3.1 Implementar contratos y baseline scripted con eventos mínimos (agent-execution, trace-capture); verificar resultado offline con evidencia y estado terminal.
  - Evidencia: pytest local 130 passed (2026-09-29), incluidos runner scripted sin ModelGateway, tool denegada, patrón diferido, replay no implementado, integridad de event_id, run `completed` con `usage.model_calls=0` y etiqueta `scripted`, `GET /runs/{id}/trace` sellada sin oráculo, y claim `SKIP LOCKED` sobre DB vacía.
- [ ] 3.2 Implementar gateway de tools, validación y aislamiento de fixtures (agent-execution); verificar tool prohibida, argumentos incorrectos y ausencia de estado compartido.
- [ ] 3.3 Implementar límites, timeouts, retries y leases/fencing (agent-execution); verificar corte por presupuesto y worker vencido sin efectos duplicados.

## 4. M3 — Deterministic evals

- [ ] 4.1 Implementar checks determinísticos y estructurales declarativos (evaluation-engine); verificar resultado correcto/incorrecto y mutaciones de salida que deben fallar.
- [ ] 4.2 Implementar checks de políticas y evidencia (evaluation-engine); verificar respuesta correcta con tool prohibida, cita inexistente y cita sin soporte.
- [ ] 4.3 Persistir evaluaciones/scores versionados y fórmulas iniciales (metric-reporting, experiment-management); verificar N/A, unknown, error, denominador cero y ejemplo 7/10.

## 5. M4 — Trace capture

- [ ] 5.1 Implementar sink durable, digest, export y redacción (trace-capture); verificar duplicado idempotente, conflicto de digest, export truncado y secretos ausentes.
- [ ] 5.2 Instrumentar OpenTelemetry con correlación de run/model/tool/evaluator (trace-capture); verificar evidencia completa durante caída del collector.
- [ ] 5.3 Implementar replay y reevaluación con manifests (reproducibility); verificar replay sin red, mismatch sin fallback y evaluación histórica preservada.

## 6. M5 — First benchmark

- [ ] 6.1 Crear piloto sintético de 14 escenarios, dos por categoría (versioned-benchmarks); verificar schemas, familias, oráculos y separación de vista pública/privada.
- [ ] 6.2 Ejecutar cinco repeticiones scripted por escenario y generar reporte descriptivo (metric-reporting); verificar todas las celdas, consumo, cobertura y etiqueta de piloto sin claims estadísticos.

## 7. M6 — ReAct

- [ ] 7.1 Implementar gateway neutral de modelo con uso/precios y errores tipados (agent-execution, metric-reporting); verificar usage ausente, revisión desconocida y rechazo de límite monetario sin precio.
- [ ] 7.2 Implementar ReAct versionado y seleccionar configuración live/presupuesto (agent-execution, reproducibility); verificar alternancia acción/observación, terminación y replay de run grabado.

## 8. M7 — Planner/Executor

- [ ] 8.1 Implementar roles y plan explícito con presupuesto global (agent-execution, trace-capture); verificar consumo sumado y dependencias de pasos.
- [ ] 8.2 Ejecutar comparación descriptiva pareada frente a ReAct (experiment-comparison); verificar igualdad de manifests salvo patrón/modelos por rol declarados y conservación de fallos.

## 9. M8 — LLM Judge

- [ ] 9.1 Implementar judge auxiliar sin tools con rúbrica/prompt versionados y abstención (evaluation-engine); verificar respuesta inválida, inyección y prohibición de sobrescribir gates.
- [ ] 9.2 Construir set independiente de al menos 30 respuestas y realizar doble anotación humana (evaluation-engine); verificar protocolo, acuerdo/kappa aplicable, matriz de confusión y etiqueta experimental si no pasa.
- [ ] 9.3 Registrar coste/tokens/latencia del judge por separado (metric-reporting); verificar que no se atribuyan al consumo del agente ni desaparezcan del total.

## 10. M9 — RAG benchmark

- [ ] 10.1 Versionar corpus/chunks/embeddings/retriever e integrar PGVector (versioned-benchmarks, reproducibility); verificar hashes, qrels privados y orden/tie-break registrado.
- [ ] 10.2 Completar diez escenarios retrieval y benchmark específico (versioned-benchmarks, evaluation-engine); verificar distractores, sin respuesta, inyección, cita falsa y métricas top-k.

## 11. M10 — Dashboard

- [ ] 11.1 Implementar vistas React de experimentos, métricas y cobertura (metric-reporting); verificar filtros por categoría/modo y distinción N/A/unknown/error.
- [ ] 11.2 Implementar navegación score -> evidencia/trace y versiones (trace-capture, experiment-management); verificar eventos paginados y trazas incompletas visibles.

## 12. M11 — Comparison reports

- [ ] 12.1 Implementar comparability gate y pares completos (experiment-comparison); verificar rechazo por dataset/suite/modo incompatible y reporte incomplete por celdas ausentes.
- [ ] 12.2 Implementar bootstrap por escenario, márgenes y gates (experiment-comparison); verificar seed reproducible, caso de no inferioridad, regresión e inconcluso con fixtures calculables.
- [ ] 12.3 Exportar comparación con manifests, métricas y evidencia por escenario (experiment-comparison); verificar que nueva violación crítica falla aunque mejore el promedio.

## 13. M12 — Public benchmark/demo

- [ ] 13.1 Completar y publicar 70 escenarios, diez por categoría y splits 42/28 por familias (versioned-benchmarks); verificar conteos, integridad, licencia y declaración de contaminación tras publicación.
- [ ] 13.2 Preparar demo de sólo lectura con artefactos sanitizados y autenticación para operaciones privadas (trace-capture, experiment-management); verificar que no exponga oráculos privados, credenciales ni escritura pública.
- [ ] 13.3 Ejecutar reproducción offline desde entorno limpio y publicar limitaciones/resultados reales (reproducibility, metric-reporting); verificar hashes y separar fixtures de mediciones.
- [ ] 13.4 Auditar cada escenario de las ocho capabilities y completar evidencia de aceptación (todas las capabilities); ejecutar validación OpenSpec estricta y archivar sólo tras implementar/verificar todos los requisitos.
