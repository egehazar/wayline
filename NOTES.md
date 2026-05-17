# Wayline — Engineering Notes

A behavioral product intelligence engine that finds activation milestones in raw event data and turns them into testable experiment specs.

---

## 1. What it is (one-liner)

Wayline ingests raw product event streams and automatically surfaces the activation paths, milestones, and cohort patterns that correlate with retention — then drafts experiment specs from those findings.

Short version: *"It finds the 'aha moments' in product data and turns them into experiment specs."*

---

## 2. Why it exists

The famous activation insights — Slack's "30 messages in 7 days," Facebook's "7 friends in 10 days," Dropbox's "1 file uploaded" — were all discovered manually by analysts running iterative cohort SQL. That process is slow, requires strong SQL fluency, and tends to happen once per company rather than continuously. Most teams either skip activation analysis or do it as a one-off deck that goes stale.

Wayline collapses that loop. Instead of an analyst hand-crafting cohort queries, the engine mines candidate milestones from the event stream, scores each one against a retention outcome, and packages the strongest signals as experiment specs.

**Differentiator vs Mixpanel/Amplitude:** those tools let you *query* behavior; Wayline *discovers* which behaviors matter.

---

## 3. Architecture — the four stages

### Stage 1: Event ingestion & journey reconstruction (Postgres)
Raw events (`user_id`, `event_name`, `ts`, `properties`) land in Postgres. Per-user journeys are materialized as ordered sequences with derived features: time-since-signup, session boundaries, event counts per type.

### Stage 2: Cohort labeling (SQL)
Users get an outcome label based on a retention window — e.g., `active_week_4 = true/false`. This is the ground-truth signal everything downstream is scored against.

### Stage 3: Behavioral mining (Polars)
Candidate milestones generated programmatically:
- "did event X within Y days of signup"
- "completed sequence A → B → C"
- "performed event X at least N times"

For each candidate, Polars computes retention lift and a statistical confidence score vs. the cohort labels. Path analysis finds common prefixes among retained vs. churned users.

**Why Polars not pandas:** the milestone scan is heavy groupby/window work across the full event table. At 250k events pandas gets sluggish; Polars handles it comfortably and its lazy API keeps query plans clean.

### Stage 4: LLM synthesis (OpenAI / Claude)
Top-scoring milestones go to an LLM that drafts experiment specs:
- Hypothesis
- Target segment
- Success event
- Guardrail metrics
- Rationale grounded in the source data

**Redis** sits across stages 3 and 4 to cache expensive milestone scans and LLM outputs. That's where the "<30 seconds" claim lives.

---

## 4. Repo layout
wayline/
├── web/                  # Next.js frontend (TypeScript)
├── api/                  # FastAPI HTTP layer — thin, routes + serialization
│   └── engine/           # Polars analysis logic, imported by api routes
├── data/                 # Synthetic event generator + schema docs
├── docker-compose.yml    # Postgres, Redis, api, web — one command up
├── .env.example
├── .gitignore
├── NOTES.md
└── README.md

**Why engine/ is a submodule of api/ instead of a sibling at root:**
Only one consumer (the api). Promoting it to top-level would imply multiple services share it, which isn't true. Still gives the clean architectural answer: api routes parse input → call into engine → serialize results. Engine has no FastAPI dependency, so it's independently unit-testable against fixture event data.

**Why data/ is separate:**
The synthetic event generator is a different concern — runs once to seed Postgres. Its design is itself a talking point: the generator must inject *latent structure* (some users programmed to be more likely to retain because of specific behaviors), or no real milestones will surface.

---

## 5. Stack rationale

| Tech | Why |
|------|-----|
| Next.js + TypeScript | Component model fits a dashboard UI; TS keeps the API contract honest. |
| FastAPI | Async; Pydantic schemas mirror TS types cleanly; auto-generated docs. |
| Polars | 5–10× faster than pandas on groupby/window ops at this scale; lazy execution. |
| PostgreSQL | Event tables fit relational well; window functions cover most cohort SQL. |
| Redis | Cache for milestone-scan results and LLM responses. Could queue background scans later. |
| OpenAI / Claude | LLM synthesis stage. Choose at runtime by which env var is set. |
| Docker | One-command local setup for db + cache + api + web. |

---

## 6. Defending the resume claims

| Claim | What backs it |
|-------|---------------|
| "Processed 250k+ synthetic product events" | Generator in `data/` writes ≥250k events covering signup, onboarding, integration, purchase, sharing, return-session journeys. |
| "Identified 12 behavioral milestones correlated with higher 4-week retention" | Engine's milestone-mining stage returns ranked milestones; generator designed so ~12 real signals exist; verify in output. |
| "Generated experiment specs with target segments, success events, guardrail metrics" | LLM synthesis stage produces structured spec per top milestone. Persist these for the demo. |
| "Reduced manual analysis time to <30s for cohort/path comparison reports" | Cached scans + pre-computed journey tables. Benchmark and record actual numbers here. |

---

## 7. Open questions (resolve as we build)

- **Event schema:** what does `properties` JSONB look like per event type? Pin down before generating data.
- **Retention definition:** active_week_4 = "any session in calendar week 4 post-signup"? Or 28-day rolling window? Pick one.
- **Milestone candidate space:** how do we bound it? Threshold sweep over "event X done N times" — what range of N?
- **Statistical confidence:** chi-squared, lift + sample-size threshold, or something else?
- **LLM cost:** how many specs per run, and is each one cached?

---

## 8. Infrastructure setup

**Postgres 16 + Redis 7** (Alpine variants), run via `docker compose up -d`. Healthchecks built into compose so services report `(healthy)` only when actually ready to accept connections — matters once the api depends on them at startup.

**Host port remap (5433 → 5432 for Postgres, 6380 → 6379 for Redis):**
Another local Docker project is already bound to the default ports. Rather than disrupting that project, Wayline binds to non-default host ports. The remap is host-side only — inside each container, Postgres and Redis still listen on their own defaults (5432, 6379). Once the api joins the same Docker network, it'll talk to `postgres:5432` directly without involving host ports.

**Named volumes (`postgres-data`, `redis-data`):**
Data persists across `docker compose down`. Lets me iterate without losing seeded event data. `.gitignore` excludes those directory names in case they're ever mounted as local paths instead.

**Why no api/web in compose yet:**
Compose tracks what's real. Until those services have code that runs, adding them creates noise. They get added when each has its first working endpoint.

---

## 9. API service

**psycopg 3 (`psycopg[binary]`):** current major Postgres driver; binary wheel avoids local compile. Sync mode for now; async swap is available later without architectural change.

**Sync `def` endpoint with sync drivers:** an `async def` handler wrapping sync drivers would block the event loop. `def` runs in FastAPI's threadpool, containing the blocking per-worker. Async drivers + async handlers is a future option; no performance reason to take that on at health-check frequency.

**No connection pooling on `/health`:** health checks should verify the connection-establishment path itself, not the existence of a warm pool. Pooling lands with real query endpoints when throughput justifies it.

**Timeouts (2s, both libraries):** `connect_timeout=2` (psycopg); `socket_connect_timeout=2, socket_timeout=2` (redis-py). Caveat for real endpoints: psycopg's `connect_timeout` bounds connect only, not query duration — query-level bounding needs `statement_timeout` via the connection's `options=` parameter.

**Error hygiene:** exceptions surface as `"error: <ExceptionClassName>"` — class name only. URLs, hosts, ports, and credentials never appear in responses.

**Always-200 with `status: degraded`:** `/health` is an info endpoint, not a liveness probe. Always returns 200; `status` flips based on dependency reachability. Liveness probes use non-200 semantics — that's a separate endpoint when one is needed.

**`.env` fallback:** loads `.env` if present, else `.env.example`. `load_dotenv` doesn't override existing env vars, so shell/container env wins as expected.

---

## 10. Data layer

**Schema (`users` + `events`):** classic one-to-many. Single events table with `event_name` + JSONB `properties` rather than per-event-type tables — the Segment/Mixpanel/Amplitude shape. Flexible per-event fields without column proliferation; queryable via `properties->>'key'` and a GIN index if/when needed.

**`persona` column as ground truth:** column exists for the *generator* and for *evaluation queries*. The engine never reads it. Without ground truth on synthetic data, "the engine works" is hand-waving — with it, we can quantitatively check whether discovered milestones correspond to the personas they should.

**No `retention` column:** retention is a derived fact about behavior (events in week 4 post-signup), not an attribute of a user. Storing it would mean syncing it with the underlying events; deriving it keeps the definition in one place.

**Indexes — `(user_id, ts)`, `(event_name, ts)`, `(ts)`:** journey reconstruction (dominant query), event-type cohort analysis, and pure time-range respectively. No GIN on JSONB yet — added only if profiling shows JSONB queries hot enough to justify the write-cost overhead.

**Check constraints on `channel`, `plan_tier`, `persona`:** enforced at the database level. The generator can't insert invalid enum values by accident.

**Raw SQL migrations over Alembic:** Alembic's main value is autogeneration from SQLAlchemy ORM models. We're not using an ORM (Polars reads SQL directly), so that value doesn't apply. A 20-line Python runner reads `api/migrations/*.sql` in lexical order, tracks applied versions in `schema_migrations`, and applies each unapplied file in its own transaction. Each migration commits separately so a midway failure doesn't roll back successful earlier files.

**Generator persona design (4 personas at 15/30/35/20%):** documented separately in `data/PERSONAS.md`. The TL;DR is in section 7 of this file — latent structure is required for milestones to exist, the four-persona distribution is the structure, evaluation is via persona-dominance queries on milestone cohorts.

---

## 11. Decisions log

Append-only. Date + decision + reasoning.

- **2026-05-16** — Layout: `engine/` as submodule of `api/`, not sibling at root. Reason: only one consumer; avoids overengineering while preserving the architectural boundary in code.
- **2026-05-16** — Stack: Polars over pandas for analysis stage. Reason: performance at 250k+ events + lazy API.
- **2026-05-16** — Postgres 16 + Redis 7 (alpine) with in-compose healthchecks. Reason: current stable majors, small images, race-condition-safe startup.
- **2026-05-16** — Host port remap to 5433/6380 due to collision with another local Docker project. Container internals unchanged.
- **2026-05-16** — psycopg 3 over 2. Reason: current major; binary wheel; sync now, async swap possible later.
- **2026-05-16** — Sync `def` health endpoint with sync drivers. Reason: blocking contained in FastAPI's threadpool; no async drivers needed yet.
- **2026-05-16** — `/health` always returns 200 with status="ok"|"degraded". Reason: info endpoint, not liveness probe.
- **2026-05-17** — Two-table schema (users + events) with JSONB `properties` on events. Reason: standard product-analytics shape; flexible per-event-type fields without column proliferation.
- **2026-05-17** — `persona` column on users as generator ground truth + engine evaluation signal. Engine treats it as opaque. Reason: lets us validate engine outputs against known persona assignments instead of hand-waving.
- **2026-05-17** — Raw SQL migrations + 20-line Python runner over Alembic. Reason: no ORM in use; Alembic's main features don't apply; clearer code path for this project's scale.
- **2026-05-17** — Four-persona synthetic design (Power/Activator/Looker/Bouncer at 15/30/35/20%). Reason: enough latent structure to make milestones discoverable; enough adjacent overlap that the engine has to compute statistics, not trivially cluster.
