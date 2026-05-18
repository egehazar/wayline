"""Path analysis — ordered event sequences and their retention lift.

For each user with at least N post-signup events, the first N event_names in
chronological order form an "activation path". Group users by path and compute
retention lift RELATIVE TO THE CANDIDATE-POOL BASE RATE (retention rate among
all users who reached N events), not against the full population.

Why candidate-pool base rate, not full-population base rate:
Sequences only exist for users who reached N events — users who never get
there (Bouncers, very-disengaged Lookers) have no path to compare against.
Using the full-population base would re-introduce the "did anything" problem
the milestone specificity filter already solved: any sequence at all would
look high-lift simply because reaching N events selects out the 20% Bouncer
cohort at 0% retention. The candidate-pool base rate isolates the question
path analysis is actually trying to answer — given a user is engaged enough
to reach N events, WHICH ORDERED SEQUENCE predicts the strongest retention.
Expect modest lifts (1.5-2.5x); the comparison group is already retaining
well above the full-population rate.
"""
from __future__ import annotations

import polars as pl


def compute_paths(
    users_df: pl.DataFrame,
    events_df: pl.DataFrame,
    prefix_length: int = 5,
    min_sample: int = 100,
) -> pl.DataFrame:
    """Mine activation paths and rank by retention lift vs. candidate-pool base rate.

    Returns a DataFrame with columns:
        sequence:     list[str] — the ordered event_names
        sequence_str: str       — sequence joined by " → " for display
        n_users:      int
        retain_pct:   float     — fraction (0-1) of users with this sequence who retained
        lift:         float     — retain_pct / candidate_pool_base_rate
    Sorted by lift descending. Requires users_df to have `active_week_4`
    (call compute_retention first).
    """
    # Drop signup_completed (universal; not an activation step) and sort so the
    # per-user aggregation below produces chronologically ordered lists.
    ev = (
        events_df.filter(pl.col("event_name") != "signup_completed")
                 .select("user_id", "event_name", "ts")
                 .sort("user_id", "ts")
    )

    # Per user, first `prefix_length` event_names as an ordered list. agg()
    # preserves the input row order within each group, so the sort above
    # makes this chronological. head(N) inside agg picks the first N rows
    # of each group; users with fewer than N events are dropped by the
    # subsequent length filter.
    per_user = (
        ev.group_by("user_id")
          .agg(pl.col("event_name").head(prefix_length).alias("sequence"))
          .filter(pl.col("sequence").list.len() == prefix_length)
          .join(users_df.select("user_id", "active_week_4"), on="user_id")
    )

    # Candidate-pool base rate (retention among users with >= prefix_length
    # post-signup events). This is the comparison anchor for lift.
    base_rate = per_user.select(
        pl.col("active_week_4").cast(pl.Float64).mean()
    ).item()
    if not base_rate:
        return pl.DataFrame(schema={
            "sequence": pl.List(pl.Utf8), "sequence_str": pl.Utf8,
            "n_users": pl.UInt32, "retain_pct": pl.Float64, "lift": pl.Float64,
        })

    # group_by on a List column isn't reliable across Polars versions; join the
    # list into a delimited string key, group on that, then recover the list
    # via first() (all rows in a group share the same sequence by construction).
    return (
        per_user.with_columns(pl.col("sequence").list.join("\x1f").alias("_key"))
                .group_by("_key")
                .agg(
                    pl.col("sequence").first().alias("sequence"),
                    pl.len().alias("n_users"),
                    pl.col("active_week_4").cast(pl.Float64).mean().alias("retain_pct"),
                )
                .filter(pl.col("n_users") >= min_sample)
                .with_columns(
                    (pl.col("retain_pct") / base_rate).alias("lift"),
                    pl.col("sequence").list.join(" → ").alias("sequence_str"),
                )
                .sort("lift", descending=True)
                .select("sequence", "sequence_str", "n_users", "retain_pct", "lift")
    )
