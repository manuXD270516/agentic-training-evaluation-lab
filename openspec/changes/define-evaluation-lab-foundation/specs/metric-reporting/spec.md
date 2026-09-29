## Purpose

Producir métricas interpretables con fórmulas, cobertura, unidades y reglas explícitas de datos ausentes.

## ADDED Requirements

### Requirement: Versioned metric semantics

El sistema SHALL aplicar fórmulas, ámbitos, unidades y agregaciones de metrics.md y persistir la versión del perfil en cada evaluación y reporte.

#### Scenario: No tool calls
- **WHEN** un escenario requiere tools pero el agente no llama ninguna
- **THEN** tool_accuracy es N/A, required_tool_coverage es cero y la tarea falla.

### Requirement: Missingness and cost disclosure

El sistema SHALL reportar denominadores, unknown, N/A, cobertura y subtotales conocidos; SHALL incluir fallos en recursos consumidos sin inventar uso o precio.

#### Scenario: Missing provider usage
- **WHEN** el proveedor no devuelve tokens y no existe estimación declarada
- **THEN** tokens/coste afectados quedan unknown y el total no se presenta como cero.

#### Scenario: Partial experiment
- **WHEN** hay 7 éxitos, 2 fallos y 1 unknown en 10 celdas
- **THEN** se reporta éxito conservador 0.70, cobertura 0.90 y rango por missingness [0.70,0.80].

### Requirement: Recovery exposure

El sistema SHALL medir recovery_success sólo para fallos recuperables inyectados observados y publicar tasa de exposición.

#### Scenario: Fault not reached
- **WHEN** el agente no llega al paso donde se inyecta el fallo
- **THEN** no obtiene crédito de recuperación y la baja exposición queda visible.

