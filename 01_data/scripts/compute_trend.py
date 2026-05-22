#!/usr/bin/env python3
"""Compute multi-timeframe trend status for watchlist symbols.

For each active symbol, classifies micro/intermediate/primary trends from
EMA crossovers, measures trend strength via ADX, evaluates price structure
(higher highs / lower lows on a 20-day lookback), then assigns a 0-100
composite trend score.

Scoring breakdown:
    EMA stack alignment   0-30
    ADX trend strength    0-25
    Price structure       0-25
    Direction bonus       0-20
                         ----
    trend_score           0-100

Re-runnable and idempotent (INSERT ON CONFLICT DO UPDATE).

Usage:
    python scripts/compute_trend.py
    python scripts/compute_trend.py --symbol NVDA
    python scripts/compute_trend.py --days 400
    python scripts/compute_trend.py --backfill
"""
from __future__ import annotations

import argparse
import sys
import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import Json, execute_values

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from constants import DB_CONFIG, load_env  # noqa: E402

load_env(".env.db")

FETCH_SYMBOLS_SQL = """
SELECT symbol FROM market.assets
WHERE asset_type IN ('stock', 'etf')
  AND active = TRUE
ORDER BY symbol;
"""

FETCH_BARS_SQL = """
SELECT o.timestamp::date AS dt, o.open, o.high, o.low, o.close, o.volume
FROM market.ohlcv o
JOIN market.assets a ON a.id = o.asset_id
WHERE a.symbol = %s
  AND o.timeframe = '1d'
  AND o.timestamp::date >= %s
  AND o.close IS NOT NULL
ORDER BY o.timestamp::date;
"""

UPSERT_SQL = """
INSERT INTO market.trend_status (
    symbol, date, trend_score,
    micro_trend, intermediate_trend, primary_trend,
    adx, ema_stack, price_structure, trend_strength, details
) VALUES %s
ON CONFLICT (symbol, date) DO UPDATE SET
    trend_score        = EXCLUDED.trend_score,
    micro_trend        = EXCLUDED.micro_trend,
    intermediate_trend = EXCLUDED.intermediate_trend,
    primary_trend      = EXCLUDED.primary_trend,
    adx                = EXCLUDED.adx,
    ema_stack          = EXCLUDED.ema_stack,
    price_structure    = EXCLUDED.price_structure,
    trend_strength     = EXCLUDED.trend_strength,
    details            = EXCLUDED.details,
    computed_at        = NOW();
"""


def ema_series(values: list[float], period: int) -> list[float | None]:
    """Compute an EMA series with SMA seed.

    Args:
        values: Chronologically ordered input prices.
        period: EMA window length.

    Returns:
        List aligned with `values`; warm-up rows are None.
    """
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1.0)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def adx_series(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> list[float | None]:
    """Compute Wilder's Average Directional Index (ADX).

    Args:
        highs: High prices.
        lows: Low prices.
        closes: Close prices.
        period: ADX lookback (default 14).

    Returns:
        ADX series aligned with inputs; warm-up rows are None.
    """
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < 2 * period + 1:
        return out

    plus_dm: list[float] = [0.0]
    minus_dm: list[float] = [0.0]
    tr: list[float] = [0.0]
    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0.0)
        tr.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )

    # Wilder smoothing seeds (sum over first `period` bars).
    sm_tr = sum(tr[1 : period + 1])
    sm_p = sum(plus_dm[1 : period + 1])
    sm_m = sum(minus_dm[1 : period + 1])

    dx_buf: list[float] = []
    # DX from the seed bar onwards.
    for i in range(period, n):
        if i > period:
            sm_tr = sm_tr - sm_tr / period + tr[i]
            sm_p = sm_p - sm_p / period + plus_dm[i]
            sm_m = sm_m - sm_m / period + minus_dm[i]
        if sm_tr == 0:
            dx_buf.append(0.0)
            continue
        plus_di = 100.0 * sm_p / sm_tr
        minus_di = 100.0 * sm_m / sm_tr
        denom = plus_di + minus_di
        dx = 100.0 * abs(plus_di - minus_di) / denom if denom > 0 else 0.0
        dx_buf.append(dx)

    if len(dx_buf) < period:
        return out

    # First ADX value = simple mean of first `period` DX readings.
    adx_prev = sum(dx_buf[:period]) / period
    first_adx_idx = period + (period - 1)  # index in original arrays
    out[first_adx_idx] = adx_prev
    for j in range(period, len(dx_buf)):
        adx_prev = (adx_prev * (period - 1) + dx_buf[j]) / period
        out[period + j] = adx_prev
    return out


def classify_ema_relation(fast: float | None, slow: float | None) -> str:
    """Classify the relationship between two EMAs.

    Args:
        fast: Faster EMA value.
        slow: Slower EMA value.

    Returns:
        'bull' / 'bear' / 'neutral' based on a 0.1% deadband.
    """
    if fast is None or slow is None:
        return "neutral"
    if slow == 0:
        return "neutral"
    rel = (fast - slow) / slow
    if rel > 0.001:
        return "bull"
    if rel < -0.001:
        return "bear"
    return "neutral"


def score_ema_stack(
    e9: float | None, e21: float | None, e50: float | None, e200: float | None
) -> tuple[float, str]:
    """Score the EMA stack alignment.

    Returns:
        (points 0-30, label aligned_bull|partial|aligned_bear).
    """
    if None in (e9, e21, e50, e200):
        return 0.0, "partial"
    if e9 > e21 > e50 > e200:  # type: ignore[operator]
        return 30.0, "aligned_bull"
    if e9 < e21 < e50 < e200:  # type: ignore[operator]
        return 0.0, "aligned_bear"
    return 15.0, "partial"


def score_adx(adx: float | None) -> tuple[float, str]:
    """Score ADX trend strength.

    Returns:
        (points 0-25, label strong|moderate|weak|no_trend).
    """
    if adx is None:
        return 0.0, "no_trend"
    if adx > 40:
        return 25.0, "strong"
    if adx >= 25:
        return 18.0, "moderate"
    if adx >= 20:
        return 10.0, "weak"
    return 0.0, "no_trend"


def score_price_structure(
    highs: list[float], lows: list[float], lookback: int = 20
) -> tuple[float, str]:
    """Score higher-highs / lower-lows over `lookback` bars.

    Splits the lookback window in halves and compares max-high & min-low.

    Returns:
        (points 0-25, label higher_highs|mixed|lower_lows).
    """
    if len(highs) < lookback or len(lows) < lookback:
        return 10.0, "mixed"
    h = highs[-lookback:]
    l = lows[-lookback:]
    half = lookback // 2
    first_high = max(h[:half])
    second_high = max(h[half:])
    first_low = min(l[:half])
    second_low = min(l[half:])
    hh = second_high > first_high
    hl = second_low > first_low
    lh = second_high < first_high
    ll = second_low < first_low
    if hh and hl:
        return 25.0, "higher_highs"
    if lh and ll:
        return 0.0, "lower_lows"
    return 10.0, "mixed"


def score_direction(
    close: float | None,
    ema_21: float | None,
    ema_21_prev: float | None,
) -> float:
    """Score the direction bonus (0-20).

    Args:
        close: Latest close.
        ema_21: Latest 21-EMA.
        ema_21_prev: 21-EMA five bars back (rising/falling proxy).

    Returns:
        Direction bonus points.
    """
    if close is None or ema_21 is None or ema_21_prev is None:
        return 0.0
    above = close > ema_21
    rising = ema_21 > ema_21_prev
    if above and rising:
        return 20.0
    if above or rising:
        return 10.0
    if (not above) and (not rising):
        return 0.0
    return 5.0


def compute_for_symbol(
    conn: Any, symbol: str, days: int, only_latest: bool
) -> list[tuple]:
    """Compute trend status rows for one symbol.

    Args:
        conn: psycopg2 connection.
        symbol: Ticker symbol.
        days: Lookback window in calendar days.
        only_latest: When True, emit a single row for the most recent bar.

    Returns:
        List of upsert-ready tuples for market.trend_status.
    """
    start = date.today() - timedelta(days=days)
    with conn.cursor() as cur:
        cur.execute(FETCH_BARS_SQL, (symbol, start))
        rows = cur.fetchall()

    n = len(rows)
    if n < 60:
        print(f"  {symbol}: insufficient bars ({n} rows), skipping")
        return []

    dates = [r[0] for r in rows]
    highs = [float(r[2]) for r in rows]
    lows = [float(r[3]) for r in rows]
    closes = [float(r[4]) for r in rows]

    e9 = ema_series(closes, 9)
    e21 = ema_series(closes, 21)
    e50 = ema_series(closes, 50)
    e200 = ema_series(closes, 200)
    adx = adx_series(highs, lows, closes, 14)

    indices = [n - 1] if only_latest else list(range(n))

    out: list[tuple] = []
    for i in indices:
        ema_pts, stack_label = score_ema_stack(e9[i], e21[i], e50[i], e200[i])
        adx_pts, strength_label = score_adx(adx[i])

        ps_start = max(0, i - 19)
        ps_pts, ps_label = score_price_structure(
            highs[ps_start : i + 1], lows[ps_start : i + 1], lookback=20
        )

        ema21_prev = e21[i - 5] if i >= 5 else None
        dir_pts = score_direction(closes[i], e21[i], ema21_prev)

        micro = classify_ema_relation(e9[i], e21[i])
        intermediate = classify_ema_relation(e21[i], e50[i])
        primary = classify_ema_relation(e50[i], e200[i])

        trend_score = ema_pts + adx_pts + ps_pts + dir_pts

        details = {
            "breakdown": {
                "ema_stack": ema_pts,
                "adx": adx_pts,
                "price_structure": ps_pts,
                "direction": dir_pts,
            },
            "inputs": {
                "ema_9": e9[i],
                "ema_21": e21[i],
                "ema_50": e50[i],
                "ema_200": e200[i],
                "ema_21_prev_5d": ema21_prev,
                "close": closes[i],
                "adx_14": adx[i],
            },
        }

        out.append(
            (
                symbol,
                dates[i],
                round(trend_score, 2),
                micro,
                intermediate,
                primary,
                adx[i],
                stack_label,
                ps_label,
                strength_label,
                Json(details),
            )
        )
    return out


def main() -> int:
    """Main entry point for trend status computation."""
    parser = argparse.ArgumentParser(
        description="Compute multi-timeframe trend status for watchlist symbols"
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default=None,
        help="Compute trend for a single symbol instead of the full watchlist",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=400,
        help="Lookback window in calendar days for fetching OHLCV (default: 400)",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Emit a trend row for every bar in the lookback (not just the latest)",
    )
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            if args.symbol:
                symbols = [args.symbol.upper()]
            else:
                cur.execute(FETCH_SYMBOLS_SQL)
                symbols = [r[0] for r in cur.fetchall()]

        all_rows: list[tuple] = []
        for sym in symbols:
            print(f"Computing trend status for {sym}...")
            rows = compute_for_symbol(conn, sym, args.days, only_latest=not args.backfill)
            all_rows.extend(rows)
            if rows:
                last = rows[-1]
                print(
                    f"  {sym}: date={last[1]} score={last[2]} "
                    f"micro={last[3]} mid={last[4]} primary={last[5]} "
                    f"adx={last[6]} stack={last[7]} structure={last[8]} strength={last[9]}"
                )

        if not all_rows:
            print("No rows to upsert.")
            return 0

        print(f"Upserting {len(all_rows)} rows into market.trend_status...")
        with conn.cursor() as cur:
            execute_values(cur, UPSERT_SQL, all_rows, page_size=500)
        conn.commit()
        print(f"Done. Upserted {len(all_rows)} trend_status rows.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
