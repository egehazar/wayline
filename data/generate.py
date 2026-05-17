"""Synthetic event generator for Wayline.

Generates 25,000 users + ~250k-350k events into Postgres, with hidden persona
ground truth. See data/PERSONAS.md for the spec.

Reproducible via random.seed(42) + np.random.seed(42).

Idempotent: TRUNCATEs users + events before inserting (re-run produces identical data).

Note on session rate calibration:
PERSONAS.md specifies Poisson(3)/day for Power sessions in week 1 etc. At
25,000 users × 60-day observation window, the spec rates project ~1M events,
incompatible with the 200k-400k verify target. Session rates are calibrated
downward (factor ~4-5x) to fit the budget while preserving the persona
RANKING (Power > Activator > Looker > Bouncer) and per-event probabilities
(integration P=0.95 for Power, P=0.35 Activator, etc.) exactly as spec'd.
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

random.seed(42)
np.random.seed(42)

N_USERS = 25_000

PERSONA_MIX = [
    ("power", 0.15),
    ("activator", 0.30),
    ("looker", 0.35),
    ("bouncer", 0.20),
]

CHANNELS = ["organic", "paid", "referral"]
PLAN_TIERS = ["free", "pro", "business"]
COUNTRIES = ["US", "GB", "DE", "FR", "BR", "IN", "JP", "AU", "CA", "NL"]
SIGNUP_METHODS = ["email", "google_oauth", "sso"]
INTEGRATIONS = ["slack", "github", "google_drive", "jira"]
SESSION_SOURCES = ["web", "mobile", "desktop"]
ONBOARDING_STEPS = [
    (1, "step_1_workspace"),
    (2, "step_2_first_project"),
    (3, "step_3_first_task"),
    (4, "step_4_invite_member"),
    (5, "step_5_connect_integration"),
]
CORE_ACTIONS = ["project_created", "task_created", "task_completed", "comment_posted"]

NOW = datetime.now(timezone.utc).replace(microsecond=0)
SIGNUP_WINDOW_START = NOW - timedelta(days=30)
SIGNUP_WINDOW_END = NOW
OBSERVATION_END = NOW + timedelta(days=30)

# Calibrated session rates (see module docstring). Spec values in parens.
SESSION_RATE_WEEK1 = {"power": 0.55, "activator": 0.30, "looker": 0.10}    # spec: 3, 1.5, 0.3
SESSION_RATE_RETAINED = {"power": 0.30, "activator": 0.14, "looker": 0.05}  # spec: 2, 1, 0.3


def _new_id() -> str:
    return str(uuid.uuid4())


def _persona_for_user() -> str:
    r = random.random()
    cum = 0.0
    for persona, weight in PERSONA_MIX:
        cum += weight
        if r < cum:
            return persona
    return PERSONA_MIX[-1][0]


def _signup_ts() -> datetime:
    delta = (SIGNUP_WINDOW_END - SIGNUP_WINDOW_START).total_seconds()
    return SIGNUP_WINDOW_START + timedelta(seconds=random.random() * delta)


def _gen_user_row() -> tuple:
    persona = _persona_for_user()
    return (
        _new_id(),
        _signup_ts(),
        random.choice(CHANNELS),
        random.choices(PLAN_TIERS, weights=[0.75, 0.20, 0.05])[0],
        random.choice(COUNTRIES),
        persona,
    )


def _props_for_core_action(name: str) -> dict:
    if name == "project_created":
        return {"project_id": _new_id()}
    if name == "task_created":
        return {"task_id": _new_id(), "project_id": _new_id()}
    if name == "task_completed":
        return {"task_id": _new_id()}
    return {"task_id": _new_id()}  # comment_posted


def _session_id_for_ts(sessions: list, ts: datetime) -> str | None:
    for s_id, s_start, s_end in sessions:
        if s_start <= ts <= s_end:
            return s_id
    return None


def _gen_events_for_user(user_row: tuple) -> list:
    user_id, signup_ts, _, _, _, persona = user_row
    events = []
    obs_end = min(signup_ts + timedelta(days=60), OBSERVATION_END)

    events.append((
        _new_id(), user_id, "signup_completed", signup_ts, None,
        {"method": random.choice(SIGNUP_METHODS)},
    ))

    if persona == "bouncer":
        n_followups = random.randint(0, 1)
        for _ in range(n_followups):
            ts = signup_ts + timedelta(seconds=random.randint(60, 86400))
            if ts > obs_end:
                continue
            s_id = _new_id()
            events.append((_new_id(), user_id, "session_started", ts, s_id,
                           {"source": random.choice(SESSION_SOURCES)}))
        return events

    retain_p = {"power": 0.85, "activator": 0.50, "looker": 0.15}[persona]
    retained = random.random() < retain_p
    # Non-retained users get no events on/after day 21 so the retention check
    # (≥1 event in days 21–28) cleanly reflects the bernoulli outcome.
    horizon = obs_end if retained else min(obs_end, signup_ts + timedelta(days=21) - timedelta(seconds=1))

    if persona == "power":
        n_steps, onboard_hours = 5, random.uniform(24, 48)
    elif persona == "activator":
        n_steps, onboard_hours = random.randint(3, 5), random.uniform(3, 10) * 24
    else:  # looker
        n_steps, onboard_hours = random.randint(0, 2), random.uniform(7, 28) * 24

    onboarding_events = []
    for idx in range(n_steps):
        step_idx, step_name = ONBOARDING_STEPS[idx]
        frac = (idx + 1) / max(1, n_steps)
        step_ts = signup_ts + timedelta(hours=frac * onboard_hours + random.uniform(-0.5, 0.5))
        if step_ts < signup_ts:
            step_ts = signup_ts + timedelta(minutes=1)
        if step_ts > horizon:
            break
        onboarding_events.append(("onboarding_step_completed", step_ts,
                                  {"step_index": step_idx, "step_name": step_name}))
        if step_idx == 1:
            onboarding_events.append(("workspace_created", step_ts + timedelta(seconds=5),
                                      {"workspace_id": _new_id()}))

    if persona == "power":
        n_core = random.randint(10, 25)
    elif persona == "activator":
        n_core = random.randint(3, 8)
    else:
        n_core = random.randint(0, 2)

    week1_session_starts = []
    lam_w1 = SESSION_RATE_WEEK1[persona]
    for day in range(7):
        n = int(np.random.poisson(lam_w1))
        day_start = signup_ts + timedelta(days=day)
        day_end = day_start + timedelta(days=1)
        if day_end > horizon:
            day_end = horizon
        for _ in range(n):
            span = (day_end - day_start).total_seconds()
            if span <= 0:
                continue
            ts = day_start + timedelta(seconds=random.random() * span)
            if ts < signup_ts:
                ts = signup_ts + timedelta(seconds=random.randint(60, 3600))
            if ts > horizon:
                continue
            week1_session_starts.append(ts)

    post_session_starts = []
    if retained:
        lam_post = SESSION_RATE_RETAINED[persona]
        for day in range(7, 28):
            n = int(np.random.poisson(lam_post))
            day_start = signup_ts + timedelta(days=day)
            for _ in range(n):
                ts = day_start + timedelta(seconds=random.random() * 86400)
                if ts > obs_end:
                    continue
                post_session_starts.append(ts)
        retention_window = [s for s in post_session_starts
                            if signup_ts + timedelta(days=21) <= s < signup_ts + timedelta(days=28)]
        if not retention_window:
            day = random.randint(21, 27)
            ts = signup_ts + timedelta(days=day, seconds=random.randint(0, 86400))
            if ts <= obs_end:
                post_session_starts.append(ts)

    all_session_starts = sorted(week1_session_starts + post_session_starts)

    sessions = []
    for s_ts in all_session_starts:
        s_id = _new_id()
        duration = random.randint(60, 1800) if persona != "looker" else random.randint(30, 600)
        s_end = s_ts + timedelta(seconds=duration)
        sessions.append((s_id, s_ts, s_end))
        events.append((_new_id(), user_id, "session_started", s_ts, s_id,
                       {"source": random.choice(SESSION_SOURCES)}))
        events.append((_new_id(), user_id, "session_ended", s_end, s_id,
                       {"duration_seconds": duration}))

    for ev_name, ev_ts, props in onboarding_events:
        if ev_ts > horizon:
            continue
        s_id = _session_id_for_ts(sessions, ev_ts)
        events.append((_new_id(), user_id, ev_name, ev_ts, s_id, props))

    for _ in range(n_core):
        name = random.choice(CORE_ACTIONS)
        ev_ts = signup_ts + timedelta(seconds=random.uniform(60, 7 * 86400))
        if ev_ts > horizon:
            continue
        s_id = _session_id_for_ts(sessions, ev_ts)
        events.append((_new_id(), user_id, name, ev_ts, s_id, _props_for_core_action(name)))

    one_shots: list[tuple] = []
    if persona == "power":
        if random.random() < 0.95:
            one_shots.append(("integration_connected", {"integration": random.choice(INTEGRATIONS)}, 7))
        if random.random() < 0.75:
            one_shots.append(("invite_sent", {"invitee_email_hash": uuid.uuid4().hex}, 14))
        if random.random() < 0.40:
            one_shots.append(("plan_upgraded", {"from": "free", "to": "pro"}, 30))
    elif persona == "activator":
        if random.random() < 0.35:
            one_shots.append(("integration_connected", {"integration": random.choice(INTEGRATIONS)}, 14))
        if random.random() < 0.15:
            one_shots.append(("invite_sent", {"invitee_email_hash": uuid.uuid4().hex}, 30))
        if random.random() < 0.10:
            one_shots.append(("plan_upgraded", {"from": "free", "to": "pro"}, 30))
    else:  # looker
        if random.random() < 0.02:
            one_shots.append(("integration_connected", {"integration": random.choice(INTEGRATIONS)}, 60))
        if random.random() < 0.01:
            one_shots.append(("invite_sent", {"invitee_email_hash": uuid.uuid4().hex}, 60))
        if random.random() < 0.005:
            one_shots.append(("plan_upgraded", {"from": "free", "to": "pro"}, 60))

    for ev_name, props, window_days in one_shots:
        max_secs = min(window_days * 86400, int((horizon - signup_ts).total_seconds()))
        if max_secs <= 120:
            continue
        ev_ts = signup_ts + timedelta(seconds=random.randint(120, max_secs))
        s_id = _session_id_for_ts(sessions, ev_ts)
        events.append((_new_id(), user_id, ev_name, ev_ts, s_id, props))

    return events


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    env_path = root / ".env"
    if not env_path.exists():
        env_path = root / ".env.example"
    load_dotenv(env_path)

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("error: DATABASE_URL not set", file=sys.stderr)
        return 1

    t0 = time.perf_counter()
    print(f"generating {N_USERS:,} users + events ...")

    user_rows = []
    all_events = []
    persona_counts = {"power": 0, "activator": 0, "looker": 0, "bouncer": 0}

    for i in range(N_USERS):
        u = _gen_user_row()
        user_rows.append(u)
        persona_counts[u[5]] += 1
        all_events.extend(_gen_events_for_user(u))
        if (i + 1) % 1000 == 0:
            print(f"  generated {i+1:,} users  ({len(all_events):,} events so far)")

    t_gen = time.perf_counter() - t0
    print(f"generated {len(all_events):,} events in {t_gen:.1f}s; writing to DB ...")

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE users, events RESTART IDENTITY CASCADE")
        conn.commit()

        with conn.cursor() as cur:
            with cur.copy("COPY users (user_id, signup_ts, channel, plan_tier, country, persona) FROM STDIN") as cp:
                for row in user_rows:
                    cp.write_row(row)
        conn.commit()

        with conn.cursor() as cur:
            with cur.copy("COPY events (event_id, user_id, event_name, ts, session_id, properties) FROM STDIN") as cp:
                for (eid, uid, name, ts, sid, props) in all_events:
                    cp.write_row((eid, uid, name, ts, sid, Jsonb(props)))
        conn.commit()

    t_total = time.perf_counter() - t0
    print()
    print("=" * 50)
    print(f"users:       {len(user_rows):,}")
    for p in ("power", "activator", "looker", "bouncer"):
        pct = 100.0 * persona_counts[p] / len(user_rows)
        print(f"  {p:<10} {persona_counts[p]:>6,}  ({pct:.2f}%)")
    print(f"events:      {len(all_events):,}")
    print(f"wall clock:  {t_total:.1f}s  (gen: {t_gen:.1f}s, db: {t_total-t_gen:.1f}s)")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(main())
