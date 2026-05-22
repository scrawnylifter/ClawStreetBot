#!/usr/bin/env python3
"""Compute realized volatility and IV-RV spread for watchlist symbols.

Reads daily close prices from market.ohlcv, calculates 20-day and 5-day
annualized realized volatility using log returns, and upserts the results
into market.realized_vol. Also computes the IV-RV spread by joining with
market.iv_rank.

Formula:
    RV = sqrt(sum(ln(P_i / P_{i-1}))^2 / window) * sqrt(252)

This is the population standard deviation of log returns annualized
by multiplying with sqrt(252 trading days).

Re-runnable and idempotent (INSERT ON CONFLICT DO UPDATE).

Usage:
    python scripts/compute_realized_vol.py                 # full watchlist
    python scripts/compute_realized_vol.py --symbol NVDA   # single symbol
    python scripts/compute_realized_vol.py --days 180      # lookback window
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import execute_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env(filename: str) -> None:
    """Load environment variables from a dotenv-style file."""
    path = PROJECT_ROOT / filename
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


load_env(".env.db")

DB_CONFIG = {
    "host": os.environ.get("POSTGRES_HOST", "localhost"),
    "port": int(os.environ.get("POSTGRES_PORT", 5432)),
    "dbname": os.environ["POSTGRES_DB"],
    "user": os.environ["POSTGRES_USER"],
    "password": os.environ["POSTGRES_PASSWORD"],
}

# ---------------------------------------------------------------------------
# SQL queries
# ---------------------------------------------------------------------------

FETCH_SYMBOLS_SQL = """
SELECT symbol FROM market.assets
WHERE asset_type IN ('stock', 'etf')
  AND active = TRUE
ORDER BY symbol;
"""

FETCH_CLOSES_SQL = """
SELECT a.symbol, o.timestamp::date AS dt, o.close
FROM market.ohlcv o
JOIN market.assets a ON a.id = o.asset_id
WHERE a.symbol = %s
  AND o.timeframe = '1d'
  AND o.timestamp::date >= %s
  AND o.close IS NOT NULL
ORDER BY o.timestamp::date;
"""

# For a specific symbol
FETCH_CLOSES_SINGLE_SQL = """
SELECT o.timestamp::date AS dt, o.close
FROM market.ohlcv o
JOIN market.assets a ON a.id = o.asset_id
WHERE a.symbol = %s
  AND o.timeframe = '1d'
  AND o.timestamp::date >= %s
  AND o.close IS NOT NULL
ORDER BY o.timestamp::date;
"""

UPSERT_RV_SQL = """
INSERT INTO market.realized_vol (symbol, date, rv_20d, rv_5d)
VALUES %s
ON CONFLICT (symbol, date) DO UPDATE SET
    rv_20d = EXCLUDED.rv_20d,
    rv_5d  = EXCLUDED.rv_5d,
    created_at = NOW();
"""

# Compute IV-RV spread: (current_iv - rv_20d) for the latest date per symbol
COMPUTE_IV_RV_SPREAD_SQL = """
WITH latest AS (
    SELECT DISTINCT ON (rv.symbol)
        rv.symbol, rv.date, rv.rv_20d
    FROM market.realized_vol rv
    ORDER BY rv.symbol, rv.date DESC
)
SELECT l.symbol, l.date, iv.current_iv, l.rv_20d,
       (iv.current_iv - l.rv_20d) AS iv_rv_spread
FROM latest l
JOIN market.iv_rank iv
  ON iv.symbol = l.symbol
  AND iv.date = l.date
WHERE l.rv_20d IS NOT NULL
  AND iv.current_iv IS NOT NULL
ORDER BY l.symbol;
"""


def compute_rv(log_returns: list[float], window: int) -> float | None:
    """Compute annualized realized volatility over a rolling window.

    Args:
        log_returns: List of log returns in chronological order.
        window: Number of days for the rolling window (e.g., 20 or 5).

    Returns:
        Annualized realized volatility as a float, or None if insufficient data.
    """
    if len(log_returns) < window:
        return None
    # Take the last `window` returns
    recent = log_returns[-window:]
    # Population variance: sum of squared returns / window
    variance = sum(r * r for r in recent) / window
    return math.sqrt(variance) * math.sqrt(252)


def compute_realized_vol(
    conn: Any, symbol: str, days: int
) -> list[tuple[str, date, float | None, float | None]]:
    """Compute realized volatility for a single symbol.

    Args:
        conn: psycopg2 database connection.
        symbol: Ticker symbol to compute RV for.
        days: Lookback window in calendar days for fetching price data.

    Returns:
        List of (symbol, date, rv_20d, rv_5d) tuples ready for upsert.
    """
    start_date = date.today() - timedelta(days=days)
    with conn.cursor() as cur:
        cur.execute(FETCH_CLOSES_SINGLE_SQL, (symbol, start_date))
        rows = cur.fetchall()

    if len(rows) < 2:
        print(f"  {symbol}: insufficient price data ({len(rows)} rows), skipping")
        return []

    # Build chronologically-ordered lists of (date, close) and log returns
    closes: list[tuple[date, float]] = []
    log_returns: list[float] = []
    prev_close: float | None = None

    for dt, close_decimal in rows:
        close = float(close_decimal)
        if prev_close is not None and prev_close > 0 and close > 0:
            lr = math.log(close / prev_close)
            log_returns.append(lr)
            closes.append((dt, close))
        elif prev_close is None:
            # First row: no return yet, but track it for the next step
            closes.append((dt, close))
        prev_close = close

    # Shift: log_returns[i] corresponds to closes[i+1] (the date of the
    # second price in the pair). We need to align RV values with the
    # date of the last close in each window.
    # log_returns[0] is the return from closes[0] to closes[1],
    # so log_returns[0] has date closes[1].

    results: list[tuple[str, date, float | None, float | None]] = []
    for i in range(min(20, len(log_returns)) - 1, len(log_returns)):
        # We need at least that many returns up to position i
        as_of_date = closes[i + 1][0]  # date of the closing price at position i+1
        rv_20d = compute_rv(log_returns[: i + 1], 20)
        rv_5d = compute_rv(log_returns[: i + 1], 5)
        results.append((symbol, as_of_date, rv_20d, rv_5d))

    return results


def main() -> int:
    """Main entry point for realized volatility computation."""
    parser = argparse.ArgumentParser(
        description="Compute realized volatility for watchlist symbols"
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default=None,
        help="Compute RV for a single symbol instead of the full watchlist",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=365,
        help="Lookback window in calendar days (default: 365)",
    )
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)

    try:
        with conn.cursor() as cur:
            if args.symbol:
                symbols = [args.symbol.upper()]
            else:
                cur.execute(FETCH_SYMBOLS_SQL)
                symbols = [row[0] for row in cur.fetchall()]

        all_rows: list[tuple[str, date, float | None, float | None]] = []
        for sym in symbols:
            print(f"Computing RV for {sym}...")
            rows = compute_realized_vol(conn, sym, args.days)
            all_rows.extend(rows)
            if rows:
                latest = rows[-1]
                rv20 = f"{latest[2]:.4f}" if latest[2] is not None else "N/A"
                rv5 = f"{latest[3]:.4f}" if latest[3] is not None else "N/A"
                print(f"  {sym}: latest date={latest[1]}, rv_20d={rv20}, rv_5d={rv5}")

        if not all_rows:
            print("No rows to upsert.")
            return 0

        # Upsert into market.realized_vol
        print(f"Upserting {len(all_rows)} rows into market.realized_vol...")
        with conn.cursor() as cur:
            execute_values(cur, UPSERT_RV_SQL, all_rows, page_size=500)
        conn.commit()

        # Compute and display IV-RV spread
        print("\nIV-RV spread (current_iv - rv_20d):")
        print("-" * 60)
        with conn.cursor() as cur:
            cur.execute(COMPUTE_IV_RV_SPREAD_SQL)
            spread_rows = cur.fetchall()
            for sym, dt, current_iv, rv_20d, iv_rv_spread in spread_rows:
                spread_str = f"{float(iv_rv_spread):.4f}" if iv_rv_spread is not None else "N/A"
                print(
                    f"  {sym:6s}  date={dt}  "
                    f"iv={float(current_iv):.4f}  "
                    f"rv_20d={float(rv_20d):.4f}  "
                    f"spread={spread_str}"
                )

        print(f"\nDone. Upserted {len(all_rows)} realized_vol rows.")

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())