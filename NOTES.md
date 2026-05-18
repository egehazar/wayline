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

## 11. Synthetic generator

**Bulk insert via `psycopg.cursor.copy()`:** rows generated entirely in memory, then bulk-streamed to Postgres. Generation took 1.9s; DB write took 5.3s. Row-by-row INSERT at this volume would be minutes per run instead of seconds — fast generator means fast iteration, which matters for tuning persona parameters.

**Reproducibility via fixed seed (`random.seed(42)`, `np.random.seed(42)`):** same seed = same data = same engine output. Required for evaluation to be meaningful — without it, "the engine surfaced this milestone" is non-reproducible.

**Idempotence via `TRUNCATE users, events RESTART IDENTITY CASCADE`:** re-running produces fresh data, not doubled-up data. Belongs at the start of every generator run.

**Deviation from PERSONAS.md spec — session rate calibration.** The spec rates (Poisson(λ=3) sessions/day for Power week 1, Poisson(λ=2) for retained Power, etc.) projected ~1M events at 25k users × 60-day window, well over the 250–400k target band. Two options: widen the verify range, or recalibrate. Chose recalibration because: (a) persona rankings are preserved (Power > Activator > Looker > Bouncer), (b) per-event-type probabilities are preserved exactly (`integration_connected` P=0.95 for Power, etc.), and (c) absolute volume per user doesn't affect the relative behavioral signature the engine learns from. Calibrated values in `SESSION_RATE_WEEK1` and `SESSION_RATE_RETAINED` constants; spec values shown in inline comments for easy revisit.

**Deviation from PERSONAS.md spec — retention as an explicit gate.** First pass had Looker retention at 33% (target 15%) because non-retained Lookers' long onboarding window (1–4 weeks per spec) leaked events into the retention check window (days 21–28). Refactored to sample the Bernoulli retention decision at user-generation time and clip non-retained users' events to before day 21. Real-world retention has spillover noise; this clip makes the synthetic data a clean evaluation testbed. Documented in generator module docstring.

---

## 12. Milestone mining engine

**Architecture:** pure Python + Polars, no FastAPI dependencies. `api/engine/milestones.py` exposes `load_data`, `compute_retention`, `candidate_milestones`, `evaluate_milestone`, `mine_milestones`, `persona_dominance`. Engine is library-shaped; `api/engine/run.py` is the current CLI entry, swappable for a FastAPI endpoint later without touching the analytical core.

**Candidate space:** ~37 milestone definitions across four template kinds — event-within-days (4 events × 4 windows), event-count-at-least-N (5 events × 4 counts), onboarding-step-completion (5 steps), sessions-on-distinct-days. Templates with explicit parameter sweeps keep each candidate interpretable. Full threshold sweeps or full sequence patterns would explode the space without adding actionable insight.

**Filters (both must hold):**
- `min_sample = 200` — statistical reliability; kills rare-cohort overfitting.
- `max_share = 0.25` — specificity; kills "did anything past signup" predicates whose lift comes purely from excluding Bouncers.

**Why the specificity filter exists:** first cut of the engine ranked `completed_onboarding_step_1` and `workspace_created_within_28_days` at top with 9.6× lift. Mathematically correct — those predicates exclude the 20% Bouncer cohort with 0% retention — but not actionable. A PM can't experiment on "did anything at all." The 25% specificity cap surfaces the specific behaviors PMs can target.

**Scoring:** `lift = P(retain | did) / P(retain | didn't)`. `retain_didnt = 0` returns None, filtered out. No chi-squared — at 25k users with cohort sizes ≥200, p-values are effectively zero and don't discriminate; sample-size filtering catches the same failure mode (small-sample overfitting) with simpler reasoning and no multiple-comparison correction baggage.

**Engine remains blind to `persona`:** `mine_milestones` queries event_name, ts, properties, user_id only. `persona_dominance` is a separate function called by `run.py` for evaluation output — it reads persona to verify engine results against ground truth. Intentional isolation so the engine works against real data where persona doesn't exist.

**Performance:** 1.0s data load + 0.1s mining (44 candidates) + 0.4s for both passes (raw + actionable) + ~0.1s persona dominance = 1.6s total. Caching `mine_milestones` raw output in Redis becomes useful once LLM synthesis is layered on top — the actionable ranking is then a cheap `pl.filter()` on the cached result.

**Two-table output:** Table A (raw lift, no specificity filter) verifies mechanical correctness. Table B (actionable, n_did ≤ 25% of users) is the headline ranking. Both visible in the CLI output — hiding either would obscure either how the engine works or what it's useful for.

---

## 13. LLM synthesis

**Claude Sonnet 4.6 via Anthropic SDK, tool use forcing structured output:**
Top milestones go to Sonnet for experiment-spec drafting. Two layers enforce grounding:

1. **Schema-level `success_event` enum** — the tool's JSON schema constrains `success_event` to the 12 valid event names. The API rejects responses with invalid names; the model literally cannot hallucinate. Pydantic re-validates as defense in depth.
2. **Prompt-level correlation-vs-causation discount** — the LLM is explicitly told the observed lift is correlation, and realistic causal effect is 10–30% of the gap. Without this, the model would cite raw lift as expected experimental effect.

**False-positive eval debugging arc:** the `_quotes_raw_lift` heuristic flagged all 10 specs. Investigation showed the model was correctly leading with discounted estimates (~14–20% of raw gap) and citing the raw gap as context for the discount. The detector was scanning the entire string; fix anchored on the first numeric value (the actual prediction). The arc itself — wrote a check, it flagged everything, investigated, found the check was wrong — is the kind of eval rigor that separates real evaluation from a checkbox.

**No prompt caching:** ~1.2K-token prompts are below Sonnet 4.6's 2048-token minimum cache prefix. Skipped.

**Sequential calls, no streaming:** 10 specs × ~20s each = ~200s total, ~$0.10–0.30. Parallelism complicates error handling without meaningful latency improvement at this scale.

---

## 14. Path analysis

**Goal:** ordered event sequences that correlate with retention — backs the resume's "activation paths" clause.

**Method:** for each user, drop `signup_completed` (universal), take the next 5 event names as an ordered tuple. Users with <5 post-signup events excluded (naturally drops Bouncers). Group by sequence; compute retention lift against a candidate-pool base.

**Candidate-pool base rate (users with ≥N events), not full-population base:** sequences only exist for users active enough to have an N-event prefix. Comparing against a base that includes Bouncers (0% retention) re-introduces the "did anything" lift problem the milestone specificity filter solved. The candidate-pool denominator isolates the ordering signal from the engagement signal.

**Top sequence: pure Power trajectory** — five onboarding events with workspace_created mixed in, retained at 81.2% vs. 54.1% candidate-pool base, 1.50× lift. Matches PERSONAS.md's "Power completes onboarding within 24–48h" exactly. Sequences leading with `task_created` or `comment_posted` before onboarding finishes sit at the base rate or below — skipping ahead isn't a high-retention pattern.

**Concentrated path space:** only 25 sequences survive at `min_sample=100`. Path space is more clustered than initially estimated; tightening or loosening parameters fragments or floods the output. The min=100 setting produces interpretable headline results — kept at spec values.

**Polars `group_by` on List columns:** unreliable across Polars versions. Worked around by joining each list into a unit-separator-delimited string for the hashable key, then recovering the list via `first()` in the aggregation.

---

## 15. FastAPI endpoints + Next.js dashboard

**Four GET endpoints:**
- `/health` — dependency check (existing).
- `/milestones` — runs mining pipeline, returns top 12 actionable milestones with persona dominance.
- `/paths` — runs path analysis, returns top 10 sequences.
- `/specs` — reads `data/experiment_specs.json`, returns parsed list. 503 with actionable message if file missing.

**Compute on demand, no Redis caching:** mining + paths each ~1–2s. Redis is provisioned; `mine_milestones`'s raw output is the natural caching seam when throughput justifies it. Doesn't yet.

**Pydantic response models + auto-generated `/docs`:** every endpoint has a typed schema. FastAPI's OpenAPI generation produces a Swagger UI at `/docs` — free piece of evidence the API is properly typed.

**CORS pinned narrowly:** `localhost:3000` (Next.js dev) + `localhost:8000` (self), GET-only, no credentials, no wildcards. Local-demo API surface, not multi-tenant.

**Error hygiene:** caught exceptions return 503 with `error: <ExceptionClassName>` — same pattern as `/health`. No URLs, hosts, or query content leaked.

**Import paths:** `from api.engine.milestones import ...` (dotted) inside `api/main.py`; bare imports inside engine CLI scripts which are invoked with `api/engine/` on `sys.path`. Python 3.3+ namespace packages cover the absent `api/__init__.py`.

**Next.js dashboard — single async Server Component:** `web/app/page.tsx` does `Promise.all` against the three API endpoints during SSR. No client components — `<details>` HTML handles spec card collapse without shipping JS. Renders in ~2s including parallel API calls.

**Visual hierarchy:** monochrome neutral palette + single indigo accent on lift values and section eyebrows. Geist Sans for prose, Geist Mono for identifiers (event names, milestone snake_case, success_event strings). Tabular numerals across numeric columns so digits align. Three sections with eyebrow numbering (01, 02, 03) — reads as a guided tour rather than navigation.

**No UI library:** Tailwind alone. Adding shadcn/MUI/etc. would add dependency surface and fingerprint as scaffolded; hand-rolled reads as deliberate.

**Error state in UI:** if any API fetch fails, the page renders an inline panel with the explicit start-backend command. Doesn't crash.

**Dark mode removed:** Next.js's default `prefers-color-scheme: dark` block deleted from `globals.css`. Handling both is a separate design problem and the B2B analytics demo benefits from a consistent reference frame.

---

## 16. Decisions log

Append-only. Date + decision + reasoning.

- **2026-05-16** — Layout: `engine/` as submodule of `api/`, not sibling at root. Reason: only one consumer; avoids overengineering while preserving the architectural boundary in code.
- **2026-05-16** — Stack: Polars over pandas for analysis stage. Reason: performance at 250k+ events + lazy API.
- **2026-05-16** — Postgres 16 + Redis 7 (alpine) with in-compose healthchecks. Reason: current stable majors, small images, race-condition-safe startup.
- **2026-05-16** — Host port remap to 5433/6380 due to collision with another local Docker project.
- **2026-05-16** — psycopg 3 over 2. Reason: current major; binary wheel; sync now, async swap possible later.
- **2026-05-16** — Sync `def` health endpoint with sync drivers. Reason: blocking contained in FastAPI's threadpool.
- **2026-05-16** — `/health` always returns 200 with status="ok"|"degraded". Reason: info endpoint, not liveness probe.
- **2026-05-17** — Two-table schema (users + events) with JSONB `properties`. Reason: standard product-analytics shape.
- **2026-05-17** — `persona` column on users as generator ground truth + engine evaluation signal. Engine treats it as opaque.
- **2026-05-17** — Raw SQL migrations + 20-line Python runner over Alembic. Reason: no ORM in use.
- **2026-05-17** — Four-persona synthetic design (Power/Activator/Looker/Bouncer at 15/30/35/20%).
- **2026-05-17** — Generator session rates calibrated ~4–5× below spec to hit 250–400k event target. Persona rankings + per-event probabilities preserved exactly.
- **2026-05-17** — Retention sampled as explicit Bernoulli at user-generation time; non-retained users' events clipped to before day 21. Reason: prevents long-onboarding spillover from contaminating retention measurement.
- **2026-05-17** — Engine as Polars-based functional module, no FastAPI dependencies.
- **2026-05-17** — Hand-defined milestone templates with parameter sweeps over thresholds. Reason: interpretability matters as much as recall.
- **2026-05-17** — Lift + minimum sample size (200) for milestone scoring over chi-squared. Reason: at 25k users, chi-squared doesn't discriminate; sample-size filtering catches the same failure mode simpler.
- **2026-05-17** — Specificity filter (max_share=0.25). Reason: lift alone surfaces "did anything past signup" predicates; specificity cap isolates the specific actionable behaviors.
- **2026-05-17** — Two-table CLI output (raw + actionable). Reason: raw verifies mechanical correctness; actionable is the headline.
- **2026-05-17** — LLM synthesis via Claude Sonnet 4.6 with tool-use forcing structured output + Pydantic re-validation. Reason: schema-level enum makes success_event hallucination impossible.
- **2026-05-17** — Correlation-vs-causation discount explicit in synthesis prompt (10–30% of observed lift). Reason: naive LLM behavior is to quote raw lift as causal effect.
- **2026-05-17** — `_quotes_raw_lift` heuristic refined to anchor on leading prediction. Reason: original scanned full string; false-positive on correct discount applications.
- **2026-05-17** — Path analysis lifts computed against candidate-pool base rate, not full-population base. Reason: prevents reintroducing the Bouncer-exclusion effect the milestone specificity filter already solved.
- **2026-05-17** — 25 sequences at min_sample=100 accepted as operating regime. Reason: lower thresholds add noise; current set represents genuinely distinct paths.
- **2026-05-17** — Polars group_by on List columns worked around with delimited-string join. Reason: cross-version reliability.
- **2026-05-17** — Four GET FastAPI endpoints, CORS pinned to localhost only, compute on demand. Reason: portfolio-demo scope.
- **2026-05-17** — Next.js dashboard as single async Server Component, no client components. Reason: SSR + Promise.all is the simplest fast path at this scope.
- **2026-05-17** — Tailwind only, no UI library. Reason: 3-section dashboard doesn't justify shadcn/MUI dependency surface.
