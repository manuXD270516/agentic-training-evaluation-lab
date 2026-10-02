## Why

Una demo convincente no demuestra fiabilidad de un agente. Necesitamos medir resultados y procesos con casos versionados, evidencia auditable y reglas que permitan detectar regresiones sin confundir cambios del benchmark con mejoras del sistema.

## What Changes

- Definir filosofía de evaluación, modelo de dominio y contratos del runner y evaluadores.
- Especificar `agentic-benchmark-v1`, trazas, métricas y reproducción/replay.
- Establecer comparación pareada, gates de política y límites de LLM-as-a-judge.
- Proponer arquitectura y roadmap incremental M0–M12; baseline scripted primero, ReAct en M6 y Planner/Executor en M7.
- Entregar exclusivamente diseño y specs; todas las tareas de implementación quedan pendientes.

## Capabilities

### New Capabilities

- `experiment-management`: configuraciones inmutables, experimentos y ciclos de ejecución.
- `versioned-benchmarks`: escenarios sintéticos, oráculos privados, particiones y retrieval.
- `agent-execution`: contratos neutrales de runner, modelos, herramientas y presupuestos.
- `trace-capture`: evidencia ordenada, duradera, correlacionable y redactada.
- `evaluation-engine`: evaluación determinística, estructural, de políticas y judge auxiliar.
- `metric-reporting`: métricas con denominadores, cobertura y costes explícitos.
- `reproducibility`: manifiestos, aislamiento, replay y re-evaluación inmutable.
- `experiment-comparison`: comparabilidad, incertidumbre y detección de regresiones.

### Modified Capabilities

Ninguna: proyecto nuevo.

## Impact

Futuros componentes: API FastAPI, worker aislado, PostgreSQL y dashboard React. OpenTelemetry correlacionará la operación; PGVector se incorporará sólo en M9. No se instalan dependencias de aplicación, no se llaman modelos ni se generan resultados en este change de diseño. MCP queda como decisión opcional posterior; no forma parte del camino crítico.

Alcance normativo: los contratos aquí propuestos abarcan M0–M12 y se implementarán por hitos. Nuevos patrones después de Planner/Executor requieren deltas adicionales antes de implementarse. Este change no se archivará como realizado mientras queden requisitos sin verificar.
