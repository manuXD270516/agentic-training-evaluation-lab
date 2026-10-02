## Purpose

Comparar variantes bajo reglas predeclaradas y detectar regresiones sin ocultar incompatibilidad o incertidumbre.

## ADDED Requirements

### Requirement: Comparability gate

El sistema SHALL exigir condiciones controladas de metrics.md y pares por escenario/version, repetición y seed; SHALL bloquear ranking de cohortes incompatibles.

#### Scenario: Different dataset versions
- **WHEN** baseline y candidato usan distintos oráculos o dataset hashes
- **THEN** se bloquea ranking controlado y sólo se permite vista descriptiva etiquetada.

#### Scenario: Mixed execution modes
- **WHEN** se intenta comparar live contra replay como rendimiento del modelo
- **THEN** se rechaza la comparación controlada.

### Requirement: Uncertainty and regression gates

El sistema SHALL usar bootstrap pareado por escenario y gates predeclarados de metrics.md; SHALL conservar regresiones individuales y no declarar superioridad con muestra insuficiente.

#### Scenario: Small pilot
- **WHEN** hay dos escenarios por categoría
- **THEN** el informe es descriptivo y no declara superioridad estadística.

#### Scenario: Aggregate masks policy regression
- **WHEN** el éxito global sube pero aparece una nueva violación crítica
- **THEN** falla el gate de política y se identifica la evidencia del escenario.

### Requirement: Incomplete comparison

El sistema SHALL mostrar pares faltantes y bloquear aprobación automática con unknown o trazas incompletas; SHALL mantener diagnóstico y fallos críticos confirmados.

#### Scenario: Missing candidate run
- **WHEN** falta una celda del candidato
- **THEN** el reporte se marca incomplete y no imputa éxito ni descarta el par silenciosamente.

