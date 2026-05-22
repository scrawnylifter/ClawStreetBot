#!/usr/bin/env python3
"""Ingest OHLCV bars from Alpaca into market.ohlcv.

Replacement for ingest_polygon_ohlcv.py. Uses Alpaca's
StockHistoricalDataClient to fetch daily and 15-minute bars for every active
watchlist symbol and UPSERTs them into market.ohlcv. Tracks the last ingested
timestamp per (symbol, timeframe) in market.ingest_state so subsequent runs
are incremental.

Usage:
    python scripts/ingest_alpaca_ohlcv.py [--timeframe 1d|15m] [--symbol NVDA]
                                          [--days N] [--all-timeframes]

Examples:
    # First-run backfill of daily bars (~2 years) for the whole watchlist
    python scripts/ingest_alpaca_ohlcv.py --timeframe 1d --days 730

    # Incremental: only fetch new bars since the last successful run
    python scripts/ingest_alpaca_ohlcv.py --timeframe 15m

    # Both timeframes for a single symbol
    python scripts/ingest_alpaca_ohlcv.py --all-timeframes --symbol NVDA
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

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


load_env(".env.alpaca")
load_env(".env.db")

from alpaca.data.historical import StockHistoricalDataClient  # noqa: E402
from alpaca.data.requests import StockBarsRequest  # noqa: E402
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit  # noqa: E402

ALPACA_API_KEY = os.environ["ALPACA_PAPER_API_KEY"]
ALPACA_SECRET_KEY = os.environ["ALPACA_PAPER_SECRET_KEY"]

DB_CONFIG = {
    "host": os.environ.get("POSTGRES_HOST", "localhost"),
    "port": int(os.environ.get("POSTGRES_PORT", 5432)),
    "dbname": os.environ["POSTGRES_DB"],
    "user": os.environ["POSTGRES_USER"],
    "password": os.environ["POSTGRES_PASSWORD"],
}

# Alpaca TimeFrame per supported label.
TIMEFRAMES: dict[str, TimeFrame] = {
    "1d":  TimeFrame.Day,
    "15m": TimeFrame(15, TimeFrameUnit.Minute),
    "5m":  TimeFrame(5, TimeFrameUnit.Minute),
}

# Default backfill window per timeframe when ingest_state has no prior entry.
DEFAULT_DAYS = {
    "1d":  730,   # ~2 years
    "15m": 60,
    "5m":  30,    # ~1 month (Alpaca limits intraday bars)
}

SOURCE = "alpaca_ohlcv"


def get_watchlist(conn) -> list[tuple[int, str]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, symbol FROM market.assets "
            "WHERE asset_type='stock' AND active = TRUE ORDER BY symbol"
        )
        return cur.fetchall()


def get_last_timestamp(conn, source: str, symbol: str, timeframe: str) -> datetime | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT last_timestamp FROM market.ingest_state "
            "WHERE source=%s AND symbol=%s AND timeframe=%s",
            (source, symbol, timeframe),
        )
        row = cur.fetchone()
        return row[0] if row else None


def upsert_ingest_state(conn, source: str, symbol: str, timeframe: str,
                        last_ts: datetime | None, rows: int, status: str = "ok",
                        err: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO market.ingest_state (source, symbol, timeframe, last_timestamp,
                                             last_run_at, row_count, status, error_message)
            VALUES (%s,%s,%s,%s,NOW(),%s,%s,%s)
            ON CONFLICT (source, symbol, timeframe) DO UPDATE SET
                last_timestamp = COALESCE(EXCLUDED.last_timestamp, market.ingest_state.last_timestamp),
                last_run_at    = NOW(),
                row_count      = market.ingest_state.row_count + EXCLUDED.row_count,
                status         = EXCLUDED.status,
                error_message  = EXCLUDED.error_message
            """,
            (source, symbol, timeframe, last_ts, rows, status, err),
        )


def upsert_bars(conn, asset_id: int, timeframe: str, bars) -> tuple[int, datetime | None]:
    """UPSERT a sequence of Alpaca Bar objects; return (count, latest_ts_utc)."""
    rows = []
    latest: datetime | None = None
    for bar in bars:
        ts = bar.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        rows.append((
            asset_id, timeframe, ts,
            bar.open, bar.high, bar.low, bar.close,
            int(bar.volume or 0),
            int(bar.trade_count) if bar.trade_count is not None else None,
            bar.vwap,
        ))
        if latest is None or ts > latest:
            latest = ts

    if not rows:
        return 0, None

    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO market.ohlcv
                (asset_id, timeframe, timestamp, open, high, low, close,
                 volume, trade_count, vwap)
            VALUES %s
            ON CONFLICT (asset_id, timeframe, timestamp) DO UPDATE SET
                open        = EXCLUDED.open,
                high        = EXCLUDED.high,
                low         = EXCLUDED.low,
                close       = EXCLUDED.close,
                volume      = EXCLUDED.volume,
                trade_count = EXCLUDED.trade_count,
                vwap        = EXCLUDED.vwap
            """,
            rows,
            page_size=1000,
        )
    return len(rows), latest


def fetch_and_store(client: StockHistoricalDataClient, conn, asset_id: int,
                    symbol: str, timeframe: str,
                    start: datetime, end: datetime) -> tuple[int, datetime | None]:
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TIMEFRAMES[timeframe],
        start=start,
        end=end,
        adjustment="all",
        feed="iex",
    )
    bar_set = client.get_stock_bars(req)
    # BarSet.data is dict[symbol, list[Bar]]; missing symbols yield empty list.
    bars = bar_set.data.get(symbol, []) if hasattr(bar_set, "data") else []
    return upsert_bars(conn, asset_id, timeframe, bars)


def ingest_timeframe(client: StockHistoricalDataClient, conn,
                     watchlist: list[tuple[int, str]],
                     timeframe: str, days: int) -> None:
    now = datetime.now(timezone.utc)
    print(f"\n=== Timeframe: {timeframe} ===")
    total = 0
    for asset_id, symbol in watchlist:
        last = get_last_timestamp(conn, SOURCE, symbol, timeframe)
        if last is not None:
            # Resume one second past the last stored bar to avoid re-fetching it.
            start = last + timedelta(seconds=1)
        else:
            start = now - timedelta(days=days)

        if start >= now:
            print(f"  {symbol:<6} up-to-date (last={last})")
            continue

        try:
            count, latest = fetch_and_store(
                client, conn, asset_id, symbol, timeframe, start, now
            )
            upsert_ingest_state(conn, SOURCE, symbol, timeframe, latest, count)
            conn.commit()
            total += count
            last_str = latest.strftime("%Y-%m-%d %H:%M") if latest else "—"
            print(f"  {symbol:<6} +{count:>6} bars  latest={last_str}")
        except Exception as e:
            conn.rollback()
            upsert_ingest_state(conn, SOURCE, symbol, timeframe, None, 0,
                                status="error", err=str(e)[:500])
            conn.commit()
            print(f"  {symbol:<6} ERROR: {e}")
        # Light pacing; Alpaca free tier allows 200 req/min.
        time.sleep(0.05)
    print(f"  total inserted/updated: {total}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeframe", choices=list(TIMEFRAMES.keys()), default="1d")
    parser.add_argument("--symbol", help="Restrict to a single symbol")
    parser.add_argument("--days", type=int,
                        help="Backfill window in days (only used on first run per symbol)")
    parser.add_argument("--all-timeframes", action="store_true",
                        help="Run every supported timeframe in sequence")
    args = parser.parse_args()

    client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    conn = psycopg2.connect(**DB_CONFIG)

    try:
        watchlist = get_watchlist(conn)
        if args.symbol:
            watchlist = [(aid, s) for aid, s in watchlist if s == args.symbol]
            if not watchlist:
                print(f"Symbol {args.symbol} not in market.assets")
                return 1
        print(f"Watchlist: {len(watchlist)} symbols → {', '.join(s for _, s in watchlist)}")

        timeframes = list(TIMEFRAMES.keys()) if args.all_timeframes else [args.timeframe]
        for tf in timeframes:
            days = args.days if args.days else DEFAULT_DAYS[tf]
            ingest_timeframe(client, conn, watchlist, tf, days)
    finally:
        conn.close()

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
