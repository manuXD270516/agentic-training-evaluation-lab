# agentic-training-evaluation-lab

Laboratorio para medir éxito, herramientas, argumentos, evidencia, recuperación, latencia y coste de sistemas agénticos mediante experimentos reproducibles.

**Estado: M0–M1 y tarea 3.1 de M2: contratos del runner, baseline scripted offline, eventos mínimos sellados en `Trace`/`TraceEvent` y worker que toma celdas `queued`. Todavía no hay gateway real de tools (3.2), leases/fencing (3.3), dataset piloto de 14 casos (M5), métricas calculadas ni resultados de negocio.**

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
  src/evallab/worker/    Worker: heartbeat, polling SKIP LOCKED, /health; leases/fencing en 3.3
  src/evallab/runner/    Contratos, sink en memoria y patrón scripted
  src/evallab/settings.py  Configuración por entorno y SandboxPolicy (red denegada)
  src/evallab/domain/    Máquinas de estados y vocabularios cerrados del design
  src/evallab/db/        Modelos SQLAlchemy, migraciones Alembic y `evallab-migrate`
  tests/                 Unitarios; tests/integration/ usa PostgreSQL real
  Dockerfile             Imagen de API, worker y migrate (bases fijadas por digest, no root)
frontend/                React + Vite + TypeScript; página inicial sin vistas funcionales
compose.yaml             PostgreSQL 18.6, migrate (una vez), API y worker
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

## Aislamiento del worker

El worker sólo está en la red Compose `internal` (sin salida a Internet), no publica puertos y no monta el host ni el socket de Docker. `WORKER_AGENT_NETWORK` sólo admite `deny` y `WORKER_AGENT_HOST_TOOLS` sólo `false`. Esto prepara el aislamiento; el sandbox de ejecución de agentes se implementa en M2.
