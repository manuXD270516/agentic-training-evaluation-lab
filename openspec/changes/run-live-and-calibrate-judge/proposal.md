## Why

`define-evaluation-lab-foundation` dejó implementados y verificados todos sus requisitos, pero tres trabajos no dependen del código sino de decisiones, personas o dinero: ejecutar los patrones con un proveedor de pago, anotar a mano el set de calibración del judge y corregir un problema que sólo apareció al ejecutar un modelo real. La ejecución live local con `qwen2.5:7b` (Ollama) acertó 1 de 14 escenarios del piloto y casi todos los fallos fueron de estructura: la vista pública de un escenario describe la tarea pero no publica el contrato de salida (el `output_schema` vive en el oráculo privado), así que un modelo no sabe qué claves debe devolver. Los agentes scripted no lo notaban porque sus guiones ya conocen las claves.

## What Changes

- Publicar el contrato de salida (schema de la respuesta, sin valores esperados ni checks) en la vista pública de los escenarios, mediante versiones nuevas de escenarios, datasets y benchmarks y sin tocar las versiones publicadas.
- Ejecutar ReAct y Planner/Executor con un proveedor de pago dentro de un presupuesto fijado antes de correr, usando la configuración ya preparada (`evallab-benchmark run-live`, `docs/live-run.md`).
- Completar la doble anotación humana del set de calibración, adjudicar desacuerdos y analizar el acuerdo del judge; el judge sólo deja de ser `experimental` si el protocolo se cumple.

## Capabilities

### New Capabilities

Ninguna.

### Modified Capabilities

- `versioned-benchmarks`: la vista pública incluye el contrato de salida de cada escenario.

## Impact

Nuevas versiones de `agentic-benchmark-pilot` y `agentic-benchmark-v1` con sus locks; los resultados y locks anteriores se conservan. La ejecución de pago genera coste y necesita la clave y el presupuesto del responsable; la anotación necesita dos personas independientes. No cambia el protocolo de comparación ni las métricas.
