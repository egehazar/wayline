from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg
import redis
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from api.engine.milestones import (
    compute_retention,
    load_data,
    mine_milestones,
    persona_dominance,
)
from api.engine.paths import compute_paths
from api.engine.synthesis import ExperimentSpec

ROOT = Path(__file__).resolve().parent.parent
env_path = ROOT / ".env"
if not env_path.exists():
    env_path = ROOT / ".env.example"
load_dotenv(env_path)

app = FastAPI(title="Wayline API")

# CORS — Next.js dev server + this API's own origin. GET only (read-only API).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8000"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Compute on demand; no Redis caching yet. mine_milestones output is the
# natural caching seam (stable across calls on the same data), but at ~1-2s
# per call we're well inside portfolio-demo latency. Layer Redis in when
# throughput justifies the invalidation logic.


class MilestoneResponse(BaseModel):
    name: str
    n_did: int
    n_did_pct: float
    retain_did: float
    retain_didnt: float
    lift: float
    persona_dominance: dict[str, float]


class PathResponse(BaseModel):
    sequence: list[str]
    sequence_str: str
    n_users: int
    retain_pct: float
    lift: float


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


@app.get("/milestones", response_model=list[MilestoneResponse])
def list_milestones() -> list[MilestoneResponse]:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise HTTPException(status_code=503, detail="error: DATABASE_URL not set")
    try:
        users_df, events_df = load_data(db_url)
        users_df = compute_retention(users_df, events_df)
        actionable = mine_milestones(users_df, events_df, min_sample=200, max_share=0.25)
        n_total = users_df.height
        out: list[MilestoneResponse] = []
        for r in actionable.head(12).iter_rows(named=True):
            out.append(MilestoneResponse(
                name=r["name"],
                n_did=r["n_did"],
                n_did_pct=100.0 * r["n_did"] / n_total,
                retain_did=r["retain_did"],
                retain_didnt=r["retain_didnt"],
                lift=r["lift"],
                persona_dominance=persona_dominance(users_df, events_df, r),
            ))
        return out
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"error: {type(exc).__name__}")


@app.get("/paths", response_model=list[PathResponse])
def list_paths() -> list[PathResponse]:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise HTTPException(status_code=503, detail="error: DATABASE_URL not set")
    try:
        users_df, events_df = load_data(db_url)
        users_df = compute_retention(users_df, events_df)
        paths_df = compute_paths(users_df, events_df, prefix_length=5, min_sample=100)
        return [
            PathResponse(
                sequence=r["sequence"],
                sequence_str=r["sequence_str"],
                n_users=r["n_users"],
                retain_pct=r["retain_pct"],
                lift=r["lift"],
            )
            for r in paths_df.head(10).iter_rows(named=True)
        ]
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"error: {type(exc).__name__}")


@app.get("/specs", response_model=list[ExperimentSpec])
def list_specs() -> list[ExperimentSpec]:
    specs_path = ROOT / "data" / "experiment_specs.json"
    if not specs_path.exists():
        raise HTTPException(
            status_code=503,
            detail="specs not yet generated — run synthesize.py",
        )
    try:
        data = json.loads(specs_path.read_text())
        return [ExperimentSpec(**d) for d in data]
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"error: {type(exc).__name__}")
