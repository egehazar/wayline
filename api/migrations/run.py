"""Apply pending migrations in api/migrations/*.sql in lexical order.

Idempotent: only applies files not already recorded in schema_migrations.

Usage (from repo root):
    uv run --project api python api/migrations/run.py
"""
from pathlib import Path
import os
import sys

import psycopg
from dotenv import load_dotenv


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

    migrations_dir = Path(__file__).resolve().parent
    files = sorted(migrations_dir.glob("*.sql"))
    if not files:
        print("no migration files found")
        return 0

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                create table if not exists schema_migrations (
                    version    text primary key,
                    applied_at timestamptz not null default now()
                )
            """)
            cur.execute("select version from schema_migrations")
            applied = {row[0] for row in cur.fetchall()}
        conn.commit()

        for f in files:
            version = f.stem
            if version in applied:
                print(f"  skip   {version}")
                continue
            sql = f.read_text()
            print(f"  apply  {version}")
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "insert into schema_migrations (version) values (%s)",
                    (version,),
                )
            conn.commit()

    print("migrations complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
