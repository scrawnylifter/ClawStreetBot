#!/usr/bin/env python3
"""Backfill historical 30-day ATM implied volatility for the watchlist.

Polygon's snapshot endpoint is current-day-only and historical aggregates
don't include greeks, so we compute IV ourselves by inverting Black-Scholes
on the closing price of near-ATM options each historical trading day.

Procedure (per symbol):
  1. Pull the contract universe (active + expired) for expirations in the
     last 365+45 days via /v3/reference/options/contracts.
  2. For each trading day D in market.ohlcv (last N days):
       - Pick the expiration closest to D+30 days
       - Pick the ATM call + ATM put (strike closest to close on D)
       - Fetch their daily aggregates, find the bar for D
       - Invert BS on each close → IV_call, IV_put
       - Daily symbol IV = mean(IV_call, IV_put)
  3. UPSERT into market.iv_rank.current_iv (the 52w rank columns are left
     NULL — compute_iv_rank.py fills them next).

Option aggregates are cached per contract (one Polygon call per ATM
contract serves ~21 trading days at the same expiration).

Usage:
    python scripts/backfill_historical_iv.py [--symbol NVDA] [--days 252]
                                             [--rate 0.05]
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


load_env(".env.polygon")
load_env(".env.db")

# py_vollib transitively imports py_lets_be_rational which needs _testcapi
# (a CPython test-only module not shipped to users). Inject a stub with the
# two constants it pulls from there before py_vollib is imported.
import sys as _sys
import types as _types
_stub = _types.ModuleType("_testcapi")
_stub.DBL_MIN = _sys.float_info.min
_stub.DBL_MAX = _sys.float_info.max
_sys.modules.setdefault("_testcapi", _stub)

import warnings  # noqa: E402
warnings.filterwarnings("ignore", category=DeprecationWarning)

from py_vollib.black_scholes.implied_volatility import implied_volatility  # noqa: E402
from py_lets_be_rational.exceptions import (  # noqa: E402
    BelowIntrinsicException, AboveMaximumException,
)
from polygon import RESTClient  # noqa: E402

POLYGON_API_KEY = os.environ["POLYGON_API_KEY"]

DB_CONFIG = {
    "host": os.environ.get("POSTGRES_HOST", "localhost"),
    "port": int(os.environ.get("POSTGRES_PORT", 5432)),
    "dbname": os.environ["POSTGRES_DB"],
    "user": os.environ["POSTGRES_USER"],
    "password": os.environ["POSTGRES_PASSWORD"],
}


def get_watchlist(conn) -> list[tuple[int, str]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, symbol FROM market.assets "
            "WHERE asset_type='stock' AND active = TRUE ORDER BY symbol"
        )
        return cur.fetchall()


def get_daily_closes(conn, asset_id: int, days: int) -> list[tuple[date, float]]:
    """Return [(date, close), ...] ascending for the last N days, from market.ohlcv 1d bars."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT (timestamp AT TIME ZONE 'UTC')::date AS d, close::float
            FROM market.ohlcv
            WHERE asset_id=%s AND timeframe='1d'
              AND timestamp >= NOW() - (%s || ' days')::interval
            ORDER BY timestamp ASC
            """,
            (asset_id, days),
        )
        return cur.fetchall()


def fetch_contracts(client: RESTClient, symbol: str, exp_min: date, exp_max: date) -> list[dict]:
    """Pull active+expired contracts for the underlying within an expiration window.

    Polygon's list_options_contracts requires expired=True to include expired
    contracts; we have to call twice and merge.
    """
    contracts: dict[str, dict] = {}
    for expired in (False, True):
        for c in client.list_options_contracts(
            underlying_ticker=symbol,
            expired=expired,
            limit=1000,
            params={
                "expiration_date.gte": exp_min.isoformat(),
                "expiration_date.lte": exp_max.isoformat(),
            },
        ):
            try:
                exp_d = datetime.strptime(c.expiration_date, "%Y-%m-%d").date()
            except (TypeError, ValueError):
                continue
            contracts[c.ticker] = {
                "ticker": c.ticker,
                "underlying": symbol,
                "type": "C" if c.contract_type == "call" else "P",
                "strike": float(c.strike_price),
                "expiration": exp_d,
            }
    return list(contracts.values())


def select_atm_pair(contracts: list[dict], d: date, close: float,
                    target_dte: int = 30, dte_tolerance: int = 30,
                    ) -> tuple[dict | None, dict | None]:
    """Pick the ATM call & put for date d: same expiration nearest d+target_dte,
    strike nearest to close."""
    candidates = [c for c in contracts if (c["expiration"] - d).days >= 7]
    if not candidates:
        return None, None
    # Group by expiration; pick exp closest to target
    exps = {c["expiration"] for c in candidates}
    target_exp = min(exps, key=lambda e: abs((e - d).days - target_dte))
    if abs((target_exp - d).days - target_dte) > dte_tolerance:
        return None, None
    same_exp = [c for c in candidates if c["expiration"] == target_exp]
    calls = [c for c in same_exp if c["type"] == "C"]
    puts = [c for c in same_exp if c["type"] == "P"]
    atm_call = min(calls, key=lambda c: abs(c["strike"] - close)) if calls else None
    atm_put = min(puts, key=lambda c: abs(c["strike"] - close)) if puts else None
    return atm_call, atm_put


def get_contract_closes(client: RESTClient, occ: str, frm: date, to: date,
                        cache: dict[str, dict[date, float]]) -> dict[date, float]:
    """Return {date: close} for a contract, cached across calls."""
    if occ in cache:
        return cache[occ]
    closes: dict[date, float] = {}
    try:
        for bar in client.list_aggs(
            ticker=occ,
            multiplier=1,
            timespan="day",
            from_=frm.isoformat(),
            to=to.isoformat(),
            adjusted=True,
            sort="asc",
            limit=50000,
        ):
            d = datetime.fromtimestamp(bar.timestamp / 1000.0, tz=timezone.utc).date()
            if bar.close is not None:
                closes[d] = float(bar.close)
    except Exception:
        pass
    cache[occ] = closes
    return closes


def safe_iv(price: float, S: float, K: float, t: float, r: float, flag: str) -> float | None:
    if price is None or price <= 0 or t <= 0:
        return None
    # Below intrinsic / above max → can't be inverted to a real IV; skip
    intrinsic = max(0.0, (S - K) if flag == "c" else (K - S))
    if price < intrinsic - 1e-6:
        return None
    try:
        iv = implied_volatility(price=price, S=S, K=K, t=t, r=r, flag=flag)
        if iv is None or iv != iv or iv <= 0 or iv > 5.0:  # 500% IV cap = junk
            return None
        return float(iv)
    except (BelowIntrinsicException, AboveMaximumException, ValueError, ZeroDivisionError):
        return None


def upsert_iv(conn, rows: list[tuple]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO market.iv_rank (symbol, date, current_iv)
            VALUES %s
            ON CONFLICT (symbol, date) DO UPDATE SET
                current_iv = EXCLUDED.current_iv
            """,
            rows,
            page_size=1000,
        )


def upsert_ingest_state(conn, symbol: str, count: int, status: str = "ok",
                        err: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO market.ingest_state
                (source, symbol, timeframe, last_timestamp, last_run_at,
                 row_count, status, error_message)
            VALUES ('polygon_iv_backfill', %s, NULL, NOW(), NOW(), %s, %s, %s)
            ON CONFLICT (source, symbol, timeframe) DO UPDATE SET
                last_timestamp = NOW(),
                last_run_at    = NOW(),
                row_count      = EXCLUDED.row_count,
                status         = EXCLUDED.status,
                error_message  = EXCLUDED.error_message
            """,
            (symbol, count, status, err),
        )


def backfill_symbol(client: RESTClient, conn, asset_id: int, symbol: str,
                    days: int, rate: float) -> int:
    today = date.today()
    exp_min = today - timedelta(days=days + 45)
    exp_max = today + timedelta(days=45)

    contracts = fetch_contracts(client, symbol, exp_min, exp_max)
    if not contracts:
        print(f"  {symbol:<6} no contracts in window")
        return 0

    closes = get_daily_closes(conn, asset_id, days)
    if not closes:
        print(f"  {symbol:<6} no underlying OHLCV — run ingest_polygon_ohlcv first")
        return 0

    cache: dict[str, dict[date, float]] = {}
    rows: list[tuple] = []
    skipped_no_pair = 0
    skipped_no_bar = 0
    skipped_iv = 0

    for d, ul_close in closes:
        atm_call, atm_put = select_atm_pair(contracts, d, ul_close)
        if not atm_call or not atm_put:
            skipped_no_pair += 1
            continue

        # Aggregate range = entire contract life within our window
        a_frm = max(d - timedelta(days=1), atm_call["expiration"] - timedelta(days=120))
        a_to = atm_call["expiration"]
        call_closes = get_contract_closes(client, atm_call["ticker"], a_frm, a_to, cache)
        put_closes = get_contract_closes(client, atm_put["ticker"],
                                         max(d - timedelta(days=1),
                                             atm_put["expiration"] - timedelta(days=120)),
                                         atm_put["expiration"], cache)

        cp = call_closes.get(d)
        pp = put_closes.get(d)
        if cp is None or pp is None:
            skipped_no_bar += 1
            continue

        t_call = (atm_call["expiration"] - d).days / 365.0
        t_put = (atm_put["expiration"] - d).days / 365.0

        iv_c = safe_iv(cp, ul_close, atm_call["strike"], t_call, rate, "c")
        iv_p = safe_iv(pp, ul_close, atm_put["strike"], t_put, rate, "p")

        ivs = [v for v in (iv_c, iv_p) if v is not None]
        if not ivs:
            skipped_iv += 1
            continue
        daily_iv = sum(ivs) / len(ivs)
        rows.append((symbol, d, daily_iv))

    upsert_iv(conn, rows)
    print(f"  {symbol:<6} {len(rows):>3} daily IV  "
          f"(skipped: pair={skipped_no_pair} bar={skipped_no_bar} iv={skipped_iv}, "
          f"agg-calls={len(cache)})")
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol")
    parser.add_argument("--days", type=int, default=252,
                        help="Trading-day lookback window (default 252 = 52 weeks)")
    parser.add_argument("--rate", type=float, default=0.05,
                        help="Risk-free rate for BS inversion (default 0.05)")
    args = parser.parse_args()

    client = RESTClient(api_key=POLYGON_API_KEY)
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        watchlist = get_watchlist(conn)
        if args.symbol:
            watchlist = [(aid, s) for aid, s in watchlist if s == args.symbol]
            if not watchlist:
                print(f"Symbol {args.symbol} not in market.assets")
                return 1

        print(f"Backfill window: {args.days} trading days, r={args.rate}")
        print(f"Watchlist: {len(watchlist)} → {', '.join(s for _, s in watchlist)}\n")

        total = 0
        for asset_id, symbol in watchlist:
            t0 = time.time()
            try:
                n = backfill_symbol(client, conn, asset_id, symbol, args.days, args.rate)
                upsert_ingest_state(conn, symbol, n)
                conn.commit()
                total += n
                print(f"         {time.time()-t0:.1f}s elapsed")
            except Exception as e:
                conn.rollback()
                upsert_ingest_state(conn, symbol, 0, status="error", err=str(e)[:500])
                conn.commit()
                print(f"  {symbol:<6} ERROR: {e}")

        print(f"\nTotal daily IV rows written: {total}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
