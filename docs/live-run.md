# Ejecución live (tarea 7.2)

El gateway de modelo tiene un adaptador compatible con la API de chat completions de OpenAI (`evallab/runner/live.py`). Está **desactivado por defecto**: ninguna ruta llama a un proveedor si no se define `MODEL_GATEWAY_LIVE_ENABLED=true` junto con URL y clave. La clave nunca se escribe en trazas, reportes ni `run.json`.

## Ejecutado: modelo local con Ollama (sin coste)

| Parámetro | Valor |
|---|---|
| Servidor | Ollama 0.34.3 local, endpoint `http://127.0.0.1:11434/v1` (sin autenticación; la "clave" es un marcador) |
| Modelo | `qwen2.5:7b`, revisión `sha256:845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e` (Q4_K_M, 7.6B) |
| Patrón | `react@1.0.0`, temperatura 0, `max_tokens` 512, seed enviada |
| Coste | sin tarifa: el coste se informa `unknown`, nunca 0 (la energía local no se mide) |

```powershell
$env:MODEL_GATEWAY_LIVE_ENABLED = "true"
$env:MODEL_GATEWAY_BASE_URL = "http://127.0.0.1:11434/v1"
$env:MODEL_GATEWAY_API_KEY = "ollama-local-no-auth"
$env:MODEL_GATEWAY_TIMEOUT_S = "300"
cd backend
uv run --env-file ../.env evallab-benchmark run-live pilot --model qwen2.5:7b `
  --revision sha256:845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e `
  --seed-support supported --temperature 0 --max-tokens 512 --repetitions 1 `
  --out ../results/m6-react-live-ollama-qwen2.5-7b
```

Resultado registrado en `results/m6-react-live-ollama-qwen2.5-7b/` (2026-10-02): 14 celdas, 1 éxito (`pilot-er-ambiguous-timeout`), 31 llamadas al modelo, 19 a tools y 14 118 tokens observados. **Es una medición real de un único modelo local pequeño en una repetición, no un ranking.** La causa dominante de los fallos es estructural: el modelo responde en prosa o con claves distintas, porque la vista pública del escenario describe la tarea pero **no publica el contrato de salida** (el `output_schema` vive en el oráculo privado). Corregirlo exige una versión nueva de los escenarios con el contrato público y se registra en el change `run-live-and-calibrate-judge`.

## Configuración lista, no ejecutada: proveedor de pago

| Decisión | Propuesta | Por qué |
|---|---|---|
| Proveedor | cualquier endpoint compatible con chat completions de OpenAI (`MODEL_GATEWAY_BASE_URL`) | es lo que implementa el adaptador; no se añade SDK |
| Modelo | uno pequeño con tool calling, fijado por nombre exacto y revisión si el proveedor la expone (por ejemplo la variante "mini" vigente del proveedor elegido) | el piloto mide el harness; un modelo grande no cambia la conclusión y multiplica el coste |
| Tarifa | la publicada por el proveedor el día de la ejecución, pasada en `--price-input/--price-output` (USD por millón de tokens) | se guarda como PriceSnapshot con fecha y fuente; no se inventa |
| Presupuesto | `--max-cost-usd 5` por experimento y `--max-model-calls 8` por run | el runner reserva el coste máximo de cada llamada antes de hacerla y para al llegar al límite |
| Alcance inicial | `pilot`, 1 repetición (14 celdas); después `v1` con 5 repeticiones (350 celdas) | con ~1 000 tokens por celda observados en local, `v1` completo son ~0.5 M tokens |

```powershell
# Sólo con una clave real del responsable, nunca versionada (.env está en .gitignore).
$env:MODEL_GATEWAY_LIVE_ENABLED = "true"
$env:MODEL_GATEWAY_BASE_URL = "https://<proveedor>/v1"
$env:MODEL_GATEWAY_API_KEY = "<clave>"
cd backend
uv run --env-file ../.env evallab-benchmark run-live pilot --model <modelo> --revision <revisión> `
  --price-input <USD/Mtok entrada> --price-output <USD/Mtok salida> `
  --price-source "<URL de la tarifa y fecha>" --max-cost-usd 5 --max-model-calls 8 `
  --repetitions 1 --out ../results/m6-react-live-<modelo>
```

`run-live` se niega a ejecutar si el proveedor no está habilitado y `--max-cost-usd` exige tarifa. Esta ejecución queda pendiente de la decisión y la clave del responsable (change `run-live-and-calibrate-judge`).
