# Roadmap

Todos los hitos están pendientes de implementación. Esta entrega cubre únicamente su definición. Cada hito debe verificar escenarios de las capabilities indicadas en tasks.md y adjuntar evidencia antes de considerarse terminado. Si una decisión cambia comportamiento, actualizar deltas OpenSpec antes de código; no archivar un diseño como si estuviera implementado.

| Hito | Entrega | Criterio de salida |
|---|---|---|
| M0 — Bootstrap | Estructura FastAPI/React, entorno PostgreSQL, dependencias fijadas, CI | Arranque limpio, checks de formato/tipos y validación OpenSpec; sin modelos live requeridos |
| M1 — Experiment model | Entidades, snapshots, versiones, estados y API de control | Rechaza mutación sellada, duplicados conflictivos y refs inválidas; migración en DB vacía |
| M2 — Agent Runner | Contratos, baseline scripted, gateways y sandbox | Ejecuta fixture offline, límites y permisos; estado aislado; consumo visible |
| M3 — Deterministic evals | Oráculos, estructura, políticas y Scores | Casos positivos/negativos y mutación de resultados; unknown/error no pasan |
| M4 — Trace capture | Persistencia completa, export, OTel y replay | Captura sin collector, detecta truncamiento, replay sin red y mismatch seguro |
| M5 — First benchmark | Piloto de 14 escenarios dev, dos por categoría | Publicación validada, 5 repeticiones offline, reporte descriptivo con todos los denominadores |
| M6 — ReAct | Primer patrón con modelo live a través del gateway | Mismos contratos, replay grabado, budgets, baseline etiquetada y manifest completo |
| M7 — Planner/Executor | Roles y planes con presupuesto global | Comparación pareada con ReAct, trazabilidad de ambos roles y coste total |
| M8 — LLM Judge | Judge auxiliar calibrado y suite adversarial | Calibración documentada según design.md, abstención y coste separado; nunca sustituye gates |
| M9 — RAG benchmark | `agentic-retrieval-v1`, PGVector, diez casos retrieval | Recall/MRR/citas/abstención verificadas con corpus/embeddings versionados |
| M10 — Dashboard | React: experimentos, categorías, trazas, evaluaciones y cobertura | Navegación desde score a evidencia; unknown/N/A e incompletos visibles; sin resultados ficticios |
| M11 — Comparison reports | Reporte exportable, gates y bootstrap pareado | Detecta incompatibilidad, regresiones e incertidumbre; preserva baseline histórica |
| M12 — Public benchmark/demo | 70 casos, protocolo público, demo sanitizada y guía de reproducción | Ejecución offline desde entorno limpio, resultados reales con manifests; limitaciones publicadas |

M2 necesita eventos mínimos en memoria para M3; M4 agrega captura durable/OTel, no redefine el contrato. M7 puede producir tablas descriptivas provisionales; M11 implementa el protocolo completo de comparación. M5 incluye retrieval por fixtures; PGVector se añade en M9. El benchmark se completa con familias nuevas en M12 y cualquier modificación de casos existentes incrementa versión.

Después de M12, priorizar mediante changes separados: Router (enrutamiento medible), Reflection y Critic (ganancia frente a coste adicional), Evaluator-Optimizer (riesgo de sobreajuste al evaluador), Supervisor (delegación/presupuesto compartido), Human-in-the-loop (tiempo de espera, decisiones humanas auténticas y autoridad). Su orden final dependerá de hipótesis y resultados, no de completar una lista de patrones.

MCP es opcional después de M11, condicionado a demostrar un consumidor que se beneficie de descubrimiento/interoperabilidad frente a la API directa. No bloquea M12.
