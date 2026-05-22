#!/usr/bin/env python3
"""Compute daily ATM current_iv per symbol from market.greeks and write it
into market.iv_rank.

Replaces the archived Polygon-based backfill_historical_iv.py. The greeks
table already holds per-contract IV from Alpaca; this script aggregates
ATM contracts (|delta| ~ 0.50) into a single per-symbol per-date IV that
compute_iv_rank.py can then turn into 52-week rank/percentile/stddev.

Usage:
    python scripts/ingest_alpaca_iv.py                 # latest date per symbol
    python scripts/ingest_alpaca_iv.py --all           # every date in greeks
    python scripts/ingest_alpaca_iv.py --symbol NVDA   # one symbol only
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent


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


load_env(".env.alpaca")
load_env(".env.db")

DB_CONFIG = {
    "host": os.environ.get("POSTGRES_HOST", "localhost"),
    "port": int(os.environ.get("POSTGRES_PORT", 5432)),
    "dbname": os.environ.get("POSTGRES_DB", "clawstreet"),
    "user": os.environ["POSTGRES_USER"],
    "password": os.environ["POSTGRES_PASSWORD"],
}

ATM_NARROW = (0.40, 0.60)
ATM_WIDE = (0.30, 0.70)
MIN_CONTRACTS = 3


def get_watchlist(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol FROM market.assets "
            "WHERE asset_type='stock' AND active = TRUE ORDER BY symbol"
        )
        return [r[0] for r in cur.fetchall()]


def get_dates_for_symbol(conn, symbol: str, all_dates: bool) -> list:
    with conn.cursor() as cur:
        if all_dates:
            cur.execute(
                """
                SELECT DISTINCT g.date
                FROM market.greeks g
                JOIN market.options o ON o.occ_symbol = g.occ_symbol
                WHERE o.underlying = %s AND g.iv IS NOT NULL
                ORDER BY g.date
                """,
                (symbol,),
            )
        else:
            cur.execute(
                """
                SELECT MAX(g.date)
                FROM market.greeks g
                JOIN market.options o ON o.occ_symbol = g.occ_symbol
                WHERE o.underlying = %s AND g.iv IS NOT NULL
                """,
                (symbol,),
            )
        return [r[0] for r in cur.fetchall() if r[0] is not None]


def fetch_atm_contracts(conn, symbol: str, d, lo: float, hi: float) -> list[tuple]:
    """Return (iv, volume) for contracts with |delta| in [lo, hi] on date d."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT g.iv, COALESCE(g.volume, 0)
            FROM market.greeks g
            JOIN market.options o ON o.occ_symbol = g.occ_symbol
            WHERE o.underlying = %s
              AND g.date = %s
              AND g.iv IS NOT NULL
              AND g.delta IS NOT NULL
              AND ABS(g.delta) BETWEEN %s AND %s
            """,
            (symbol, d, lo, hi),
        )
        return [(float(r[0]), int(r[1])) for r in cur.fetchall()]


def compute_atm_iv(rows: list[tuple]) -> float | None:
    """Volume-weighted average IV. Falls back to simple mean if all volumes are 0."""
    if not rows:
        return None
    total_vol = sum(v for _, v in rows)
    if total_vol > 0:
        return sum(iv * v for iv, v in rows) / total_vol
    return sum(iv for iv, _ in rows) / len(rows)


def upsert_iv(conn, symbol: str, d, current_iv: float) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO market.iv_rank (symbol, date, current_iv)
            VALUES (%s, %s, %s)
            ON CONFLICT (symbol, date) DO UPDATE
                SET current_iv = EXCLUDED.current_iv
            """,
            (symbol, d, current_iv),
        )


def process_symbol(conn, symbol: str, all_dates: bool) -> int:
    dates = get_dates_for_symbol(conn, symbol, all_dates)
    if not dates:
        print(f"[{symbol}] no greeks data with iv — skipping")
        return 0

    written = 0
    for d in dates:
        rows = fetch_atm_contracts(conn, symbol, d, *ATM_NARROW)
        band = "narrow"
        if len(rows) < MIN_CONTRACTS:
            rows = fetch_atm_contracts(conn, symbol, d, *ATM_WIDE)
            band = "wide"

        if len(rows) < MIN_CONTRACTS:
            print(f"[{symbol}] {d} only {len(rows)} ATM contracts (wide), skipping")
            continue

        atm_iv = compute_atm_iv(rows)
        if atm_iv is None:
            continue

        upsert_iv(conn, symbol, d, atm_iv)
        written += 1

        if not all_dates or len(dates) == 1:
            print(
                f"[{symbol}] {d}  contracts={len(rows)} ({band})  "
                f"atm_iv={atm_iv:.4f}"
            )

    if all_dates and written:
        print(f"[{symbol}] wrote {written} iv_rank rows across {len(dates)} dates")
    conn.commit()
    return written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", help="Process only this symbol")
    ap.add_argument(
        "--all",
        action="store_true",
        help="Process every date in market.greeks (backfill). Default: latest date.",
    )
    args = ap.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        if args.symbol:
            symbols = [args.symbol.upper()]
        else:
            symbols = get_watchlist(conn)

        total = 0
        for sym in symbols:
            total += process_symbol(conn, sym, args.all)

        print(f"\nDone. Wrote/updated {total} iv_rank rows.")
        print("Next: run compute_iv_rank.py to refresh 52w rank/percentile/stddev.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
