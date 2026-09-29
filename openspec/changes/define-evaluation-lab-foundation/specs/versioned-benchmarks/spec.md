## Purpose

Publicar datasets sintéticos verificables con cobertura por categoría, oráculos privados y retrieval.

## ADDED Requirements

### Requirement: Versioned benchmark contract

El sistema SHALL validar manifests y escenarios conforme benchmark-format.md, resolver referencias por versión/hash y rechazar contenido incompatible o mutable.

#### Scenario: Invalid publication
- **WHEN** un escenario carece de oráculo resoluble o referencia a una fixture no existente
- **THEN** se rechaza la publicación sin producir una versión parcialmente válida.

#### Scenario: Mutation of published version
- **WHEN** se cambia una respuesta esperada de una versión publicada
- **THEN** se exige versión y digest nuevos.

### Requirement: Private oracle boundary

El sistema SHALL separar ScenarioPublicView del oráculo, fallos programados y etiquetas privadas; SHALL mantener familias disjuntas entre splits.

#### Scenario: Oracle leakage prevention
- **WHEN** un agente solicita escenario o contexto
- **THEN** recibe sólo la vista pública y herramientas autorizadas, sin expected ni qrels privados.

### Requirement: Category coverage and honest release

El sistema SHALL etiquetar M5 como piloto de 14 casos y exigir 70 casos únicos, 10 por categoría y split 42/28 para la publicación completa v1 definida en benchmark-format.md.

#### Scenario: Incomplete dataset
- **WHEN** sólo están disponibles los 14 casos del piloto
- **THEN** el reporte declara piloto y no publica una cobertura de 70 casos.

### Requirement: Retrieval benchmark

El sistema SHALL ofrecer un benchmark específico retrieval con corpus, chunks, qrels, configuración de recuperación y reglas de evidencia versionados.

#### Scenario: Unanswerable query
- **WHEN** el corpus no contiene respuesta para una consulta declarada sin respuesta
- **THEN** la abstención se evalúa como resultado esperado y recall/MRR son N/A.

#### Scenario: Unsupported citation
- **WHEN** la respuesta cita un chunk existente que no soporta la afirmación
- **THEN** falla el check de evidencia y no se acredita grounding.

