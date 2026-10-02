# reproducibility Specification

## Purpose
Hacer auditables las condiciones de ejecución y distinguir replay exacto de variabilidad en modelos remotos.

## Requirements

### Requirement: Complete reproducibility manifest

El sistema SHALL sellar hashes de datos, configuración, código, entorno, fixtures, evaluadores y parámetros conforme design.md; SHALL rechazar baseline oficial con árbol dirty o referencias no resueltas.

#### Scenario: Changed fixture
- **WHEN** se intenta reproducir un run con fixture de distinto hash
- **THEN** se rechaza como reproducción idéntica y se exige otro manifest.

### Requirement: Strict offline replay

El sistema SHALL separar live, replay y reevaluation; SHALL verificar request digest en replay y nunca hacer fallback a llamadas live.

#### Scenario: Replay divergence
- **WHEN** el request emitido no coincide con el request grabado
- **THEN** el replay termina replay_mismatch sin llamar al proveedor.

#### Scenario: Offline replay
- **WHEN** se reproduce un run live con traza sellada completa
- **THEN** los resultados de tools y modelo se sirven desde la grabación sin ejecutar fixtures ni abrir red, y el replay es una cohorte distinta que referencia su run de origen.

#### Scenario: Unverifiable recording
- **WHEN** la traza de origen está incompleta, no coincide con su digest sellado o tiene llamadas redactadas no reproducibles
- **THEN** el replay se rechaza antes de crearse y no se degrada a ejecución live.

### Requirement: Honest remote reproducibility

El sistema SHALL registrar revisión del proveedor o desconocida y no garantizar identidad live a partir de seed/temperatura; SHALL reiniciar estado y reloj sintético entre runs.

#### Scenario: Remote variability
- **WHEN** dos runs con igual seed producen distinta respuesta del proveedor
- **THEN** se conservan ambos y se reporta variabilidad sin alterar snapshots.

#### Scenario: Fixture contamination
- **WHEN** un run modifica estado sintético
- **THEN** el siguiente comienza con su snapshot inicial sin heredar la modificación.
