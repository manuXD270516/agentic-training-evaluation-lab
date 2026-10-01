# agentic-training-evaluation-lab

Laboratorio para medir éxito, herramientas, argumentos, evidencia, recuperación, latencia y coste de sistemas agénticos mediante experimentos reproducibles.

**Estado (2026-10-01).** Ningún resultado de este repositorio mide un LLM: todas las ejecuciones usan agentes scripted o "modelos" de fixture (guiones versionados, sin red ni llamadas de pago). El detalle y la evidencia de cada tarea están en [tasks.md](openspec/changes/define-evaluation-lab-foundation/tasks.md).

| Hito | Estado |
|---|---|
| M0–M3 | Bootstrap, modelo de experimentos, runner scripted con gateway de tools, límites, retries y leases; suite determinística `deterministic-core@1.0.0` y perfil `core-metrics@1.0.0` |
| M4 | Persistencia idempotente y redactada de trazas, export verificable, replay estricto offline y spans OpenTelemetry correlacionados (la evidencia no depende del collector) |
| M5 | Piloto sintético `agentic-benchmark-pilot@0.1.0` (14 escenarios, lock de hashes) y [reporte descriptivo de 5 repeticiones](results/m5-pilot-scripted/report.md) con dos agentes scripted (mide el harness) |
| M6 | Gateway de modelo neutral (uso, coste por price snapshot, errores tipados) y ReAct `react@1.0.0` con replay de modelo, verificados con modelos de fixture. **Pendiente:** elegir proveedor, modelo y presupuesto live (decisión y gasto del responsable); el adaptador live existe desactivado |
| M7 | Planner/Executor `planner_executor@1.0.0` (plan validado, dependencias, presupuesto global) y [comparación descriptiva pareada](results/m7-react-vs-planner-fixture/comparison.md) con ReAct sobre fixtures (sus diferencias las fija el guion) |
| M8 | Judge auxiliar sin tools (rúbrica y prompt versionados, abstención, suite de inyección, `scope=judge`, nunca cambia gates) con consumo aparte del agente. **Pendiente:** doble anotación humana del [set de calibración](docs/judge-calibration.md); el judge es `experimental` |
| M9 | Corpus, chunks, embeddings (`hash-embed@1.0.0`, léxico y determinista) y retriever exacto versionados en PGVector; `agentic-retrieval-v1` (10 escenarios) con recall/MRR@k y citas verificadas contra qrels privados; [ejecución scripted](results/m9-retrieval-scripted/report.md) |
| M10 | Dashboard React de sólo lectura (`frontend/`): experimentos, task_success por agente y categoría con cobertura y rango de missingness, métricas con una columna por estado (pass, fail, unknown, N/A, error), celdas ausentes visibles, filtros por categoría, agente, estado y modo live/replay, y navegación score → evento de la traza paginada con versiones y trazas incompletas señaladas |
| M11–M12 | Pendientes: protocolo estadístico de comparación, benchmark de 70 casos y demo |

API disponible (localhost:8000; esquema OpenAPI en `/docs`):

| Método y ruta | Efecto |
|---|---|
| `POST /fixtures`, `GET /fixtures/{hash}` | Fixture sintética identificada por SHA-256 |
| `POST /tool-definitions`, `GET .../{id}/versions/{version}` | Tool publicada e inmutable |
| `POST /scenarios`, `GET .../{id}/versions/{version}` | Escenario; GET es vista pública |
| `GET /scenarios/{id}/versions/{version}/oracle` | Oráculo privado (evaluador, no runner) |
| `POST /datasets`, `GET .../{id}/versions/{version}` | Dataset; `coverage_class` lo calcula el servidor |
| `POST /benchmarks`, `GET .../{id}/versions/{version}` | Protocolo de evaluación sobre un dataset |
| `POST /model-configurations`, `POST /agent-configurations`, `GET .../{id}/versions/{version}` | Configuraciones inmutables de modelo y de agente (patrón, parámetros, roles→modelo, tools); exigen `Idempotency-Key` |
| `POST /price-snapshots`, `GET /price-snapshots/{hash}` | Tarifa por millón de tokens direccionada por contenido (`synthetic` explícito); un límite `max_cost_usd` exige precio en cada modelo |
| `POST /experiments` | Crea un draft; exige `Idempotency-Key` |
| `GET` / `PATCH /experiments/{id}` | Lee o edita un draft; editar uno sellado devuelve 409 |
| `POST /experiments/{id}/seal` | Sella con manifest RFC 8785 + SHA-256; exige benchmark y agentes |
| `GET /experiments/{id}/manifest` | Manifest sellado y su hash |
| `GET /experiments` | Lista de experimentos (más recientes primero) |
| `GET /experiments/{id}/report?mode=live\|replay` | Reporte descriptivo del modo pedido (por defecto `live`): N = celdas programadas, S/N, cobertura, rango de missingness, métricas micro/macro, consumo y latencia; sin afirmaciones estadísticas |
| `GET /experiments/{id}/cells?mode=live\|replay` | Una fila por celda programada, también las que no tienen run: estado del run, completeness de la traza, última evaluación y `task_success` (`null` = sin evaluar) |
| `POST /experiments/{id}/runs` | Encola una celda (202, `queued`); exige `Idempotency-Key` |
| `GET /experiments/{id}/runs`, `GET /runs/{id}` | Consulta de celdas; el resultado aparece tras la ejecución |
| `GET /runs/{id}/trace?after_sequence=&limit=` | Eventos ordenados, digest y completeness de la traza sellada; con `limit` (1–500) devuelve una página keyset con `page.next_after_sequence` (`null` en la última) |
| `GET /runs/{id}/trace/events/{event_id}` | Un evento de la traza: destino de las referencias de evidencia de los scores |
| `GET /runs/{id}/trace/export`, `GET /runs/{id}/trace/manifest` | Export JSONL completo (redactado) y manifest con digest y SHA-256; verificable con `uv run evallab-verify-trace manifest.json events.jsonl` |
| `POST /runs/{id}/replays` | Replay offline de un run live con traza completa (202, `mode=replay`); diverge como `replay_mismatch`, nunca cae a live; exige `Idempotency-Key` |
| `POST /runs/{id}/evaluations` | Evalúa un run terminal con suite y perfil versionados; exige `Idempotency-Key`; cada reevaluación crea una evaluación nueva; `{"judge_model": ...}` añade el judge auxiliar si el escenario declara una dimensión subjetiva |
| `GET /runs/{id}/evaluations`, `GET /evaluations/{id}` | Historial de evaluaciones con report, scores y su estado (`pass/fail/unknown/not_applicable/error`) |

Primer change: [define-evaluation-lab-foundation](openspec/changes/define-evaluation-lab-foundation/proposal.md).

- [Diseño: filosofía, dominio, arquitectura, interfaces y toolchain (§9)](openspec/changes/define-evaluation-lab-foundation/design.md)
- [Formato del benchmark](openspec/changes/define-evaluation-lab-foundation/benchmark-format.md)
- [Catálogo de métricas](openspec/changes/define-evaluation-lab-foundation/metrics.md)
- [Formato de trazas](openspec/changes/define-evaluation-lab-foundation/trace-format.md)
- [Roadmap M0–M12](openspec/changes/define-evaluation-lab-foundation/roadmap.md)
- [Tareas](openspec/changes/define-evaluation-lab-foundation/tasks.md)

Las specs del change describen comportamiento futuro. `openspec/specs` permanecerá sin specs consolidadas hasta implementar, verificar y archivar el change. Validar documentación no demuestra que el sistema funcione.

## Estructura

```text
backend/                 Python 3.14.7 + uv (paquete `evallab`)
  src/evallab/api/       Control plane FastAPI: /health, /health/ready
  src/evallab/worker/    Worker: heartbeat, claim con lease + fencing token, /health
  src/evallab/runner/    Contratos, límites, sink en memoria, patrón scripted y gateway de tools
  src/evallab/benchmarks/ Suites sintéticas declarativas, lock de hashes y CLI evallab-benchmark
  src/evallab/retrieval/  Chunking, embedding hash-embed y retriever exacto sobre PGVector
  src/evallab/evaluation/ Suites determinística, retrieval y judge; métricas y calibración
  src/evallab/settings.py  Configuración por entorno y SandboxPolicy (red denegada)
  src/evallab/domain/    Máquinas de estados y vocabularios cerrados del design
  src/evallab/db/        Modelos SQLAlchemy, migraciones Alembic y `evallab-migrate`
  tests/                 Unitarios; tests/integration/ usa PostgreSQL real
  Dockerfile             Imagen de API, worker y migrate (bases fijadas por digest, no root)
frontend/                Dashboard React + Vite + TypeScript de sólo lectura (proxy /api -> API local)
compose.yaml             PostgreSQL 18.6 con pgvector, migrate (una vez), API, worker y collector OTel opcional
otel/collector.yaml      Configuración del collector opcional (sólo log de spans)
.env.example             Configuración de ejemplo sin credenciales
.github/workflows/ci.yml CI: OpenSpec, backend, frontend y smoke de Compose
```

## Requisitos

uv 0.12.20, Node 22.23.1, pnpm 12.4.2 y Docker con Compose v2. uv descarga Python 3.14.7 si no está disponible. No se necesitan claves de proveedores de modelos.

## Arranque desde un clone limpio

PowerShell:

```powershell
Copy-Item .env.example .env
# Editar .env y definir POSTGRES_PASSWORD (Compose no arranca si está vacía).
docker compose up --build --detach --wait
curl.exe http://127.0.0.1:8000/health/ready
docker compose exec -T worker python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8001/health/ready').read().decode())"
docker compose down --volumes
```

Desarrollo local fuera de contenedores (con `docker compose up --detach --wait db`):

```powershell
cd backend; uv sync --locked
uv run --locked --env-file ../.env evallab-migrate
uv run --locked --env-file ../.env evallab-api
pnpm install --frozen-lockfile; pnpm --filter evallab-frontend run dev   # http://127.0.0.1:5173
```

El dashboard (`http://127.0.0.1:5173`) lee la API a través del proxy `/api` de Vite; `EVALLAB_API_URL` cambia el destino (por defecto `http://127.0.0.1:8000`). Para tener datos, `uv run --locked --env-file ../.env evallab-benchmark run pilot --repetitions 1 --out <carpeta>` crea, ejecuta y evalúa un experimento del piloto.

## Checks (idénticos a CI)

```powershell
pnpm install --frozen-lockfile
pnpm run spec:validate
pnpm --filter evallab-frontend run format:check
pnpm --filter evallab-frontend run typecheck
pnpm --filter evallab-frontend run test
pnpm --filter evallab-frontend run build
cd backend
uv sync --locked
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked mypy
$env:EVALLAB_REQUIRE_DB = "1"; uv run --locked --env-file ../.env pytest
```

Los tests de integración crean una base de datos vacía y temporal por sesión en el servidor de `POSTGRES_*` (requiere `docker compose up --detach --wait db`), aplican las migraciones y la eliminan al terminar. Sin `EVALLAB_REQUIRE_DB=1` se omiten si PostgreSQL no está disponible; CI los exige.

## Piloto sintético (M5)

```powershell
cd backend
uv run --locked --env-file ../.env evallab-benchmark publish pilot   # publica y compara con locks/pilot.json
uv run --locked --env-file ../.env evallab-benchmark run pilot --repetitions 5 --out ../results/m5-pilot-scripted
```

`run` sella un experimento con los dos agentes, ejecuta las 140 celdas con las fases del worker, las evalúa y escribe `report.json`/`report.md` con el manifest y el lock. El resultado registrado está en [results/](results/README.md).

`agentic-benchmark-pilot@0.1.0` son 14 escenarios `dev` inventados (dos por categoría) sobre tools de lookup; no es el benchmark v1 de 70 casos ni sirve para afirmaciones estadísticas. Sus dos agentes son scripted: `pilot-scripted-reference` ejecuta la solución de referencia y `pilot-scripted-faulty` comete un error deliberado por escenario (tool prohibida, unidades erróneas, cita inventada, inyección obedecida...). Ambos prueban el harness y los evaluadores, no un LLM.

## Patrones con modelo (sin llamadas de pago)

`evallab-benchmark run pilot-models ...` ejecuta el mismo piloto con `pilot-react-fixture`: un agente ReAct cuyo "modelo" es un guion versionado (proveedor `fixture`, tokens y tarifa sintéticos). Prueba el bucle decisión → acción → observación, la contabilidad de tokens/coste y el replay sin red; sus resultados se etiquetan `attribution=fixture_model` y no miden ningún LLM.

Existe un adaptador opcional compatible con chat completions de OpenAI, **desactivado por defecto** (`MODEL_GATEWAY_LIVE_ENABLED`, ver `.env.example`). Sólo se ha probado contra un servidor local que imita el formato; no se ha ejecutado contra ningún proveedor real y el worker de Compose no tiene salida a Internet.

## Observabilidad (opcional)

Sin `OTEL_EXPORTER_OTLP_ENDPOINT` la telemetría está desactivada. Para ver spans de API, worker, tools y evaluador:

```powershell
$env:OTEL_EXPORTER_OTLP_ENDPOINT = "http://otel-collector:4318"
docker compose --profile otel up --build --detach --wait
docker compose logs otel-collector
```

Los spans llevan ids (`evallab.run_id`, `attempt_id`, `tool.call_id`, `evaluation_id`) y nunca payloads; cada evento de traza guarda `otel_trace_id`/`otel_span_id` para correlacionar. Si el collector cae, sólo se pierden spans: la traza de evaluación se persiste en PostgreSQL igual.

## Aislamiento del worker

El worker sólo está en la red Compose `internal` (sin salida a Internet), no publica puertos y no monta el host ni el socket de Docker. `WORKER_AGENT_NETWORK` sólo admite `deny` y `WORKER_AGENT_HOST_TOOLS` sólo `false`. Las tools se ejecutan sólo sobre fixtures en memoria, sin sistema de archivos, procesos ni red.

Cada ejecución registra un intento en `run_attempts` con lease (`WORKER_LEASE_S`, 300 s por defecto) y fencing token. Un lease vencido se reclama con token nuevo hasta `WORKER_MAX_ATTEMPTS` (2); agotados, el run termina `failed` con `infrastructure_error`. Un worker con token obsoleto no puede persistir su traza. `WORKER_WORKER_ID` identifica al worker (por defecto `host-pid`).
