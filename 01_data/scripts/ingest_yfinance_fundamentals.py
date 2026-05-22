#!/usr/bin/env python3
"""Ingest fundamental data from yfinance into market.fundamentals.

Replacement for the archived ingest_polygon_fundamentals.py. Pulls a current
snapshot (market_cap, P/E, debt/equity, dividend yield, shares outstanding)
from Ticker.info and quarterly historical series (revenue, net_income, eps,
gross_profit, operating_income, free_cash_flow, total_assets,
total_liabilities) from Ticker.quarterly_financials and
Ticker.quarterly_balance_sheet. UPSERTs into market.fundamentals on
(symbol, date, period).

Usage:
    python scripts/ingest_yfinance_fundamentals.py --all
    python scripts/ingest_yfinance_fundamentals.py --symbol NVDA
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date
from decimal import Decimal
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

import yfinance as yf  # noqa: E402

DB_CONFIG = {
    "host": os.environ.get("POSTGRES_HOST", "localhost"),
    "port": int(os.environ.get("POSTGRES_PORT", 5432)),
    "dbname": os.environ.get("POSTGRES_DB", "clawstreet"),
    "user": os.environ["POSTGRES_USER"],
    "password": os.environ["POSTGRES_PASSWORD"],
}

# Map yfinance quarterly_financials row labels → our column names.
INCOME_LABELS = {
    "Total Revenue": "revenue",
    "Net Income": "net_income",
    "Gross Profit": "gross_profit",
    "Operating Income": "operating_income",
    "Diluted EPS": "eps",  # fallback to "Basic EPS" if missing
}

# Map yfinance quarterly_balance_sheet row labels → our columns.
BALANCE_LABELS = {
    "Total Assets": "total_assets",
    "Total Liabilities Net Minority Interest": "total_liabilities",
}

# Map yfinance quarterly_cashflow row labels → our columns.
CASHFLOW_LABELS = {
    "Free Cash Flow": "free_cash_flow",
}


def get_watchlist(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol FROM market.assets "
            "WHERE asset_type='stock' AND active = TRUE ORDER BY symbol"
        )
        return [r[0] for r in cur.fetchall()]


def _safe(v) -> Decimal | None:
    """Coerce a yfinance value into a Decimal or None."""
    if v is None:
        return None
    try:
        if hasattr(v, "item"):  # numpy scalar
            v = v.item()
        if v != v:  # NaN
            return None
        return Decimal(str(v))
    except (TypeError, ValueError, ArithmeticError):
        return None


def _row_value(df, label: str, col):
    """Return df.loc[label, col] or None if missing."""
    if df is None or df.empty:
        return None
    if label not in df.index:
        return None
    try:
        return df.at[label, col]
    except KeyError:
        return None


def period_label(period_end: date) -> str:
    """Map a fiscal period-end month to a calendar quarter label."""
    return f"Q{((period_end.month - 1) // 3) + 1}"


def collect_quarters(ticker: yf.Ticker, snapshot: dict) -> list[dict]:
    """Build one row per fiscal quarter from yfinance frames."""
    income = ticker.quarterly_financials
    balance = ticker.quarterly_balance_sheet
    cashflow = ticker.quarterly_cashflow

    period_cols = set()
    for df in (income, balance, cashflow):
        if df is not None and not df.empty:
            period_cols.update(df.columns)

    rows = []
    for col in sorted(period_cols, reverse=True):
        try:
            period_end = col.date() if hasattr(col, "date") else col
        except Exception:
            continue

        eps_val = _row_value(income, "Diluted EPS", col)
        if eps_val is None:
            eps_val = _row_value(income, "Basic EPS", col)

        row = {
            "date": period_end,
            "period": period_label(period_end),
            "fiscal_year": period_end.year,
            "revenue": _safe(_row_value(income, "Total Revenue", col)),
            "net_income": _safe(_row_value(income, "Net Income", col)),
            "eps": _safe(eps_val),
            "gross_profit": _safe(_row_value(income, "Gross Profit", col)),
            "operating_income": _safe(_row_value(income, "Operating Income", col)),
            "free_cash_flow": _safe(_row_value(cashflow, "Free Cash Flow", col)),
            "total_assets": _safe(_row_value(balance, "Total Assets", col)),
            "total_liabilities": _safe(
                _row_value(balance, "Total Liabilities Net Minority Interest", col)
            ),
            # Snapshot fields (same for every row in this run — yfinance only exposes current)
            "pe_ratio": snapshot["pe_ratio"],
            "market_cap": snapshot["market_cap"],
            "debt_to_equity": snapshot["debt_to_equity"],
            "dividend_yield": snapshot["dividend_yield"],
            "shares_outstanding": snapshot["shares_outstanding"],
        }
        rows.append(row)
    return rows


def fetch_snapshot(ticker: yf.Ticker) -> dict:
    info = ticker.info or {}
    # yfinance returns debt/equity as a percentage (e.g., 72.55 for 0.7255).
    # Normalize to a ratio so downstream code can compare against ratio thresholds.
    de = _safe(info.get("debtToEquity"))
    if de is not None and de > 1:
        de = de / Decimal(100)
    return {
        "market_cap": _safe(info.get("marketCap")),
        "pe_ratio": _safe(info.get("trailingPE")),
        "debt_to_equity": de,
        "dividend_yield": _safe(info.get("dividendYield")),
        "shares_outstanding": _safe(info.get("sharesOutstanding")),
        "ev_to_revenue": _safe(info.get("enterpriseToRevenue")),
        "ev_to_ebitda": _safe(info.get("enterpriseToEbitda")),
    }


def upsert_rows(conn, symbol: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    sql = """
        INSERT INTO market.fundamentals (
            symbol, date, period, fiscal_year,
            revenue, net_income, eps, pe_ratio, market_cap, debt_to_equity,
            free_cash_flow, dividend_yield, gross_profit, operating_income,
            total_assets, total_liabilities, shares_outstanding
        ) VALUES (
            %(symbol)s, %(date)s, %(period)s, %(fiscal_year)s,
            %(revenue)s, %(net_income)s, %(eps)s, %(pe_ratio)s, %(market_cap)s,
            %(debt_to_equity)s, %(free_cash_flow)s, %(dividend_yield)s,
            %(gross_profit)s, %(operating_income)s, %(total_assets)s,
            %(total_liabilities)s, %(shares_outstanding)s
        )
        ON CONFLICT (symbol, date, period) DO UPDATE SET
            fiscal_year        = EXCLUDED.fiscal_year,
            revenue            = COALESCE(EXCLUDED.revenue, market.fundamentals.revenue),
            net_income         = COALESCE(EXCLUDED.net_income, market.fundamentals.net_income),
            eps                = COALESCE(EXCLUDED.eps, market.fundamentals.eps),
            pe_ratio           = COALESCE(EXCLUDED.pe_ratio, market.fundamentals.pe_ratio),
            market_cap         = COALESCE(EXCLUDED.market_cap, market.fundamentals.market_cap),
            debt_to_equity     = COALESCE(EXCLUDED.debt_to_equity, market.fundamentals.debt_to_equity),
            free_cash_flow     = COALESCE(EXCLUDED.free_cash_flow, market.fundamentals.free_cash_flow),
            dividend_yield     = COALESCE(EXCLUDED.dividend_yield, market.fundamentals.dividend_yield),
            gross_profit       = COALESCE(EXCLUDED.gross_profit, market.fundamentals.gross_profit),
            operating_income   = COALESCE(EXCLUDED.operating_income, market.fundamentals.operating_income),
            total_assets       = COALESCE(EXCLUDED.total_assets, market.fundamentals.total_assets),
            total_liabilities  = COALESCE(EXCLUDED.total_liabilities, market.fundamentals.total_liabilities),
            shares_outstanding = COALESCE(EXCLUDED.shares_outstanding, market.fundamentals.shares_outstanding)
    """
    with conn.cursor() as cur:
        for r in rows:
            cur.execute(sql, {"symbol": symbol, **r})
    return len(rows)


def ingest_symbol(conn, symbol: str) -> int:
    ticker = yf.Ticker(symbol)
    snapshot = fetch_snapshot(ticker)
    rows = collect_quarters(ticker, snapshot)
    if not rows:
        # No quarterly history available (ETFs like SPY) — record the snapshot
        # under the first day of the current calendar quarter so repeated weekly
        # runs land on the same (symbol, date, period) row instead of accumulating.
        today = date.today()
        q_start_month = ((today.month - 1) // 3) * 3 + 1
        q_start = date(today.year, q_start_month, 1)
        rows = [{
            "date": q_start,
            "period": period_label(q_start),
            "fiscal_year": q_start.year,
            "revenue": None, "net_income": None, "eps": None,
            "gross_profit": None, "operating_income": None, "free_cash_flow": None,
            "total_assets": None, "total_liabilities": None,
            "pe_ratio": snapshot["pe_ratio"],
            "market_cap": snapshot["market_cap"],
            "debt_to_equity": snapshot["debt_to_equity"],
            "dividend_yield": snapshot["dividend_yield"],
            "shares_outstanding": snapshot["shares_outstanding"],
        }]
    n = upsert_rows(conn, symbol, rows)
    conn.commit()
    return n


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--symbol", help="Ingest a single symbol")
    group.add_argument("--all", action="store_true",
                       help="Ingest the full active watchlist")
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        if args.symbol:
            symbols = [args.symbol.upper()]
        else:
            symbols = get_watchlist(conn)

        print(f"Fundamentals (yfinance): {len(symbols)} symbol(s) → "
              f"{', '.join(symbols)}")
        total = 0
        for sym in symbols:
            try:
                n = ingest_symbol(conn, sym)
                total += n
                print(f"  {sym:<6} +{n:>3} quarters")
            except Exception as e:
                conn.rollback()
                print(f"  {sym:<6} ERROR: {e}", file=sys.stderr)
            time.sleep(0.5)  # be polite to Yahoo
        print(f"\nDone. {total} rows upserted.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
