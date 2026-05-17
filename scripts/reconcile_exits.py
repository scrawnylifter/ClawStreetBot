#!/usr/bin/env python3
"""reconcile_exits.py — Phase 5B SELL-fill reconciliation.

SELL-side mirror of reconcile_orders.py. Walks trading.positions rows that
have a SELL submitted (sell_order_id IS NOT NULL AND status='open'), fetches
the order from Alpaca, and applies the right DB transition:

    Alpaca status                  → DB action
    -----------------------------    ---------------------------------------
    filled                         → UPDATE positions status='closed',
                                      closed_at, realized_pnl from the fill
    canceled / rejected /          → CLEAR sell_order_id + exit_submitted_at
    expired / done_for_day            + exit_reason so exit_monitor.py
                                      re-evaluates and retries on its next
                                      pass (the position is still open at
                                      Alpaca)
    partially_filled / new /       → no-op (close still in flight)
    accepted / pending_*

Designed for a ~1-min n8n cron alongside reconcile_orders.py. Read-only
against Alpaca; touches only the affected positions row per fill.

Realized P&L:
    options (long, the standard case):
        (filled_avg - entry_price) × filled_qty × 100
    stock long:
        (filled_avg - entry_price) × filled_qty
    stock short (currently unreachable — blocked at submit time):
        (entry_price - filled_avg) × filled_qty

Usage:
    python scripts/reconcile_exits.py                # all open positions w/ SELL in flight
    python scripts/reconcile_exits.py --id 5
    python scripts/reconcile_exits.py --limit 10
    python scripts/reconcile_exits.py --dry-run
    python scripts/reconcile_exits.py --verbose
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent))
import process_approved as pa  # noqa: E402
import reconcile_orders as rc  # reuse _alpaca_client + status partitions  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("reconcile_exits")


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def fetch_pending_exits(conn, position_id: int | None, limit: int) -> list[dict]:
    """Open positions with a SELL submitted, joined to their originating signal
    so we know whether it's an option (×100 multiplier on P&L)."""
    sql = """
        SELECT p.id AS position_id, p.asset_id, p.direction AS pos_direction,
               p.entry_price, p.quantity, p.sell_order_id, p.exit_submitted_at,
               p.exit_reason, p.opened_at,
               s.option_symbol
          FROM trading.positions p
          LEFT JOIN market.signal_alerts s ON s.position_id = p.id
         WHERE p.status = 'open'
           AND p.sell_order_id IS NOT NULL
    """
    params: list = []
    if position_id is not None:
        sql += " AND p.id = %s"
        params.append(position_id)
    sql += " ORDER BY p.exit_submitted_at ASC NULLS LAST LIMIT %s"
    params.append(limit)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def mark_closed(
    conn, position_id: int, closed_at: datetime, realized_pnl: Decimal,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE trading.positions
                  SET status       = 'closed',
                      closed_at    = %s,
                      realized_pnl = %s
                WHERE id = %s""",
            (closed_at, realized_pnl, position_id),
        )


def clear_exit_submission(conn, position_id: int) -> None:
    """SELL didn't fill — wipe the submission stamps so exit_monitor retries."""
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE trading.positions
                  SET sell_order_id     = NULL,
                      exit_submitted_at = NULL,
                      exit_reason       = NULL
                WHERE id = %s""",
            (position_id,),
        )


# ---------------------------------------------------------------------------
# P&L
# ---------------------------------------------------------------------------

def compute_realized_pnl(
    *, is_option: bool, pos_direction: str,
    entry_price: Decimal, exit_price: Decimal, qty: Decimal,
) -> Decimal:
    multiplier = Decimal("100") if is_option else Decimal("1")
    if pos_direction == "long":
        per_unit = exit_price - entry_price
    else:  # 'short' — stock only
        per_unit = entry_price - exit_price
    return (per_unit * multiplier * qty).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# Per-row reconciliation
# ---------------------------------------------------------------------------

def reconcile_one(conn, client, row: dict, dry_run: bool, verbose: bool) -> str:
    pid = row["position_id"]
    sell_oid = row["sell_order_id"]

    try:
        order = client.get_order_by_id(sell_oid)
    except Exception as e:
        log.warning("position #%s SELL %s lookup failed: %s", pid, sell_oid, e)
        return (f"position #{pid} WARN — SELL order lookup failed "
                f"({type(e).__name__}: {e})")

    status = rc._alpaca_status(order)
    filled_qty = rc._to_decimal(getattr(order, "filled_qty", None)) or Decimal("0")
    filled_avg = rc._to_decimal(getattr(order, "filled_avg_price", None))

    # --- Pending ---
    if status not in rc.FILLED_STATUSES and status not in rc.DEAD_STATUSES:
        msg = (f"position #{pid} CLOSING — alpaca_status={status} "
               f"filled_qty={filled_qty}")
        if verbose:
            log.info(msg)
        return msg

    # --- Dead (canceled / rejected / expired / done_for_day) ---
    if status in rc.DEAD_STATUSES:
        msg = f"SELL didn't fill (alpaca_status={status}) — will retry"
        if dry_run:
            return f"position #{pid} DRY-RUN would clear sell_order_id — {msg}"
        clear_exit_submission(conn, pid)
        conn.commit()
        log.info("position #%s CLEARED — %s", pid, msg)
        return f"position #{pid} CLEARED — {msg}"

    # --- Filled ---
    if filled_qty <= 0 or filled_avg is None or filled_avg <= 0:
        log.warning("position #%s SELL filled but qty=%s avg=%s — skipping",
                    pid, filled_qty, filled_avg)
        return (f"position #{pid} WARN — SELL filled but fill data incomplete "
                f"(qty={filled_qty}, avg={filled_avg})")

    entry_price = rc._to_decimal(row["entry_price"])
    is_option = bool(row.get("option_symbol"))
    realized = compute_realized_pnl(
        is_option=is_option,
        pos_direction=row["pos_direction"],
        entry_price=entry_price,
        exit_price=filled_avg,
        qty=filled_qty,
    )

    if dry_run:
        return (f"position #{pid} DRY-RUN would close — "
                f"entry={entry_price} exit={filled_avg} qty={filled_qty} "
                f"realized_pnl={realized}")

    try:
        mark_closed(conn, pid, datetime.now(timezone.utc), realized)
        conn.commit()
    except Exception as e:
        conn.rollback()
        log.exception("position #%s DB error closing", pid)
        return (f"position #{pid} WARN — DB error during close: "
                f"{type(e).__name__}: {e}")

    sign = "+" if realized >= 0 else ""
    log.info("position #%s CLOSED — exit=%s qty=%s realized_pnl=%s%s",
             pid, filled_avg, filled_qty, sign, realized)
    return (f"position #{pid} CLOSED — entry={entry_price} → exit={filled_avg} "
            f"qty={filled_qty} realized_pnl={sign}{realized}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", type=int, default=None,
                        help="Reconcile a single position id.")
    parser.add_argument("--limit", type=int, default=50,
                        help="Max rows per run (default 50).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Still hit Alpaca, but don't write to the DB.")
    parser.add_argument("--verbose", action="store_true",
                        help="Print CLOSING (still-pending) rows too.")
    args = parser.parse_args()

    conn = pa.get_connection()
    try:
        rows = fetch_pending_exits(conn, args.id, args.limit)
    except Exception:
        conn.close()
        raise

    if not rows:
        if args.id is not None:
            print(f"No open position with id={args.id} and a SELL in flight.")
        else:
            print("No SELL orders to reconcile.")
        conn.close()
        return 0

    log.info("Reconciling %d position(s) with SELL in flight%s",
             len(rows), " (DRY RUN)" if args.dry_run else "")

    try:
        client = rc._alpaca_client()
    except Exception as e:
        log.error("Could not build Alpaca client: %s", e)
        conn.close()
        return 2

    results: list[str] = []
    try:
        for r in rows:
            try:
                results.append(reconcile_one(conn, client, r, args.dry_run, args.verbose))
            except Exception:
                log.exception("Unhandled error reconciling position #%s",
                              r["position_id"])
                results.append(f"position #{r['position_id']} ERROR — internal")
                try:
                    conn.rollback()
                except Exception:
                    pass
    finally:
        conn.close()

    print()
    for line in results:
        if "CLOSING" in line and not args.verbose:
            continue
        print(line)
    closed   = sum(1 for line in results if "CLOSED" in line and "would close" not in line)
    cleared  = sum(1 for line in results if "CLEARED" in line)
    pending  = sum(1 for line in results if "CLOSING" in line)
    warned   = sum(1 for line in results if "WARN" in line)
    print(f"\nDONE — closed={closed}, cleared={cleared}, "
          f"pending={pending}, warn={warned}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
