#!/usr/bin/env python3
"""snapshot_equity.py — record daily Alpaca paper account equity.

Writes one row per calendar date to market.equity_snapshots, the denominator
table for process_approved.calc_drawdown's drawdown halts. Idempotent for
a given date: on conflict the row is updated so re-runs refresh today's
value with the latest equity.

Run once per trading day after market close (after INGEST_DAILY_AFTER_CLOSE ET). Without these
snapshots, drawdown halts can't fire — preflight will warn instead.

Usage:
    python scripts/snapshot_equity.py             # snapshot today
    python scripts/snapshot_equity.py --date 2026-05-17
    python scripts/snapshot_equity.py --no-alpaca # $100k default — testing only
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from constants import INGEST_DAILY_AFTER_CLOSE  # noqa: E402
import process_approved as pa  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("snapshot_equity")


def upsert_snapshot(conn, snapshot_date: date, equity: Decimal) -> None:
    """Idempotent insert — re-running for the same date updates equity in place."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO market.equity_snapshots (snapshot_date, equity)
                  VALUES (%s, %s)
                  ON CONFLICT (snapshot_date) DO UPDATE
                    SET equity      = EXCLUDED.equity,
                        recorded_at = NOW()""",
            (snapshot_date, equity),
        )
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-alpaca", action="store_true",
                        help="Use pa.DEFAULT_EQUITY ($100k) instead of Alpaca — testing only.")
    parser.add_argument("--date", type=str, default=None,
                        help="Snapshot date YYYY-MM-DD (default: today).")
    args = parser.parse_args()

    equity = pa.DEFAULT_EQUITY if args.no_alpaca else pa.get_alpaca_equity()
    if equity is None or equity <= 0:
        log.error("Refusing to snapshot non-positive equity (%s)", equity)
        return 2

    snap_date = date.fromisoformat(args.date) if args.date else date.today()

    conn = pa.get_connection()
    try:
        upsert_snapshot(conn, snap_date, equity)
    finally:
        conn.close()

    log.info("Snapshot recorded: %s equity=$%s", snap_date, f"{equity:,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
