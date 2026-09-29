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

