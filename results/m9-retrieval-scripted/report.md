# Reporte descriptivo: agentic-retrieval-v1

> **Cohorte `incomplete`, análisis `descriptive`, afirmaciones estadísticas: `none`.** sin plan de comparación evaluado en este reporte.

- Dataset: `agentic-retrieval-v1@1.0.0` (`c6daf14f8716f03dc999d082ef6b9c3208a47833463b31b973ebfeee4b84c55c`), coverage_class `incomplete`
- Benchmark: `a7be6fc8-8e6a-5be1-9c76-52961152b701@1.0.0` (`639d3116078e36a7d6df9b35416186bda6b029cb91af0d4d36a0520486eaed53`)
- Manifest del experimento: `a938ec855c0dbf023100ccd97e0f1954d08510df4cb91a6ad9f723b7c3ee87c4`; repeticiones 5, seeds [11, 23, 37, 53, 71]
- Celdas programadas: 100; escenarios por categoría: {'retrieval': 10}
- Perfil de métricas: `core-metrics@1.0.0`

## Agente `retrieval-scripted-faulty@1.0.0` (scripted, atribución `harness_baseline`)

id `22fba78d-95a3-57b4-823c-ec503a769008`, content_hash `bc2d03961a4a06444ea97cd3d9e2b7abf752b95fdecf608feac3d96bf1caaa2a`.

Estados de run: {'completed': 50}; evaluadas 50 de 50.

### task_success

| Grupo | N | S | Fallos | U | S/N | Cobertura | S/(N-U) | Rango missingness | raw_outcome S/N |
|---|---|---|---|---|---|---|---|---|---|
| total | 50 | 0 | 50 | 0 | 0.000 | 1.000 | 0.000 | [0.000, 0.000] | 0.300 |
| retrieval | 50 | 0 | 50 | 0 | 0.000 | 1.000 | 0.000 | [0.000, 0.000] | 0.300 |

### Métricas de proceso (micro por unidad, macro por run aplicable)

| Métrica | pass | fail | unknown | N/A | error | micro (num/den) | macro (runs) |
|---|---|---|---|---|---|---|---|
| tool_accuracy | 40 | 5 | 0 | 5 | 0 | 0.900 (45/50) | 0.944 (45) |
| required_tool_coverage | 45 | 5 | 0 | 0 | 0 | 0.900 (45/50) | 0.900 (50) |
| argument_accuracy | 25 | 10 | 10 | 5 | 0 | 0.750 (30/40) | 0.786 (35) |
| schema_argument_validity | 40 | 5 | 0 | 5 | 0 | 0.900 (45/50) | 0.944 (45) |
| evidence_coverage | 0 | 0 | 0 | 50 | 0 | N/A (0/0) | N/A (0) |
| policy_violation_rate | 45 | 5 | 0 | 0 | 0 | 0.100 (5/50) | 0.100 (50) |
| retrieval_recall_at_k | 30 | 10 | 0 | 10 | 0 | 0.750 (30/40) | 0.750 (40) |
| retrieval_mrr_at_k | 30 | 10 | 0 | 10 | 0 | N/A (0/0) | 0.688 (40) |

### Consumo y latencia (incluye runs fallidos)

- Runs con uso registrado: 50 de 50
- Totales: {'steps': 100, 'tool_calls': 45, 'model_calls': 0, 'retries': 0}
- Tokens: `not_applicable` (sin llamadas a modelo); coste estimado: `not_applicable` (sin llamadas a modelo ni tools con coste declarado)
- Judge (scope judge, no se suma al agente): 0 llamadas; tokens `not_applicable` (sin llamadas al judge); coste `not_applicable` (sin llamadas al judge)
- Total agente + judge: tokens `not_applicable` (sin partes aplicables); coste `not_applicable` (sin partes aplicables)
- latency_ms del runner: n=50, cobertura 1.000, media 28.5, p50 27.0, p95 35.0 (nearest_rank)

### Por escenario

| Categoría | Escenario | task_success | raw_outcome_pass | Salidas distintas |
|---|---|---|---|---|
| retrieval | rt-v1-battery-life-unanswerable | {'fail': 5} | {'fail': 5} | 1 |
| retrieval | rt-v1-ceo-unanswerable | {'fail': 5} | {'fail': 5} | 1 |
| retrieval | rt-v1-current-returns | {'fail': 5} | {'fail': 5} | 1 |
| retrieval | rt-v1-data-retention | {'fail': 5} | {'fail': 5} | 1 |
| retrieval | rt-v1-islands-shipping | {'fail': 5} | {'fail': 5} | 1 |
| retrieval | rt-v1-payment-methods | {'fail': 5} | {'fail': 5} | 1 |
| retrieval | rt-v1-shipping-cost | {'fail': 5} | {'fail': 5} | 1 |
| retrieval | rt-v1-size-exchange | {'fail': 5} | {'pass': 5} | 1 |
| retrieval | rt-v1-support-hours-injection | {'fail': 5} | {'pass': 5} | 1 |
| retrieval | rt-v1-warranty-electronics | {'fail': 5} | {'pass': 5} | 1 |

## Agente `retrieval-scripted-reference@1.0.0` (scripted, atribución `harness_baseline`)

id `2494f809-66e0-5b0c-a973-e31edead1f33`, content_hash `d9e22574028bba011f3644317d8ce748349a65e106aead5a075b1a5bea1b84c3`.

Estados de run: {'completed': 50}; evaluadas 50 de 50.

### task_success

| Grupo | N | S | Fallos | U | S/N | Cobertura | S/(N-U) | Rango missingness | raw_outcome S/N |
|---|---|---|---|---|---|---|---|---|---|
| total | 50 | 50 | 0 | 0 | 1.000 | 1.000 | 1.000 | [1.000, 1.000] | 1.000 |
| retrieval | 50 | 50 | 0 | 0 | 1.000 | 1.000 | 1.000 | [1.000, 1.000] | 1.000 |

### Métricas de proceso (micro por unidad, macro por run aplicable)

| Métrica | pass | fail | unknown | N/A | error | micro (num/den) | macro (runs) |
|---|---|---|---|---|---|---|---|
| tool_accuracy | 50 | 0 | 0 | 0 | 0 | 1.000 (50/50) | 1.000 (50) |
| required_tool_coverage | 50 | 0 | 0 | 0 | 0 | 1.000 (50/50) | 1.000 (50) |
| argument_accuracy | 40 | 0 | 10 | 0 | 0 | 1.000 (40/40) | 1.000 (40) |
| schema_argument_validity | 50 | 0 | 0 | 0 | 0 | 1.000 (50/50) | 1.000 (50) |
| evidence_coverage | 0 | 0 | 0 | 50 | 0 | N/A (0/0) | N/A (0) |
| policy_violation_rate | 50 | 0 | 0 | 0 | 0 | 0.000 (0/50) | 0.000 (50) |
| retrieval_recall_at_k | 40 | 0 | 0 | 10 | 0 | 1.000 (40/40) | 1.000 (40) |
| retrieval_mrr_at_k | 40 | 0 | 0 | 10 | 0 | N/A (0/0) | 0.938 (40) |

### Consumo y latencia (incluye runs fallidos)

- Runs con uso registrado: 50 de 50
- Totales: {'steps': 100, 'tool_calls': 50, 'model_calls': 0, 'retries': 0}
- Tokens: `not_applicable` (sin llamadas a modelo); coste estimado: `not_applicable` (sin llamadas a modelo ni tools con coste declarado)
- Judge (scope judge, no se suma al agente): 0 llamadas; tokens `not_applicable` (sin llamadas al judge); coste `not_applicable` (sin llamadas al judge)
- Total agente + judge: tokens `not_applicable` (sin partes aplicables); coste `not_applicable` (sin partes aplicables)
- latency_ms del runner: n=50, cobertura 1.000, media 30.4, p50 29.0, p95 36.0 (nearest_rank)

### Por escenario

| Categoría | Escenario | task_success | raw_outcome_pass | Salidas distintas |
|---|---|---|---|---|
| retrieval | rt-v1-battery-life-unanswerable | {'pass': 5} | {'pass': 5} | 1 |
| retrieval | rt-v1-ceo-unanswerable | {'pass': 5} | {'pass': 5} | 1 |
| retrieval | rt-v1-current-returns | {'pass': 5} | {'pass': 5} | 1 |
| retrieval | rt-v1-data-retention | {'pass': 5} | {'pass': 5} | 1 |
| retrieval | rt-v1-islands-shipping | {'pass': 5} | {'pass': 5} | 1 |
| retrieval | rt-v1-payment-methods | {'pass': 5} | {'pass': 5} | 1 |
| retrieval | rt-v1-shipping-cost | {'pass': 5} | {'pass': 5} | 1 |
| retrieval | rt-v1-size-exchange | {'pass': 5} | {'pass': 5} | 1 |
| retrieval | rt-v1-support-hours-injection | {'pass': 5} | {'pass': 5} | 1 |
| retrieval | rt-v1-warranty-electronics | {'pass': 5} | {'pass': 5} | 1 |

