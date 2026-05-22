#!/usr/bin/env python3
"""Sync ClawStreetBot watchlist from config/watchlist.yml to Alpaca + Postgres.

Behavior:
    desired   = symbols in config/watchlist.yml
    current   = symbols in market.assets WHERE active

    desired - current  → INSERT or reactivate (active=true, backfill_status='pending')
    current - desired  → soft-deactivate (active=false, deactivated_at=NOW())
    intersection       → metadata refresh only (name/sector/industry/exchange)

The script is idempotent — running it when the YAML is unchanged is a no-op.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

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

from alpaca.trading.client import TradingClient  # noqa: E402
from alpaca.trading.requests import CreateWatchlistRequest, GetAssetsRequest, UpdateWatchlistRequest  # noqa: E402
from alpaca.trading.enums import AssetClass, AssetStatus  # noqa: E402
import psycopg2  # noqa: E402

ALPACA_API_KEY = os.environ["ALPACA_PAPER_API_KEY"]
ALPACA_SECRET_KEY = os.environ["ALPACA_PAPER_SECRET_KEY"]
WATCHLIST_NAME = "ClawStreetBot"
CONFIG_PATH = PROJECT_ROOT / "config" / "watchlist.yml"

DB_CONFIG = {
    "host": os.environ.get("POSTGRES_HOST", "localhost"),
    "port": int(os.environ.get("POSTGRES_PORT", 5432)),
    "dbname": os.environ["POSTGRES_DB"],
    "user": os.environ["POSTGRES_USER"],
    "password": os.environ["POSTGRES_PASSWORD"],
}


def load_yaml_watchlist() -> dict[str, dict]:
    with open(CONFIG_PATH) as f:
        data = yaml.safe_load(f)
    out: dict[str, dict] = {}
    for entry in data.get("symbols", []):
        ticker = entry["ticker"].upper()
        out[ticker] = {
            "sector": entry.get("sector"),
            "industry": entry.get("industry"),
            "notes": entry.get("notes"),
        }
    return out


def fetch_db_state(conn) -> dict[str, dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol, active, backfill_status FROM market.assets "
            "WHERE asset_type='stock' ORDER BY symbol"
        )
        return {
            row[0]: {"active": row[1], "backfill_status": row[2]}
            for row in cur.fetchall()
        }


def fetch_alpaca_assets(trading: TradingClient, symbols: list[str]) -> dict:
    if not symbols:
        return {}
    all_assets = trading.get_all_assets(GetAssetsRequest(
        asset_class=AssetClass.US_EQUITY,
        status=AssetStatus.ACTIVE,
    ))
    return {a.symbol: a for a in all_assets if a.symbol in set(symbols)}


def sync_alpaca_watchlist(trading: TradingClient, symbols: list[str]) -> None:
    """Create or update the Alpaca watchlist to match `symbols` exactly."""
    existing = None
    try:
        for wl in trading.get_watchlists():
            if wl.name == WATCHLIST_NAME:
                existing = wl
                break
    except Exception as e:
        print(f"   ⚠️  Could not list Alpaca watchlists: {e}")

    try:
        if existing is None:
            trading.create_watchlist(CreateWatchlistRequest(
                name=WATCHLIST_NAME, symbols=symbols,
            ))
            print(f"   ✅ Created Alpaca watchlist with {len(symbols)} symbols")
        else:
            trading.update_watchlist_by_id(
                watchlist_id=str(existing.id),
                watchlist_data=UpdateWatchlistRequest(
                    name=WATCHLIST_NAME, symbols=symbols,
                ),
            )
            print(f"   ✅ Updated Alpaca watchlist to {len(symbols)} symbols")
    except Exception as e:
        print(f"   ❌ Alpaca watchlist sync error: {e}")


def upsert_asset(conn, symbol: str, meta: dict, alpaca_asset) -> None:
    name = alpaca_asset.name if alpaca_asset else symbol
    exchange = str(alpaca_asset.exchange).split(".")[-1] if alpaca_asset else None
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO market.assets
                (symbol, name, asset_type, exchange, sector, industry,
                 active, added_at, deactivated_at, backfill_status)
            VALUES (%s, %s, 'stock', %s, %s, %s, TRUE, NOW(), NULL, 'pending')
            ON CONFLICT (symbol) DO UPDATE SET
                name           = COALESCE(EXCLUDED.name, market.assets.name),
                exchange       = COALESCE(EXCLUDED.exchange, market.assets.exchange),
                sector         = COALESCE(EXCLUDED.sector, market.assets.sector),
                industry       = COALESCE(EXCLUDED.industry, market.assets.industry),
                active         = TRUE,
                deactivated_at = NULL,
                backfill_status = CASE
                    WHEN market.assets.active = FALSE THEN 'pending'
                    ELSE market.assets.backfill_status
                END,
                updated_at     = NOW()
            """,
            (symbol, name, exchange, meta.get("sector"), meta.get("industry")),
        )


def deactivate_asset(conn, symbol: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE market.assets
               SET active = FALSE,
                   deactivated_at = NOW(),
                   updated_at = NOW()
             WHERE symbol = %s
            """,
            (symbol,),
        )


def main() -> int:
    if not CONFIG_PATH.exists():
        print(f"❌ Missing {CONFIG_PATH}")
        return 1

    desired = load_yaml_watchlist()
    desired_symbols = sorted(desired.keys())

    print("=" * 70)
    print(f"🦞 WATCHLIST SYNC  ({len(desired_symbols)} desired symbols)")
    print("=" * 70)

    trading = TradingClient(api_key=ALPACA_API_KEY, secret_key=ALPACA_SECRET_KEY, paper=True)
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        db_state = fetch_db_state(conn)
        active_now = {s for s, v in db_state.items() if v["active"]}

        to_add_or_reactivate = [s for s in desired_symbols if s not in active_now]
        to_deactivate = sorted(active_now - set(desired_symbols))
        unchanged = sorted(set(desired_symbols) & active_now)

        print(f"   add/reactivate : {to_add_or_reactivate or '—'}")
        print(f"   deactivate     : {to_deactivate or '—'}")
        print(f"   unchanged      : {len(unchanged)} symbol(s)")

        alpaca_assets = fetch_alpaca_assets(trading, desired_symbols)
        missing_in_alpaca = [s for s in desired_symbols if s not in alpaca_assets]
        if missing_in_alpaca:
            print(f"   ⚠️  Not found in Alpaca: {missing_in_alpaca}")

        # 1. Upsert every desired symbol (refreshes metadata, reactivates if needed)
        for sym in desired_symbols:
            upsert_asset(conn, sym, desired[sym], alpaca_assets.get(sym))

        # 2. Deactivate symbols no longer in YAML
        for sym in to_deactivate:
            deactivate_asset(conn, sym)

        conn.commit()
        print(f"\n   ✅ Postgres updated")

        # 3. Sync Alpaca watchlist (always send the desired set)
        sync_alpaca_watchlist(trading, desired_symbols)

        # 4. Report pending backfill queue
        with conn.cursor() as cur:
            cur.execute(
                "SELECT symbol FROM market.assets "
                "WHERE active=TRUE AND backfill_status='pending' ORDER BY symbol"
            )
            pending = [r[0] for r in cur.fetchall()]
        if pending:
            print(f"   📋 Pending backfill: {pending}")
        else:
            print(f"   📋 Pending backfill: none")
    finally:
        conn.close()

    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
