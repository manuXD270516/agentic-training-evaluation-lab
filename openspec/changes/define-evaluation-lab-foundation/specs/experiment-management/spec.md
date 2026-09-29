## Purpose

Administrar experimentos y configuraciones con identidad inmutable y ciclos de vida auditables.

## ADDED Requirements

### Requirement: Immutable experiment snapshot

El sistema SHALL sellar configuraciones, benchmark, presupuestos, seeds y plan de comparación antes de crear runs; SHALL rechazar mutaciones del snapshot sellado.

#### Scenario: Configuration changed after seal
- **WHEN** se intenta cambiar el prompt de un experimento sellado
- **THEN** se rechaza la edición y se requiere una nueva configuración y experimento.

### Requirement: Run identity and lifecycle

El sistema SHALL crear una sola celda lógica por experimento, escenario/version, configuración, repetición y seed; SHALL conservar intentos y estados terminales según design.md; SHALL rechazar toda transición de estado de Experiment, Run o Evaluation no declarada en design.md.

#### Scenario: Reopening a terminal run
- **WHEN** se intenta pasar un run `completed` a `running`
- **THEN** se rechaza la transición y el run conserva su estado terminal.

#### Scenario: Cancellation before start
- **WHEN** se cancela un experimento con celdas todavía `queued`
- **THEN** esas celdas pasan a `cancelled` sin iniciarse y siguen contando en el reporte.

#### Scenario: Duplicate submission
- **WHEN** se repite una solicitud con igual idempotency key y payload
- **THEN** se devuelve la misma identidad sin duplicar ejecución lógica.

#### Scenario: Conflicting submission
- **WHEN** se reutiliza la clave con otro payload
- **THEN** se rechaza con conflicto y no se encola otra ejecución.

### Requirement: Historical evaluation integrity

El sistema SHALL asociar Scores a Evaluation y digest de Trace; SHALL crear una nueva Evaluation al cambiar suite sin sobrescribir la anterior.

#### Scenario: Reevaluation
- **WHEN** una traza histórica se evalúa con otra suite
- **THEN** ambas evaluaciones conservan versión, evidencia y procedencia.

