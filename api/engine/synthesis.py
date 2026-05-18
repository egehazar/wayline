"""LLM synthesis stage — turns top milestones into structured experiment specs.

Uses Claude Sonnet 4.6 with forced tool use to constrain output to a strict schema.
Pure library; no FastAPI dependencies, no I/O of its own (caller writes JSON/text).

Anti-hallucination posture:
  - `success_event` enum in the tool schema makes invalid event names impossible.
  - Pydantic re-validates `success_event` against EVENT_TAXONOMY as defense in depth.
  - Prompt explicitly frames the observed lift as a correlation and provides a
    pre-computed plausible causal-lift range (10-30% of observed gap), so the
    model has the right anchor for `expected_effect_size`.
"""
from __future__ import annotations

import anthropic
from pydantic import BaseModel, field_validator

EVENT_TAXONOMY: dict[str, str] = {
    "signup_completed":          "User completes signup (method: email | google_oauth | sso).",
    "onboarding_step_completed": "User completes one of 5 onboarding steps (step_index 1-5, step_name).",
    "workspace_created":         "User creates their first workspace (typically fires alongside onboarding step 1).",
    "project_created":           "User creates a new project.",
    "task_created":              "User creates a new task (project_id).",
    "task_completed":            "User marks a task as done.",
    "comment_posted":            "User posts a comment on a task.",
    "integration_connected":     "User connects an integration (slack | github | google_drive | jira).",
    "invite_sent":               "User invites a teammate.",
    "plan_upgraded":             "User upgrades their plan tier (from/to in free | pro | business).",
    "session_started":           "User begins a session (source: web | mobile | desktop).",
    "session_ended":             "User ends a session (duration_seconds property).",
}


class ExperimentSpec(BaseModel):
    milestone_name: str
    hypothesis: str
    target_segment: str
    success_event: str
    guardrail_metrics: list[str]
    expected_effect_size: str
    rationale: str

    @field_validator("success_event")
    @classmethod
    def _success_event_in_taxonomy(cls, v: str) -> str:
        if v not in EVENT_TAXONOMY:
            raise ValueError(
                f"success_event '{v}' is not in EVENT_TAXONOMY. "
                f"Valid: {sorted(EVENT_TAXONOMY)}"
            )
        return v


_SUBMIT_TOOL = {
    "name": "submit_experiment_spec",
    "description": (
        "Submit a structured experiment specification grounded in the milestone "
        "statistics provided in the user message."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "hypothesis": {
                "type": "string",
                "description": (
                    "Clear If/Then statement linking the proposed intervention "
                    "to the retention outcome. Concrete, not aspirational."
                ),
            },
            "target_segment": {
                "type": "string",
                "description": (
                    "Specific user segment to enroll. Bad: 'new users'. "
                    "Good: 'users in their first 7 days post-signup who have not "
                    "yet connected an integration'."
                ),
            },
            "success_event": {
                "type": "string",
                "enum": list(EVENT_TAXONOMY),
                "description": "Must be one of the exact event names in the taxonomy.",
            },
            "guardrail_metrics": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "2 to 3 metrics to monitor for regression — e.g., post-intervention "
                    "churn rate, support contact rate, downgrade rate, complaint rate."
                ),
            },
            "expected_effect_size": {
                "type": "string",
                "description": (
                    "Realistic estimated CAUSAL effect — typically 10 to 30 percent "
                    "of the observed retention gap, expressed in percentage points "
                    "of retention rate. Must not quote the raw observed lift verbatim."
                ),
            },
            "rationale": {
                "type": "string",
                "description": (
                    "1 to 2 paragraphs grounded in the stats provided. "
                    "Reference only numbers in the prompt; do not invent stats."
                ),
            },
        },
        "required": [
            "hypothesis", "target_segment", "success_event",
            "guardrail_metrics", "expected_effect_size", "rationale",
        ],
        "additionalProperties": False,
    },
}


def _describe_predicate(m: dict) -> str:
    kind = m["predicate_kind"]
    if kind == "event_within_days":
        return (f"User has a `{m['event_name']}` event within "
                f"{m['days']} days of signup.")
    if kind == "event_count_at_least_in_first_week":
        return (f"User has at least {m['min_count']} `{m['event_name']}` "
                f"events within the first 7 days post-signup.")
    if kind == "completed_onboarding_step":
        return (f"User has completed onboarding step {m['step_index']} "
                f"(`onboarding_step_completed` event with `step_index={m['step_index']}`).")
    if kind == "sessions_on_distinct_days_in_first_week":
        return (f"User has `session_started` events on at least "
                f"{m['min_days']} distinct calendar days in their first 7 days.")
    return "<unknown predicate>"


def build_prompt(milestone_row: dict, dominance: dict[str, float]) -> str:
    """Construct the LLM prompt: stats + dominance + taxonomy + constraints."""
    pct = milestone_row["n_did_pct"]
    retain_did_pct = milestone_row["retain_did"] * 100
    retain_didnt_pct = milestone_row["retain_didnt"] * 100
    gap_pp = retain_did_pct - retain_didnt_pct

    lo = gap_pp * 0.10
    hi = gap_pp * 0.30

    taxonomy_block = "\n".join(f"  - `{k}` — {v}" for k, v in EVENT_TAXONOMY.items())
    dom_block = "\n".join(
        f"  - {p}: {d:.1f}%"
        for p, d in sorted(dominance.items(), key=lambda kv: -kv[1])
    )

    return f"""You are designing a real product experiment, grounded in observed usage statistics from a B2B project-management tool. The goal is to take a milestone behavior that correlates with retention and design an intervention to drive more users toward it. Be specific and concrete — this is a real experiment, not a marketing pitch.

## Milestone

**Name:** `{milestone_row['name']}`
**Predicate:** {_describe_predicate(milestone_row)}

## Observed statistics

  - Cohort size (n_did):             {milestone_row['n_did']:,} users ({pct:.1f}% of total population)
  - Retention rate, "did" cohort:    {retain_did_pct:.1f}%
  - Retention rate, "didn't" cohort: {retain_didnt_pct:.1f}%
  - Retention gap:                   +{gap_pp:.1f} percentage points
  - Lift (ratio):                    {milestone_row['lift']:.2f}x

## Persona dominance — post-hoc engine validation against hidden ground truth

The engine never reads persona; this is a validation cross-check. Personas:
  power     = high-engagement, ~85% retention
  activator = moderate engagement, ~50% retention
  looker    = low engagement, ~15% retention
  bouncer   = signup-only, ~0% retention

This milestone's cohort breakdown:
{dom_block}

## Event taxonomy

`success_event` MUST be one of these exact strings:

{taxonomy_block}

## Constraints (read carefully)

1. **`success_event`** must be one of the exact event names above. No new event types.

2. **`expected_effect_size`** must be a realistic FRACTION of the observed retention gap — NOT the raw observed lift. The +{gap_pp:.1f}pp gap is a CORRELATION measured on existing users; some of it reflects pre-existing intent (motivated users seek out these behaviors on their own), not pure behavior-to-retention causation. Real-world interventions typically convert 10-30% of correlation into causal effect.
   - Plausible causal lift range for this milestone: **+{lo:.1f}pp to +{hi:.1f}pp** in week-4 retention rate.
   - Do NOT quote the raw {milestone_row['lift']:.2f}x lift or the full +{gap_pp:.1f}pp gap as the expected effect.

3. **`rationale`** must reference only numbers provided above (cohort size, retention rates, lift, persona breakdown). Do not invent stats. 1-2 paragraphs.

4. **Intervention** should be plausible product UX: an in-app prompt, a new onboarding step, an email nudge, a suggestion card, a contextual tooltip, etc.

5. **`target_segment`** should be specific. Bad: "new users". Good: "users in their first 7 days who have not yet completed [the action]".

6. **`guardrail_metrics`** should list 2-3 things to watch for regression: faster churn among nudged users, higher support-contact rate, downgrade rate, complaints, etc.

Submit your spec via the `submit_experiment_spec` tool."""


def synthesize_spec(milestone_row: dict, dominance: dict[str, float]) -> ExperimentSpec:
    """Call Claude Sonnet 4.6 with forced tool use; return a validated ExperimentSpec."""
    client = anthropic.Anthropic()
    prompt = build_prompt(milestone_row, dominance)

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        tools=[_SUBMIT_TOOL],
        tool_choice={"type": "tool", "name": "submit_experiment_spec"},
        messages=[{"role": "user", "content": prompt}],
    )

    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use is None:
        raise RuntimeError(
            f"no tool_use block returned for {milestone_row['name']} "
            f"(stop_reason={response.stop_reason})"
        )

    return ExperimentSpec(milestone_name=milestone_row["name"], **tool_use.input)


def synthesize_top_n(actionable_df, users_df, events_df, n: int = 10) -> list[ExperimentSpec]:
    """Iterate top N actionable milestones; one sequential API call per milestone."""
    from milestones import persona_dominance

    n_total = users_df.height
    top = actionable_df.head(n)

    specs: list[ExperimentSpec] = []
    for i, row in enumerate(top.iter_rows(named=True), start=1):
        dominance = persona_dominance(users_df, events_df, row)
        milestone_row = {**row, "n_did_pct": 100.0 * row["n_did"] / n_total}
        print(f"  [{i}/{n}] {row['name']} ...", end=" ", flush=True)
        spec = synthesize_spec(milestone_row, dominance)
        print("ok")
        specs.append(spec)

    return specs
