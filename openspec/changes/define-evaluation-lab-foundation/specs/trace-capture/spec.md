## Purpose

Preservar evidencia completa de decisiones observables, llamadas y resultados sin depender de sampling.

## ADDED Requirements

### Requirement: Durable ordered evidence

El sistema SHALL persistir eventos conforme trace-format.md, con ids, secuencia, causalidad y terminalidad; SHALL separar la evidencia de evaluación de telemetría muestreada.

#### Scenario: Collector unavailable
- **WHEN** el collector OpenTelemetry está caído y la persistencia está disponible
- **THEN** la traza de evaluación conserva todos los eventos.

#### Scenario: Scripted events are sealed after in-memory capture
- **WHEN** un run scripted termina
- **THEN** el sink asigna secuencia en memoria, se sella contador/digest/completeness y `GET /runs/{id}/trace` devuelve los eventos ordenados sin oráculo.

### Requirement: Trace integrity

El sistema SHALL sellar contador y digest, detectar duplicados conflictivos y marcar trazas incompletas; SHALL impedir éxito verificado con evidencia obligatoria ausente.

#### Scenario: Missing tool result
- **WHEN** se pierde el resultado de una llamada iniciada por caída del worker
- **THEN** la traza se declara incompleta y la evaluación no fabrica resultado.

#### Scenario: Repeated event
- **WHEN** se reenvía un event_id con el mismo digest
- **THEN** se confirma sin duplicarlo; otro digest produce error de integridad.

### Requirement: Safe observable trace

El sistema SHALL redactar secretos antes de persistir/exportar y registrar acciones/observaciones sin exigir chain-of-thought oculto.

#### Scenario: Sensitive payload
- **WHEN** una respuesta de tool contiene un campo secreto
- **THEN** se elimina o sustituye antes de persistir y se documenta si el replay queda limitado.

