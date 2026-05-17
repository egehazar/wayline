# Wayline API

FastAPI HTTP layer. Thin routes + serialization; analysis logic lives in `engine/`.

## Run locally

Prerequisites:

- Dependencies installed at the repo root (`uv sync`).
- Postgres + Redis up: `docker compose up -d` from the repo root.
- A `.env` at the repo root (copy from `.env.example`). If `.env` is missing, the app falls back to `.env.example`.

Start the dev server from inside `api/`:

```bash
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

## Endpoints

### `GET /health`

Reports liveness of the API process and reachability of its dependencies.

Always returns HTTP 200 — this is an info endpoint, not a Kubernetes liveness probe. `status` is `"ok"` when both dependencies are reachable, `"degraded"` otherwise.

Sample (services up):

```json
{"status": "ok", "postgres": "connected", "redis": "connected"}
```

Sample (services down):

```json
{"status": "degraded", "postgres": "error: OperationalError", "redis": "error: ConnectionError"}
```

Error messages are exception class names only — connection strings and credentials are never echoed back.

Quick check:

```bash
curl -s localhost:8000/health
```
