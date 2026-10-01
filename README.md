# agentic-training-evaluation-lab

Laboratorio para medir éxito, herramientas, argumentos, evidencia, recuperación, latencia y coste de sistemas agénticos mediante experimentos reproducibles.

**Estado: M0–M3 y parte de M4: contratos del runner, baseline scripted offline, gateway de tools sobre fixtures declarativas con validación JSON Schema, allowlist y estado aislado por run, límites de pasos/llamadas/deadline, retries trazados sobre fallos inyectados, eventos sellados en `Trace`/`TraceEvent`, worker con leases y fencing token, suite determinística `deterministic-core@1.0.0` y perfil de métricas `core-metrics@1.0.0` con evaluaciones versionadas, y (M4) persistencia idempotente de eventos, redacción de secretos antes del digest, export verificable, replay estricto offline y spans OpenTelemetry correlacionados con la traza (opcionales; la evidencia no depende del collector). Todavía no hay dataset piloto de 14 casos (M5), benchmarks publicados ni resultados.**

API disponible (localhost:8000; esquema OpenAPI en `/docs`):

| Método y ruta | Efecto |
|---|---|
| `POST /fixtures`, `GET /fixtures/{hash}` | Fixture sintética identificada por SHA-256 |
| `POST /tool-definitions`, `GET .../{id}/versions/{version}` | Tool publicada e inmutable |
| `POST /scenarios`, `GET .../{id}/versions/{version}` | Escenario; GET es vista pública |
| `GET /scenarios/{id}/versions/{version}/oracle` | Oráculo privado (evaluador, no runner) |
| `POST /datasets`, `GET .../{id}/versions/{version}` | Dataset; `coverage_class` lo calcula el servidor |
| `POST /benchmarks`, `GET .../{id}/versions/{version}` | Protocolo de evaluación sobre un dataset |
| `POST /experiments` | Crea un draft; exige `Idempotency-Key` |
| `GET` / `PATCH /experiments/{id}` | Lee o edita un draft; editar uno sellado devuelve 409 |
| `POST /experiments/{id}/seal` | Sella con manifest RFC 8785 + SHA-256; exige benchmark y agentes |
| `GET /experiments/{id}/manifest` | Manifest sellado y su hash |
| `POST /experiments/{id}/runs` | Encola una celda (202, `queued`); exige `Idempotency-Key` |
| `GET /experiments/{id}/runs`, `GET /runs/{id}` | Consulta de celdas; el resultado aparece tras la ejecución |
| `GET /runs/{id}/trace` | Eventos ordenados, digest y completeness de la traza sellada |
| `GET /runs/{id}/trace/export`, `GET /runs/{id}/trace/manifest` | Export JSONL completo (redactado) y manifest con digest y SHA-256; verificable con `uv run evallab-verify-trace manifest.json events.jsonl` |
| `POST /runs/{id}/replays` | Replay offline de un run live con traza completa (202, `mode=replay`); diverge como `replay_mismatch`, nunca cae a live; exige `Idempotency-Key` |
| `POST /runs/{id}/evaluations` | Evalúa un run terminal con suite y perfil versionados; exige `Idempotency-Key`; cada reevaluación crea una evaluación nueva |
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
  src/evallab/settings.py  Configuración por entorno y SandboxPolicy (red denegada)
  src/evallab/domain/    Máquinas de estados y vocabularios cerrados del design
  src/evallab/db/        Modelos SQLAlchemy, migraciones Alembic y `evallab-migrate`
  tests/                 Unitarios; tests/integration/ usa PostgreSQL real
  Dockerfile             Imagen de API, worker y migrate (bases fijadas por digest, no root)
frontend/                React + Vite + TypeScript; página inicial sin vistas funcionales
compose.yaml             PostgreSQL 18.6, migrate (una vez), API, worker y collector OTel opcional (perfil otel)
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

## Checks (idénticos a CI)

```powershell
pnpm install --frozen-lockfile
pnpm run spec:validate
pnpm --filter evallab-frontend run format:check
pnpm --filter evallab-frontend run typecheck
pnpm --filter evallab-frontend run build
cd backend
uv sync --locked
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked mypy
$env:EVALLAB_REQUIRE_DB = "1"; uv run --locked --env-file ../.env pytest
```

Los tests de integración crean una base de datos vacía y temporal por sesión en el servidor de `POSTGRES_*` (requiere `docker compose up --detach --wait db`), aplican las migraciones y la eliminan al terminar. Sin `EVALLAB_REQUIRE_DB=1` se omiten si PostgreSQL no está disponible; CI los exige.

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
