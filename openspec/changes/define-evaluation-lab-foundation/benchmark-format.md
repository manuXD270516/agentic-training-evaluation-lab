# Benchmark format v1

Contrato documental; el ejemplo siguiente no es todavía un dataset ejecutable. Serialización futura: manifest JSON y escenarios JSONL, validados con JSON Schema versionado y referencias por hash. Schema incompatible incrementa major; cualquier cambio de contenido crea versión y digest nuevos. `agentic-benchmark-v1` es el nombre de familia; `1.0.0` será la primera publicación completa.

## Manifest

Campos obligatorios: `schema_version`, `dataset_id`, `dataset_version`, `content_hash`, `synthetic=true`, `license`, `generator_version`, `scenario_refs` (id, version, digest), `split_manifest`, `category_counts`, `fixture_refs`, `corpus_refs`, `created_at`. El hash de contenido se calcula sobre payload canónico sin el propio hash ni timestamp de publicación. Cambiar split, oráculo o fixture cambia digest; no se permite sobrescribir versión publicada.

Benchmark manifest separado: `benchmark_id`, `version`, `dataset_ref`, `selection`, `evaluator_suite_ref`, `metric_profile_ref`, `comparison_rules_ref`, `default_repetitions=5`, `seed_schedule`, `budgets`. Todo ref incluye versión y digest. No resolver `latest` durante ejecución.

## Scenario record

| Campo | Contrato |
|---|---|
| schema_version, id, version, digest | Identidad y schema inmutables |
| primary_category, tags, difficulty, split, family_id | Categoría única; tags múltiples; variantes de misma familia quedan en mismo split |
| task | Instrucción pública y input sintético |
| tools | Definiciones/versiones permitidas; schemas de entrada/salida |
| environment | Fixture snapshot, reloj, estado inicial; fault schedule privado |
| limits | max_steps, max_model_calls, max_tool_calls, max_tokens, deadline_ms, max_retries; coste opcional con precio obligatorio |
| expected | Oráculo privado: output schema, checks determinísticos, tolerancias/unidades, evidence expectations, acciones requeridas/prohibidas |
| evaluation | Suite/version, checks obligatorios, dimensiones aplicables, políticas, optional judge rationale |
| retrieval | Corpus/chunks relevantes por query, top_k, reglas de cita y corpus versionado; sólo si aplica |
| recovery | trigger, fault id, retry permitido y estado final esperado; sólo si aplica |

Ejemplo legible YAML del registro que se serializará a JSON (refs simbólicas por ser diseño):

```yaml
schema_version: '1.0'
id: tool-arguments-001
version: '1.0.0'
primary_category: tool_arguments
tags: [arithmetic, exact_output]
difficulty: easy
split: dev
family_id: sum-integers-a
task:
  instruction: Usa calculator para sumar 17 y 25. Devuelve total y evidence_ids.
  input: {a: 17, b: 25}
tools: [calculator@1.0.0]
environment: {fixture_ref: calculator-fixture@1.0.0}
limits:
  max_steps: 4
  max_model_calls: 4
  max_tool_calls: 2
  max_tokens: 2000
  deadline_ms: 30000
  max_retries: 0
expected:
  output_schema_ref: total-with-evidence@1.0.0
  checks:
    - {operator: json_value_equals, path: /total, value: 42}
    - {operator: required_tool, tool: calculator, min_calls: 1}
    - {operator: arguments_equal, tool: calculator, value: {op: add, a: 17, b: 25}}
    - {operator: evidence_from_successful_call, path: /evidence_ids}
evaluation:
  suite_ref: deterministic-core@1.0.0
  required_checks: [outcome, output_structure, required_tool, semantic_arguments, evidence]
  applicable_metrics: [task_success, tool_accuracy, argument_accuracy]
```

El manifest de publicación sustituirá todas las refs simbólicas por refs con digest. Se rechazan registros sin identidad/hash válido, límites positivos, checks resolubles, output schema válido o categorías conocidas. Oráculos son operadores declarativos de una allowlist, nunca código arbitrario del dataset. La igualdad numérica fija tolerancia absoluta/relativa por campo; ausencia de tolerancia exige igualdad exacta. Arrays ordenados por defecto; igualdad de conjuntos debe declararse.

## Coverage plan

Objetivo de publicación M12: **70 casos**, 10 por categoría; dentro del objetivo final de 50–100. M5 entrega 14 casos dev, dos por categoría; no se presenta como benchmark v1 completo. M9 entrega 10 casos retrieval, incluidos los dos anteriores si no cambian su significado. M12 completa 70: 6 dev + 4 held-out por categoría (42/28), con familias disjuntas. Casos multi-etiqueta sólo cuentan en su categoría primaria.

| Categoría | Cobertura mínima en los 10 casos finales |
|---|---|
| tool_selection | herramientas parecidas, sin tool necesaria, tool inexistente, tool prohibida |
| tool_arguments | tipos, campos faltantes, enum, unidades, rangos, argumentos válidos pero semánticamente erróneos |
| retrieval | documento correcto, distractores, versiones antiguas, sin respuesta, citas y prompt injection |
| reasoning | aritmética, restricciones y lógica verificable; no calificar chain-of-thought |
| multi_step_execution | dependencias, estado final, pasos omitidos, orden parcial válido |
| error_recovery | timeout/transient, reintento válido, límite de reintentos, efectos ambiguos |
| policy_compliance | acción denegada, exfiltración sintética, evidencia falsa, instrucción maliciosa recuperada |

M5 retrieval usa lookup determinístico sobre fixtures; M9 agrega benchmark `agentic-retrieval-v1` que selecciona los 10 casos retrieval del dataset, con corpus sintético versionado y PGVector. Fijar documentos, chunk ids/text hashes, segmentación, embedding/model revision/dimensión, distancia, índice, filtros, top_k y tie-break por chunk_id. Guardar embeddings o su artefacto reproducible; registrar orden real recuperado. No asumir índice aproximado idéntico entre máquinas.

Cada query tiene qrels privados por chunk y respuesta derivable; consultas sin respuesta esperan abstención. Recall/MRR son N/A cuando no hay relevantes; medir abstención correcta como check de tarea. Hallucination determinística sólo sobre hechos con oráculo explícito. Se requiere citar chunk + intervalo o campo verificable, no basta citar un id existente.

## Publication and contamination

Registrar quién accede a held-out y fecha. No seleccionar prompts/modelos con sus resultados; después de publicar todos los casos, etiquetar held-out como público y no reclamar resistencia a contaminación. Versiones nuevas requieren change de dataset y re-ejecución comparable. Publicar licencia, procedencia sintética, limitaciones y resultados reales separados de fixtures. El conjunto de calibración del judge es independiente de dev/held-out del benchmark.
