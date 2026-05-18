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
import alert_telegram as at  # exit-fill push notifications  # noqa: E402


def _notify_exit(
    row: dict, reason: str, exit_price: Decimal, qty: Decimal,
    realized_pnl: Decimal, is_partial: bool, residual_qty: Decimal | None,
) -> None:
    """Build + fire the exit-fill Telegram notification. Wrapped here so the
    three close call-sites stay readable. Never raises — at.send_exit_notification
    swallows all I/O failures.

    Called AFTER conn.commit() at each close site so we never notify on a
    rolled-back DB write. Row data (symbol, strategy, direction) is pulled
    from the SELECT joins, NOT re-queried — keeps this a pure formatter
    call."""
    try:
        is_option = bool(row.get("option_symbol"))
        text = at.format_exit_notification(
            symbol=row.get("symbol") or "?",
            strategy=row.get("strategy") or "unknown",
            direction=row.get("direction") or "?",
            reason=reason,
            entry_price=row["entry_price"],
            exit_price=exit_price,
            qty=qty,
            realized_pnl=realized_pnl,
            is_option=is_option,
            is_partial=is_partial,
            residual_qty=residual_qty,
        )
        at.send_exit_notification(text)
    except Exception:
        log.exception("_notify_exit failed — close already committed, "
                      "operator can grep DB by position_id=%s",
                      row.get("position_id"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("reconcile_exits")


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Orphan exit recovery — scan Alpaca for SELLs whose DB stamp was lost
# ---------------------------------------------------------------------------

# client_order_id format from exit_monitor.submit_close:
#   csb-exit-<position_id>-<reason>
# reason ∈ {stop, premium_stop, tp1_partial, tp2, time_stop, expiry, trail_stop}
_EXIT_CLIENT_ID_PREFIX = "csb-exit-"


def _parse_exit_client_order_id(client_order_id: str) -> tuple[int, str] | None:
    """Returns (position_id, reason) parsed from a 'csb-exit-N-reason' id,
    or None if it doesn't match the pattern. Bot-side defensive parser —
    Alpaca will pass through any string we sent."""
    if not client_order_id or not client_order_id.startswith(_EXIT_CLIENT_ID_PREFIX):
        return None
    suffix = client_order_id[len(_EXIT_CLIENT_ID_PREFIX):]
    parts = suffix.split("-", 1)
    if len(parts) != 2:
        return None
    try:
        pid = int(parts[0])
    except ValueError:
        return None
    return pid, parts[1]


def recover_orphan_exit_sells(conn, client) -> int:
    """Backfill positions.sell_order_id / tp1_sell_order_id for SELLs that
    are live at Alpaca but missing from the DB.

    The submit-close → DB-stamp pair in exit_monitor.process_one is not
    atomic. A SIGKILL between submit_close (which returns from Alpaca with
    a real order id) and stamp_full_close / stamp_tp1_partial leaves the
    DB with sell_order_id IS NULL while Alpaca has a live SELL. Next
    exit_monitor tick re-detects the exit condition and re-submits — but
    Alpaca rejects the duplicate client_order_id, so exit_monitor logs an
    ERROR and the position is stuck open while the original SELL runs to
    completion at Alpaca. Money parks at the broker.

    This helper queries Alpaca for OPEN orders with our csb-exit-* prefix,
    parses position_id and reason from each client_order_id, and stamps
    the corresponding DB column if it's still NULL. Idempotent: an
    already-stamped row is left alone.

    Returns the number of rows backfilled (for logging)."""
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        req = GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500)
        orders = client.get_orders(filter=req)
    except Exception:
        log.exception("recover_orphan_exit_sells: Alpaca order list failed")
        return 0

    recovered = 0
    for order in orders:
        cid = getattr(order, "client_order_id", None) or ""
        parsed = _parse_exit_client_order_id(cid)
        if parsed is None:
            continue
        pid, reason = parsed
        order_id = str(order.id)
        submitted_at = getattr(order, "submitted_at", None)

        # tp1_partial → tp1_sell_order_id column; everything else →
        # sell_order_id column. Match the column exit_monitor would have
        # stamped if it hadn't crashed.
        if reason == "tp1_partial":
            target_col = "tp1_sell_order_id"
            extras = """
                  AND tp1_filled_at IS NULL
                  AND tp1_sell_order_id IS NULL
            """
        else:
            target_col = "sell_order_id"
            extras = "AND sell_order_id IS NULL"

        with conn.cursor() as cur:
            cur.execute(
                f"""UPDATE trading.positions
                       SET {target_col}     = %s,
                           exit_submitted_at = COALESCE(exit_submitted_at, %s),
                           exit_reason       = COALESCE(exit_reason, %s)
                     WHERE id = %s
                       AND status = 'open'
                       {extras}
                    RETURNING id""",
                (order_id, submitted_at, reason, pid),
            )
            if cur.fetchone() is not None:
                recovered += 1
                log.warning(
                    "Recovered orphan SELL → positions.id=%s %s=%s reason=%s",
                    pid, target_col, order_id, reason,
                )
    conn.commit()
    if recovered:
        log.info("recover_orphan_exit_sells: backfilled %d row(s)", recovered)
    return recovered


_PENDING_EXIT_SELECT = """
    SELECT p.id AS position_id, p.asset_id, p.direction AS pos_direction,
           p.entry_price, p.quantity, p.sell_order_id, p.exit_submitted_at,
           p.exit_reason, p.opened_at,
           s.symbol, s.strategy, s.direction, s.option_symbol
      FROM trading.positions p
      LEFT JOIN market.signal_alerts s ON s.position_id = p.id
     WHERE p.status = 'open'
       AND p.sell_order_id IS NOT NULL
"""


def fetch_pending_exits(conn, position_id: int | None, limit: int) -> list[dict]:
    """Plain (unlocked) batch fetch — retained for read-only inspection / tests.
    The live main loop uses fetch_one_pending_exit_locked instead.
    """
    sql = _PENDING_EXIT_SELECT
    params: list = []
    if position_id is not None:
        sql += " AND p.id = %s"
        params.append(position_id)
    sql += " ORDER BY p.exit_submitted_at ASC NULLS LAST LIMIT %s"
    params.append(limit)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def fetch_one_pending_exit_locked(
    conn, position_id: int | None, exclude_ids: list[int] | None = None,
) -> dict | None:
    """Lock the next single open position with a SELL in flight.

    FOR UPDATE OF p SKIP LOCKED guarantees two overlapping reconcile_exits
    runs see DIFFERENT rows — otherwise both could call mark_closed's
    COALESCE+= or partial_close_sell's quantity-= on the same row and
    double-count P&L / under-count quantity.

    exclude_ids: rows already visited in THIS run. After commit, the lock
    is released; if reconcile_one's outcome leaves the row in a state that
    still matches the WHERE predicates (shouldn't happen in steady-state
    but possible on the dry-run + WARN paths), we'd refetch it
    indefinitely.
    """
    sql = _PENDING_EXIT_SELECT
    params: list = []
    if position_id is not None:
        sql += " AND p.id = %s"
        params.append(position_id)
    if exclude_ids:
        sql += " AND p.id <> ALL(%s)"
        params.append(list(exclude_ids))
    sql += " ORDER BY p.exit_submitted_at ASC NULLS LAST LIMIT 1 FOR UPDATE OF p SKIP LOCKED"
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def mark_closed(
    conn, position_id: int, closed_at: datetime, realized_pnl: Decimal,
) -> None:
    """Close the position and flip the originating signal_alerts row to
    status='exited' (the documented terminal lifecycle state from migration
    020_alert_lifecycle). Both updates run in the caller's transaction —
    the caller commits.

    realized_pnl is ADDED to any previously-recorded P&L on this position
    (a closing SELL that only partially filled before cancellation will
    have already accumulated some P&L into realized_pnl via
    partial_close_sell). For the common case where the closing SELL fills
    fully on the first attempt, realized_pnl was NULL and COALESCE(...,0)+x
    is identical to `= x`."""
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE trading.positions
                  SET status       = 'closed',
                      closed_at    = %s,
                      realized_pnl = COALESCE(realized_pnl, 0) + %s
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


def partial_close_sell(
    conn, position_id: int,
    filled_qty: Decimal, realized_pnl: Decimal,
) -> None:
    """A closing SELL only partially filled (typically a cancel-with-partial
    or, defensively, status='filled' with filled_qty < quantity). Record
    realized P&L on the filled slice, decrement quantity, and wipe sell
    stamps so exit_monitor retries the residual on the next pass.

    Note this does NOT close the position — quantity is reduced and the row
    remains status='open' so the monitor's WHERE p.status='open' still
    matches. signal_alerts.status stays 'filled' (the position is still
    partially live)."""
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE trading.positions
                  SET quantity          = quantity - %s,
                      realized_pnl      = COALESCE(realized_pnl, 0) + %s,
                      sell_order_id     = NULL,
                      exit_submitted_at = NULL,
                      exit_reason       = NULL
                WHERE id = %s""",
            (filled_qty, realized_pnl, position_id),
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


_PENDING_TP1_SELECT = """
    SELECT p.id AS position_id, p.asset_id, p.direction AS pos_direction,
           p.entry_price, p.quantity, p.tp1_sell_order_id,
           p.tp1_hit_at, p.exit_reason, p.opened_at,
           s.symbol, s.strategy, s.direction, s.option_symbol
      FROM trading.positions p
      LEFT JOIN market.signal_alerts s ON s.position_id = p.id
     WHERE p.status = 'open'
       AND p.tp1_sell_order_id IS NOT NULL
       AND p.tp1_filled_at IS NULL
"""


def fetch_pending_tp1_partials(
    conn, position_id: int | None, limit: int,
) -> list[dict]:
    """Plain (unlocked) batch fetch — for tests / inspection only. The live
    main loop uses fetch_one_pending_tp1_locked instead."""
    sql = _PENDING_TP1_SELECT
    params: list = []
    if position_id is not None:
        sql += " AND p.id = %s"
        params.append(position_id)
    sql += " ORDER BY p.tp1_hit_at ASC NULLS LAST LIMIT %s"
    params.append(limit)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def fetch_one_pending_tp1_locked(
    conn, position_id: int | None, exclude_ids: list[int] | None = None,
) -> dict | None:
    """Lock the next single open position with a TP1 partial SELL in flight.
    Same FOR UPDATE OF p SKIP LOCKED + seen-set pattern as
    fetch_one_pending_exit_locked. Without this, concurrent reconcile_exits
    runs could both apply mark_tp1_partial_filled, decrementing
    positions.quantity twice (real money-loss of accounting)."""
    sql = _PENDING_TP1_SELECT
    params: list = []
    if position_id is not None:
        sql += " AND p.id = %s"
        params.append(position_id)
    if exclude_ids:
        sql += " AND p.id <> ALL(%s)"
        params.append(list(exclude_ids))
    sql += " ORDER BY p.tp1_hit_at ASC NULLS LAST LIMIT 1 FOR UPDATE OF p SKIP LOCKED"
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchone()


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

    pos_qty = rc._to_decimal(row["quantity"]) or Decimal("0")
    entry_price = rc._to_decimal(row["entry_price"])
    is_option = bool(row.get("option_symbol"))

    # --- Dead (canceled / rejected / expired / done_for_day) ---
    if status in rc.DEAD_STATUSES:
        # Alpaca can report a partial fill alongside a terminal cancel:
        # filled_qty=2 out of 3 then canceled. If we just clear the sell
        # stamps without recording those 2, the next exit_monitor pass
        # sees positions.quantity=3 and submits another SELL for 3 —
        # double-selling the 2 that already executed at Alpaca.
        if filled_qty > 0 and filled_avg is not None and filled_avg > 0:
            realized = compute_realized_pnl(
                is_option=is_option,
                pos_direction=row["pos_direction"],
                entry_price=entry_price,
                exit_price=filled_avg,
                qty=filled_qty,
            )
            if filled_qty >= pos_qty:
                # All of it filled before cancel — treat as a full close.
                if dry_run:
                    return (f"position #{pid} DRY-RUN would close "
                            f"(fully filled before {status}) — "
                            f"qty={filled_qty} realized_pnl={realized}")
                try:
                    mark_closed(conn, pid, datetime.now(timezone.utc), realized)
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    log.exception("position #%s DB error closing", pid)
                    return (f"position #{pid} WARN — DB error during close: "
                            f"{type(e).__name__}: {e}")
                # Notify AFTER commit — never on a rolled-back close.
                _notify_exit(
                    row, reason=row.get("exit_reason") or status,
                    exit_price=filled_avg, qty=filled_qty,
                    realized_pnl=realized, is_partial=False, residual_qty=None,
                )
                sign = "+" if realized >= 0 else ""
                log.info("position #%s CLOSED — fully filled before %s exit=%s "
                         "qty=%s realized_pnl=%s%s",
                         pid, status, filled_avg, filled_qty, sign, realized)
                return (f"position #{pid} CLOSED — fully filled before {status} "
                        f"exit={filled_avg} qty={filled_qty} "
                        f"realized_pnl={sign}{realized}")
            # Partial fill before cancel — record what filled, retry the rest.
            residual = pos_qty - filled_qty
            if dry_run:
                return (f"position #{pid} DRY-RUN would partial-close "
                        f"(partial before {status}) — "
                        f"filled={filled_qty}/{pos_qty} residual={residual}")
            try:
                partial_close_sell(conn, pid, filled_qty, realized)
                conn.commit()
            except Exception as e:
                conn.rollback()
                log.exception("position #%s DB error on partial close", pid)
                return (f"position #{pid} WARN — DB error during partial close: "
                        f"{type(e).__name__}: {e}")
            _notify_exit(
                row, reason=f"partial_before_{status}",
                exit_price=filled_avg, qty=filled_qty,
                realized_pnl=realized, is_partial=True, residual_qty=residual,
            )
            sign = "+" if realized >= 0 else ""
            log.info("position #%s PARTIAL — %s after %s/%s @ %s realized=%s%s",
                     pid, status, filled_qty, pos_qty, filled_avg, sign, realized)
            return (f"position #{pid} PARTIAL — {status} after {filled_qty}/{pos_qty} "
                    f"@ {filled_avg} realized_pnl={sign}{realized}, "
                    f"residual {residual} will retry")
        # Cancel with zero fills — wipe stamps and retry next pass.
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

    realized = compute_realized_pnl(
        is_option=is_option,
        pos_direction=row["pos_direction"],
        entry_price=entry_price,
        exit_price=filled_avg,
        qty=filled_qty,
    )

    # Defensive: Alpaca reports status='filled' with filled_qty < submitted
    # qty rarely (if ever), but if it does, closing the row here would leave
    # the residual long at Alpaca with DB saying closed. Treat as partial.
    if pos_qty > 0 and filled_qty < pos_qty:
        residual = pos_qty - filled_qty
        if dry_run:
            return (f"position #{pid} DRY-RUN would partial-close "
                    f"(filled<qty) — filled={filled_qty}/{pos_qty} "
                    f"residual={residual}")
        try:
            partial_close_sell(conn, pid, filled_qty, realized)
            conn.commit()
        except Exception as e:
            conn.rollback()
            log.exception("position #%s DB error on partial close (filled)", pid)
            return (f"position #{pid} WARN — DB error during partial close: "
                    f"{type(e).__name__}: {e}")
        _notify_exit(
            row, reason="partial_filled_lt_qty",
            exit_price=filled_avg, qty=filled_qty,
            realized_pnl=realized, is_partial=True, residual_qty=residual,
        )
        sign = "+" if realized >= 0 else ""
        log.warning("position #%s PARTIAL — alpaca filled but %s/%s @ %s",
                    pid, filled_qty, pos_qty, filled_avg)
        return (f"position #{pid} PARTIAL — filled={filled_qty}/{pos_qty} "
                f"@ {filled_avg} realized_pnl={sign}{realized}, "
                f"residual {residual} will retry")

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

    _notify_exit(
        row, reason=row.get("exit_reason") or "exit",
        exit_price=filled_avg, qty=filled_qty,
        realized_pnl=realized, is_partial=False, residual_qty=None,
    )
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

    pos_qty = rc._to_decimal(row.get("quantity")) or Decimal("0")
    try:
        mark_tp1_partial_filled(conn, pid, filled_qty, filled_avg, realized)
        conn.commit()
    except Exception as e:
        conn.rollback()
        log.exception("position #%s DB error recording TP1 partial", pid)
        return (f"position #{pid} WARN — DB error during TP1 partial: "
                f"{type(e).__name__}: {e}")

    residual = pos_qty - filled_qty if pos_qty > 0 else None
    _notify_exit(
        row, reason="tp1_partial",
        exit_price=filled_avg, qty=filled_qty,
        realized_pnl=realized, is_partial=True, residual_qty=residual,
    )
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
        client = rc._alpaca_client()
    except Exception as e:
        log.error("Could not build Alpaca client: %s", e)
        conn.close()
        return 2

    # Reap orphan exit SELLs (Alpaca-live, DB-missing) BEFORE the main loop.
    # exit_monitor's submit→stamp pair isn't atomic; a crash between them
    # leaves the SELL live at Alpaca and the position stuck open with
    # sell_order_id IS NULL. The Alpaca scan backfills the missing stamp
    # so the normal reconcile path picks it up this tick.
    if args.id is None and not args.dry_run:
        try:
            recover_orphan_exit_sells(conn, client)
        except Exception:
            log.exception("recover_orphan_exit_sells failed — continuing")
            try:
                conn.rollback()
            except Exception:
                pass

    results: list[str] = []
    # One-at-a-time fetch under FOR UPDATE OF p SKIP LOCKED. The PER-ROW
    # lock holds across the inner reconcile_*'s Alpaca lookup + DB writes
    # and is released by the inner commit/rollback, so a second cron run
    # sees DIFFERENT rows. A previous batch-fetch implementation released
    # all locks after the first row's commit, leaving the remaining batch
    # racy.
    seen_full: list[int] = []
    seen_partial: list[int] = []
    try:
        while len(results) < args.limit:
            try:
                row = fetch_one_pending_exit_locked(conn, args.id, seen_full)
            except Exception:
                log.exception("Lock fetch (full) failed; aborting full pass")
                conn.rollback()
                break
            if row is None:
                conn.commit()
                break
            seen_full.append(row["position_id"])
            try:
                results.append(reconcile_one(conn, client, row, args.dry_run, args.verbose))
            except Exception:
                log.exception("Unhandled error reconciling position #%s",
                              row["position_id"])
                results.append(f"position #{row['position_id']} ERROR — internal")
                try:
                    conn.rollback()
                except Exception:
                    pass

        while len(results) < args.limit + len(seen_full):
            try:
                row = fetch_one_pending_tp1_locked(conn, args.id, seen_partial)
            except Exception:
                log.exception("Lock fetch (TP1 partial) failed; aborting partial pass")
                conn.rollback()
                break
            if row is None:
                conn.commit()
                break
            seen_partial.append(row["position_id"])
            try:
                results.append(
                    reconcile_partial_one(conn, client, row, args.dry_run, args.verbose)
                )
            except Exception:
                log.exception("Unhandled error reconciling TP1 partial on position #%s",
                              row["position_id"])
                results.append(f"position #{row['position_id']} ERROR — TP1 partial internal")
                try:
                    conn.rollback()
                except Exception:
                    pass

        if not results:
            if args.id is not None:
                print(f"No open position with id={args.id} and a SELL in flight "
                      f"(or all are locked by another reconcile_exits run).")
            else:
                print("No SELL orders to reconcile.")
            return 0

        log.info("Reconciled %d row(s)%s", len(results),
                 " (DRY RUN)" if args.dry_run else "")
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
