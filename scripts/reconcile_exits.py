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

Also reconciles TP1 50% partial closes (tp1_sell_order_id IS NOT NULL AND
tp1_filled_at IS NULL): on fill, subtract the filled quantity from
positions.quantity, persist tp1_filled_* and tp1_realized_pnl, clear
tp1_sell_order_id. The position stays open for the remaining quantity. On a
dead status, clear tp1_sell_order_id AND tp1_hit_at so exit_monitor retries.

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
    """Close the position and flip the originating signal_alerts row to
    status='exited' (the documented terminal lifecycle state from migration
    020_alert_lifecycle). Both updates run in the caller's transaction —
    the caller commits."""
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE trading.positions
                  SET status       = 'closed',
                      closed_at    = %s,
                      realized_pnl = %s
                WHERE id = %s""",
            (closed_at, realized_pnl, position_id),
        )
        cur.execute(
            """UPDATE market.signal_alerts
                  SET status = 'exited'
                WHERE position_id = %s
                  AND status = 'filled'""",
            (position_id,),
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


def fetch_pending_tp1_partials(
    conn, position_id: int | None, limit: int,
) -> list[dict]:
    """Open positions with a TP1 partial SELL submitted but not yet reconciled."""
    sql = """
        SELECT p.id AS position_id, p.asset_id, p.direction AS pos_direction,
               p.entry_price, p.quantity, p.tp1_sell_order_id,
               p.tp1_hit_at, p.exit_reason, p.opened_at,
               s.option_symbol
          FROM trading.positions p
          LEFT JOIN market.signal_alerts s ON s.position_id = p.id
         WHERE p.status = 'open'
           AND p.tp1_sell_order_id IS NOT NULL
           AND p.tp1_filled_at IS NULL
    """
    params: list = []
    if position_id is not None:
        sql += " AND p.id = %s"
        params.append(position_id)
    sql += " ORDER BY p.tp1_hit_at ASC NULLS LAST LIMIT %s"
    params.append(limit)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def mark_tp1_partial_filled(
    conn, position_id: int,
    filled_qty: Decimal, filled_avg: Decimal, realized_pnl: Decimal,
) -> None:
    """Persist TP1 partial fill: subtract from quantity, record fill details,
    clear tp1_sell_order_id so the position is eligible for further exits."""
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE trading.positions
                  SET quantity          = quantity - %s,
                      tp1_filled_qty    = %s,
                      tp1_filled_avg    = %s,
                      tp1_filled_at     = %s,
                      tp1_realized_pnl  = %s,
                      tp1_sell_order_id = NULL,
                      exit_submitted_at = NULL
                WHERE id = %s""",
            (filled_qty, filled_qty, filled_avg, now, realized_pnl, position_id),
        )


def clear_tp1_partial(conn, position_id: int) -> None:
    """TP1 partial SELL didn't fill — wipe partial stamps AND tp1_hit_at so
    exit_monitor's decision tree re-detects TP1 on the next pass."""
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE trading.positions
                  SET tp1_sell_order_id = NULL,
                      tp1_hit_at        = NULL,
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
# Per-row TP1 partial reconciliation
# ---------------------------------------------------------------------------

def reconcile_partial_one(conn, client, row: dict, dry_run: bool, verbose: bool) -> str:
    """Mirror of reconcile_one() for TP1 50% partials.

    Outcomes:
      - filled  → reduce positions.quantity by filled_qty, stamp tp1_filled_*
      - dead    → clear tp1_sell_order_id AND tp1_hit_at (lets TP1 re-detect)
      - pending → no-op
    """
    pid = row["position_id"]
    tp1_oid = row["tp1_sell_order_id"]

    try:
        order = client.get_order_by_id(tp1_oid)
    except Exception as e:
        log.warning("position #%s TP1 partial %s lookup failed: %s",
                    pid, tp1_oid, e)
        return (f"position #{pid} WARN — TP1 partial lookup failed "
                f"({type(e).__name__}: {e})")

    status = rc._alpaca_status(order)
    filled_qty = rc._to_decimal(getattr(order, "filled_qty", None)) or Decimal("0")
    filled_avg = rc._to_decimal(getattr(order, "filled_avg_price", None))

    # Pending
    if status not in rc.FILLED_STATUSES and status not in rc.DEAD_STATUSES:
        msg = (f"position #{pid} TP1 partial pending — alpaca_status={status} "
               f"filled_qty={filled_qty}")
        if verbose:
            log.info(msg)
        return msg

    # Dead — let TP1 re-detect on the next monitor pass.
    if status in rc.DEAD_STATUSES:
        msg = f"TP1 partial didn't fill (alpaca_status={status}) — will retry"
        if dry_run:
            return f"position #{pid} DRY-RUN would clear tp1_sell_order_id — {msg}"
        clear_tp1_partial(conn, pid)
        conn.commit()
        log.info("position #%s TP1 CLEARED — %s", pid, msg)
        return f"position #{pid} TP1 CLEARED — {msg}"

    # Filled.
    if filled_qty <= 0 or filled_avg is None or filled_avg <= 0:
        log.warning("position #%s TP1 partial filled but qty=%s avg=%s — skipping",
                    pid, filled_qty, filled_avg)
        return (f"position #{pid} WARN — TP1 partial filled but fill data incomplete "
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
        return (f"position #{pid} DRY-RUN would record TP1 partial — "
                f"entry={entry_price} exit={filled_avg} qty={filled_qty} "
                f"tp1_realized_pnl={realized}")

    try:
        mark_tp1_partial_filled(conn, pid, filled_qty, filled_avg, realized)
        conn.commit()
    except Exception as e:
        conn.rollback()
        log.exception("position #%s DB error recording TP1 partial", pid)
        return (f"position #{pid} WARN — DB error during TP1 partial: "
                f"{type(e).__name__}: {e}")

    sign = "+" if realized >= 0 else ""
    log.info("position #%s TP1 PARTIAL FILLED — exit=%s qty=%s tp1_pnl=%s%s",
             pid, filled_avg, filled_qty, sign, realized)
    return (f"position #{pid} TP1 PARTIAL FILLED — entry={entry_price} → "
            f"exit={filled_avg} qty={filled_qty} tp1_realized_pnl={sign}{realized}")


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
        full_rows = fetch_pending_exits(conn, args.id, args.limit)
        partial_rows = fetch_pending_tp1_partials(conn, args.id, args.limit)
    except Exception:
        conn.close()
        raise

    if not full_rows and not partial_rows:
        if args.id is not None:
            print(f"No open position with id={args.id} and a SELL in flight.")
        else:
            print("No SELL orders to reconcile.")
        conn.close()
        return 0

    log.info("Reconciling %d full close(s) + %d TP1 partial(s)%s",
             len(full_rows), len(partial_rows),
             " (DRY RUN)" if args.dry_run else "")

    try:
        client = rc._alpaca_client()
    except Exception as e:
        log.error("Could not build Alpaca client: %s", e)
        conn.close()
        return 2

    results: list[str] = []
    try:
        for r in full_rows:
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
        for r in partial_rows:
            try:
                results.append(
                    reconcile_partial_one(conn, client, r, args.dry_run, args.verbose)
                )
            except Exception:
                log.exception("Unhandled error reconciling TP1 partial on position #%s",
                              r["position_id"])
                results.append(f"position #{r['position_id']} ERROR — TP1 partial internal")
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
        if "TP1 partial pending" in line and not args.verbose:
            continue
        print(line)
    closed       = sum(1 for line in results if "CLOSED" in line and "would close" not in line)
    cleared      = sum(1 for line in results if "CLEARED" in line)
    pending      = sum(1 for line in results if "CLOSING" in line)
    tp1_filled   = sum(1 for line in results if "TP1 PARTIAL FILLED" in line)
    tp1_pending  = sum(1 for line in results if "TP1 partial pending" in line)
    warned       = sum(1 for line in results if "WARN" in line)
    print(f"\nDONE — closed={closed}, cleared={cleared}, "
          f"pending={pending}, tp1_filled={tp1_filled}, "
          f"tp1_pending={tp1_pending}, warn={warned}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
