#!/usr/bin/env python3
"""Ingest options chain snapshots from Alpaca.

Replacement for ingest_polygon_options.py. For each active watchlist symbol,
pulls the live option chain via Alpaca's OptionHistoricalDataClient, upserts
contract metadata into market.options, and writes a per-contract greeks +
bid/ask snapshot row into market.greeks keyed by (occ_symbol, date).

Re-running on the same trading day overwrites that day's snapshot row.

Usage:
    python scripts/ingest_alpaca_options.py --all
    python scripts/ingest_alpaca_options.py --symbol NVDA
    python scripts/ingest_alpaca_options.py --symbol NVDA --min-dte 30 --max-dte 120
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
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

from alpaca.data.historical.option import OptionHistoricalDataClient  # noqa: E402
from alpaca.data.requests import OptionChainRequest  # noqa: E402

ALPACA_API_KEY = os.environ["ALPACA_PAPER_API_KEY"]
ALPACA_SECRET_KEY = os.environ["ALPACA_PAPER_SECRET_KEY"]

DB_CONFIG = {
    "host": os.environ.get("POSTGRES_HOST", "localhost"),
    "port": int(os.environ.get("POSTGRES_PORT", 5432)),
    "dbname": os.environ["POSTGRES_DB"],
    "user": os.environ["POSTGRES_USER"],
    "password": os.environ["POSTGRES_PASSWORD"],
}

INGEST_SOURCE = "alpaca_options"


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


def parse_occ(occ: str) -> tuple[str, str, date, float] | None:
    """Parse OCC symbol → (root, type 'C'/'P', expiration, strike).

    Alpaca uses the raw OCC format (no 'O:' prefix), e.g. NVDA260619C00125000.
    Layout: <root><YYMMDD><C|P><strike*1000 zero-padded to 8 digits>.
    """
    if not occ or len(occ) < 16:
        return None
    body = occ[-15:]  # YYMMDD + C/P + 8-digit strike
    root = occ[:-15]
    if not root:
        return None
    try:
        yy = int(body[0:2])
        mm = int(body[2:4])
        dd = int(body[4:6])
        ctype = body[6]
        strike = int(body[7:15]) / 1000.0
    except (ValueError, IndexError):
        return None
    if ctype not in ("C", "P"):
        return None
    year = 2000 + yy
    try:
        exp = date(year, mm, dd)
    except ValueError:
        return None
    return root, ctype, exp, strike


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
                underlying          = EXCLUDED.underlying,
                contract_type       = EXCLUDED.contract_type,
                strike              = EXCLUDED.strike,
                expiration          = EXCLUDED.expiration,
                exercise_style      = EXCLUDED.exercise_style,
                shares_per_contract = EXCLUDED.shares_per_contract,
                updated_at          = NOW()
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
                (occ_symbol, date, delta, gamma, theta, vega, rho,
                 iv, bid, ask, midpoint, last_price)
            VALUES %s
            ON CONFLICT (occ_symbol, date) DO UPDATE SET
                delta              = EXCLUDED.delta,
                gamma              = EXCLUDED.gamma,
                theta              = EXCLUDED.theta,
                vega               = EXCLUDED.vega,
                rho                = EXCLUDED.rho,
                iv                 = EXCLUDED.iv,
                bid                = EXCLUDED.bid,
                ask                = EXCLUDED.ask,
                midpoint           = EXCLUDED.midpoint,
                last_price         = EXCLUDED.last_price
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


def ingest_symbol(client: OptionHistoricalDataClient, conn, symbol: str,
                  min_dte: int, max_dte: int, snapshot_date: date) -> int:
    today = date.today()
    exp_min = today + timedelta(days=max(min_dte, 0))
    exp_max = today + timedelta(days=max_dte)

    req = OptionChainRequest(
        underlying_symbol=symbol,
        expiration_date_gte=exp_min,
        expiration_date_lte=exp_max,
    )
    chain = client.get_option_chain(req)

    contracts: list[tuple] = []
    greeks: list[tuple] = []

    # chain is a dict[occ_symbol, OptionsSnapshot]
    for occ, snap in chain.items():
        parsed = parse_occ(occ)
        if parsed is None:
            continue
        root, ctype, exp, strike = parsed

        contracts.append((
            occ, symbol, ctype, strike, exp, "american", 100,
        ))

        g = getattr(snap, "greeks", None)
        q = getattr(snap, "latest_quote", None)
        t = getattr(snap, "latest_trade", None)
        iv = fnum(getattr(snap, "implied_volatility", None))

        bid = fnum(getattr(q, "bid_price", None)) if q else None
        ask = fnum(getattr(q, "ask_price", None)) if q else None
        mid = (bid + ask) / 2.0 if bid is not None and ask is not None else None
        last_price = fnum(getattr(t, "price", None)) if t else None

        greeks.append((
            occ, snapshot_date,
            fnum(getattr(g, "delta", None)) if g else None,
            fnum(getattr(g, "gamma", None)) if g else None,
            fnum(getattr(g, "theta", None)) if g else None,
            fnum(getattr(g, "vega", None)) if g else None,
            fnum(getattr(g, "rho", None)) if g else None,
            iv,
            bid, ask, mid, last_price,
        ))

    upsert_contracts(conn, contracts)
    upsert_greeks(conn, greeks)
    return len(contracts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", help="Restrict to a single underlying")
    parser.add_argument("--all", action="store_true",
                        help="Process every active watchlist symbol")
    parser.add_argument("--min-dte", type=int, default=0,
                        help="Minimum days to expiration (default 0)")
    parser.add_argument("--max-dte", type=int, default=120,
                        help="Maximum days to expiration (default 120)")
    args = parser.parse_args()

    if not args.symbol and not args.all:
        parser.error("must pass --symbol SYM or --all")

    client = OptionHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    conn = psycopg2.connect(**DB_CONFIG)
    snapshot_date = datetime.now(timezone.utc).date()

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
