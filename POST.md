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

## LLM-drafted experiment specs

(To fill in when the synthesis stage is built. How to constrain generation so specs are grounded in observed data rather than plausible-sounding hallucinations.)

## Results

(Milestones surfaced, retention lift numbers, time-to-analysis benchmarks, demo artifacts.)

## Limitations

(Honest. To fill in.)
