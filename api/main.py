from pathlib import Path
import os

import psycopg
import redis
from dotenv import load_dotenv
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parent.parent
env_path = ROOT / ".env"
if not env_path.exists():
    env_path = ROOT / ".env.example"
load_dotenv(env_path)

app = FastAPI(title="Wayline API")


def _check_postgres() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        return "error: DATABASE_URL not set"
    try:
        with psycopg.connect(url, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return "connected"
    except Exception as exc:
        return f"error: {type(exc).__name__}"


def _check_redis() -> str:
    url = os.environ.get("REDIS_URL")
    if not url:
        return "error: REDIS_URL not set"
    try:
        client = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
        try:
            client.ping()
        finally:
            client.close()
        return "connected"
    except Exception as exc:
        return f"error: {type(exc).__name__}"


@app.get("/health")
def health() -> dict[str, str]:
    pg = _check_postgres()
    rd = _check_redis()
    status = "ok" if pg == "connected" and rd == "connected" else "degraded"
    return {"status": status, "postgres": pg, "redis": rd}
