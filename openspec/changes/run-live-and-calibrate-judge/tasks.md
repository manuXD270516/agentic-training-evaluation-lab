## 1. Ejecución live con proveedor de pago

- [ ] 1.1 Publicar versiones de escenarios, datasets y benchmarks con contrato de salida público y sus locks (versioned-benchmarks); verificar que la vista pública no incluye valores esperados, checks ni qrels y que las versiones anteriores no cambian.
- [ ] 1.2 Elegir proveedor, modelo con revisión y tarifa vigente, y ejecutar `evallab-benchmark run-live` sobre el piloto con `--max-cost-usd 5` (agent-execution, metric-reporting); verificar coste estimado dentro del límite, tokens observados, revisión registrada y ausencia de la clave en trazas y resultados.
- [ ] 1.3 Comparar ReAct y Planner/Executor live con el protocolo `paired-comparison@1.0.0` sobre `agentic-benchmark-v1` (experiment-comparison); verificar comparabilidad, intervalo con seed 2026 y gate de política, y publicar el export con sus limitaciones.

## 2. Calibración humana del judge

- [ ] 2.1 Dos anotadores independientes puntúan los 32 ítems con `evallab-judge-calibration template` sin ver al judge ni al otro anotador (evaluation-engine); verificar `human=true` sólo con anotaciones reales y ambos identificadores registrados.
- [ ] 2.2 Adjudicar los desacuerdos en sesión conjunta y documentar cada motivo (evaluation-engine); verificar que no queda ningún desacuerdo sin adjudicar.
- [ ] 2.3 Generar votos con `evallab-judge-calibration votes` y ejecutar `analyze` (evaluation-engine); verificar acuerdo ≥ 0.80 y ninguna inyección aprobada antes de quitar la etiqueta `experimental`, o documentar el resultado `experimental`.
