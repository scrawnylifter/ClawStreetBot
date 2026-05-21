#!/usr/bin/env python3
"""Ingest options chain snapshots from Polygon.io.

Pulls the live options chain for each watchlist symbol (via
`/v3/snapshot/options/{underlying}`), upserts contract metadata into
`market.options`, and writes a daily greeks/quote snapshot row into
`market.greeks` keyed by (occ_symbol, date).

Re-running on the same trading day overwrites that day's snapshot row
(UPSERT). Running on subsequent days appends a new row per contract.

Usage:
    python scripts/ingest_polygon_options.py                # full watchlist
    python scripts/ingest_polygon_options.py --symbol NVDA
    python scripts/ingest_polygon_options.py --max-dte 120  # default
    python scripts/ingest_polygon_options.py --min-dte 30   # respect Law 5
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

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


load_env(".env.polygon")
load_env(".env.db")

from polygon import RESTClient  # noqa: E402

POLYGON_API_KEY = os.environ["POLYGON_API_KEY"]

DB_CONFIG = {
    "host": os.environ.get("POSTGRES_HOST", "localhost"),
    "port": int(os.environ.get("POSTGRES_PORT", 5432)),
    "dbname": os.environ["POSTGRES_DB"],
    "user": os.environ["POSTGRES_USER"],
    "password": os.environ["POSTGRES_PASSWORD"],
}

INGEST_SOURCE = "polygon_options"


def get_watchlist(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol FROM market.assets "
            "WHERE asset_type='stock' AND active = TRUE ORDER BY symbol"
        )
        return [r[0] for r in cur.fetchall()]


def fnum(x) -> float | None:
    """Coerce to float, returning None for None/NaN."""
    if x is None:
        return None
    try:
        v = float(x)
        if v != v:  # NaN
            return None
        return v
    except (TypeError, ValueError):
        return None


def upsert_contracts(conn, rows: list[tuple]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO market.options
                (occ_symbol, underlying, contract_type, strike, expiration,
                 exercise_style, shares_per_contract)
            VALUES %s
            ON CONFLICT (occ_symbol) DO UPDATE SET
                underlying      = EXCLUDED.underlying,
                contract_type   = EXCLUDED.contract_type,
                strike          = EXCLUDED.strike,
                expiration      = EXCLUDED.expiration,
                exercise_style  = EXCLUDED.exercise_style,
                shares_per_contract = EXCLUDED.shares_per_contract,
                updated_at      = NOW()
            """,
            rows,
            page_size=1000,
        )


def upsert_greeks(conn, rows: list[tuple]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO market.greeks
                (occ_symbol, date, delta, gamma, theta, vega, iv,
                 open_interest, bid, ask, midpoint, last_price, volume, vwap,
                 break_even, underlying_price)
            VALUES %s
            ON CONFLICT (occ_symbol, date) DO UPDATE SET
                delta=EXCLUDED.delta, gamma=EXCLUDED.gamma, theta=EXCLUDED.theta,
                vega=EXCLUDED.vega, iv=EXCLUDED.iv,
                open_interest=EXCLUDED.open_interest,
                bid=EXCLUDED.bid, ask=EXCLUDED.ask, midpoint=EXCLUDED.midpoint,
                last_price=EXCLUDED.last_price, volume=EXCLUDED.volume,
                vwap=EXCLUDED.vwap, break_even=EXCLUDED.break_even,
                underlying_price=EXCLUDED.underlying_price
            """,
            rows,
            page_size=1000,
        )


def upsert_ingest_state(conn, symbol: str, count: int,
                        status: str = "ok", err: str | None = None) -> None:
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO market.ingest_state
                (source, symbol, timeframe, last_timestamp, last_run_at,
                 row_count, status, error_message)
            VALUES (%s,%s,NULL,%s,NOW(),%s,%s,%s)
            ON CONFLICT (source, symbol, timeframe) DO UPDATE SET
                last_timestamp = EXCLUDED.last_timestamp,
                last_run_at    = NOW(),
                row_count      = market.ingest_state.row_count + EXCLUDED.row_count,
                status         = EXCLUDED.status,
                error_message  = EXCLUDED.error_message
            """,
            (INGEST_SOURCE, symbol, now, count, status, err),
        )


def ingest_symbol(client: RESTClient, conn, symbol: str,
                  min_dte: int, max_dte: int, snapshot_date: date) -> int:
    today = date.today()
    exp_min = (today + timedelta(days=max(min_dte, 0))).isoformat()
    exp_max = (today + timedelta(days=max_dte)).isoformat()

    contracts: list[tuple] = []
    greeks: list[tuple] = []
    seen_occ: set[str] = set()

    chain = client.list_snapshot_options_chain(
        underlying_asset=symbol,
        params={
            "expiration_date.gte": exp_min,
            "expiration_date.lte": exp_max,
            "limit": 250,
        },
    )

    for snap in chain:
        d = snap.details
        if d is None or d.ticker is None:
            continue
        occ = d.ticker
        if occ in seen_occ:
            continue
        seen_occ.add(occ)

        ctype = "C" if (d.contract_type or "").lower().startswith("c") else "P"
        try:
            exp = datetime.strptime(d.expiration_date, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue

        contracts.append((
            occ, symbol, ctype, fnum(d.strike_price), exp,
            d.exercise_style or "american",
            d.shares_per_contract or 100,
        ))

        g = snap.greeks
        day = snap.day
        q = snap.last_quote
        t = snap.last_trade
        ul = snap.underlying_asset

        bid = fnum(q.bid) if q else None
        ask = fnum(q.ask) if q else None
        mid = (bid + ask) / 2.0 if bid is not None and ask is not None else None
        last_price = fnum(t.price) if t else (fnum(day.close) if day else None)

        greeks.append((
            occ, snapshot_date,
            fnum(g.delta) if g else None,
            fnum(g.gamma) if g else None,
            fnum(g.theta) if g else None,
            fnum(g.vega) if g else None,
            fnum(snap.implied_volatility),
            int(snap.open_interest) if snap.open_interest is not None else None,
            bid, ask, mid, last_price,
            int(day.volume) if day and day.volume is not None else None,
            fnum(day.vwap) if day else None,
            fnum(snap.break_even_price),
            fnum(ul.price) if ul else None,
        ))

    upsert_contracts(conn, contracts)
    upsert_greeks(conn, greeks)
    return len(contracts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", help="Restrict to a single underlying")
    parser.add_argument("--min-dte", type=int, default=0,
                        help="Minimum days to expiration (default 0 = include front-week)")
    parser.add_argument("--max-dte", type=int, default=120,
                        help="Maximum days to expiration (default 120)")
    args = parser.parse_args()

    client = RESTClient(api_key=POLYGON_API_KEY)
    conn = psycopg2.connect(**DB_CONFIG)
    snapshot_date = date.today()

    try:
        symbols = get_watchlist(conn)
        if args.symbol:
            if args.symbol not in symbols:
                print(f"Symbol {args.symbol} not in market.assets")
                return 1
            symbols = [args.symbol]

        print(f"Snapshot date: {snapshot_date}  DTE window: {args.min_dte}–{args.max_dte}")
        print(f"Watchlist: {len(symbols)} → {', '.join(symbols)}\n")

        total = 0
        for sym in symbols:
            try:
                count = ingest_symbol(client, conn, sym, args.min_dte,
                                      args.max_dte, snapshot_date)
                upsert_ingest_state(conn, sym, count)
                conn.commit()
                total += count
                print(f"  {sym:<6} +{count:>5} contracts")
            except Exception as e:
                conn.rollback()
                upsert_ingest_state(conn, sym, 0, status="error", err=str(e)[:500])
                conn.commit()
                print(f"  {sym:<6} ERROR: {e}")
            time.sleep(0.05)

        print(f"\nTotal contracts touched: {total}")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
