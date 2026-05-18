# Wayline API

FastAPI HTTP layer. Thin routes + serialization; analytical logic lives in `engine/`.

See the [root README](../README.md) for the full project setup. This file documents the four endpoints.

## Run locally

From the **project root** (not from `api/` — `main.py` uses dotted package imports):

```bash
uv run uvicorn api.main:app --port 8000
```

OpenAPI docs render at `http://localhost:8000/docs`.

## Endpoints

| Method | Path           | Returns |
|--------|----------------|---------|
| `GET`  | `/health`      | dependency reachability (postgres, redis) |
| `GET`  | `/milestones`  | top 12 actionable milestones with persona dominance |
| `GET`  | `/paths`       | top 10 activation paths (5-event ordered prefixes) |
| `GET`  | `/specs`       | parsed `data/experiment_specs.json` |

All endpoints are `GET`-only. CORS allows `http://localhost:3000` and `http://localhost:8000` only.

### `/health`

Always returns HTTP 200 — info endpoint, not a Kubernetes liveness probe. `status` is `"ok"` when both dependencies are reachable, `"degraded"` otherwise.

```json
{"status": "ok", "postgres": "connected", "redis": "connected"}
```

Errors are surfaced as `"error: <ExceptionClassName>"` — connection strings and credentials are never echoed.

### `/milestones`, `/paths`, `/specs`

Engine failures return HTTP 503 with `{"detail": "error: <ExceptionClassName>"}`. `/specs` additionally returns 503 with an actionable message if `data/experiment_specs.json` is missing (`"specs not yet generated — run synthesize.py"`).

Compute is on-demand; no Redis caching is wired in yet. `mine_milestones`'s raw output is the natural caching seam if throughput justifies it.

## Engine CLIs

The engine is also runnable directly, without the HTTP layer:

```bash
uv run python api/engine/run.py          # mining + paths + persona-dominance tables
uv run python api/engine/synthesize.py   # top 10 specs → JSON + rendered text
```
