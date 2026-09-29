# agentic-training-evaluation-lab

Laboratorio para medir éxito, herramientas, argumentos, evidencia, recuperación, latencia y coste de sistemas agénticos mediante experimentos reproducibles.

**Estado: M0 (bootstrap) y tarea 2.1 de M1 implementados. Hay esqueletos de API, worker y frontend con endpoints de salud, y el esquema relacional de las entidades del dominio con sus estados validados en PostgreSQL. Todavía no hay sellado ni API de experimentos (2.2), manifests de benchmark (2.3), dataset, benchmarks, métricas calculadas ni resultados.**

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
  src/evallab/worker/    Worker: heartbeat, /health, /health/ready; sin cola todavía (M2)
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
