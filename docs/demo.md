# Demo de sólo lectura

La demo enseña experimentos, métricas, comparaciones y trazas sin permitir escrituras ni exponer oráculos privados.

## Qué protege

| Operación | Desarrollo local (por defecto) | Demo (`EVALLAB_READ_ONLY=1`) | Con `EVALLAB_ADMIN_TOKEN` |
|---|---|---|---|
| Lecturas públicas (experimentos, reportes, celdas, trazas redactadas, comparaciones, vista pública de escenarios) | abiertas | abiertas | abiertas |
| Escrituras (`POST`, `PUT`, `PATCH`, `DELETE`) | abiertas | 403 `read_only`, también con token | 401 sin `Authorization: Bearer <token>` |
| Oráculo privado (`GET .../oracle`) | abierto | 403 `private_operation`; con token configurado, 401 sin él | 401 sin token |

El token se compara en tiempo constante, nunca aparece en respuestas ni en spans y debe tener al menos 24 caracteres. Las trazas y sus exports ya se guardan redactados (M4). Un test (`tests/integration/test_demo_access.py`) recorre `results/` y falla si encuentra patrones de credenciales o claves de oráculo (`expected`, `qrels`, `relevant`, `family_id`).

## Arranque

```powershell
docker compose up --detach --wait db
cd backend
uv run --locked --env-file ../.env evallab-migrate
# Datos de la demo: experimentos scripted del benchmark v1 (sin modelos ni red).
uv run --locked --env-file ../.env evallab-benchmark run v1 --repetitions 1 --out $env:TEMP\evallab-demo
$env:EVALLAB_READ_ONLY = "1"; uv run --locked --env-file ../.env evallab-api
# En otra terminal: dashboard servido por Vite con proxy /api -> 127.0.0.1:8000
pnpm --filter evallab-frontend run build; pnpm --filter evallab-frontend run preview   # http://127.0.0.1:4173
```

Con Compose, `EVALLAB_READ_ONLY=1` y `EVALLAB_ADMIN_TOKEN` en `.env` se pasan al servicio `api`.

## Límites

No hay usuarios ni roles: el token es único y administrativo. La demo no está desplegada en ningún servidor público (no se crearon recursos en la nube); publicarla exige además TLS y un proxy delante, fuera del alcance de este repositorio.
