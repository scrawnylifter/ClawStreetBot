#!/usr/bin/env python3
"""Run the full backfill chain for a single symbol.

Used by:
    - n8n `backfill_pending` workflow (picks symbols WHERE backfill_status='pending')
    - manual invocation when adding/re-adding a symbol outside the YAML flow

Chain:
    1. ingest_alpaca_ohlcv  --symbol $S --timeframe 1d (and 5m/15m if --all-timeframes)
    2. ingest_alpaca_options --symbol $S
    3. ingest_yfinance_fundamentals --symbol $S
    4. compute_realized_vol  --symbol $S
    5. compute_gex_dex       --symbol $S
    6. compute_iv_rank        (bulk, but cheap)
    7. UPDATE market.assets.backfill_status = 'complete' | 'failed'

Idempotent — re-running fills only the gap (OHLCV incremental from ingest_state).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable


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

DB_CONFIG = {
    "host": os.environ.get("POSTGRES_HOST", "localhost"),
    "port": int(os.environ.get("POSTGRES_PORT", 5432)),
    "dbname": os.environ["POSTGRES_DB"],
    "user": os.environ["POSTGRES_USER"],
    "password": os.environ["POSTGRES_PASSWORD"],
}


def set_status(symbol: str, status: str, error: str | None = None) -> None:
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE market.assets
                   SET backfill_status = %s,
                       backfill_error  = %s,
                       updated_at      = NOW()
                 WHERE symbol = %s
                """,
                (status, error, symbol),
            )
    finally:
        conn.close()


def run_step(label: str, cmd: list[str]) -> None:
    print(f"\n▶ {label}: {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        raise RuntimeError(f"{label} exited {result.returncode}")


def script(name: str) -> str:
    return str(PROJECT_ROOT / "01_data" / "scripts" / name)


def backfill(symbol: str, *, days_ohlcv: int,
             all_timeframes: bool, skip_options: bool) -> None:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"=== backfill_symbol  symbol={symbol}  started={started} ===")
    set_status(symbol, "running")

    try:
        # 1. OHLCV (Alpaca)
        ohlcv_cmd = [PYTHON, script("ingest_alpaca_ohlcv.py"),
                     "--symbol", symbol, "--days", str(days_ohlcv)]
        if all_timeframes:
            ohlcv_cmd.append("--all-timeframes")
        else:
            ohlcv_cmd.extend(["--timeframe", "1d"])
        run_step("OHLCV", ohlcv_cmd)

        # 2. Options snapshot (Alpaca)
        if not skip_options:
            run_step("Options", [PYTHON, script("ingest_alpaca_options.py"),
                                 "--symbol", symbol])

        # 3. Fundamentals (yfinance)
        run_step("Fundamentals", [PYTHON, script("ingest_yfinance_fundamentals.py"),
                                  "--symbol", symbol])

        # 4. Realized vol
        run_step("Realized Vol", [PYTHON, script("compute_realized_vol.py"),
                                  "--symbol", symbol])

        # 5. GEX/DEX
        run_step("GEX/DEX", [PYTHON, script("compute_gex_dex.py"),
                             "--symbol", symbol])

        # 6. IV rank (bulk; refresh once at the end)
        run_step("IV Rank", [PYTHON, script("compute_iv_rank.py")])

        set_status(symbol, "complete", error=None)
        print(f"\n✅ {symbol} backfill complete")
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"[:1000]
        set_status(symbol, "failed", error=msg)
        print(f"\n❌ {symbol} backfill failed: {msg}", file=sys.stderr)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol", help="Ticker to backfill (case-insensitive)")
    parser.add_argument("--days-ohlcv", type=int, default=730,
                        help="OHLCV backfill window in days (default 730)")
    parser.add_argument("--all-timeframes", action="store_true",
                        help="Also backfill 5m and 15m bars (default: 1d only)")
    parser.add_argument("--skip-options", action="store_true",
                        help="Skip options snapshot (e.g., for non-optionable underlyings)")
    args = parser.parse_args()

    symbol = args.symbol.upper()

    # Sanity: symbol must exist in market.assets and be active
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT active, backfill_status FROM market.assets WHERE symbol=%s",
                (symbol,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        print(f"❌ {symbol} not in market.assets — add it to config/watchlist.yml first")
        return 2
    if not row[0]:
        print(f"❌ {symbol} is inactive — reactivate via watchlist.yml first")
        return 2

    try:
        backfill(
            symbol,
            days_ohlcv=args.days_ohlcv,
            all_timeframes=args.all_timeframes,
            skip_options=args.skip_options,
        )
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
