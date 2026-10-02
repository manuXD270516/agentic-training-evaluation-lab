## Purpose

Producir métricas interpretables con fórmulas, cobertura, unidades y reglas explícitas de datos ausentes.

## ADDED Requirements

### Requirement: Versioned metric semantics

El sistema SHALL aplicar fórmulas, ámbitos, unidades y agregaciones de metrics.md y persistir la versión del perfil en cada evaluación y reporte.

#### Scenario: No tool calls
- **WHEN** un escenario requiere tools pero el agente no llama ninguna
- **THEN** tool_accuracy es N/A, required_tool_coverage es cero y la tarea falla.

#### Scenario: Zero denominator
- **WHEN** una métrica de ratio no tiene unidades que medir en un run
- **THEN** su score es not_applicable con numerador y denominador 0 y valor nulo, nunca 0, 1 ni NaN.

#### Scenario: Evaluator error
- **WHEN** un check o el evaluador falla con una excepción
- **THEN** la métrica afectada queda error sin valor, task_success queda unknown y en agregados cuenta como unknown.

### Requirement: Missingness and cost disclosure

El sistema SHALL reportar denominadores, unknown, N/A, cobertura y subtotales conocidos; SHALL incluir fallos en recursos consumidos sin inventar uso o precio.

#### Scenario: Missing provider usage
- **WHEN** el proveedor no devuelve tokens y no existe estimación declarada
- **THEN** tokens/coste afectados quedan unknown y el total no se presenta como cero.

#### Scenario: Judge consumption reported apart
- **WHEN** un run se evalúa una o más veces con el judge auxiliar
- **THEN** sus tokens, coste y latencia se informan con `scope=judge` aparte del consumo del agente, cuentan todas las evaluaciones con judge y el total agente + judge los incluye sin presentarlos como cero.

#### Scenario: Partial experiment
- **WHEN** hay 7 éxitos, 2 fallos y 1 unknown en 10 celdas
- **THEN** se reporta éxito conservador 0.70, cobertura 0.90 y rango por missingness [0.70,0.80].

### Requirement: Recovery exposure

El sistema SHALL medir recovery_success sólo para fallos recuperables inyectados observados y publicar tasa de exposición.

#### Scenario: Fault not reached
- **WHEN** el agente no llega al paso donde se inyecta el fallo
- **THEN** no obtiene crédito de recuperación y la baja exposición queda visible.

