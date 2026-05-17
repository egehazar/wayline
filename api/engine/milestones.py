"""Milestone mining engine for Wayline.

Pure Python + Polars. No FastAPI dependencies; importable as a library.

Pipeline:
  load_data        -> read users + events from Postgres into Polars DataFrames
  compute_retention -> derive active_week_4 from events in days 21-28 post-signup
  candidate_milestones -> hand-defined templates × parameter sweeps (~37 candidates)
  evaluate_milestone -> per candidate: n_did, retain_did, retain_didnt, lift
  mine_milestones    -> evaluate all candidates, filter, rank by lift

The engine is BLIND to the `persona` column except in persona_dominance(), which
is called separately for evaluation against ground truth.
"""
from __future__ import annotations

import polars as pl
import psycopg


def load_data(db_url: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Read users and events from Postgres into Polars DataFrames.

    step_index is pre-extracted from the events.properties JSONB so milestone
    predicates can compare it as a typed column without per-row JSON parsing.
    """
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT user_id::text, signup_ts, channel, plan_tier, country, persona
                FROM users
            """)
            user_rows = cur.fetchall()
        users_df = pl.DataFrame(
            user_rows,
            schema={
                "user_id":   pl.Utf8,
                "signup_ts": pl.Datetime("us", time_zone="UTC"),
                "channel":   pl.Utf8,
                "plan_tier": pl.Utf8,
                "country":   pl.Utf8,
                "persona":   pl.Utf8,
            },
            orient="row",
        )

        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    event_id::text,
                    user_id::text,
                    event_name,
                    ts,
                    session_id::text,
                    (properties->>'step_index')::int AS step_index
                FROM events
            """)
            event_rows = cur.fetchall()
        events_df = pl.DataFrame(
            event_rows,
            schema={
                "event_id":   pl.Utf8,
                "user_id":    pl.Utf8,
                "event_name": pl.Utf8,
                "ts":         pl.Datetime("us", time_zone="UTC"),
                "session_id": pl.Utf8,
                "step_index": pl.Int32,
            },
            orient="row",
        )

    return users_df, events_df


def compute_retention(users_df: pl.DataFrame, events_df: pl.DataFrame) -> pl.DataFrame:
    """Add active_week_4 (bool) — true iff user has >=1 event in days 21-28 post-signup."""
    joined = events_df.select("user_id", "ts").join(
        users_df.select("user_id", "signup_ts"), on="user_id"
    )
    in_window = joined.filter(
        (pl.col("ts") >= pl.col("signup_ts") + pl.duration(days=21))
        & (pl.col("ts") < pl.col("signup_ts") + pl.duration(days=28))
    )
    retained_ids = in_window.select("user_id").unique()
    return users_df.with_columns(
        pl.col("user_id").is_in(retained_ids["user_id"]).alias("active_week_4")
    )


def candidate_milestones() -> list[dict]:
    out: list[dict] = []

    for ev in ["integration_connected", "invite_sent", "plan_upgraded", "workspace_created"]:
        for days in [3, 7, 14, 28]:
            out.append({
                "name": f"{ev}_within_{days}_days",
                "predicate_kind": "event_within_days",
                "event_name": ev,
                "days": days,
            })

    for ev in ["project_created", "task_created", "task_completed", "comment_posted", "session_started"]:
        for n in [1, 3, 5, 10]:
            out.append({
                "name": f"{ev}_at_least_{n}_in_week1",
                "predicate_kind": "event_count_at_least_in_first_week",
                "event_name": ev,
                "min_count": n,
            })

    for step in [1, 2, 3, 4, 5]:
        out.append({
            "name": f"completed_onboarding_step_{step}",
            "predicate_kind": "completed_onboarding_step",
            "step_index": step,
        })

    for d in [2, 3, 5]:
        out.append({
            "name": f"sessions_on_{d}_distinct_days_in_week1",
            "predicate_kind": "sessions_on_distinct_days_in_first_week",
            "min_days": d,
        })

    return out


def _users_matching(users_df: pl.DataFrame, events_df: pl.DataFrame, m: dict) -> pl.Series:
    """Return the unique Series of user_id values that satisfy milestone m.

    Never reads the persona column.
    """
    kind = m["predicate_kind"]

    if kind == "event_within_days":
        signup = users_df.select("user_id", "signup_ts")
        ev = events_df.filter(pl.col("event_name") == m["event_name"]).select("user_id", "ts")
        joined = ev.join(signup, on="user_id")
        hit = joined.filter(pl.col("ts") < pl.col("signup_ts") + pl.duration(days=m["days"]))
        return hit.select("user_id").unique()["user_id"]

    if kind == "event_count_at_least_in_first_week":
        signup = users_df.select("user_id", "signup_ts")
        ev = events_df.filter(pl.col("event_name") == m["event_name"]).select("user_id", "ts")
        joined = ev.join(signup, on="user_id")
        in_w1 = joined.filter(pl.col("ts") < pl.col("signup_ts") + pl.duration(days=7))
        counts = in_w1.group_by("user_id").len()
        return counts.filter(pl.col("len") >= m["min_count"]).select("user_id")["user_id"]

    if kind == "completed_onboarding_step":
        hit = events_df.filter(
            (pl.col("event_name") == "onboarding_step_completed")
            & (pl.col("step_index") == m["step_index"])
        )
        return hit.select("user_id").unique()["user_id"]

    if kind == "sessions_on_distinct_days_in_first_week":
        signup = users_df.select("user_id", "signup_ts")
        ev = events_df.filter(pl.col("event_name") == "session_started").select("user_id", "ts")
        joined = ev.join(signup, on="user_id")
        in_w1 = joined.filter(pl.col("ts") < pl.col("signup_ts") + pl.duration(days=7))
        with_day = in_w1.with_columns(
            ((pl.col("ts") - pl.col("signup_ts")).dt.total_seconds() // 86400).alias("day_offset")
        )
        per_user = with_day.group_by("user_id").agg(pl.col("day_offset").n_unique().alias("n_days"))
        return per_user.filter(pl.col("n_days") >= m["min_days"]).select("user_id")["user_id"]

    raise ValueError(f"unknown predicate_kind: {kind}")


def evaluate_milestone(users_df: pl.DataFrame, events_df: pl.DataFrame, milestone: dict) -> dict:
    """Compute n_did, n_didnt, retain_did, retain_didnt, lift for one milestone.

    Requires `users_df` to already have `active_week_4` (call compute_retention first).
    """
    did_ids = _users_matching(users_df, events_df, milestone)
    flagged = users_df.with_columns(pl.col("user_id").is_in(did_ids).alias("did"))

    did = flagged.filter(pl.col("did"))
    didnt = flagged.filter(~pl.col("did"))

    n_did = did.height
    n_didnt = didnt.height

    retain_did = (
        did.select(pl.col("active_week_4").cast(pl.Float64).mean()).item() if n_did else 0.0
    )
    retain_didnt = (
        didnt.select(pl.col("active_week_4").cast(pl.Float64).mean()).item() if n_didnt else 0.0
    )

    lift = retain_did / retain_didnt if retain_didnt and retain_didnt > 0 else None

    return {
        **milestone,
        "n_did": n_did,
        "n_didnt": n_didnt,
        "retain_did": retain_did,
        "retain_didnt": retain_didnt,
        "lift": lift,
    }


def mine_milestones(
    users_df: pl.DataFrame,
    events_df: pl.DataFrame,
    min_sample: int = 200,
    max_share: float = 0.25,
) -> pl.DataFrame:
    """Run all candidate milestones, filter, rank by lift desc.

    Filters applied (all must hold):
      - n_did >= min_sample           (statistical power)
      - lift is not null              (retain_didnt > 0)
      - n_did / n_total <= max_share  (specificity: an actionable milestone
                                       is one a meaningful but not universal
                                       fraction of users hit; "did anything"
                                       predicates fail this gate)

    Pass max_share=1.0 to disable the specificity filter.
    """
    n_total = users_df.height
    rows = [evaluate_milestone(users_df, events_df, m) for m in candidate_milestones()]
    df = pl.DataFrame(
        rows,
        schema={
            "name":           pl.Utf8,
            "predicate_kind": pl.Utf8,
            "event_name":     pl.Utf8,
            "days":           pl.Int32,
            "min_count":      pl.Int32,
            "step_index":     pl.Int32,
            "min_days":       pl.Int32,
            "n_did":          pl.Int64,
            "n_didnt":        pl.Int64,
            "retain_did":     pl.Float64,
            "retain_didnt":   pl.Float64,
            "lift":           pl.Float64,
        },
        orient="row",
    )
    return (
        df.filter(pl.col("n_did") >= min_sample)
          .filter(pl.col("lift").is_not_null())
          .filter(pl.col("n_did") / n_total <= max_share)
          .sort("lift", descending=True)
    )


def persona_dominance(
    users_df: pl.DataFrame, events_df: pl.DataFrame, milestone: dict
) -> dict[str, float]:
    """Percent of each persona within the milestone's 'did' cohort.

    The only function in this module that reads the persona column.
    """
    did_ids = _users_matching(users_df, events_df, milestone)
    cohort = users_df.filter(pl.col("user_id").is_in(did_ids))
    total = cohort.height
    if total == 0:
        return {}
    counts = cohort.group_by("persona").len()
    return {row["persona"]: 100.0 * row["len"] / total for row in counts.iter_rows(named=True)}
