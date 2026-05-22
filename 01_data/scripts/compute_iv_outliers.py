#!/usr/bin/env python3
"""Flag IV outliers per symbol using a 1-year rolling z-score.

For each (symbol, date) in market.iv_rank, computes the mean and
standard deviation of `current_iv` over the trailing 252 trading days
(approximated here as the 252 most-recent iv_rank rows for that symbol
on or before the evaluation date). The z-score is

    z = (current_iv - mean_1y) / std_1y

Rows with |z| > 3 are upserted into market.iv_outliers, tagged with
direction 'high' (z > 3) or 'low' (z < -3). Non-outlier rows are not
inserted, keeping the table sparse.

Re-runnable and idempotent (INSERT ON CONFLICT DO UPDATE).

Usage:
    python scripts/compute_iv_outliers.py
    python scripts/compute_iv_outliers.py --symbol NVDA
    python scripts/compute_iv_outliers.py --threshold 2.5
"""
from __future__ import annotations

import argparse
import math
import os
from datetime import date
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import execute_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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

WINDOW = 252  # ~1 trading year

FETCH_SYMBOLS_SQL = """
SELECT symbol FROM market.assets
WHERE asset_type IN ('stock', 'etf')
  AND active = TRUE
ORDER BY symbol;
"""

FETCH_IV_SQL = """
SELECT date, current_iv
FROM market.iv_rank
WHERE symbol = %s
  AND current_iv IS NOT NULL
ORDER BY date;
"""

UPSERT_SQL = """
INSERT INTO market.iv_outliers (
    symbol, date, current_iv, iv_mean_1y, iv_std_1y, z_score, direction
) VALUES %s
ON CONFLICT (symbol, date) DO UPDATE SET
    current_iv = EXCLUDED.current_iv,
    iv_mean_1y = EXCLUDED.iv_mean_1y,
    iv_std_1y  = EXCLUDED.iv_std_1y,
    z_score    = EXCLUDED.z_score,
    direction  = EXCLUDED.direction,
    created_at = NOW();
"""


def detect_outliers(
    series: list[tuple[date, float]], window: int, threshold: float
) -> list[tuple[date, float, float, float, float, str]]:
    """Find rolling-window IV outliers in a single symbol's history.

    Args:
        series: Chronologically ordered (date, current_iv) tuples.
        window: Rolling window size in observations (e.g., 252).
        threshold: Absolute z-score above which a row is flagged.

    Returns:
        List of (date, current_iv, mean, std, z_score, direction) tuples
        for rows whose |z| exceeds the threshold.
    """
    out: list[tuple[date, float, float, float, float, str]] = []
    n = len(series)
    if n < max(20, window // 4):
        return out
    for i in range(window - 1 if n >= window else max(20, n // 4) - 1, n):
        start = max(0, i - window + 1)
        win = [v for _, v in series[start : i + 1]]
        k = len(win)
        if k < 20:
            continue
        mean = sum(win) / k
        var = sum((x - mean) ** 2 for x in win) / (k - 1)
        std = math.sqrt(var)
        if std == 0:
            continue
        dt, iv = series[i]
        z = (iv - mean) / std
        if abs(z) > threshold:
            out.append((dt, iv, mean, std, z, "high" if z > 0 else "low"))
    return out


def compute_for_symbol(
    conn: Any, symbol: str, threshold: float
) -> list[tuple]:
    """Detect outliers for a single symbol.

    Args:
        conn: psycopg2 database connection.
        symbol: Ticker symbol.
        threshold: Absolute z-score threshold for flagging.

    Returns:
        List of tuples ready for upsert into market.iv_outliers.
    """
    with conn.cursor() as cur:
        cur.execute(FETCH_IV_SQL, (symbol,))
        rows = cur.fetchall()

    series: list[tuple[date, float]] = [(r[0], float(r[1])) for r in rows]
    if not series:
        print(f"  {symbol}: no iv_rank rows, skipping")
        return []

    flagged = detect_outliers(series, WINDOW, threshold)
    return [
        (symbol, dt, iv, mean, std, z, direction)
        for dt, iv, mean, std, z, direction in flagged
    ]


def main() -> int:
    """Main entry point for IV outlier detection."""
    parser = argparse.ArgumentParser(
        description="Flag rolling-window IV outliers per symbol"
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default=None,
        help="Evaluate a single symbol instead of the full watchlist",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=3.0,
        help="Absolute z-score above which a row is flagged (default: 3.0)",
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

        all_rows: list[tuple] = []
        for sym in symbols:
            print(f"Scanning IV outliers for {sym}...")
            rows = compute_for_symbol(conn, sym, args.threshold)
            all_rows.extend(rows)
            if rows:
                last = rows[-1]
                print(
                    f"  {sym}: {len(rows)} outlier(s); latest"
                    f" date={last[1]} z={float(last[5]):+.2f} dir={last[6]}"
                )
            else:
                print(f"  {sym}: no outliers")

        if not all_rows:
            print("No outlier rows to upsert.")
            return 0

        print(f"Upserting {len(all_rows)} rows into market.iv_outliers...")
        with conn.cursor() as cur:
            execute_values(cur, UPSERT_SQL, all_rows, page_size=500)
        conn.commit()
        print(f"Done. Upserted {len(all_rows)} iv_outliers rows.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
