#!/usr/bin/env python3
"""snapshot_equity.py — Record the current Alpaca paper account equity.

Writes one row per invocation to market.equity_snapshots. Downstream
scripts (process_approved, exit_monitor) use this to compute drawdown
and enforce the Law-of-Ruin halts.

Designed for a cron schedule — hourly during market hours, once at EOD
outside them. The UPSERT on (snapshot_date) means multiple same-day
snapshots overwrite harmlessly; the last one of the day survives.

Usage:
    python 05_execution/scripts/snapshot_equity.py              # one snapshot now
    python 05_execution/scripts/snapshot_equity.py --verbose     # show equity + DB row
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import psycopg2
import psycopg2.extras

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("snapshot_equity")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "shared"))
from constants import DB_CONFIG, load_env  # noqa: E402

load_env(".env.db")
load_env(".env.alpaca")


def get_connection():
    return psycopg2.connect(**DB_CONFIG)


def get_alpaca_equity() -> Decimal:
    """Query the paper account equity. Raises on failure."""
    from alpaca.trading.client import TradingClient

    key = os.environ.get("ALPACA_PAPER_API_KEY")
    sec = os.environ.get("ALPACA_PAPER_SECRET_KEY")
    if not key or not sec:
        raise RuntimeError("Missing ALPACA_PAPER_API_KEY / _SECRET_KEY in environment")

    client = TradingClient(api_key=key, secret_key=sec, paper=True)
    account = client.get_account()
    equity = Decimal(str(account.equity))
    return equity


def upsert_snapshot(conn, snap_date: date, equity: Decimal) -> None:
    """INSERT or UPDATE the equity snapshot for today."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO market.equity_snapshots (snapshot_date, equity, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (snapshot_date)
            DO UPDATE SET equity    = EXCLUDED.equity,
                          updated_at = EXCLUDED.updated_at
            """,
            (snap_date, equity),
        )
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true",
                        help="Print equity value and DB confirmation.")
    args = parser.parse_args()

    equity = get_alpaca_equity()
    today = date.today()

    conn = get_connection()
    try:
        upsert_snapshot(conn, today, equity)
    finally:
        conn.close()

    if args.verbose:
        print(f"Equity snapshot for {today}: ${equity:,.2f}")

    log.info("Recorded equity snapshot for %s: $%s", today, f"{equity:,.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
