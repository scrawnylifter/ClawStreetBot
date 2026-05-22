#!/usr/bin/env python3
"""Compute daily market regime classification.

Classifies each trading day as ``bull`` / ``bear`` / ``transition`` using:
  - SPY 50d vs 200d SMA (golden cross / death cross / neutral)
  - VIX level (calm < 18, elevated 18-30, panic > 30) — falls back to SPY
    20d realized-vol proxy if no VIX data available
  - Breadth proxy (% of watchlist proxies above their own 50d SMA)

Writes to ``market.regime`` with ON CONFLICT DO UPDATE (idempotent).

Usage::

    python compute_regime.py                          # classify last 400 days
    python compute_regime.py --start 2024-01-01        # from specific date
    python compute_regime.py --start 2024-01-01 --end 2026-05-01
    python compute_regime.py --days 30                 # last 30 days

Layer: 01_data
Schedule: daily (derived_daily n8n workflow)
"""
from __future__ import annotations

import argparse
import math
import os
import statistics
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from sys import stderr

import psycopg2
from psycopg2.extras import Json

# ---------------------------------------------------------------------------
# Shared config — add shared/ to path before local imports
# ---------------------------------------------------------------------------
_project_root = Path(__file__).resolve().parents[2]
if str(_project_root / "shared") not in sys.path:
    sys.path.insert(0, str(_project_root / "shared"))

from constants import load_env  # noqa: E402

load_env(".env.db")

DB_CONFIG = {
    "host": os.environ.get("POSTGRES_HOST", "localhost"),
    "port": int(os.environ.get("POSTGRES_PORT", 5432)),
    "dbname": os.environ["POSTGRES_DB"],
    "user": os.environ["POSTGRES_USER"],
    "password": os.environ["POSTGRES_PASSWORD"],
}

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# VIX bands (per CLAUDE.md market regime classifier)
VIX_CALM = 18.0
VIX_PANIC = 30.0

# Sector proxies used for breadth when no full SPY500 feed is available.
# Fraction of these above their own 50d SMA → breadth_proxy (0–1).
# Sourced from constants watchlist so we don't hardcode.
BREADTH_PROXY_SYMBOLS = ("NVDA", "AMD", "MU")

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SpyRow:
    """SPY daily close with rolling 50d / 200d SMAs."""

    d: date
    close: float
    sma_50: float | None
    sma_200: float | None


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def fetch_spy_series(conn, start: date, end: date) -> list[SpyRow]:
    """Pull SPY closes + 50/200 SMAs from ``market.ohlcv``.

    Pulls 320 calendar days before ``start`` so the 200d SMA is defined on
    the first in-range row.
    """
    window_start = start - timedelta(days=320)
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH spy AS (
                SELECT o.timestamp::date AS d, o.close::float AS close
                FROM market.ohlcv o
                JOIN market.assets a ON a.id = o.asset_id
                WHERE a.symbol = 'SPY'
                  AND o.timeframe = '1d'
                  AND o.timestamp::date BETWEEN %s AND %s
            )
            SELECT d, close,
                   AVG(close) OVER (
                       ORDER BY d ROWS BETWEEN 49 PRECEDING AND CURRENT ROW
                   ) AS sma_50,
                   AVG(close) OVER (
                       ORDER BY d ROWS BETWEEN 199 PRECEDING AND CURRENT ROW
                   ) AS sma_200,
                   COUNT(*) OVER (
                       ORDER BY d ROWS BETWEEN 49 PRECEDING AND CURRENT ROW
                   ) AS n_50,
                   COUNT(*) OVER (
                       ORDER BY d ROWS BETWEEN 199 PRECEDING AND CURRENT ROW
                   ) AS n_200
            FROM spy
            ORDER BY d
            """,
            (window_start, end),
        )
        rows: list[SpyRow] = []
        for d, close, s50, s200, n50, n200 in cur.fetchall():
            if d < start:
                continue
            rows.append(
                SpyRow(
                    d=d,
                    close=float(close),
                    sma_50=float(s50) if n50 >= 50 else None,
                    sma_200=float(s200) if n200 >= 200 else None,
                )
            )
        return rows


def fetch_vix_series(conn, start: date, end: date) -> dict[date, float]:
    """Return ``{date: vix_value}`` if a VIX asset exists in market.ohlcv."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT o.timestamp::date, o.close::float
            FROM market.ohlcv o
            JOIN market.assets a ON a.id = o.asset_id
            WHERE a.symbol IN ('VIX','^VIX','I:VIX')
              AND o.timeframe = '1d'
              AND o.timestamp::date BETWEEN %s AND %s
            ORDER BY o.timestamp
            """,
            (start, end),
        )
        return {d: float(c) for d, c in cur.fetchall()}


def spy_realized_vol_proxy(spy: list[SpyRow]) -> dict[date, float]:
    """Compute SPY 20d annualized realized vol (×100) as a VIX stand-in.

    Annualization uses sqrt(252). The returned scale is roughly comparable to
    the VIX index so the same band thresholds (18 / 30) apply.
    """
    proxy: dict[date, float] = {}
    closes = [r.close for r in spy]
    dates = [r.d for r in spy]
    rets: list[float] = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            rets.append(math.log(closes[i] / closes[i - 1]))
        else:
            rets.append(0.0)
    for i in range(20, len(rets) + 1):
        window = rets[i - 20 : i]
        if len(window) < 2:
            continue
        sd = statistics.stdev(window)
        rv = sd * math.sqrt(252) * 100.0
        proxy[dates[i]] = rv
    return proxy


def fetch_breadth_series(conn, start: date, end: date) -> dict[date, float]:
    """Fraction of proxy symbols whose close > their own 50d SMA."""
    if not BREADTH_PROXY_SYMBOLS:
        return {}
    window_start = start - timedelta(days=120)
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH px AS (
                SELECT a.symbol, o.timestamp::date AS d, o.close::float AS close
                FROM market.ohlcv o
                JOIN market.assets a ON a.id = o.asset_id
                WHERE a.symbol = ANY(%s)
                  AND o.timeframe = '1d'
                  AND o.timestamp::date BETWEEN %s AND %s
            ), sma AS (
                SELECT symbol, d, close,
                       AVG(close) OVER (
                           PARTITION BY symbol ORDER BY d
                           ROWS BETWEEN 49 PRECEDING AND CURRENT ROW
                       ) AS sma_50,
                       COUNT(*) OVER (
                           PARTITION BY symbol ORDER BY d
                           ROWS BETWEEN 49 PRECEDING AND CURRENT ROW
                       ) AS n
                FROM px
            )
            SELECT d,
                   SUM(CASE WHEN n >= 50 AND close > sma_50 THEN 1 ELSE 0 END)::float
                       / NULLIF(SUM(CASE WHEN n >= 50 THEN 1 ELSE 0 END), 0)::float
                       AS above_pct
            FROM sma
            WHERE d BETWEEN %s AND %s
            GROUP BY d
            ORDER BY d
            """,
            (list(BREADTH_PROXY_SYMBOLS), window_start, end, start, end),
        )
        return {d: float(p) for d, p in cur.fetchall() if p is not None}


# ---------------------------------------------------------------------------
# Classify
# ---------------------------------------------------------------------------

def classify_day(
    spy: SpyRow,
    vix: float | None,
    breadth: float | None,
) -> tuple[str, str, str]:
    """Combine the three components into ``(regime, spy_trend, vix_level)``.

    Rules (per CLAUDE.md):
      - spy_trend: golden_cross if SMA50 > SMA200 by >2%, death_cross if
        SMA50 < SMA200 by >2%, otherwise neutral.
      - vix_level: <18 calm, 18-30 elevated, >30 panic.
      - regime:
          - bull if golden_cross and not panic and breadth >= 0.5
          - bear if death_cross or panic
          - transition otherwise
    """
    spy_trend = "neutral"
    if spy.sma_50 is not None and spy.sma_200 is not None and spy.sma_200 > 0:
        gap = (spy.sma_50 - spy.sma_200) / spy.sma_200
        if gap > 0.02:
            spy_trend = "golden_cross"
        elif gap < -0.02:
            spy_trend = "death_cross"

    if vix is None:
        vix_level = "unknown"
    elif vix < VIX_CALM:
        vix_level = "calm"
    elif vix <= VIX_PANIC:
        vix_level = "elevated"
    else:
        vix_level = "panic"

    breadth_ok = breadth is not None and breadth >= 0.5

    if spy_trend == "death_cross" or vix_level == "panic":
        regime = "bear"
    elif spy_trend == "golden_cross" and vix_level != "panic" and breadth_ok:
        regime = "bull"
    else:
        regime = "transition"

    return regime, spy_trend, vix_level


def classify_regimes(conn, start: date, end: date) -> int:
    """Write a row to ``market.regime`` for every SPY trading day in range.

    Returns the number of days classified.
    """
    spy = fetch_spy_series(conn, start, end)
    if not spy:
        print("No SPY rows found. Backfill SPY into market.ohlcv first.", file=stderr)
        return 0

    vix = fetch_vix_series(conn, start, end)
    vix_source = "vix"
    if not vix:
        print("No VIX asset found — falling back to SPY 20d realized-vol proxy.")
        vix = spy_realized_vol_proxy(spy)
        vix_source = "rv_proxy"

    breadth = fetch_breadth_series(conn, start, end)

    counts = {"bull": 0, "bear": 0, "transition": 0}
    with conn.cursor() as cur:
        for row in spy:
            v = vix.get(row.d)
            b = breadth.get(row.d)
            regime, spy_trend, vix_level = classify_day(row, v, b)
            counts[regime] += 1
            cur.execute(
                """
                INSERT INTO market.regime (
                    date, regime, spy_trend, spy_sma_50, spy_sma_200, spy_close,
                    vix_level, vix_value, breadth_proxy, details
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (date) DO UPDATE SET
                    regime         = EXCLUDED.regime,
                    spy_trend      = EXCLUDED.spy_trend,
                    spy_sma_50     = EXCLUDED.spy_sma_50,
                    spy_sma_200    = EXCLUDED.spy_sma_200,
                    spy_close      = EXCLUDED.spy_close,
                    vix_level      = EXCLUDED.vix_level,
                    vix_value      = EXCLUDED.vix_value,
                    breadth_proxy  = EXCLUDED.breadth_proxy,
                    details        = EXCLUDED.details,
                    computed_at    = NOW()
                """,
                (
                    row.d,
                    regime,
                    spy_trend,
                    row.sma_50,
                    row.sma_200,
                    row.close,
                    vix_level,
                    v,
                    b,
                    Json({"vix_source": vix_source}),
                ),
            )
    conn.commit()
    print(
        f"Classified {len(spy)} days  "
        f"bull={counts['bull']} bear={counts['bear']} transition={counts['transition']}"
    )
    return len(spy)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Compute daily market regime")
    parser.add_argument("--start", type=date.fromisoformat, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=date.fromisoformat, help="End date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, default=400, help="Days to backfill (default: 400)")
    args = parser.parse_args()

    if args.start and args.end:
        start, end = args.start, args.end
    elif args.start:
        start = args.start
        end = date.today()
    else:
        end = date.today()
        start = end - timedelta(days=args.days)

    print(f"Computing regime from {start} to {end} ...")

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        n = classify_regimes(conn, start, end)
        print(f"Done — {n} days classified.")
        return 0 if n > 0 else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())