#!/usr/bin/env python3
"""reconcile_orders.py — Phase 5B order-state reconciliation.

For every signal_alerts row in status='executing' with an alpaca_order_id,
fetches the order from Alpaca and applies the right DB transition:

    Alpaca status                   → DB action
    --------------------------------  --------------------------------------
    filled                          → INSERT trading.positions + UPDATE
                                       signal_alerts status='filled' +
                                       position_id
    canceled / rejected / expired / → UPDATE signal_alerts status='error'
    done_for_day                       + error_message
    partially_filled / new /        → no-op (leave at 'executing')
    accepted / pending_*

Designed to run on a 1-minute n8n cron. Read-only against Alpaca; writes
only the two affected DB rows per fill.

Usage:
    python scripts/reconcile_orders.py                # process all 'executing'
    python scripts/reconcile_orders.py --id 7
    python scripts/reconcile_orders.py --limit 10
    python scripts/reconcile_orders.py --dry-run      # print, no DB writes
    python scripts/reconcile_orders.py --verbose
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent))
import process_approved as pa  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("reconcile_orders")


# ---------------------------------------------------------------------------
# Alpaca status partitioning
# ---------------------------------------------------------------------------

FILLED_STATUSES = {"filled"}
DEAD_STATUSES   = {"canceled", "cancelled", "rejected", "expired", "done_for_day"}
# Anything not in those two sets is treated as still-open.


def _alpaca_status(order) -> str:
    """Coerce alpaca-py's OrderStatus enum (or a plain string) to a lowercase str."""
    s = getattr(order, "status", None)
    if s is None:
        return ""
    return str(getattr(s, "value", s)).lower()


def _alpaca_client():
    from alpaca.trading.client import TradingClient
    cfg = pa.load_env("alpaca")
    key = cfg.get("ALPACA_PAPER_API_KEY") or os.environ.get("ALPACA_PAPER_API_KEY")
    sec = cfg.get("ALPACA_PAPER_SECRET_KEY") or os.environ.get("ALPACA_PAPER_SECRET_KEY")
    if not key or not sec:
        raise RuntimeError("Missing ALPACA_PAPER_API_KEY / _SECRET_KEY in .env.alpaca")
    return TradingClient(api_key=key, secret_key=sec, paper=True)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def fetch_executing(conn, signal_id: int | None, limit: int) -> list[dict]:
    """Rows that submit_to_alpaca returned successfully on but haven't been
    reconciled yet. Filter requires alpaca_order_id so we have something to
    look up.

    Uses FOR UPDATE SKIP LOCKED so two overlapping 1-min cron runs see
    DIFFERENT rows — eliminates the race where both inserted a position
    for the same fill (phantom position). The lock is held by the caller's
    transaction and released at conn.commit/rollback in main().
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if signal_id is not None:
            cur.execute(
                "SELECT * FROM market.signal_alerts WHERE id = %s FOR UPDATE",
                (signal_id,),
            )
        else:
            cur.execute(
                """SELECT * FROM market.signal_alerts
                    WHERE status = 'executing'
                      AND alpaca_order_id IS NOT NULL
                      AND position_id IS NULL
                    ORDER BY executed_at ASC NULLS LAST, id ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED""",
                (limit,),
            )
        return list(cur.fetchall())


def resolve_asset_id(conn, symbol: str) -> int | None:
    """Look up market.assets.id for a symbol. None if not found."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM market.assets WHERE symbol = %s",
            (symbol,),
        )
        row = cur.fetchone()
        return row[0] if row else None


def insert_position(
    conn, *,
    asset_id: int,
    direction: str,
    entry_price: Decimal,
    quantity: Decimal,
    stop_loss: Decimal | None,
    take_profit: Decimal | None,
) -> int:
    """INSERT one row into trading.positions, return its id.

    For option positions we still store the underlying-level stop_loss /
    take_profit from the signal. The exit monitor will read the option
    contract via signal_alerts.option_symbol (joined via position_id) and
    decide whether to flatten based on either the underlying or the option
    premium.
    """
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO trading.positions
                  (asset_id, direction, entry_price, quantity,
                   stop_loss, take_profit, status, opened_at)
               VALUES (%s, %s, %s, %s, %s, %s, 'open', %s)
               RETURNING id""",
            (asset_id, direction, entry_price, quantity,
             stop_loss, take_profit, datetime.now(timezone.utc)),
        )
        return cur.fetchone()[0]


def mark_filled(conn, signal_id: int, position_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE market.signal_alerts
                  SET status = 'filled',
                      position_id = %s,
                      error_message = NULL
                WHERE id = %s""",
            (position_id, signal_id),
        )


def mark_error(conn, signal_id: int, message: str) -> None:
    msg = (message or "")[:1000]
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE market.signal_alerts
                  SET status = 'error',
                      error_message = %s
                WHERE id = %s""",
            (msg, signal_id),
        )


# ---------------------------------------------------------------------------
# Per-row reconciliation
# ---------------------------------------------------------------------------

def _to_decimal(v) -> Decimal | None:
    if v is None or v == "":
        return None
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def position_direction(signal: dict, has_option: bool) -> str:
    """Map signal direction + instrument to trading.positions.direction.

    Options are always 'long' from a position-tracking standpoint — you
    bought a call (bullish) or a put (bearish). Stocks follow the signal:
    bullish→long, bearish→short.
    """
    if has_option:
        return "long"
    return "long" if signal["direction"] == "bullish" else "short"


def reconcile_one(
    conn, client, signal: dict, dry_run: bool, verbose: bool,
) -> str:
    sid = signal["id"]
    order_id = signal["alpaca_order_id"]
    sym = signal["symbol"]

    # --- Pull the order from Alpaca ---
    try:
        order = client.get_order_by_id(order_id)
    except Exception as e:
        # 404 on order means Alpaca has no record — likely a stale row from a
        # failed submission. Leave it at 'executing' and surface a warning so
        # an operator can investigate.
        log.warning("#%s order %s lookup failed: %s", sid, order_id, e)
        return f"#{sid} {sym} WARN — order lookup failed ({type(e).__name__}: {e})"

    status = _alpaca_status(order)
    filled_qty = _to_decimal(getattr(order, "filled_qty", None)) or Decimal("0")
    filled_avg_price = _to_decimal(getattr(order, "filled_avg_price", None))

    # --- Pending → no-op ---
    if status not in FILLED_STATUSES and status not in DEAD_STATUSES:
        msg = f"#{sid} {sym} PENDING — alpaca_status={status} filled_qty={filled_qty}"
        if verbose:
            log.info(msg)
        return msg

    # --- Dead (canceled / rejected / expired / done_for_day) ---
    if status in DEAD_STATUSES:
        reason = f"alpaca_status={status}"
        # Surface the reject reason if Alpaca provided one (only some statuses do).
        for attr in ("failed_at", "canceled_at", "expired_at", "reject_reason"):
            v = getattr(order, attr, None)
            if v:
                reason += f" {attr}={v}"
        if dry_run:
            return f"#{sid} {sym} DRY-RUN would mark error — {reason}"
        mark_error(conn, sid, reason)
        conn.commit()
        log.info("#%s %s ERROR — %s", sid, sym, reason)
        return f"#{sid} {sym} ERROR — {reason}"

    # --- Filled ---
    if filled_qty <= 0 or filled_avg_price is None or filled_avg_price <= 0:
        # Status says filled but the numbers are missing — refuse to write a
        # bad position row. The next run will see the same state and retry.
        log.warning("#%s status=filled but filled_qty=%s avg=%s — skipping",
                    sid, filled_qty, filled_avg_price)
        return f"#{sid} {sym} WARN — filled but fill data incomplete"

    asset_id = resolve_asset_id(conn, sym)
    if asset_id is None:
        msg = f"asset '{sym}' not in market.assets — can't open position row"
        if dry_run:
            return f"#{sid} {sym} DRY-RUN would error — {msg}"
        mark_error(conn, sid, msg)
        conn.commit()
        log.error("#%s %s ERROR — %s", sid, sym, msg)
        return f"#{sid} {sym} ERROR — {msg}"

    has_option = bool(signal.get("option_symbol"))
    direction = position_direction(signal, has_option)
    stop_loss   = _to_decimal(signal.get("stop_price"))
    take_profit = _to_decimal(signal.get("tp1_price"))

    if dry_run:
        return (f"#{sid} {sym} DRY-RUN would fill — "
                f"INSERT positions(asset_id={asset_id}, direction={direction}, "
                f"entry={filled_avg_price}, qty={filled_qty}, "
                f"sl={stop_loss}, tp={take_profit}); "
                f"mark signal filled")

    # Single transaction: position INSERT + signal_alerts UPDATE.
    try:
        position_id = insert_position(
            conn,
            asset_id=asset_id,
            direction=direction,
            entry_price=filled_avg_price,
            quantity=filled_qty,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        mark_filled(conn, sid, position_id)
        conn.commit()
    except Exception as e:
        conn.rollback()
        log.exception("#%s DB error during fill reconciliation", sid)
        # Don't promote a DB error to an Alpaca 'error' on the signal — the
        # row is still 'executing' so the next run retries cleanly.
        return f"#{sid} {sym} WARN — DB error during fill: {type(e).__name__}: {e}"

    log.info(
        "#%s %s FILLED — position_id=%s qty=%s avg=%s",
        sid, sym, position_id, filled_qty, filled_avg_price,
    )
    return (f"#{sid} {sym} FILLED — position_id={position_id} "
            f"qty={filled_qty} @ ${filled_avg_price}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", type=int, default=None,
                        help="Reconcile a single signal_alerts row.")
    parser.add_argument("--limit", type=int, default=50,
                        help="Max rows to process (default 50).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Still hit Alpaca, but don't write to the DB.")
    parser.add_argument("--verbose", action="store_true",
                        help="Print PENDING rows too (default skips them).")
    args = parser.parse_args()

    conn = pa.get_connection()
    try:
        rows = fetch_executing(conn, args.id, args.limit)
    except Exception:
        conn.close()
        raise

    if not rows:
        if args.id is not None:
            print(f"No signal_alerts row with id={args.id}.")
        else:
            print("No executing signals to reconcile.")
        conn.close()
        return 0

    log.info("Reconciling %d executing row(s)%s",
             len(rows), " (DRY RUN)" if args.dry_run else "")

    try:
        client = _alpaca_client()
    except Exception as e:
        log.error("Could not build Alpaca client: %s", e)
        conn.close()
        return 2

    results: list[str] = []
    try:
        for r in rows:
            try:
                results.append(reconcile_one(conn, client, r, args.dry_run, args.verbose))
                # Release the FOR UPDATE lock acquired by fetch_executing even
                # on PENDING / WARN paths that don't write. Without this, the
                # row stays locked until the loop's final commit/rollback, so
                # an overlapping cron run sees an artificially empty set.
                conn.commit()
            except Exception:
                log.exception("Unhandled error reconciling signal #%s", r["id"])
                results.append(f"#{r['id']} {r['symbol']} ERROR — internal")
                try:
                    conn.rollback()
                except Exception:
                    pass
    finally:
        conn.close()

    print()
    for line in results:
        # Suppress PENDING lines unless --verbose (they're the steady-state for
        # a 1-min cron — would spam logs otherwise).
        if "PENDING" in line and not args.verbose:
            continue
        print(line)
    filled  = sum(1 for line in results if "FILLED" in line)
    errored = sum(1 for line in results if "ERROR" in line)
    pending = sum(1 for line in results if "PENDING" in line)
    warned  = sum(1 for line in results if "WARN" in line)
    print(f"\nDONE — filled={filled}, error={errored}, "
          f"pending={pending}, warn={warned}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
