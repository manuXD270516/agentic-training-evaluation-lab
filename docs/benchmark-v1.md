# agentic-benchmark-v1@1.0.0

Ficha del dataset completo de M12 (tarea 13.1). Definición: `backend/src/evallab/benchmarks/v1.py`; lock de hashes: `backend/src/evallab/benchmarks/locks/v1.json` (`coverage_class=complete_v1`).

## Composición

70 escenarios sintéticos en español sobre una tienda inventada, diez por cada una de las siete categorías. Cada categoría tiene cinco familias de dos escenarios: tres familias `dev` y dos `held-out`, de modo que el reparto es 42/28 y ninguna familia cruza splits (el servidor lo rechaza al publicar).

| Categoría | Familias dev | Familias held-out |
|---|---|---|
| tool_selection | stock frente a precio, precio frente a stock, sin tool necesaria | almacén del SKU, convertir con la tool adecuada |
| tool_arguments | km→mi, cantidad del pedido, °C→°F | kg→lb, orden de los operandos de una resta |
| reasoning | cadena aritmética con paréntesis, franjas con restricciones, media con calculator | orden lógico, días laborables |
| multi_step_execution | stock y luego pedido, no pedir sin stock, precio × cantidad | convertir y sumar, sumar el stock de dos SKU |
| error_recovery | fallo transitorio en inventario, timeout ambiguo de un pedido, fallo transitorio en precios | fallo transitorio en calculator, tool caída tras agotar reintentos |
| policy_compliance | reembolso no autorizado, inyección en documentos recuperados, límite de unidades | datos personales de terceros, producto descatalogado |
| retrieval | los diez escenarios de `agentic-retrieval-v1` sin cambios (6 dev / 4 held-out, corpus en PGVector) | |

Las tools de los escenarios nuevos son versiones `1.1.0` de las tools sintéticas del piloto; sus fixtures se generan a partir de las llamadas que declaran los escenarios y sus guiones, así que los resultados son aritméticamente correctos y toda llamada de los agentes de prueba está cubierta. Las tools no tocan red, procesos ni sistema de archivos.

## Agentes de comprobación

`v1-scripted-reference` resuelve los 70 escenarios y `v1-scripted-faulty` comete un error controlado distinto en cada uno (tool parecida, argumentos invertidos, paso omitido, reintento ciego, dato inventado, inyección obedecida...). Sólo prueban que los oráculos aceptan una solución correcta y rechazan errores concretos; no miden ningún modelo.

## Licencia, procedencia y contaminación

- Licencia: CC-BY-4.0 (la misma que el piloto y `agentic-retrieval-v1`). **Pendiente de confirmar por el responsable del repositorio**: es una elección del autor de los datos, no una decisión técnica.
- Procedencia: escrito a mano y generado por código de este repositorio; no contiene datos personales ni de sistemas reales. Nombres, SKU, pedidos y teléfonos son inventados.
- Contaminación: escenarios, oráculos y splits son públicos en el repositorio, también `held-out`. El split sólo separa familias para no ajustar prompts o modelos con ellas; no se afirma resistencia a contaminación de modelos entrenados después de la publicación.
- Estas declaraciones se publican también en `comparison_rules.declarations` del benchmark, dentro de su hash.

## Limitaciones

Tareas cortas y deterministas por lookup; no cubren conversación, tareas largas ni herramientas reales. Diez escenarios por categoría permiten aplicar el protocolo de comparación de M11, pero los intervalos describen incertidumbre dentro de este dataset, no validez externa.
