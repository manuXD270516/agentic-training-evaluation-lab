## ADDED Requirements

### Requirement: Public output contract

El sistema SHALL publicar en la vista pública de cada escenario el schema de la salida esperada (tipos, claves requeridas y restricciones estructurales), sin valores esperados, checks del oráculo, qrels ni etiquetas privadas; SHALL hacerlo sólo en versiones nuevas de escenario, dataset y benchmark.

#### Scenario: Agent sees the output contract
- **WHEN** un runner pide la vista pública de un escenario publicado con contrato de salida
- **THEN** recibe el schema de la respuesta y ningún valor esperado ni check del oráculo.

#### Scenario: Published versions stay immutable
- **WHEN** se añade el contrato de salida a un escenario ya publicado
- **THEN** se publica una versión nueva con otro hash y los experimentos y locks anteriores siguen referenciando la versión original.
