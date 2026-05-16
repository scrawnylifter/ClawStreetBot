#!/usr/bin/env python3
"""Backfill trading.signals for the full historical OHLCV range.

Walks every distinct trading day in market.ohlcv (timeframe='1d') and
re-runs the composite scoring engine (see scripts/generate_signals.py)
as-of that date. Only symbols with a daily bar on the target date are
scored, so the backfill won't manufacture rows for delisted/inactive
symbols on days they weren't trading.

generate_signals.py already handles missing derived inputs gracefully
(returning 0 for that factor's score), so this script will produce
sparse-but-valid signals deep in the lookback where IV rank, GEX, and
sentiment have no data yet.

The script imports generate_signals directly (no subprocess) so the
500+ trading-day backfill stays in a single Python process. Writes use
ON CONFLICT (symbol, signal_date) DO UPDATE so it's safe to re-run.

Usage:
    python scripts/backfill_signals.py
    python scripts/backfill_signals.py --start 2024-05-16
    python scripts/backfill_signals.py --start 2024-05-16 --end 2024-12-31
    python scripts/backfill_signals.py --symbol NVDA
    python scripts/backfill_signals.py --batch-size 50
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date
from pathlib import Path

import psycopg2

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from generate_signals import (  # noqa: E402
    DB_CONFIG,
    fetch_inputs,
    score_symbol,
    write_signal,
)


def fetch_trading_days(
    conn, start: date | None, end: date | None
) -> list[date]:
    """Return distinct daily-bar dates within the requested window.

    Args:
        conn: psycopg2 connection.
        start: Inclusive lower bound (None = no lower bound).
        end:   Inclusive upper bound (None = no upper bound).

    Returns:
        Sorted list of trading dates.
    """
    sql = [
        "SELECT DISTINCT timestamp::date AS d",
        "FROM market.ohlcv",
        "WHERE timeframe = '1d'",
    ]
    params: list[object] = []
    if start is not None:
        sql.append("AND timestamp::date >= %s")
        params.append(start)
    if end is not None:
        sql.append("AND timestamp::date <= %s")
        params.append(end)
    sql.append("ORDER BY d")
    with conn.cursor() as cur:
        cur.execute(" ".join(sql), params)
        return [r[0] for r in cur.fetchall()]


def fetch_symbols_with_bar_on(conn, day: date, restrict: list[str] | None) -> list[str]:
    """Return symbols that have a 1d bar on `day`.

    Args:
        conn: psycopg2 connection.
        day: Trading date to filter on.
        restrict: Optional whitelist of symbols.

    Returns:
        Sorted list of symbols.
    """
    sql = """
        SELECT a.symbol
        FROM market.ohlcv o
        JOIN market.assets a ON a.id = o.asset_id
        WHERE o.timeframe = '1d'
          AND o.timestamp::date = %s
    """
    params: list[object] = [day]
    if restrict:
        sql += " AND a.symbol = ANY(%s)"
        params.append(restrict)
    sql += " ORDER BY a.symbol"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [r[0] for r in cur.fetchall()]


def main() -> int:
    """Main entry point for the historical signal backfill."""
    p = argparse.ArgumentParser(
        description="Backfill trading.signals for the full historical OHLCV range"
    )
    p.add_argument(
        "--start",
        type=date.fromisoformat,
        default=None,
        help="Inclusive start date (YYYY-MM-DD). Default: earliest 1d bar.",
    )
    p.add_argument(
        "--end",
        type=date.fromisoformat,
        default=None,
        help="Inclusive end date (YYYY-MM-DD). Default: latest 1d bar.",
    )
    p.add_argument(
        "--symbol",
        type=str,
        default=None,
        help="Restrict backfill to a single symbol (default: every symbol with a bar)",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Commit every N trading days (default: 50)",
    )
    args = p.parse_args()

    restrict = [args.symbol.upper()] if args.symbol else None

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        days = fetch_trading_days(conn, args.start, args.end)
        if not days:
            print("No trading days in the requested range.")
            return 0
        print(
            f"Backfilling signals for {len(days)} trading days "
            f"({days[0]} → {days[-1]}), batch size {args.batch_size}"
        )

        total_signals = 0
        t0 = time.monotonic()
        for idx, day in enumerate(days, start=1):
            symbols = fetch_symbols_with_bar_on(conn, day, restrict)
            for sym in symbols:
                inputs = fetch_inputs(conn, sym, day)
                scored = score_symbol(inputs)
                write_signal(conn, inputs, scored)
                total_signals += 1

            if idx % args.batch_size == 0:
                conn.commit()
            if idx % 10 == 0 or idx == len(days):
                elapsed = time.monotonic() - t0
                rate = idx / elapsed if elapsed > 0 else 0.0
                eta = (len(days) - idx) / rate if rate > 0 else 0.0
                print(
                    f"  [{idx:>4}/{len(days)}] {day}  "
                    f"signals_written={total_signals}  "
                    f"elapsed={elapsed:5.1f}s  "
                    f"rate={rate:4.2f} days/s  "
                    f"eta={eta:5.0f}s"
                )

        conn.commit()
        print(f"Done. Wrote/updated {total_signals} signal rows across {len(days)} days.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
