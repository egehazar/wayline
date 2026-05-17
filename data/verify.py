"""Sanity-check the generated synthetic data against PERSONAS.md.

Exits non-zero if any check fails. Idempotent: read-only against the DB.
"""
from __future__ import annotations

import os
import random
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

random.seed(42)

PERSONA_MIX_PCT = {"power": 15.0, "activator": 30.0, "looker": 35.0, "bouncer": 20.0}
DIST_TOL_PP = 2.0
RETAIN_RANGE = {
    "power":     (80.0, 90.0),
    "activator": (45.0, 55.0),
    "looker":    (10.0, 20.0),
    "bouncer":   (0.0, 5.0),
}


def _connect():
    root = Path(__file__).resolve().parent.parent
    env_path = root / ".env"
    if not env_path.exists():
        env_path = root / ".env.example"
    load_dotenv(env_path)
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("error: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)
    return psycopg.connect(db_url)


def _report(name: str, ok: bool, detail: str) -> bool:
    tag = "PASS" if ok else "FAIL"
    print(f"  [{tag}]  {name:<48} {detail}")
    return ok


def main() -> int:
    failed = False
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM users")
            n_users = cur.fetchone()[0]
            ok = n_users == 25_000
            failed |= not _report("user count = 25,000", ok, f"got {n_users:,}")

            cur.execute("SELECT persona, COUNT(*) FROM users GROUP BY persona")
            counts = {row[0]: row[1] for row in cur.fetchall()}
            for persona, target_pct in PERSONA_MIX_PCT.items():
                actual_pct = 100.0 * counts.get(persona, 0) / max(1, n_users)
                lo, hi = target_pct - DIST_TOL_PP, target_pct + DIST_TOL_PP
                ok = lo <= actual_pct <= hi
                failed |= not _report(
                    f"persona mix {persona} in [{lo:.0f}, {hi:.0f}]%",
                    ok, f"got {actual_pct:.2f}%",
                )

            cur.execute("""
                SELECT u.persona,
                       COUNT(*) AS total,
                       SUM(CASE WHEN EXISTS (
                           SELECT 1 FROM events e
                           WHERE e.user_id = u.user_id
                             AND e.ts >= u.signup_ts + interval '21 days'
                             AND e.ts <  u.signup_ts + interval '28 days'
                       ) THEN 1 ELSE 0 END) AS retained
                FROM users u
                GROUP BY u.persona
            """)
            retention = {row[0]: (row[1], row[2]) for row in cur.fetchall()}
            for persona, (lo, hi) in RETAIN_RANGE.items():
                total, retained = retention.get(persona, (0, 0))
                actual_pct = 100.0 * retained / max(1, total)
                ok = lo <= actual_pct <= hi
                failed |= not _report(
                    f"retention {persona} in [{lo:.0f}, {hi:.0f}]%",
                    ok, f"got {actual_pct:.2f}% ({retained:,}/{total:,})",
                )

            cur.execute("SELECT COUNT(*) FROM events")
            n_events = cur.fetchone()[0]
            ok = 200_000 <= n_events <= 400_000
            failed |= not _report(
                "event count in [200,000, 400,000]", ok, f"got {n_events:,}",
            )

            cur.execute("""
                SELECT COUNT(*) FROM events e
                WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.user_id = e.user_id)
            """)
            n_orphans = cur.fetchone()[0]
            failed |= not _report("orphan events = 0", n_orphans == 0, f"got {n_orphans}")

            cur.execute("""
                SELECT u.user_id
                FROM users u
                WHERE u.persona = 'power'
                  AND EXISTS (
                      SELECT 1 FROM events e
                      WHERE e.user_id = u.user_id
                        AND e.event_name = 'integration_connected'
                        AND e.ts < u.signup_ts + interval '7 days'
                  )
                  AND (
                      SELECT COUNT(*) FROM events e
                      WHERE e.user_id = u.user_id
                        AND e.event_name IN ('project_created','task_created','task_completed','comment_posted')
                        AND e.ts < u.signup_ts + interval '7 days'
                  ) >= 10
                LIMIT 1
            """)
            row = cur.fetchone()
            failed |= not _report(
                "power user w/ integration + 10+ core actions in 7d",
                row is not None,
                f"example user_id={row[0]}" if row else "no match",
            )

            cur.execute("""
                SELECT u.user_id, COUNT(e.event_id) AS n_events
                FROM users u
                LEFT JOIN events e ON e.user_id = u.user_id
                WHERE u.persona = 'bouncer'
                GROUP BY u.user_id
                HAVING COUNT(e.event_id) <= 2
                LIMIT 1
            """)
            row = cur.fetchone()
            failed |= not _report(
                "bouncer w/ <= 2 total events",
                row is not None,
                f"example user_id={row[0]} ({row[1]} events)" if row else "no match",
            )

    print()
    if failed:
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
