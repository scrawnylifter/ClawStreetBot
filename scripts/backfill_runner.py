#!/usr/bin/env python3
"""Runner for backfill_pending n8n workflow.
Loads .env.db, queries pending symbols from the database,
then invokes backfill_symbol.py for each one.
"""
import os
import sys
import subprocess
from pathlib import Path


def load_env(env_path: Path) -> None:
    """Load key=value pairs from an env file into os.environ."""
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ[k.strip()] = v.strip()


def get_pending_symbols(dsn: str) -> list[str]:
    """Return symbols with backfill_status='pending'."""
    import psycopg2

    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(
        "SELECT symbol FROM market.assets "
        "WHERE active = TRUE AND backfill_status = 'pending' "
        "ORDER BY symbol"
    )
    symbols = [row[0] for row in cur.fetchall()]
    conn.close()
    return symbols


def main() -> None:
    # Load DB credentials
    env_path = Path("/app/.env.db")
    load_env(env_path)

    db_host = os.environ.get("POSTGRES_HOST", "postgres")
    db_user = os.environ.get("POSTGRES_USER", "clawstreet")
    db_pass = os.environ.get("POSTGRES_PASSWORD", "")
    db_name = os.environ.get("POSTGRES_DB", "clawstreetbot")

    dsn = f"host={db_host} user={db_user} password={db_pass} dbname={db_name}"

    symbols = get_pending_symbols(dsn)

    if not symbols:
        print("No pending symbols.")
        return

    print(f"Pending: {' '.join(symbols)}")

    for sym in symbols:
        print(f"--- backfill {sym} ---")
        result = subprocess.run(
            [sys.executable, "/app/scripts/backfill_symbol.py", sym],
            capture_output=False,
        )
        if result.returncode != 0:
            print(f"FAILED {sym}")


if __name__ == "__main__":
    main()