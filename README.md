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

## 8. Decisions log

Append-only. Date + decision + reasoning.

- **2026-05-16** — Layout: `engine/` as submodule of `api/`, not sibling at root. Reason: only one consumer; avoids overengineering while preserving the architectural boundary in code.
- **2026-05-16** — Stack: Polars over pandas for analysis stage. Reason: performance at 250k+ events + lazy API.
EOF