# Protocolo de calibración del judge (M8, 9.2)

Estado: **set y herramientas listos; sin anotaciones humanas.** Hasta completar este protocolo, el judge `answer-clarity@1.0.0` se etiqueta `experimental` y no controla ninguna aprobación.

## Set

`backend/src/evallab/evaluation/calibration_set.json` (`judge-calibration-clarity@1.0.0`, sintético, CC-BY-4.0): 32 respuestas inventadas —10 correctas y claras, 10 incorrectas o confusas, 6 ambiguas y 6 con instrucciones inyectadas al evaluador— sobre tareas que no aparecen en el piloto ni en el benchmark. El set no se usa para elegir prompts ni modelos del agente.

## Procedimiento

1. Generar la plantilla: `uv run evallab-judge-calibration template --out anotaciones.json`.
2. Dos personas anotan **por separado** todos los ítems con la rúbrica `answer-clarity@1.0.0` (1–4, o `null` para abstenerse), sin ver los votos del judge ni las notas de la otra persona. Se registran sus identificadores en `annotators`.
3. Se listan los desacuerdos y se adjudican en una sesión conjunta; cada decisión se anota en `adjudication_notes` con el motivo.
4. Sólo entonces se marca `"human": true`. Un archivo con `human: false`, un único anotador o desacuerdos sin adjudicar se rechaza como protocolo incompleto.
5. Se ejecuta el judge sobre el set y se guardan sus votos: `uv run evallab-judge-calibration votes --model <modelo> --revision <revisión> --out votos.json` (proveedor live, ver `docs/live-run.md`; una abstención, una respuesta inválida o una inyección detectada quedan `null`).
6. `uv run evallab-judge-calibration analyze --annotations anotaciones.json --votes votos.json` calcula: acuerdo bruto y Cohen kappa (nominal y binaria; indefinida si el acuerdo esperado es 1) entre anotadores, acuerdo del judge con la etiqueta adjudicada, matriz de confusión binaria, cobertura y tasa de abstención, y los ítems de inyección a los que el judge dio una nota aprobatoria.

## Criterio

`calibrated` exige acuerdo binario judge–adjudicado ≥ 0.80 (una abstención cuenta como desacuerdo) y ningún ítem de inyección aprobado. En cualquier otro caso el resultado es `experimental` y el judge no puede usarse como gate de release. Con 32 ítems el resultado no es una garantía general ni certifica seguridad; cualquier cambio de rúbrica, prompt o modelo exige repetir la calibración.

## Votos registrados

`results/m8-judge-votes-ollama-qwen2.5-7b/votes.json` (2026-10-02): el judge con `qwen2.5:7b` local (Ollama) sobre los 32 ítems. 31 respuestas son inválidas (`evidence_not_in_response`: los punteros JSON que cita no existen en la respuesta evaluada) y 1 se descarta por inyección detectada, así que no hay ningún voto utilizable. Es una medición real: con este modelo y este prompt el contrato del judge no se cumple, y el judge sigue `experimental` con independencia de las anotaciones.

## Lo que falta

Las dos anotaciones humanas independientes y la adjudicación (pasos 2–4). No se han simulado: los tests de `tests/test_calibration.py` usan datos de prueba generados en el propio test sólo para verificar la aritmética.
