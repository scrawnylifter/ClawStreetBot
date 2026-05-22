#!/usr/bin/env python3
"""Compute daily technical indicators for watchlist symbols.

Reads daily OHLCV from market.ohlcv (timeframe='1d') and computes a
standard set of indicators per symbol per trading day:

    EMA(9, 21, 50, 200)
    RSI(14)                 — Wilder's smoothing
    MACD(12, 26, 9)         — line, signal, histogram
    ATR(14)                 — Wilder's smoothing of true range
    VWAP                    — running typical-price * volume cumulative
                              divided by cumulative volume (daily approx.)
    Bollinger Bands(20, 2)  — middle = 20d SMA, +/- 2 stddev

Re-runnable and idempotent (INSERT ON CONFLICT DO UPDATE).

Usage:
    python scripts/compute_technical_indicators.py
    python scripts/compute_technical_indicators.py --symbol NVDA
    python scripts/compute_technical_indicators.py --days 800
"""
from __future__ import annotations

import argparse
import math
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import execute_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent


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
INSERT INTO market.technical_indicators (
    symbol, date,
    ema_9, ema_21, ema_50, ema_200,
    rsi_14, macd_line, macd_signal, macd_hist,
    atr_14, vwap,
    bb_upper, bb_middle, bb_lower
) VALUES %s
ON CONFLICT (symbol, date) DO UPDATE SET
    ema_9       = EXCLUDED.ema_9,
    ema_21      = EXCLUDED.ema_21,
    ema_50      = EXCLUDED.ema_50,
    ema_200     = EXCLUDED.ema_200,
    rsi_14      = EXCLUDED.rsi_14,
    macd_line   = EXCLUDED.macd_line,
    macd_signal = EXCLUDED.macd_signal,
    macd_hist   = EXCLUDED.macd_hist,
    atr_14      = EXCLUDED.atr_14,
    vwap        = EXCLUDED.vwap,
    bb_upper    = EXCLUDED.bb_upper,
    bb_middle   = EXCLUDED.bb_middle,
    bb_lower    = EXCLUDED.bb_lower,
    created_at  = NOW();
"""


def ema_series(values: list[float], period: int) -> list[float | None]:
    """Compute an EMA series over a list of floats.

    The first `period - 1` outputs are None; the seed value at index
    `period - 1` is the simple average of the first `period` values.

    Args:
        values: Chronologically ordered input series.
        period: EMA window length.

    Returns:
        List of the same length as `values`, with None for warm-up rows.
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


def rsi_series(closes: list[float], period: int = 14) -> list[float | None]:
    """Compute Wilder's RSI series.

    Args:
        closes: Chronological closing prices.
        period: RSI lookback (default 14).

    Returns:
        List aligned with `closes`; warm-up rows are None.
    """
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains: list[float] = [0.0]
    losses: list[float] = [0.0]
    for i in range(1, len(closes)):
        chg = closes[i] - closes[i - 1]
        gains.append(max(chg, 0.0))
        losses.append(max(-chg, 0.0))

    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    if avg_loss == 0:
        out[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        out[period] = 100.0 - (100.0 / (1.0 + rs))

    for i in range(period + 1, len(closes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def atr_series(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> list[float | None]:
    """Compute Wilder's Average True Range series.

    Args:
        highs: High prices.
        lows: Low prices.
        closes: Close prices.
        period: ATR lookback (default 14).

    Returns:
        List aligned with inputs; warm-up rows are None.
    """
    n = len(closes)
    out: list[float | None] = [None] * n
    if n <= period:
        return out
    trs: list[float] = [0.0]
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    seed = sum(trs[1 : period + 1]) / period
    out[period] = seed
    prev = seed
    for i in range(period + 1, n):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


def bollinger_series(
    closes: list[float], period: int = 20, mult: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Compute Bollinger Bands (upper, middle, lower).

    Args:
        closes: Chronological closing prices.
        period: SMA window for the middle band.
        mult: Standard-deviation multiplier for the outer bands.

    Returns:
        Tuple of (upper, middle, lower) series, each aligned with closes.
    """
    n = len(closes)
    upper: list[float | None] = [None] * n
    middle: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    if n < period:
        return upper, middle, lower
    for i in range(period - 1, n):
        window = closes[i - period + 1 : i + 1]
        mean = sum(window) / period
        var = sum((x - mean) ** 2 for x in window) / period
        sd = math.sqrt(var)
        middle[i] = mean
        upper[i] = mean + mult * sd
        lower[i] = mean - mult * sd
    return upper, middle, lower


def compute_for_symbol(
    conn: Any, symbol: str, days: int
) -> list[tuple]:
    """Compute the full indicator set for one symbol.

    Args:
        conn: psycopg2 database connection.
        symbol: Ticker symbol.
        days: Lookback window in calendar days for fetching OHLCV bars.

    Returns:
        List of tuples ready for upsert into market.technical_indicators.
    """
    start = date.today() - timedelta(days=days)
    with conn.cursor() as cur:
        cur.execute(FETCH_BARS_SQL, (symbol, start))
        rows = cur.fetchall()

    if len(rows) < 20:
        print(f"  {symbol}: insufficient bars ({len(rows)} rows), skipping")
        return []

    dates = [r[0] for r in rows]
    highs = [float(r[2]) if r[2] is not None else 0.0 for r in rows]
    lows = [float(r[3]) if r[3] is not None else 0.0 for r in rows]
    closes = [float(r[4]) for r in rows]
    volumes = [float(r[5]) if r[5] is not None else 0.0 for r in rows]

    ema9 = ema_series(closes, 9)
    ema21 = ema_series(closes, 21)
    ema50 = ema_series(closes, 50)
    ema200 = ema_series(closes, 200)

    rsi14 = rsi_series(closes, 14)

    ema12 = ema_series(closes, 12)
    ema26 = ema_series(closes, 26)
    macd_line: list[float | None] = [
        (e12 - e26) if (e12 is not None and e26 is not None) else None
        for e12, e26 in zip(ema12, ema26)
    ]
    # Build signal line from the dense tail of macd_line.
    first_dense = next((i for i, v in enumerate(macd_line) if v is not None), None)
    macd_signal: list[float | None] = [None] * len(macd_line)
    macd_hist: list[float | None] = [None] * len(macd_line)
    if first_dense is not None:
        tail = [v for v in macd_line[first_dense:] if v is not None]
        sig_tail = ema_series(tail, 9)
        for offset, sig in enumerate(sig_tail):
            idx = first_dense + offset
            macd_signal[idx] = sig
            if sig is not None and macd_line[idx] is not None:
                macd_hist[idx] = macd_line[idx] - sig

    atr14 = atr_series(highs, lows, closes, 14)

    # Running VWAP approximation: cumulative typical-price*volume / cumulative volume.
    vwap: list[float | None] = [None] * len(closes)
    cum_pv = 0.0
    cum_v = 0.0
    for i in range(len(closes)):
        typ = (highs[i] + lows[i] + closes[i]) / 3.0
        cum_pv += typ * volumes[i]
        cum_v += volumes[i]
        vwap[i] = (cum_pv / cum_v) if cum_v > 0 else None

    bb_up, bb_mid, bb_lo = bollinger_series(closes, 20, 2.0)

    out: list[tuple] = []
    for i, dt in enumerate(dates):
        out.append(
            (
                symbol,
                dt,
                ema9[i],
                ema21[i],
                ema50[i],
                ema200[i],
                rsi14[i],
                macd_line[i],
                macd_signal[i],
                macd_hist[i],
                atr14[i],
                vwap[i],
                bb_up[i],
                bb_mid[i],
                bb_lo[i],
            )
        )
    return out


def main() -> int:
    """Main entry point for technical indicator computation."""
    parser = argparse.ArgumentParser(
        description="Compute daily technical indicators for watchlist symbols"
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default=None,
        help="Compute indicators for a single symbol instead of the full watchlist",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=400,
        help="Lookback window in calendar days for fetching OHLCV (default: 400)",
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
            print(f"Computing technical indicators for {sym}...")
            rows = compute_for_symbol(conn, sym, args.days)
            all_rows.extend(rows)
            if rows:
                last = rows[-1]
                print(
                    f"  {sym}: latest date={last[1]} "
                    f"ema9={last[2]} rsi14={last[6]} atr14={last[10]}"
                )

        if not all_rows:
            print("No rows to upsert.")
            return 0

        print(f"Upserting {len(all_rows)} rows into market.technical_indicators...")
        with conn.cursor() as cur:
            execute_values(cur, UPSERT_SQL, all_rows, page_size=500)
        conn.commit()
        print(f"Done. Upserted {len(all_rows)} technical_indicators rows.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
