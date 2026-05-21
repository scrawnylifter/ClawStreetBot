#!/usr/bin/env python3
"""Ingest quarterly fundamentals from Polygon.io.

Pulls per-period financials (revenue, net income, EPS, balance-sheet,
cash-flow) via Polygon's reference-financials endpoint and ticker-level
metadata (market cap, shares outstanding) via the ticker-details
endpoint, then upserts the merged snapshot into ``market.fundamentals``
keyed by ``(symbol, period_end_date, fiscal_period)``.

The table is intentionally narrower than what Polygon exposes — extra
fields the schema does not store (``eps_basic``, ``sector``, ``industry``)
are dropped on the floor. Re-running on the same fiscal period overwrites
the row (idempotent UPSERT).

Usage:
    python scripts/ingest_polygon_fundamentals.py                # full watchlist
    python scripts/ingest_polygon_fundamentals.py --symbol NVDA
    python scripts/ingest_polygon_fundamentals.py --limit 8      # periods per symbol
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import requests
from psycopg2.extras import execute_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env(filename: str) -> None:
    """Populate ``os.environ`` from a ``KEY=VALUE`` dotenv file."""
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

POLYGON_API_KEY = os.environ["POLYGON_API_KEY"]
POLYGON_BASE = "https://api.polygon.io"

DB_CONFIG = {
    "host": os.environ.get("POSTGRES_HOST", "localhost"),
    "port": int(os.environ.get("POSTGRES_PORT", 5432)),
    "dbname": os.environ["POSTGRES_DB"],
    "user": os.environ["POSTGRES_USER"],
    "password": os.environ["POSTGRES_PASSWORD"],
}

INGEST_SOURCE = "polygon_fundamentals"
RATE_LIMIT_SLEEP = 0.25  # 5 req/sec ceiling on Stocks plan


def get_watchlist(conn) -> list[str]:
    """Return active stock symbols from the watchlist."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol FROM market.assets "
            "WHERE asset_type='stock' AND active = TRUE ORDER BY symbol"
        )
        return [r[0] for r in cur.fetchall()]


def fnum(x: Any) -> float | None:
    """Coerce to float, returning ``None`` for ``None`` / NaN / non-numeric."""
    if x is None:
        return None
    try:
        v = float(x)
        if v != v:  # NaN
            return None
        return v
    except (TypeError, ValueError):
        return None


def _vget(node: dict | None, key: str) -> float | None:
    """Fetch ``node[key]['value']`` if present, else ``None``."""
    if not node:
        return None
    item = node.get(key)
    if not isinstance(item, dict):
        return None
    return fnum(item.get("value"))


def fetch_ticker_details(symbol: str) -> dict[str, Any]:
    """Fetch ticker-level metadata (market cap, shares, sector, industry)."""
    url = f"{POLYGON_BASE}/v3/reference/tickers/{symbol}"
    resp = requests.get(url, params={"apiKey": POLYGON_API_KEY}, timeout=30)
    if resp.status_code == 404:
        return {}
    resp.raise_for_status()
    return resp.json().get("results", {}) or {}


def fetch_financials(symbol: str, limit: int) -> list[dict[str, Any]]:
    """Fetch quarterly financials for a ticker.

    Args:
        symbol: Underlying ticker.
        limit: Maximum number of fiscal periods to pull (most recent first).

    Returns:
        List of period dicts as returned by Polygon's ``vX/reference/financials``.
    """
    url = f"{POLYGON_BASE}/vX/reference/financials"
    params = {
        "ticker": symbol,
        "timeframe": "quarterly",
        "limit": limit,
        "order": "desc",
        "sort": "period_of_report_date",
        "apiKey": POLYGON_API_KEY,
    }
    resp = requests.get(url, params=params, timeout=30)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    return resp.json().get("results", []) or []


def build_row(symbol: str, period: dict[str, Any],
              details: dict[str, Any]) -> tuple | None:
    """Flatten a Polygon financials period into a ``market.fundamentals`` row.

    Returns ``None`` if the period lacks an end date (cannot satisfy the
    UNIQUE constraint).
    """
    end_date = period.get("end_date") or period.get("period_of_report_date")
    if not end_date:
        return None
    try:
        period_end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None

    fiscal_period = period.get("fiscal_period")  # 'Q1'..'Q4','FY','TTM'
    fiscal_year_raw = period.get("fiscal_year")
    try:
        fiscal_year = int(fiscal_year_raw) if fiscal_year_raw is not None else None
    except (TypeError, ValueError):
        fiscal_year = None

    fin = period.get("financials") or {}
    inc = fin.get("income_statement") or {}
    bal = fin.get("balance_sheet") or {}
    cf = fin.get("cash_flow_statement") or {}

    revenue = _vget(inc, "revenues")
    net_income = _vget(inc, "net_income_loss")
    eps = _vget(inc, "diluted_earnings_per_share")
    if eps is None:
        eps = _vget(inc, "basic_earnings_per_share")
    gross_profit = _vget(inc, "gross_profit")
    operating_income = _vget(inc, "operating_income_loss")
    total_assets = _vget(bal, "assets")
    total_liabilities = _vget(bal, "liabilities")
    equity = _vget(bal, "equity")
    free_cash_flow = _vget(cf, "net_cash_flow_from_operating_activities")

    debt_to_equity = None
    if total_liabilities is not None and equity not in (None, 0):
        debt_to_equity = total_liabilities / equity

    market_cap = fnum(details.get("market_cap"))
    shares_outstanding = (
        fnum(details.get("weighted_shares_outstanding"))
        or fnum(details.get("share_class_shares_outstanding"))
    )
    # Polygon ticker-details does not include trailing PE or dividend yield;
    # leave NULL when unavailable.
    pe_ratio = None
    dividend_yield = None

    return (
        symbol, period_end, fiscal_period, fiscal_year,
        revenue, net_income, eps, pe_ratio, market_cap,
        debt_to_equity, free_cash_flow, dividend_yield,
        gross_profit, operating_income, total_assets,
        total_liabilities, shares_outstanding,
    )


def upsert_fundamentals(conn, rows: list[tuple]) -> None:
    """Idempotently upsert fundamentals rows."""
    if not rows:
        return
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO market.fundamentals
                (symbol, date, period, fiscal_year, revenue, net_income, eps,
                 pe_ratio, market_cap, debt_to_equity, free_cash_flow,
                 dividend_yield, gross_profit, operating_income, total_assets,
                 total_liabilities, shares_outstanding)
            VALUES %s
            ON CONFLICT (symbol, date, period) DO UPDATE SET
                fiscal_year       = EXCLUDED.fiscal_year,
                revenue           = EXCLUDED.revenue,
                net_income        = EXCLUDED.net_income,
                eps               = EXCLUDED.eps,
                pe_ratio          = EXCLUDED.pe_ratio,
                market_cap        = EXCLUDED.market_cap,
                debt_to_equity    = EXCLUDED.debt_to_equity,
                free_cash_flow    = EXCLUDED.free_cash_flow,
                dividend_yield    = EXCLUDED.dividend_yield,
                gross_profit      = EXCLUDED.gross_profit,
                operating_income  = EXCLUDED.operating_income,
                total_assets      = EXCLUDED.total_assets,
                total_liabilities = EXCLUDED.total_liabilities,
                shares_outstanding= EXCLUDED.shares_outstanding
            """,
            rows,
            page_size=500,
        )


def upsert_ingest_state(conn, symbol: str, count: int,
                        status: str = "ok", err: str | None = None) -> None:
    """Record per-symbol ingestion result in ``market.ingest_state``."""
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


def ingest_symbol(conn, symbol: str, limit: int) -> int:
    """Pull + upsert fundamentals for a single symbol. Returns row count."""
    details = fetch_ticker_details(symbol)
    periods = fetch_financials(symbol, limit)
    rows = [r for r in (build_row(symbol, p, details) for p in periods) if r]
    upsert_fundamentals(conn, rows)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", help="Restrict to a single underlying")
    parser.add_argument("--limit", type=int, default=8,
                        help="Periods per symbol (default 8 = 2 years quarterly)")
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        symbols = get_watchlist(conn)
        if args.symbol:
            if args.symbol not in symbols:
                print(f"Symbol {args.symbol} not in market.assets")
                return 1
            symbols = [args.symbol]

        print(f"Run date: {date.today()}  Periods/symbol: {args.limit}")
        print(f"Watchlist: {len(symbols)} → {', '.join(symbols)}\n")

        total = 0
        for sym in symbols:
            try:
                count = ingest_symbol(conn, sym, args.limit)
                upsert_ingest_state(conn, sym, count)
                conn.commit()
                total += count
                print(f"  {sym:<6} +{count:>3} periods")
            except Exception as e:
                conn.rollback()
                upsert_ingest_state(conn, sym, 0, status="error", err=str(e)[:500])
                conn.commit()
                print(f"  {sym:<6} ERROR: {e}")
            time.sleep(RATE_LIMIT_SLEEP)

        print(f"\nTotal periods touched: {total}")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
