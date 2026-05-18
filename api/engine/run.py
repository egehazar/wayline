"""CLI entry for the milestone-mining engine.

Run: uv run python api/engine/run.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from milestones import (
    candidate_milestones,
    compute_retention,
    load_data,
    mine_milestones,
    persona_dominance,
)
from paths import compute_paths

PERSONAS = ("power", "activator", "looker", "bouncer")


def _print_table(rows: list[list[str]], align: list[str]) -> None:
    """rows: row 0 is header. align: '<' or '>' per column."""
    n_cols = len(rows[0])
    widths = [max(len(r[i]) for r in rows) for i in range(n_cols)]
    for i, r in enumerate(rows):
        print("  ".join(f"{r[j]:{align[j]}{widths[j]}}" for j in range(n_cols)))
        if i == 0:
            print("  ".join("-" * widths[j] for j in range(n_cols)))


def main() -> int:
    root = Path(__file__).resolve().parent.parent.parent
    env_path = root / ".env"
    if not env_path.exists():
        env_path = root / ".env.example"
    load_dotenv(env_path)

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("error: DATABASE_URL not set", file=sys.stderr)
        return 1

    t0 = time.perf_counter()
    print("loading users + events from postgres ...")
    users_df, events_df = load_data(db_url)
    print(f"  loaded {users_df.height:,} users, {events_df.height:,} events  "
          f"({time.perf_counter()-t0:.1f}s)")

    print("computing retention (active_week_4) ...")
    users_df = compute_retention(users_df, events_df)
    overall_retention = users_df["active_week_4"].cast(float).mean()
    print(f"  base rate retain = {overall_retention*100:.2f}%")

    print("mining milestones ...")
    t_mine = time.perf_counter()
    raw = mine_milestones(users_df, events_df, min_sample=200, max_share=1.0)
    actionable = mine_milestones(users_df, events_df, min_sample=200, max_share=0.25)
    print(f"  raw: {raw.height} pass min_sample; actionable: {actionable.height} also pass "
          f"max_share <= 25%  ({time.perf_counter()-t_mine:.1f}s)")

    def render(df: "pl.DataFrame", n: int) -> None:
        rows: list[list[str]] = [["rank", "milestone", "n_did", "retain_did%", "retain_didnt%", "lift"]]
        for i, r in enumerate(df.head(n).iter_rows(named=True), start=1):
            rows.append([
                str(i),
                r["name"],
                f"{r['n_did']:,}",
                f"{r['retain_did']*100:.1f}",
                f"{r['retain_didnt']*100:.1f}",
                f"{r['lift']:.2f}x",
            ])
        _print_table(rows, align=[">", "<", ">", ">", ">", ">"])

    print()
    print("=" * 88)
    print("  Table A — Top 10 by lift, RAW  (min_sample=200; no specificity filter)")
    print("  Mechanical correctness check. Broad 'did-anything' predicates dominate here.")
    print("=" * 88)
    render(raw, 10)

    print()
    print("=" * 88)
    print("  Table B — Top 10 by lift, ACTIONABLE  (min_sample=200; n_did <= 25% of users)")
    print("  Headline ranking. Drops the broad early-funnel predicates that just exclude Bouncers.")
    print("=" * 88)
    render(actionable, 10)

    print()
    print("=" * 88)
    print("  Persona dominance — top 5 ACTIONABLE milestones (% of 'did' cohort by persona)")
    print("=" * 88)

    name_to_milestone = {m["name"]: m for m in candidate_milestones()}
    dom_rows: list[list[str]] = [["milestone", "n_did"] + [p for p in PERSONAS]]
    for r in actionable.head(5).iter_rows(named=True):
        m = name_to_milestone[r["name"]]
        dom = persona_dominance(users_df, events_df, m)
        dom_rows.append(
            [r["name"], f"{r['n_did']:,}"]
            + [f"{dom.get(p, 0.0):.1f}%" for p in PERSONAS]
        )
    _print_table(dom_rows, align=["<", ">", ">", ">", ">", ">"])

    t_paths = time.perf_counter()
    paths_df = compute_paths(users_df, events_df, prefix_length=5, min_sample=100)
    print()
    print("=" * 88)
    print("  Table C — Top 10 activation paths  (prefix_length=5, min_sample=100)")
    print("  Lift relative to base rate among users with >=5 post-signup events.")
    print("=" * 88)
    print(f"  {paths_df.height} unique sequences passed min_sample  "
          f"({time.perf_counter()-t_paths:.1f}s)")
    print()

    path_rows: list[list[str]] = [["rank", "sequence", "n_users", "retain%", "lift"]]
    for i, r in enumerate(paths_df.head(10).iter_rows(named=True), start=1):
        path_rows.append([
            str(i),
            r["sequence_str"],
            f"{r['n_users']:,}",
            f"{r['retain_pct']*100:.1f}",
            f"{r['lift']:.2f}x",
        ])
    _print_table(path_rows, align=[">", "<", ">", ">", ">"])

    print()
    print(f"wall clock: {time.perf_counter()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
