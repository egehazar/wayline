"""CLI entry for the LLM synthesis stage.

Run: uv run python api/engine/synthesize.py

Loads users + events, mines actionable milestones, synthesizes structured
experiment specs for the top 10, writes JSON to data/experiment_specs.json,
and renders each spec to stdout as a formatted text block.
"""
from __future__ import annotations

import json
import os
import re
import sys
import textwrap
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
from synthesis import EVENT_TAXONOMY, synthesize_top_n


PERSONAS = ("power", "activator", "looker", "bouncer")
WRAP_WIDTH = 78
INDENT = "  "


def _wrap(text: str) -> str:
    return textwrap.fill(text, width=WRAP_WIDTH, initial_indent=INDENT,
                         subsequent_indent=INDENT)


def _render_spec(idx: int, spec, stats: dict, dominance: dict[str, float],
                 n_total: int) -> None:
    pct = 100.0 * stats["n_did"] / n_total
    power_pct = dominance.get("power", 0.0)

    print("=" * 80)
    print(f"  Experiment {idx}: {spec.milestone_name}")
    print(f"  Lift: {stats['lift']:.2f}x  |  "
          f"Cohort: {stats['n_did']:,} users ({pct:.1f}%)  |  "
          f"Power dominance: {power_pct:.0f}%")
    print("=" * 80)
    print()
    print("Hypothesis:")
    print(_wrap(spec.hypothesis))
    print()
    print("Target segment:")
    print(_wrap(spec.target_segment))
    print()
    print("Success event:")
    print(f"{INDENT}{spec.success_event}")
    print()
    print("Guardrail metrics:")
    for m in spec.guardrail_metrics:
        print(f"{INDENT}- {m}")
    print()
    print("Expected effect size:")
    print(_wrap(spec.expected_effect_size))
    print()
    print("Rationale:")
    print(_wrap(spec.rationale))
    print()


_LEADING_PREDICTION_RE = re.compile(
    r"\+?(\d+(?:\.\d+)?)\s*(?:to\s*\+?(\d+(?:\.\d+)?)\s*)?(?:pp|percentage points?)",
    re.IGNORECASE,
)


def _quotes_raw_lift(effect_size: str, raw_lift: float, raw_gap_pp: float) -> str | None:
    """Flag if the LEADING numeric prediction is within ±5pp of the raw observed gap.

    The model's actual prediction is the first numeric value (or range) in
    `expected_effect_size`; anything cited later is context (e.g. naming the
    raw gap to show the discount). The failure mode we care about is the
    model quoting the raw correlation as its experimental prediction — which
    means the leading value, not any later mention, has to match the gap.
    """
    m = _LEADING_PREDICTION_RE.search(effect_size)
    if not m:
        return None
    lo = float(m.group(1))
    hi = float(m.group(2)) if m.group(2) else lo
    leading_value = (lo + hi) / 2
    if abs(leading_value - raw_gap_pp) <= 5.0:
        rng = f"{lo:.1f}pp" if lo == hi else f"{lo:.1f}-{hi:.1f}pp (midpoint {leading_value:.1f}pp)"
        return f"leading prediction {rng} ≈ raw gap {raw_gap_pp:.1f}pp"
    return None


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

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("error: ANTHROPIC_API_KEY not set (or empty) in .env / .env.example",
              file=sys.stderr)
        return 1

    t0 = time.perf_counter()
    print("loading users + events ...")
    users_df, events_df = load_data(db_url)
    print(f"  loaded {users_df.height:,} users, {events_df.height:,} events "
          f"({time.perf_counter()-t0:.1f}s)")

    print("computing retention ...")
    users_df = compute_retention(users_df, events_df)

    print("mining actionable milestones (min_sample=200, max_share<=0.25) ...")
    t_mine = time.perf_counter()
    actionable = mine_milestones(users_df, events_df, min_sample=200, max_share=0.25)
    print(f"  {actionable.height} actionable milestones  "
          f"({time.perf_counter()-t_mine:.1f}s)")

    print()
    print(f"synthesizing top 10 experiment specs via claude-sonnet-4-6 ...")
    t_synth = time.perf_counter()
    specs = synthesize_top_n(actionable, users_df, events_df, n=10)
    print(f"  done  ({time.perf_counter()-t_synth:.1f}s, "
          f"avg {(time.perf_counter()-t_synth)/max(1,len(specs)):.1f}s/spec)")
    print()

    out_path = root / "data" / "experiment_specs.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps([s.model_dump() for s in specs], indent=2))
    print(f"wrote {len(specs)} specs to {out_path.relative_to(root)}")
    print()

    name_to_stats = {r["name"]: r for r in actionable.head(len(specs)).iter_rows(named=True)}
    name_to_milestone = {m["name"]: m for m in candidate_milestones()}
    n_total = users_df.height

    warnings: list[str] = []
    for i, spec in enumerate(specs, start=1):
        stats = name_to_stats[spec.milestone_name]
        dom = persona_dominance(users_df, events_df, name_to_milestone[spec.milestone_name])
        _render_spec(i, spec, stats, dom, n_total)

        raw_lift = stats["lift"]
        raw_gap_pp = (stats["retain_did"] - stats["retain_didnt"]) * 100
        quoted = _quotes_raw_lift(spec.expected_effect_size, raw_lift, raw_gap_pp)
        if quoted is not None:
            warnings.append(
                f"  [{spec.milestone_name}] expected_effect_size appears to quote raw correlation '{quoted}'"
            )

    if warnings:
        print("=" * 80)
        print("  Warnings — possible correlation-vs-causation leakage in expected_effect_size")
        print("=" * 80)
        for w in warnings:
            print(w)
        print()

    print(f"wall clock: {time.perf_counter()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
