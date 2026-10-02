# evaluation-engine Specification

## Purpose
Evaluar resultados y procesos con oráculos verificables, políticas y juicio auxiliar explícitamente limitado.

## Requirements

### Requirement: Deterministic first evaluation

El sistema SHALL preferir oráculos determinísticos; SHALL exigir outcome, estructura obligatoria y políticas para task_success; SHALL conservar raw_outcome_pass separado.

#### Scenario: Correct answer with forbidden action
- **WHEN** el resultado cumple el oráculo pero el agente intenta una tool prohibida
- **THEN** raw_outcome_pass es pass y task_success es fail.

#### Scenario: Evaluator failure
- **WHEN** un evaluador obligatorio falla internamente
- **THEN** el resultado es error/unknown y no se convierte en pass.

### Requirement: Structural and policy evaluation

El sistema SHALL validar salida, elección de tools, schemas, semántica de argumentos, acciones prohibidas y soporte de evidencia; SHALL limitar afirmaciones de detección de hallucination al alcance verificable.

#### Scenario: Valid JSON wrong units
- **WHEN** los argumentos cumplen tipos pero usan una unidad incorrecta
- **THEN** schema_argument_validity pasa y argument_accuracy falla.

#### Scenario: Unverifiable claim
- **WHEN** no existe oráculo ni evidencia suficiente para verificar una afirmación abierta
- **THEN** el estado es unknown o rating auxiliar, no hecho verificado.

#### Scenario: Output mutation
- **WHEN** la salida cambia valor, tipo, campo obligatorio o añade un campo no permitido respecto al oráculo
- **THEN** el check de outcome o de estructura correspondiente falla y task_success falla.

#### Scenario: Nonexistent citation
- **WHEN** la salida cita un id de evidencia que no existe en la traza
- **THEN** el check de evidencia falla aunque el valor de la respuesta sea correcto.

#### Scenario: Unsupported citation
- **WHEN** la salida cita un evento existente que no es el resultado exitoso de una tool
- **THEN** el check de evidencia falla como cita sin soporte.

### Requirement: Restricted LLM judge

El sistema SHALL exigir razón de ausencia de oráculo, rúbrica/modelo versionados, schema, evidencia y abstención para judge; SHALL impedir que sobreescriba gates determinísticos.

#### Scenario: Judge disagrees with oracle
- **WHEN** el judge aprueba una respuesta que falla un check obligatorio
- **THEN** el fallo obligatorio permanece y el voto se conserva separado.

#### Scenario: Injected judge instructions
- **WHEN** la respuesta evaluada pide ignorar la rúbrica
- **THEN** se trata como dato no confiable y se evalúa con la suite de inyección sin habilitar tools.

### Requirement: Judge calibration disclosure

El sistema SHALL aplicar el protocolo de calibración de design.md antes de etiquetar el judge como calibrado y publicar acuerdo, abstención, sesgos y coste separado.

#### Scenario: Calibration below threshold
- **WHEN** el acuerdo adjudicado es menor que 0.80 o falla la suite de inyección
- **THEN** el judge se etiqueta experimental y no controla aprobación de release.
