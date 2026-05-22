#!/usr/bin/env python3
"""Compute 52-week IV rank & percentile from market.iv_rank.current_iv history.

For each (symbol, date) in market.iv_rank we already wrote `current_iv` via
backfill_historical_iv.py. This script populates the remaining columns:

    iv_low_52w     min current_iv over trailing 252 days
    iv_high_52w    max current_iv over trailing 252 days
    iv_rank_52w    100 * (current_iv - low) / (high - low)         (range-based)
    iv_percentile  100 * fraction of trailing days where IV < current_iv
    iv_std_dev     std-dev of current_iv over trailing 252 days

Definitions match what the Greeks Strategy doc gates on:
    IV Rank  < 25%   → option-buying zone
    IV Rank  > 75%   → no naked buying (premium too expensive)

Re-runnable. Idempotent UPDATEs only.
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg2

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_env(filename: str) -> None:
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


COMPUTE_SQL = """
WITH windowed AS (
    SELECT
        a.symbol,
        a.date,
        a.current_iv,
        MIN(b.current_iv)    AS iv_low,
        MAX(b.current_iv)    AS iv_high,
        STDDEV_SAMP(b.current_iv) AS iv_std,
        AVG((b.current_iv < a.current_iv)::int)::numeric AS pct_below
    FROM market.iv_rank a
    JOIN market.iv_rank b
      ON b.symbol = a.symbol
     AND b.date >= a.date - INTERVAL '365 days'
     AND b.date <= a.date
    WHERE a.current_iv IS NOT NULL
    GROUP BY a.symbol, a.date, a.current_iv
)
UPDATE market.iv_rank r
SET
    iv_low_52w    = w.iv_low,
    iv_high_52w   = w.iv_high,
    iv_rank_52w   = CASE
        WHEN w.iv_high > w.iv_low
            THEN 100.0 * (r.current_iv - w.iv_low) / (w.iv_high - w.iv_low)
        ELSE NULL
    END,
    iv_percentile = 100.0 * w.pct_below,
    iv_std_dev    = w.iv_std
FROM windowed w
WHERE r.symbol = w.symbol AND r.date = w.date;
"""


def main() -> int:
    conn = psycopg2.connect(**DB_CONFIG)
    with conn:
        with conn.cursor() as cur:
            cur.execute(COMPUTE_SQL)
            print(f"Updated {cur.rowcount} (symbol, date) rows.")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
