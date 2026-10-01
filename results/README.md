# Resultados registrados

Cada carpeta es la salida literal de un comando del repositorio, con el manifest sellado y el lock de hashes del benchmark usados. No se editan a mano.

| Carpeta | Comando | Qué mide | Qué no mide |
|---|---|---|---|
| `m5-pilot-scripted/` | `evallab-benchmark run pilot --repetitions 5 --out ../results/m5-pilot-scripted` (desde `backend/`, 2026-10-01) | Que el harness ejecuta las 140 celdas del piloto (14 escenarios × 2 agentes scripted × 5 seeds), que los oráculos aceptan la solución de referencia y que la suite detecta cada error deliberado del agente defectuoso | Rendimiento de ningún modelo: los dos agentes son scripted y sus resultados (100 % y 0 %) están fijados por construcción |

| `m7-react-vs-planner-fixture/` | `evallab-benchmark compare pilot-models --baseline pilot-react-fixture --candidate pilot-planner-executor-fixture --repetitions 5 --out ../results/m7-react-vs-planner-fixture` (2026-10-01) | Que dos experimentos gemelos sólo difieren en las variables declaradas del agente, que las 140 celdas se emparejan por escenario/repetición/seed, que cada fallo se conserva con su run y evaluación, y que tokens y coste se suman por rol | Calidad de ReAct o Planner/Executor: ambos "modelos" son guiones de fixture; los 10 `new_failure` (dos escenarios × 5 seeds) los fija el guion del candidato a propósito, y tokens y precio son sintéticos |

| `m9-retrieval-scripted/` | `evallab-benchmark run retrieval-v1 --repetitions 5 --out ../results/m9-retrieval-scripted` (2026-10-01) | Que el retriever versionado en PGVector devuelve el chunk relevante en top-3 para la referencia (recall 1.0, MRR medio 0.938) y que la suite detecta citas obsoletas, inventadas o vacías, alucinaciones e inyección en el agente defectuoso | Calidad de un buscador real: `hash-embed` es léxico y el corpus tiene 18 chunks inventados; los agentes son scripted |

Las cifras de éxito y de proceso del piloto son deterministas y se reproducen con cualquier seed. La latencia (`latency_ms`) depende de la máquina y sólo describe esta ejecución. El piloto tiene dos escenarios por categoría: el reporte es descriptivo y no contiene intervalos ni afirmaciones estadísticas.
