# Wayline — a behavioral product intelligence engine

## What it is

Wayline ingests raw product event streams and automatically surfaces the activation paths, milestones, and cohort patterns that correlate with retention — then drafts experiment specs from those findings. Where Mixpanel and Amplitude let you query behavior, Wayline discovers which behaviors matter.

## Why it exists

The famous activation insights — Slack's "30 messages in 7 days," Facebook's "7 friends in 10 days," Dropbox's "1 file uploaded" — were all discovered manually. An analyst writes a series of cohort SQL queries, iterates on hypotheses, eyeballs the results, eventually finds the metric. Most companies do this exercise once and never revisit it. Many never do it at all.

The cost is steep. Activation is the largest controllable lever on retention, and it's getting addressed with intuition and one-off analyses instead of a continuous discovery loop.

Wayline closes that loop. The engine mines candidate milestones from raw event streams, scores each against a retention outcome, and packages the strongest signals as experiment specs grounded in the underlying data.

## Methodology — milestone discovery

The engine has three stages. **Loading** pulls users and events into Polars DataFrames from Postgres — 1.0 seconds for 25,000 users and 370,489 events. **Cohort labeling** computes a binary retention label per user: did they have any event in days 21–28 post-signup? **Milestone mining** generates ~37 candidate "milestone shapes" — `did_event_X_within_Y_days`, `event_X_at_least_N_times_in_first_week`, `completed_onboarding_step_M`, `sessions_on_distinct_days_in_first_week` — and scores each one for retention lift.

A first cut of the ranking surprised me. The top result wasn't `integration_connected_within_7_days` (what I'd predicted). It was `completed_onboarding_step_1` and `workspace_created_within_28_days`, tied at 9.6× lift. The mechanism was real: those broad predicates cleanly exclude the 20% Bouncer cohort whose retention is 0%, and removing a 0%-retention cohort from the denominator generates more lift than any other behavior could. The engine was working correctly. The problem was that "did anything past signup" isn't an actionable milestone — a PM can't run an experiment to nudge users toward "doing literally anything."

So I added a specificity filter: a milestone counts as actionable only if it's hit by ≤ 25% of all users. That cut 15 of 39 surviving milestones — all the broad early-funnel ones — and surfaced what I'd been looking for. The actionable ranking:

| Rank | Milestone | n | Lift | Power dominance |
|---|---|---|---|---|
| 1 | `completed_onboarding_step_5` | 6,188 | 3.37× | 60% |
| 2 | `integration_connected_within_14_days` | 6,248 | 3.24× | 57% |
| 3 | `integration_connected_within_7_days` | 4,936 | 3.20× | 72% |
| 4 | `task_completed_at_least_3_in_week1` | 4,161 | 3.03× | 73% |
| 5 | `task_created_at_least_3_in_week1` | 4,142 | 3.00× | 72% |

Persona dominance validates the result against ground truth: every top-5 actionable milestone is 57–73% Power persona, with Activator second, Looker ≤1.4%, Bouncer 0%. The engine correctly isolated behaviors that discriminate *among engaged users* — not just between engaged and disengaged.

One milestone deserves separate mention. `task_completed_at_least_5_in_week1` ranks #8 with the smallest cohort (1,754 users) but the highest individual retention rate (83.5%). It's a tight Power signal — completing 5+ tasks in week 1 nearly guarantees you're a deeply-engaged user. The aggregate lift is lower because the cohort is too small to move the population denominator much, but in product terms it's the most predictive single signal in the entire output.

Engine wall-clock: 1.6 seconds. The `<30 seconds vs. multi-query SQL exploration` claim has room to spare — there's headroom for the LLM synthesis stage that follows.

## Synthetic data — the latent-structure problem

Naive synthetic data destroys this project. Generate events uniformly at random and every event correlates with retention at the base rate — the engine finds nothing, because there's nothing to find. Real product data has latent structure: some users are deeply invested and reveal it through specific behaviors, others are tire-kickers, and the relationship between behavior and retention is statistical, not deterministic. The engine's job is to discover the behavioral signatures of types it doesn't know exist.

So the generator simulates a B2B project management tool and assigns each user one of four hidden personas: Power (15%), Activator (30%), Looker (35%), Bouncer (20%). Each persona has its own event-probability vector, timing distributions, and retention probability. The engine never queries the persona column; it sees only events and a retention label derived from events in days 21–28 post-signup. Whatever it surfaces gets validated against ground truth by joining back to persona.

Two design moments worth being honest about:

**Spec calibration.** The initial persona spec called for Poisson(λ=3) sessions/day for Power users in week 1, scaling down through the personas. Across 25k users over a 60-day observation window, that math projected ~1M events — well above the 250–400k target band. The choice was widen the target or recalibrate the rates. I recalibrated, preserving the two things the engine actually learns from: the persona ranking (Power > Activator > Looker > Bouncer) and the per-event-type probabilities (integration_connected at P=0.95 for Power, P=0.35 for Activator, etc.). The absolute session volume per user doesn't affect the relative behavioral signature.

**Retention as an explicit gate.** The first run had Looker retention at 33% instead of the 15% target. The bug was structural: Looker's spec includes a long onboarding window (1–4 weeks), and non-retained Lookers' onboarding events were leaking into the retention check window. I made retention a Bernoulli decision at user-generation time and clipped non-retained users' events to before day 21. Real-world retention has spillover noise like this; for an evaluation testbed where we want measured retention to cleanly reflect the persona's intent, the clip is correct.

After both corrections, the generator produces 370,489 events for 25,000 users in 7.2 seconds — 1.9s generating in memory, 5.3s bulk-streaming to Postgres via psycopg's `copy()`. Verifier passes all 13 sanity checks: persona distribution within 0.5pp of spec, retention rates within 1pp per persona, no orphan events. Same seed produces the same data, which matters because the engine's outputs need to be reproducible for evaluation to mean anything.

## Path analysis — what order things happen in

Milestone mining answers "which behaviors correlate with retention?" Path analysis answers "which ordered trajectories do?" Order matters: a user who creates a workspace, completes onboarding, then connects an integration may be a fundamentally different cohort from one who connects an integration on day 1 before touching anything else.

The implementation: for each user, drop the universal `signup_completed` event (every user has it; it's useless as a prefix), then take the next 5 event names as an ordered tuple. Users with fewer than 5 post-signup events are excluded — this naturally drops Bouncers, who never engage enough to have a trajectory. Group users by sequence, compute retention rate per group, rank by lift.

One design choice deserves attention. Lift here is computed against a *candidate-pool* base rate (users with ≥5 events, ~54% retention) rather than the full-population base (~33%). The full-population base would inflate every lift by ~2× — but most of that inflation captures "this user reached 5 events," which is just the engagement signal already isolated at the milestone layer via the specificity filter. The candidate-pool denominator isolates the *ordering* signal from the *engagement* signal. The two analyses do different work and shouldn't double-count.

The top path is the pure Power trajectory:

> `onboarding_step_completed → workspace_created → onboarding_step_completed → onboarding_step_completed → onboarding_step_completed`
>
> n=112, retention 81.2%, lift 1.50×

That's exactly the "Power user completes all 5 onboarding steps within 24–48 hours" pattern the synthetic generator encodes. Sequences that lead with `task_created` or `comment_posted` before completing onboarding sit at or below the candidate-pool base rate — skipping ahead isn't a high-retention pattern, it's just impatience.

One practical implementation note: Polars' `group_by` on `List` columns is unreliable across minor versions. The workaround is to join each list into a delimited string using the ASCII unit separator (`\x1f`) for a stable hashable key, then recover the list via `first()` in the aggregation. Event names are letters and underscores, so the unit separator never collides. Small thing, but the kind of compatibility detail that takes 10 minutes to fix once you've seen it and an hour the first time.

## LLM-drafted experiment specs

The engine surfaces *what* correlates with retention. The final stage takes the top milestones and asks Claude Sonnet 4.6 to draft what you'd actually test: hypothesis, target segment, success event, guardrail metrics, expected effect size. The output is a structured Pydantic object per spec.

The default failure mode of LLM-drafted experiment specs is plausibility theater — hypotheses that sound right but reference nothing real, invented event names, retention numbers pulled from nowhere. Three defenses keep the output grounded:

**Schema-level enum on `success_event`.** The Anthropic tool's JSON schema declares `success_event` as a string enum constrained to the 12 valid event names. The model literally cannot return an invalid event name — the API rejects the response. Pydantic re-validates as defense in depth.

**Prompt-level correlation-vs-causation discount.** The default LLM behavior is to quote the observed lift (e.g., 3.20× for `integration_connected_within_7_days`) as the expected experimental effect. That's naive — the observed lift includes pre-existing intent (motivated users seek out integrations on their own) and selection effects (the cohort skews heavily toward the Power persona), not just behavior-to-retention causation. The prompt explicitly tells the model: *"the observed lift is correlation, not causal effect; realistic causal slice of an intervention is 10–30% of the observed gap."* With this framing, every spec lands with a discounted prediction in the 7–10 percentage-point range — about 14–20% of the raw gap — and the rationale field shows its work.

**Anchored post-validation.** A heuristic check anchors on the first numeric value in `expected_effect_size` (the model's actual prediction) and flags if it's within ±5pp of the raw observed gap. That's the actual failure mode: the model quoting raw correlation as the experimental prediction.

The first run of the validation heuristic flagged all 10 specs as suspect. Investigation showed every spec was applying the discount correctly — leading with a 7–10pp estimate, then citing the raw 47–54pp gap as context for the discount. The detector was scanning the whole string and treating the cited raw number as if it were the prediction. The fix was anchoring on the leading value only. One line of regex. Worth doing not because the bug was high-impact, but because a validation system that produces 100% false positives is worse than no validation system at all. The arc — wrote a check, it flagged everything, investigated, found the check was wrong, fixed the check — is the kind of detail that distinguishes real evaluation work from a checkbox.

A representative spec, abbreviated:

> **Milestone:** `integration_connected_within_7_days` — lift 3.20×, 72% Power-persona dominance
>
> **Hypothesis:** If users in their first 7 days who haven't connected an integration are shown a persistent sidebar suggestion card surfacing the four available integrations (Slack, GitHub, Google Drive, Jira) with one-click setup, plus a 48-hour email nudge, more users will fire `integration_connected` within 7 days, increasing week-4 retention.
>
> **Target segment:** Days 1–7 post-signup, has created a workspace or project, no `integration_connected` event yet.
>
> **Success event:** `integration_connected`
>
> **Guardrails:** week-4 churn rate among nudged users; support contact rate within 7 days; plan downgrade rate within 30 days.
>
> **Expected effect:** +7 to +10pp in week-4 retention — about 15–20% of the +50.8pp correlation gap, accounting for self-selection in the original "did" cohort.
>
> **Rationale (grounded in actual stats):** *"72.2% of the 'did' cohort are Power users with ~85% baseline retention, so a substantial portion of the gap reflects pre-existing intent rather than behavior-to-retention causation. A nudge can only convert the marginal user — likely the activator-profile users who would benefit from reduced setup friction but won't seek integrations out on their own."*

That last sentence is the core of why the discount matters. The 3.20× lift looks like a 3.20× retention bump waiting to be captured. It isn't. Most of the lift is selection. The honest number is the 15–20% slice an intervention can plausibly convert. The whole synthesis stage is designed to produce that honest number rather than the marketing-deck one.

## Results

End-to-end on 25,000 synthetic users and 370,489 events generated against a four-persona behavioral model:

- **24 actionable milestones** surfaced (top 10 displayed). Headline result: `completed_onboarding_step_5` at 3.37× retention lift, 60% Power-persona dominance among the "did" cohort.
- **25 distinct activation paths** discovered via ordered-sequence analysis. Top sequence is the pure Power trajectory at 1.50× lift against the candidate-pool base.
- **10 experiment specs** drafted by Sonnet 4.6, each grounded in the milestone's actual statistics with the correlation-vs-causation discount applied. Expected effect sizes in the 7–10pp range, 14–20% of observed correlation gaps.
- **Persona-dominance check passes cleanly:** every top-5 actionable milestone is 57–73% Power-persona, Bouncers consistently 0%, Lookers ≤1.4%. The engine correctly isolated behaviors that discriminate *among engaged users* — not just between engaged and unengaged.

**Engine wall-clock:** 1.6 seconds for the full mining + path analysis pass on 370k events. The "<30 seconds vs. multi-query SQL exploration" target leaves substantial headroom. Synthesis adds ~200 seconds for 10 sequential LLM calls; that's the bottleneck, and it parallelizes trivially if it ever needs to.

**Stack:** Python + FastAPI + Polars + Postgres for the engine and API; Next.js (App Router, Server Components) + Tailwind for the dashboard; Anthropic Sonnet 4.6 for synthesis; Redis provisioned but not yet used; Docker Compose to bring up the data layer. A single FastAPI service exposes `/health /milestones /paths /specs`; the Next.js dashboard fetches all three in parallel during SSR and renders them in three labeled sections.

[Insert dashboard screenshot here.]

The whole thing — engine, paths, synthesis, API, dashboard — runs against the synthetic generator's seeded data, fully reproducible with `random.seed(42)`. Same seed produces the same milestones, the same paths, and (modulo the LLM's sampling) the same experiment specs.

## Limitations

A serious project should be honest about what it doesn't do. Wayline's scope is deliberately constrained.

**Synthetic data only.** Real product event streams have noise, missing data, late-arriving events, schema drift, and identity-resolution problems that a generator can't fully simulate. The four-persona model is a useful caricature, not a realistic distribution — real user populations have many more latent types, with smoother and noisier boundaries between them. The "synthetic" qualifier in the project description matters when reading the results.

**No real-time ingestion.** Events are batched into Postgres once by the generator. A production version would need stream ingestion (Kafka or managed equivalent), incremental computation of cohort labels, and a freshness guarantee on milestone rankings. The engine itself is fast enough to run on a schedule; the work would be in the ingestion side.

**Engine is blind to anti-patterns by design.** It surfaces behaviors *positively* correlated with retention. Behaviors *negatively* correlated — friction events, error patterns, dead-end flows — are equally interesting product signals but invisible to this version. Adding negative-lift mining is a one-page change; not in scope here.

**Single fixed analysis window.** Cohort labeling uses a hard-coded 21–28 day post-signup window for week-4 retention. Real analysis wants comparison across multiple windows (week 1, week 4, week 12). The engine is parameterized; no UI exposes the parameter.

**Authentication scoped out.** The FastAPI surface has no auth — CORS is pinned narrowly to localhost. Local-demo surface, not a multi-tenant service.

**Path analysis is shallow.** Sequences of length 5, no variants on prefix length, no parameterized "any of these N events in any order within Y days" patterns. The combinatorial space is large; this is the simplest version that still produces interpretable headline results.

**Engine reads Postgres on every API call.** Mining + path analysis each cost ~1–2 seconds. Redis is provisioned but not used for caching the engine's outputs. The natural caching seam is `mine_milestones`'s raw output; not warranted at current throughput.

The bigger meta-limitation is that Wayline is a *demonstration* of what a behavioral product intelligence engine looks like, not a deployed product. The honest version of the resume bullet is: *built a working prototype of an activation-mining engine, with a synthetic test bed, an evaluation methodology grounded in known persona ground truth, and an LLM synthesis stage that produces specs grounded in actual statistics rather than plausible-sounding hallucinations.* That version is what the code actually backs.
