"""Watchlist sync: fetch from Alpaca, diff against Postgres, apply changes.

This is the core sync engine for L01. It:
1. Fetches all watchlists with asset details from Alpaca
2. Flattens into (watchlist_id, symbol) rows with metadata
3. Compares against current Postgres state
4. Upserts new/changed rows
5. Deletes stale rows (in DB but not in Alpaca)
6. Prints CBS_RESULT sentinel for n8n workflow gating
"""

import json
from datetime import datetime, timezone

import structlog

from layer.db.connection import get_connection
from layer.db.watchlist import (
    ensure_schema_and_table,
    upsert_watchlist_rows,
    delete_stale_rows,
    get_current_watchlist_keys,
)
from layer.sync.alpaca_client import (
    fetch_all_watchlists,
    get_trading_client,
    add_asset,
    remove_asset,
)

logger = structlog.get_logger(__name__)

# Postgres session-level advisory lock guarding against overlapping syncs: the
# n8n schedule fires every 60s, but a slow N+1 fetch can run longer. A second
# run that can't grab the lock no-ops instead of racing the delete/upsert diff.
_SYNC_ADVISORY_LOCK_KEY = 0x10_01  # unique to L01 watchlist sync


def flatten_watchlist_data(watchlists: list[dict]) -> tuple[list[dict], set[tuple[str, str]]]:
    """Flatten Alpaca watchlist data into rows suitable for upsert.

    Returns:
        rows: list of dicts with keys matching upsert_watchlist_rows params
        alpaca_keys: set of (id, symbol) tuples representing Alpaca's current state
    """
    now = datetime.now(timezone.utc)
    rows: list[dict] = []
    alpaca_keys: set[tuple[str, str]] = set()

    for wl in watchlists:
        wl_id = wl["id"]
        for asset in wl["assets"]:
            key = (wl_id, asset["symbol"])
            alpaca_keys.add(key)
            rows.append({
                "id": wl_id,
                "name": wl["name"],
                "account_id": wl["account_id"],
                "symbol": asset["symbol"],
                "asset_id": asset["id"],
                "asset_class": asset.get("class", "us_equity"),
                "exchange": asset.get("exchange", ""),
                "asset_name": asset.get("name", ""),
                "status": asset.get("status", "active"),
                "tradable": asset.get("tradable", False),
                "marginable": asset.get("marginable", False),
                "shortable": asset.get("shortable", False),
                "easy_to_borrow": asset.get("easy_to_borrow", False),
                "fractionable": asset.get("fractionable", False),
                "alpaca_created_at": wl["created_at"],
                "alpaca_updated_at": wl["updated_at"],
                "synced_at": now,
            })

    return rows, alpaca_keys


def sync_watchlists(emit_result: bool = True) -> dict:
    """Main sync function. Fetch from Alpaca, diff, upsert, delete stale.

    Returns a CBS_RESULT dict for n8n workflow gating:
        CBS_RESULT {"watchlists": N, "synced": M, "added": A, "removed": R}

    emit_result controls whether the CBS_RESULT sentinel is printed. Write-through
    callers (add/remove) re-sync internally and set emit_result=False so the only
    sentinel on stdout is their own — keeping n8n's gate unambiguous.
    """
    logger.info("watchlist_sync_started")

    # 1. Fetch from Alpaca
    watchlists = fetch_all_watchlists()
    logger.info("alpaca_watchlists_fetched", count=len(watchlists))

    # 2. Flatten into row dicts
    rows, alpaca_keys = flatten_watchlist_data(watchlists)
    logger.info("watchlist_entries_flattened", total_entries=len(rows), unique_keys=len(alpaca_keys))

    # 3. Connect to DB
    conn = get_connection()
    try:
        # 4. Guard against overlapping runs (session lock; auto-released on close)
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (_SYNC_ADVISORY_LOCK_KEY,))
            got_lock = cur.fetchone()[0]
        if not got_lock:
            logger.warning("watchlist_sync_skipped", reason="another_sync_in_progress")
            result = {"watchlists": 0, "synced": 0, "added": 0, "removed": 0, "skipped": True}
            if emit_result:
                print(f"CBS_RESULT {json.dumps(result)}")
            return result

        # 5. Ensure schema+table exist (idempotent DDL, own transaction)
        ensure_schema_and_table(conn)

        # 6. Current DB state — reused for both the add diff and stale deletion
        db_keys = get_current_watchlist_keys(conn)
        new_keys = alpaca_keys - db_keys

        # 7. Upsert + delete stale in a SINGLE transaction (atomic mirror update)
        synced_count = upsert_watchlist_rows(conn, rows)
        removed_count = delete_stale_rows(conn, alpaca_keys, db_keys)
        conn.commit()

        result = {
            "watchlists": len(watchlists),
            "synced": synced_count,
            "added": len(new_keys),
            "removed": removed_count,
        }

        logger.info("watchlist_sync_completed", **result)

        # 8. CBS_RESULT sentinel for n8n workflow gating (must be valid JSON)
        if emit_result:
            print(f"CBS_RESULT {json.dumps(result)}")

        return result
    except Exception:
        conn.rollback()
        logger.exception("watchlist_sync_failed")
        raise
    finally:
        conn.close()


# ── Write-through operations ─────────────────────────────────────────────
# Alpaca is the source of truth. These mutate Alpaca FIRST, then re-mirror the
# whole watchlist back into Postgres via sync_watchlists() and verify the DB
# reflects the change — never write to the DB directly.


def _resolve_target_watchlist(watchlist_name: str | None, watchlists: list[dict]) -> dict:
    """Pick which Alpaca watchlist to mutate.

    - If watchlist_name is given: match by name (case-insensitive); error if absent.
    - Else if exactly one watchlist exists: use it (the common single-list case).
    - Else: raise — ambiguous (multiple) or none — with a helpful message.
    """
    if not watchlists:
        raise ValueError("No watchlists exist in Alpaca; create one before add/remove.")
    if watchlist_name:
        for wl in watchlists:
            if wl["name"].lower() == watchlist_name.lower():
                return wl
        names = sorted(wl["name"] for wl in watchlists)
        raise ValueError(f"Watchlist {watchlist_name!r} not found. Available: {names}")
    if len(watchlists) == 1:
        return watchlists[0]
    names = sorted(wl["name"] for wl in watchlists)
    raise ValueError(f"Multiple watchlists exist; pass --watchlist to disambiguate. Available: {names}")


def _db_contains(watchlist_id: str, symbol: str) -> bool:
    """True if (watchlist_id, symbol) is present in market.watchlist right now.

    Reuses get_current_watchlist_keys (string-normalized ids) so we compare keys
    without an id cast. Opens and closes its own connection.
    """
    conn = get_connection()
    try:
        return (str(watchlist_id), symbol) in get_current_watchlist_keys(conn)
    finally:
        conn.close()


def add_watchlist_asset(symbol: str, watchlist_name: str | None = None) -> dict:
    """Write-through ADD: add `symbol` to an Alpaca watchlist, then re-sync the DB.

    Flow: mutate Alpaca → sync_watchlists() re-mirrors Alpaca into Postgres →
    verify the DB now contains the symbol. Idempotent: adding an already-present
    symbol is a no-op but still re-syncs and verifies.

    Returns a CBS_RESULT dict:
        {"op":"add","symbol":S,"watchlist":W,"action":"added|noop","verified":true,"sync":{...}}
    """
    symbol = symbol.strip().upper()
    if not symbol:
        raise ValueError("symbol must be a non-empty ticker")

    logger.info("watchlist_add_started", symbol=symbol, watchlist=watchlist_name)
    client = get_trading_client()
    target = _resolve_target_watchlist(watchlist_name, fetch_all_watchlists(client))
    wl_id, wl_name = target["id"], target["name"]

    if symbol in {a["symbol"] for a in target["assets"]}:
        action = "noop"
        logger.info("watchlist_add_noop", symbol=symbol, watchlist=wl_name, reason="already_present")
    else:
        add_asset(client, wl_id, symbol)
        action = "added"
        logger.info("watchlist_add_applied", symbol=symbol, watchlist=wl_name)

    # Re-mirror Alpaca → DB, then confirm the DB matches the intended state.
    sync_result = sync_watchlists(emit_result=False)
    if not _db_contains(wl_id, symbol):
        raise RuntimeError(
            f"post-sync verification failed: {symbol} expected in DB watchlist {wl_name!r} but absent"
        )

    result = {
        "op": "add", "symbol": symbol, "watchlist": wl_name,
        "action": action, "verified": True, "sync": sync_result,
    }
    logger.info("watchlist_add_completed", op="add", symbol=symbol, watchlist=wl_name, action=action)
    print(f"CBS_RESULT {json.dumps(result)}")
    return result


def remove_watchlist_asset(symbol: str, watchlist_name: str | None = None) -> dict:
    """Write-through REMOVE: remove `symbol` from an Alpaca watchlist, then re-sync the DB.

    Flow: mutate Alpaca → sync_watchlists() re-mirrors Alpaca into Postgres →
    verify the DB no longer contains the symbol. Idempotent: removing an absent
    symbol is a no-op but still re-syncs and verifies.

    Returns a CBS_RESULT dict:
        {"op":"remove","symbol":S,"watchlist":W,"action":"removed|noop","verified":true,"sync":{...}}
    """
    symbol = symbol.strip().upper()
    if not symbol:
        raise ValueError("symbol must be a non-empty ticker")

    logger.info("watchlist_remove_started", symbol=symbol, watchlist=watchlist_name)
    client = get_trading_client()
    target = _resolve_target_watchlist(watchlist_name, fetch_all_watchlists(client))
    wl_id, wl_name = target["id"], target["name"]

    if symbol not in {a["symbol"] for a in target["assets"]}:
        action = "noop"
        logger.info("watchlist_remove_noop", symbol=symbol, watchlist=wl_name, reason="not_present")
    else:
        remove_asset(client, wl_id, symbol)
        action = "removed"
        logger.info("watchlist_remove_applied", symbol=symbol, watchlist=wl_name)

    # Re-mirror Alpaca → DB, then confirm the symbol is gone from the DB.
    sync_result = sync_watchlists(emit_result=False)
    if _db_contains(wl_id, symbol):
        raise RuntimeError(
            f"post-sync verification failed: {symbol} expected absent from DB watchlist {wl_name!r} but present"
        )

    result = {
        "op": "remove", "symbol": symbol, "watchlist": wl_name,
        "action": action, "verified": True, "sync": sync_result,
    }
    logger.info("watchlist_remove_completed", op="remove", symbol=symbol, watchlist=wl_name, action=action)
    print(f"CBS_RESULT {json.dumps(result)}")
    return result