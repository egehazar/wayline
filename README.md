# Wayline

**Behavioral product intelligence engine** — discovers activation milestones, common paths, and cohort patterns from raw product event streams, then drafts experiment specs grounded in the data.

Activation analysis (the work behind Slack's "30 messages in 7 days," Dropbox's "1 file uploaded," Facebook's "7 friends in 10 days") is usually done manually by analysts running iterative cohort SQL. Wayline collapses that loop: the engine mines candidate milestones, scores them by retention lift with a specificity filter, validates against persona ground truth, and hands the top results to Claude Sonnet 4.6 which drafts experiment specs (hypothesis, target segment, success event, guardrail metrics, expected effect size — calibrated against the correlation-vs-causation gap).

End-to-end runtime: ~200 seconds for a fresh analysis pass on 25,000 users and ~370,000 synthetic events. Engine itself (no LLM calls) is 1.6 seconds.

## Stack

TypeScript, Next.js 16 (App Router, Server Components), Tailwind • Python, FastAPI, Polars • PostgreSQL 16, Redis 7 • Anthropic SDK (Claude Sonnet 4.6) • Docker Compose

## What's in this repo

```
api/
  main.py                  FastAPI service: /health /milestones /paths /specs
  engine/
    milestones.py          Candidate generation, lift scoring, specificity filter
    paths.py               Ordered-sequence retention lift
    synthesis.py           LLM-driven experiment spec generation (Sonnet 4.6, tool use)
    run.py                 CLI: mining + paths + persona-dominance tables
    synthesize.py          CLI: top 10 specs → JSON + rendered text
  migrations/
    001_init.sql           Users + events schema
    run.py                 Idempotent migration runner
data/
  generate.py              Synthetic event generator (25k users, ~370k events, ~7s)
  verify.py                13 sanity checks against generated data
  PERSONAS.md              Persona spec: distribution, behavior parameters, evaluation method
  experiment_specs.json    Generated specs (output)
web/                       Next.js 16 dashboard
  app/page.tsx             Single Server Component, parallel API fetches, 3 sections
docker-compose.yml         Postgres + Redis (host ports 5433 / 6380 to avoid collisions)
NOTES.md                   Engineering notes — architecture, decisions, deviations
POST.md                    Project writeup (in progress)
```

## Running it

Requires Docker, Python 3.11+, and Node.js 20+. Tested on macOS.

```bash
# 1. Start data infrastructure
docker compose up -d

# 2. Install Python deps (uses uv)
uv sync

# 3. Set up environment
cp .env.example .env
# Edit .env: add ANTHROPIC_API_KEY for the synthesis stage

# 4. Apply database schema
uv run python api/migrations/run.py

# 5. Generate synthetic data (~7 seconds)
uv run python data/generate.py
uv run python data/verify.py

# 6. Run the engine (mining + paths)
uv run python api/engine/run.py

# 7. Generate experiment specs (~3 minutes, requires ANTHROPIC_API_KEY)
uv run python api/engine/synthesize.py

# 8. Start the API
uv run uvicorn api.main:app --port 8000

# 9. Start the frontend (in a separate terminal)
cd web && npm install && npm run dev

# Open http://localhost:3000
```

## Key results

On 25,000 synthetic users + ~370,000 events generated against a four-persona behavioral model:

- **24 actionable milestones** surfaced (top 10 displayed). Top result: `completed_onboarding_step_5` at 3.37× retention lift, 60% Power-persona dominance among the "did" cohort.
- **25 distinct activation paths** discovered via ordered-sequence analysis. Top sequence is the pure Power trajectory (five onboarding events back-to-back) at 1.50× lift against the candidate-pool base.
- **10 experiment specs** drafted by Claude Sonnet 4.6, each grounded in the milestone's actual statistics with a correlation-vs-causation discount applied (expected effect sizes in the 7–10pp range, ~14–20% of observed correlation gaps).
- **Persona dominance validation:** every top-5 actionable milestone is 57–73% Power-persona, Bouncers consistently 0%, Lookers ≤1.4%. The engine correctly isolated behaviors that discriminate *among engaged users* — not just between engaged and unengaged.

## Architecture notes

See `NOTES.md` for the full set. Highlights:

- **Engine is blind to persona.** The `users.persona` column is generator ground truth used only for evaluation — `mine_milestones` and `compute_paths` never read it. Validation queries (in `persona_dominance`) cross-reference it after the fact.
- **Specificity filter (`max_share=0.25`)** on milestone mining. Without it, the top result is `completed_onboarding_step_1` at 9.6× lift, which is mathematically correct but actionably meaningless — it's just "this user did anything at all." The 25%-of-users cap surfaces the specific behaviors PMs can target.
- **Candidate-pool base rate for path analysis.** Paths only exist for users with ≥5 events. Lifts are computed against that pool, not the full population — otherwise the analysis re-introduces the Bouncer-exclusion effect already filtered out at the milestone layer.
- **LLM grounding via JSON-schema enum.** `success_event` is declared as a string enum constrained to the 12 valid event names; the model literally cannot return an invalid event. Pydantic re-validates as defense in depth.
- **Bulk insert via psycopg `copy()`.** Generator inserts 370k events in ~5 seconds. Row-by-row INSERT would be minutes per run; the difference matters for iterating on persona parameters.

## Limitations (honest)

- **No real-time event ingestion.** Synthetic data only — fitting for a portfolio demo, not for production.
- **No authentication on the FastAPI surface.** Local-demo only; CORS is pinned narrowly to localhost.
- **Engine reads from Postgres on every API call** rather than caching to Redis. Provisioned but not justified at current throughput.
- **Single fixed analysis window** (30-day signup, 60-day total). Configurable in code; no UI for it.
- **Engine is blind by design** — won't surface anti-patterns (behaviors correlated with churn rather than retention). Adding negative milestones is a one-page change.

## License

Personal portfolio project.
