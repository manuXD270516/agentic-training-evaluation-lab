# versioned-benchmarks Specification

## Purpose
Publicar datasets sintéticos verificables con cobertura por categoría, oráculos privados y retrieval.

## Requirements

### Requirement: Versioned benchmark contract

El sistema SHALL validar manifests y escenarios conforme benchmark-format.md, resolver referencias por versión y digest, calcular el hash de contenido sobre JSON canónico RFC 8785 sin el propio hash ni `created_at`, y rechazar contenido incompatible, refs `latest` o mutable. La publicación de una versión es atómica: un fallo no deja filas de esa versión. Los oráculos son operadores de la allowlist de design.md, nunca código del dataset.

#### Scenario: Invalid publication
- **WHEN** un escenario carece de oráculo resoluble o referencia a una fixture o tool no existente
- **THEN** se rechaza la publicación sin producir una versión parcialmente válida.

#### Scenario: Mutation of published version
- **WHEN** se cambia una respuesta esperada de una versión publicada
- **THEN** se exige versión y digest nuevos.

#### Scenario: Duplicate identity or content
- **WHEN** se republica el mismo `(id, version)` con otro contenido, o el mismo `content_hash` con otra identidad
- **THEN** se rechaza con conflicto y se identifica la versión ya publicada.

### Requirement: Private oracle boundary

El sistema SHALL separar ScenarioPublicView del oráculo, fallos programados y etiquetas privadas; SHALL mantener familias disjuntas entre splits. GET `/scenarios/{id}/versions/{version}` expone la vista pública; el oráculo queda en `/oracle` y no forma parte del contrato del runner.

#### Scenario: Oracle leakage prevention
- **WHEN** un agente solicita escenario o contexto
- **THEN** recibe sólo la vista pública y herramientas autorizadas, sin expected, qrels, fault schedule, split, family_id ni recovery.

#### Scenario: Family crosses splits
- **WHEN** dos escenarios de la misma `family_id` declaran splits distintos en un dataset
- **THEN** se rechaza la publicación del dataset.

### Requirement: Category coverage and honest release

El sistema SHALL calcular `coverage_class` al publicar un dataset (`pilot` = 14 casos, dos por categoría, sólo split `dev`; `complete_v1` = 70 únicos, 10 por categoría, 42/28; cualquier otro recuento es `incomplete`) y no aceptar una etiqueta de cobertura declarada por el cliente.

#### Scenario: Incomplete dataset
- **WHEN** sólo están disponibles los 14 casos del piloto
- **THEN** el dataset queda `coverage_class=pilot` y no se publica como cobertura de 70 casos.

### Requirement: Retrieval benchmark

El sistema SHALL ofrecer un benchmark específico retrieval con corpus, chunks, qrels, configuración de recuperación y reglas de evidencia versionados.

#### Scenario: Unanswerable query
- **WHEN** el corpus no contiene respuesta para una consulta declarada sin respuesta
- **THEN** la abstención se evalúa como resultado esperado y recall/MRR son N/A.

#### Scenario: Unsupported citation
- **WHEN** la respuesta cita un chunk existente que no soporta la afirmación
- **THEN** falla el check de evidencia y no se acredita grounding.
