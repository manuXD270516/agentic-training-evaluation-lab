## Purpose

Ejecutar patrones agénticos con contratos comunes, herramientas aisladas y límites observables.

## ADDED Requirements

### Requirement: Pattern neutral runner

El runner SHALL consumir configuración y vista pública del escenario, emitir Trace y devolver Result/Metrics bajo los contratos design.md; SHALL identificar versión y roles del patrón.

#### Scenario: Pattern substitution
- **WHEN** se sustituye ReAct por Planner/Executor con iguales condiciones experimentales
- **THEN** los roles comparten el presupuesto global y reportan consumo con el mismo perfil de métricas.

### Requirement: Tool validation and isolation

El sistema SHALL validar identidad, argumentos y autorización antes de invocar una tool; SHALL aislar fixtures por run y denegar acceso al host/red fuera de gateways autorizados.

#### Scenario: Invalid arguments
- **WHEN** el agente emite argumentos con tipo incorrecto
- **THEN** no se ejecuta la tool y se conserva el intento fallido en la traza.

#### Scenario: Forbidden tool
- **WHEN** el agente solicita una acción prohibida
- **THEN** se deniega y registra una violación aunque el resultado final sea correcto.

#### Scenario: Isolated fixture state
- **WHEN** dos runs invocan la misma tool con efectos sobre la misma fixture
- **THEN** cada run parte del estado inicial del escenario y los cambios de uno no son visibles en el otro ni alteran la fixture publicada.

### Requirement: Bounded execution and retries

El runner SHALL aplicar límites de pasos, llamadas, tokens, tiempo y coste estimado cuando configurado; SHALL registrar todos los retries y no repetir efectos ambiguos sin reconciliación.

#### Scenario: Budget exhausted
- **WHEN** se alcanza el máximo de pasos antes de completar la tarea
- **THEN** el run termina budget_exceeded y no inicia otra llamada.

#### Scenario: Ambiguous side effect
- **WHEN** una tool con efectos devuelve timeout sin confirmar ejecución
- **THEN** no se reintenta automáticamente sin comprobar idempotencia o estado.

### Requirement: Incremental pattern delivery

El sistema SHALL identificar baseline scripted como prueba del harness, introducir ReAct en M6 y Planner/Executor en M7 y no presentar patrones diferidos como implementados.

#### Scenario: Scripted pilot
- **WHEN** M5 se ejecuta sin modelo live
- **THEN** los resultados indican scripted y no se atribuyen a rendimiento de un LLM.

#### Scenario: Scripted live without model
- **WHEN** un run `live` usa `pattern=scripted` con `pattern_parameters.script` de acciones tool/final
- **THEN** el runner no llama a ModelGateway, `usage.model_calls` es 0, el resultado lleva etiqueta `scripted` y la traza incluye evidencia de tools y un evento terminal.

#### Scenario: Deferred patterns are not presented as implemented
- **WHEN** un run usa `pattern=react` o `pattern=planner_executor` antes de M6/M7
- **THEN** el run termina en fallo tipado y no se reporta como ejecución de esos patrones.

