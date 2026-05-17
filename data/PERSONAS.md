# Personas

The synthetic generator assigns each user one of four hidden personas. The Wayline engine is blind to this column — it queries events and retention labels only. Personas exist to:

1. **Inject latent structure** into the synthetic data so milestones actually exist to discover (uniformly random events produce no signal).
2. **Provide ground truth** for evaluating the engine's output — *"the engine surfaced milestone X; does X actually correspond to a behavior the Power persona has?"*

## Population mix and retention probabilities

| Persona | % users | P(retain @ week 4) | One-line behavior summary |
|---|---|---|---|
| **Power** | 15% | 0.85 | Onboarding <48h, integration connected, 10+ core actions in week 1, invites teammate, multi-day sessions |
| **Activator** | 30% | 0.50 | Onboarding within 2 weeks, 3–5 core actions in week 1, sometimes integrates, 2–3 sessions per week |
| **Looker** | 35% | 0.15 | 0–1 onboarding steps, sporadic visits, no integrations, no invites |
| **Bouncer** | 20% | 0.02 | Signup + maybe one follow-up, never returns |

## Domain — B2B project management

The synthetic data simulates a B2B project management tool. Event taxonomy:

| Event name | Description | Key properties |
|---|---|---|
| `signup_completed` | User completes signup | `method`: email/google_oauth/sso |
| `onboarding_step_completed` | Onboarding step done | `step_index` (1–5), `step_name` |
| `workspace_created` | First workspace created | `workspace_id` |
| `project_created` | New project | `project_id` |
| `task_created` | New task | `task_id`, `project_id` |
| `task_completed` | Task marked done | `task_id` |
| `comment_posted` | Comment on a task | `task_id` |
| `integration_connected` | Integration connected | `integration`: slack/github/google_drive/jira |
| `invite_sent` | Teammate invited | `invitee_email_hash` |
| `plan_upgraded` | Plan changed | `from`, `to` |
| `session_started` | Session begins | `source`: web/mobile/desktop |
| `session_ended` | Session ends | `duration_seconds` |

Onboarding flow has 5 steps: `step_1_workspace`, `step_2_first_project`, `step_3_first_task`, `step_4_invite_member`, `step_5_connect_integration`.

Core actions = `project_created` + `task_created` + `task_completed` + `comment_posted`.

## Per-persona behavior parameters

### Power user
- Sessions per day in week 1: Poisson(λ=3)
- Onboarding: completes all 5 steps within 24–48h of signup
- Core actions in week 1: Uniform(10, 25)
- `integration_connected`: P=0.95, within 7 days of signup
- `invite_sent`: P=0.75, within 14 days
- `plan_upgraded`: P=0.40, within 30 days
- Retention week 4: P=0.85
- If retained: sessions continue at Poisson(λ=2) per day in weeks 2–4

### Activator
- Sessions per day in week 1: Poisson(λ=1.5)
- Onboarding: completes 3–5 of 5 steps over 3–10 days
- Core actions in week 1: Uniform(3, 8)
- `integration_connected`: P=0.35, within 14 days
- `invite_sent`: P=0.15, within 30 days
- `plan_upgraded`: P=0.10, within 30 days
- Retention week 4: P=0.50
- If retained: sessions at Poisson(λ=1) per day in weeks 2–4

### Looker
- Sessions per day in week 1: Poisson(λ=0.3)
- Onboarding: completes 0–2 of 5 steps over 1–4 weeks
- Core actions in week 1: Uniform(0, 2)
- `integration_connected`: P=0.02
- `invite_sent`: P=0.01
- `plan_upgraded`: P=0.005
- Retention week 4: P=0.15
- If retained: sessions at Poisson(λ=0.3) per day in weeks 2–4

### Bouncer
- One `signup_completed` event, plus 0–1 follow-up events within 24h
- No onboarding, no core actions, no integrations, no invites, no upgrades
- Retention week 4: P=0.02 (effectively all churned)

## Why these parameters

Not calibrated to a real product — chosen so that:

1. Each persona has a distinctive behavioral fingerprint discoverable from event data alone.
2. Adjacent personas overlap meaningfully (Power/Activator both do core actions; Looker/Bouncer both have low engagement). Pure separation would be unrealistic and would make the engine's job trivial.
3. The ~12 milestones target falls out naturally at a reasonable retention-lift threshold.

## What the engine should discover

Approximate ranked list of milestones the engine should surface, descending by retention lift:

1. Integration connected within 7 days *(Power-dominant)* — highest lift
2. Onboarding completed within 48h *(Power-dominant)*
3. 10+ core actions in week 1 *(Power-dominant)*
4. Invited a teammate *(Power-dominant)*
5. Plan upgraded within 30 days *(subset of Power)*
6. 3+ sessions in week 1 *(Power + Activator)*
7. Onboarding completed at any pace *(Power + Activator)*
8. 3+ core actions in week 1 *(Power + Activator)*
9. Any integration connected *(Power + some Activator)*
10. Returned for a session in week 2 *(Power + Activator)*
11. Multi-day session pattern in week 1 *(Power + Activator)*
12. Workspace created within 24h of signup *(Power-dominant)*

Exact set depends on the milestone-mining implementation. ~12 is the target ballpark, not a precise count.

## Evaluation — validating discovered milestones

When the engine surfaces a milestone, this query checks which personas dominate that user cohort:

```sql
select
    u.persona,
    count(*) as user_count
from users u
where u.user_id in (
    -- example milestone condition: integration_connected within 7 days
    select e.user_id
    from events e
    join users u2 on u2.user_id = e.user_id
    where e.event_name = 'integration_connected'
      and e.ts < u2.signup_ts + interval '7 days'
)
group by u.persona
order by user_count desc;
```

A clean milestone shows Power persona dominating. A noisy milestone shows a more uniform persona distribution — that's a signal the engine surfaced something near the threshold but not robustly correlated. This is the evaluation criterion the post will quote.
