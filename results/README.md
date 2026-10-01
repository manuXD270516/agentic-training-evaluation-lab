# Resultados registrados

Cada carpeta es la salida literal de un comando del repositorio, con el manifest sellado y el lock de hashes del benchmark usados. No se editan a mano.

| Carpeta | Comando | Qué mide | Qué no mide |
|---|---|---|---|
| `m5-pilot-scripted/` | `evallab-benchmark run pilot --repetitions 5 --out ../results/m5-pilot-scripted` (desde `backend/`, 2026-10-01) | Que el harness ejecuta las 140 celdas del piloto (14 escenarios × 2 agentes scripted × 5 seeds), que los oráculos aceptan la solución de referencia y que la suite detecta cada error deliberado del agente defectuoso | Rendimiento de ningún modelo: los dos agentes son scripted y sus resultados (100 % y 0 %) están fijados por construcción |

Las cifras de éxito y de proceso del piloto son deterministas y se reproducen con cualquier seed. La latencia (`latency_ms`) depende de la máquina y sólo describe esta ejecución. El piloto tiene dos escenarios por categoría: el reporte es descriptivo y no contiene intervalos ni afirmaciones estadísticas.
