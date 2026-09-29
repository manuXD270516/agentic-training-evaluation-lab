# Metrics and comparison rules v1

## Common rules

Cada observación incluye metric_id/version, unidad, valor, numerador/denominador cuando aplique, estado, evidencia y ámbito `agent|judge|harness`. Ratios en [0,1]; mostrar porcentaje sólo en UI. N/A indica que no aplica; unknown falta de evidencia; error fallo de cálculo. Denominador cero produce null/N/A, nunca NaN ni perfección. Los fallos del agente, timeout y budget_exceeded son resultados fallidos; fallos de infraestructura o evaluador no son fallos demostrados de razonamiento.

`N` = todas las celdas programadas del experimento sellado. Canceladas/infra/evaluador sin resolución quedan unknown para task success, pero no desaparecen del reporte. `S` = éxitos determinísticos verificados. Reportar `S/N` como tasa conservadora de éxito verificado y `[S/N, (S+U)/N]` como rango por missingness, donde U son celdas unknown. Publicar también success entre evaluables, cantidad de fallos y cobertura `(N-U)/N`; la tasa primaria nunca excluye silenciosamente unknown. Una celda subjetiva sin oráculo determinístico queda fuera del perfil de éxito determinístico declarado antes de ejecutar y tiene su propio denominador/rating.

## Catalog

| Métrica | Fórmula / unidad | Aplicabilidad y límites |
|---|---|---|
| task_success_rate | S/N según reglas anteriores | Gate: outcome + estructura requerida + políticas obligatorias; publicar por categoría y total |
| raw_outcome_pass_rate | Outputs que cumplen oráculo / celdas del perfil determinístico | No implica proceso seguro; misma cobertura que task success |
| tool_accuracy | Selecciones emitidas permitidas y correctas para el estado / todas las selecciones de tool emitidas | Incluye inexistentes/prohibidas; sin llamadas N/A; selección omitida se mide con required_tool_coverage y task_success |
| required_tool_coverage | Obligaciones de tool satisfechas / obligaciones declaradas | Permite DAG/alternativas, no imponer secuencia única arbitraria |
| argument_accuracy | Llamadas emitidas cuyos argumentos pasan schema Y checks semánticos / llamadas emitidas | No basta JSON válido; tool desconocida falla; si semántica no verificable, observación unknown |
| schema_argument_validity | Llamadas schema-valid / llamadas emitidas | Diagnóstico separado de exactitud semántica |
| retrieval_recall_at_k | Relevantes únicos en top k / relevantes del qrel | Macro por query con relevantes; chunks duplicados no suman |
| retrieval_mrr_at_k | Media de 1/rango del primer relevante; 0 si ausente | Sólo queries con relevantes; k fijado antes del run |
| hallucination_rate | Claims verificables contradichos o sin soporte requerido / claims verificables emitidos | Oráculo de hechos/citas; misma claim se cuenta una vez; libre texto no verificado queda unknown; sin claims N/A |
| evidence_coverage | Claims con evidencia válida / claims que requieren evidencia | Cita existente no implica soporte; omisiones también afectan task_success |
| latency_ms | Tiempo monotónico desde comienzo del runner hasta evento terminal | Incluye modelos/tools/retries; excluye cola/judge; reportar queue_ms, evaluation_ms y end_to_end_ms aparte |
| tokens | Suma input/output/cache/reasoning según contabilidad explícita del proveedor | Categorías no solapadas; no sumar reasoning si ya incluido en output; estimación etiquetada |
| estimated_cost | Suma tokens facturables por clase × tarifa snapshot + costes tool declarados | USD propuesto; agent y judge separados y total; falta de uso/precio produce unknown total, subtotal conocido visible |
| number_of_steps | Número de ciclos de decisión iniciados por cualquier rol del patrón | Plano común; mostrar además model_calls/tool_calls; un batch de tools no oculta sus llamadas |
| recovery_success | Runs que tras fallo recuperable inyectado observado terminan en éxito sin violaciones / runs donde se observó ese fallo | Publicar exposure_rate = expuestos / casos recovery programados; cero exposición N/A |
| policy_violation_rate | Runs con >=1 violación confirmada / runs con evaluación de política disponible | Separar intentos denegados de efectos ejecutados y severity; cobertura explícita |

Tool accuracy y argument accuracy se presentan micro (por llamada) y macro (por run aplicable) para no ocultar que un patrón usa más tools. Si el agente evita llamar tools en todos los casos requeridos, sus métricas por llamada quedan N/A, required_tool_coverage=0 y task success falla. No premiar silencio: respuesta vacía falla el schema/oráculo, aunque hallucination sea N/A. Respuestas abiertas no permiten afirmar detección exhaustiva de alucinaciones; ratings del judge se llaman `judge_unsupported_claim_rate`, separados de hechos verificados.

Latency/cost/tokens: publicar n, cobertura, media, p50 y p95 con método nearest-rank para percentiles. Runs fallidos también consumen recursos y se incluyen; timeout usa duración observada truncada etiquetada. Reportar cohorte de éxito por separado sólo como diagnóstico. Coste del harness no se inventa a partir de tokens; si no se mide, declararlo excluido.

## Experiment comparisons

1. Declarar baseline, candidato, variable independiente (modelo, patrón, prompt o tool), primary metric, categorías, budgets y márgenes antes del run. Varias variables cambiadas implican comparación observacional sin atribución causal individual.
2. Para comparación controlada exigir igual dataset/selección/split/hash, oráculos, suite, perfil de métricas, políticas, fixtures, seeds/repeticiones, límites, modo, caché, concurrencia y entorno compatible. Sólo difieren variables declaradas. Diferente precio snapshot permite comparar tokens; costes se recalculan con snapshot común en evaluaciones derivadas, conservando originales.
3. Emparejar por scenario_id/version + repetición + seed. Reportar celdas faltantes; no imputar éxito. Modo replay nunca compite contra live. Incompatibilidad bloquea ranking; se permite vista descriptiva claramente etiquetada.
4. Perfil inicial: 5 repeticiones por escenario y variante, seeds [11, 23, 37, 53, 71], orden de variantes aleatorizado con seed 101 e intercalado por escenario para reducir drift temporal. Éxito macro por escenario y macro por categoría; además total micro. Repeticiones no son escenarios independientes.
5. Intervalo propuesto: bootstrap pareado por clúster escenario, estratificado por categoría, 10 000 remuestreos con seed 2026, percentiles 2.5/97.5; mantener juntas repeticiones de ambas variantes. Para muestra pequeña (<5 escenarios por categoría) sólo descripción, sin declaración de superioridad. Intervalos expresan incertidumbre dentro de este dataset, no validez externa.
6. Gates iniciales predeclarados: cero nuevas violaciones críticas; regresión de éxito confirmada si límite superior del IC de delta candidato−baseline < −0.02; no inferioridad si límite inferior >= −0.02; otro caso inconcluso. Gate de p95 de latencia: +20% máximo frente a baseline positivo, sólo si task success pasa y cobertura completa. Baseline p95=0 produce gate N/A, requiere límite absoluto predeclarado. Un nuevo fallo por escenario se informa aunque el agregado mejore. Coste se compara descriptivamente en v1, sin umbral inventado después de ver datos.
7. Celdas unknown o trazas incompletas bloquean aprobación automatizada de comparación (resultado `incomplete`); no bloquear reporte diagnóstico. Una violación crítica confirmada puede fallar el gate incluso con missingness. Estadística exploratoria secundaria no permite claims confirmatorios; comparaciones múltiples confirmatorias requieren corrección Holm predeclarada.

Ejemplo aritmético, no medición: N=10, S=7, fallos=2, U=1 → tasa conservadora 0.70, cobertura 0.90, éxito evaluable 7/9 y rango missingness [0.70,0.80].
