#!/usr/bin/env python3
"""Ingest OHLCV bars from Polygon.io into market.ohlcv.

Supports daily, 5-minute, and 15-minute timeframes. Idempotent via UPSERT.
Tracks last_timestamp per (symbol, timeframe) in market.ingest_state for
incremental fetches on subsequent runs.

Usage:
    python scripts/ingest_polygon_ohlcv.py [--timeframe 1d|5m|15m] [--symbol NVDA]
                                           [--days N] [--all-timeframes]

Examples:
    # Backfill ~2 years of daily bars for entire watchlist (first run)
    python scripts/ingest_polygon_ohlcv.py --timeframe 1d --days 730

    # Incremental: pull only new bars since last run
    python scripts/ingest_polygon_ohlcv.py --timeframe 5m

    # All three timeframes for the whole watchlist
    python scripts/ingest_polygon_ohlcv.py --all-timeframes --days 30
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
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
    "host": "localhost",
    "port": int(os.environ.get("POSTGRES_PORT", 5432)),
    "dbname": os.environ.get("POSTGRES_DB", "clawstreet"),
    "user": os.environ.get("POSTGRES_USER", "clawstreet"),
    "password": os.environ.get("POSTGRES_PASSWORD", "ClawStr33tBot2026"),
}

# (multiplier, timespan) per Polygon aggregate API
TIMEFRAMES = {
    "1d":  (1,  "day"),
    "5m":  (5,  "minute"),
    "15m": (15, "minute"),
}

# Default backfill window per timeframe (Polygon Options Starter = 2yr; intraday limited per req)
DEFAULT_DAYS = {
    "1d":  730,   # ~2 years
    "5m":  30,
    "15m": 60,
}


def get_watchlist(conn) -> list[tuple[int, str]]:
    with conn.cursor() as cur:
        cur.execute("SELECT id, symbol FROM market.assets WHERE asset_type='stock' ORDER BY symbol")
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


def upsert_bars(conn, asset_id: int, timeframe: str, bars: Iterable) -> tuple[int, datetime | None]:
    """UPSERT bars; return (count, latest_timestamp_utc)."""
    rows = []
    latest: datetime | None = None
    for bar in bars:
        # Polygon agg.timestamp is ms since epoch
        ts = datetime.fromtimestamp(bar.timestamp / 1000.0, tz=timezone.utc)
        rows.append((
            asset_id, timeframe, ts,
            bar.open, bar.high, bar.low, bar.close, int(bar.volume or 0),
        ))
        if latest is None or ts > latest:
            latest = ts

    if not rows:
        return 0, None

    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO market.ohlcv (asset_id, timeframe, timestamp, open, high, low, close, volume)
            VALUES %s
            ON CONFLICT (asset_id, timeframe, timestamp) DO UPDATE SET
                open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                close=EXCLUDED.close, volume=EXCLUDED.volume
            """,
            rows,
            page_size=1000,
        )
    return len(rows), latest


def fetch_and_store(client: RESTClient, conn, asset_id: int, symbol: str,
                    timeframe: str, start: datetime, end: datetime) -> tuple[int, datetime | None]:
    mult, span = TIMEFRAMES[timeframe]
    from_str = start.strftime("%Y-%m-%d")
    to_str = end.strftime("%Y-%m-%d")

    # list_aggs paginates automatically and returns an iterator
    bars = client.list_aggs(
        ticker=symbol,
        multiplier=mult,
        timespan=span,
        from_=from_str,
        to=to_str,
        adjusted=True,
        sort="asc",
        limit=50000,
    )
    return upsert_bars(conn, asset_id, timeframe, bars)


def ingest_timeframe(client: RESTClient, conn, watchlist: list[tuple[int, str]],
                     timeframe: str, days: int) -> None:
    source = "polygon_ohlcv"
    now = datetime.now(timezone.utc)
    print(f"\n=== Timeframe: {timeframe} ===")
    total = 0
    for asset_id, symbol in watchlist:
        last = get_last_timestamp(conn, source, symbol, timeframe)
        if last is not None:
            # Start one bar after last to avoid re-fetching the same trailing bar
            start = last + timedelta(seconds=1)
        else:
            start = now - timedelta(days=days)

        if start >= now:
            print(f"  {symbol:<6} up-to-date (last={last})")
            continue

        try:
            count, latest = fetch_and_store(client, conn, asset_id, symbol, timeframe, start, now)
            upsert_ingest_state(conn, source, symbol, timeframe, latest, count)
            conn.commit()
            total += count
            last_str = latest.strftime("%Y-%m-%d %H:%M") if latest else "—"
            print(f"  {symbol:<6} +{count:>6} bars  latest={last_str}")
        except Exception as e:
            conn.rollback()
            upsert_ingest_state(conn, source, symbol, timeframe, None, 0,
                                status="error", err=str(e)[:500])
            conn.commit()
            print(f"  {symbol:<6} ERROR: {e}")
        # Tiny pause to be polite; Polygon Stocks plan is unlimited but avoid bursting
        time.sleep(0.05)
    print(f"  total inserted/updated: {total}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeframe", choices=list(TIMEFRAMES.keys()), default="1d")
    parser.add_argument("--symbol", help="Restrict to a single symbol")
    parser.add_argument("--days", type=int, help="Backfill window in days (only used on first run)")
    parser.add_argument("--all-timeframes", action="store_true",
                        help="Run 1d, 5m, and 15m in sequence")
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
