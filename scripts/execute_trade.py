#!/usr/bin/env python3
"""execute_trade.py — Phase 5B real order submission.

Picks up market.signal_alerts WHERE status='approved' AND executed_at IS NULL
and submits orders to Alpaca paper.

Default behavior is **dry-run** (no orders, no DB writes — same output as
process_approved.py). Real submission requires the explicit `--confirm`
flag. paper=True is hard-coded; this script will not place live orders.

Lifecycle per row (with --confirm):
    1. SELECT ... FOR UPDATE the row, verify still status='approved'.
    2. Run preflight; CHECK_FAIL aborts the row (CHECK_WARN does not).
    3. UPDATE status='executing' and COMMIT (releases the row lock so a
       second invocation can't double-submit).
    4. Submit order to Alpaca.
    5. On success: UPDATE alpaca_order_id, executed_at  (keep status='executing').
       A future reconcile_orders.py will flip executing → filled when the
       fill arrives and populate position_id.
    6. On failure: UPDATE status='error', error_message.

Usage:
    python scripts/execute_trade.py                       # DRY RUN
    python scripts/execute_trade.py --confirm             # really submit
    python scripts/execute_trade.py --id 7 --confirm
    python scripts/execute_trade.py --limit 3 --confirm
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

# Reuse every helper from the dry-run logger — single source of truth for
# env loading, mode mapping, sizing, preflight, and the human-readable plan.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import process_approved as pa  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("execute_trade")


# ---------------------------------------------------------------------------
# Alpaca submission
# ---------------------------------------------------------------------------

def _alpaca_client():
    """Lazy-import alpaca-py so --dry-run users don't need it installed."""
    from alpaca.trading.client import TradingClient
    cfg = pa.load_env("alpaca")
    key = cfg.get("ALPACA_PAPER_API_KEY") or os.environ.get("ALPACA_PAPER_API_KEY")
    sec = cfg.get("ALPACA_PAPER_SECRET_KEY") or os.environ.get("ALPACA_PAPER_SECRET_KEY")
    if not key or not sec:
        raise RuntimeError("Missing ALPACA_PAPER_API_KEY / _SECRET_KEY in .env.alpaca")
    # paper=True is non-negotiable in this script; live trading lives elsewhere.
    return TradingClient(api_key=key, secret_key=sec, paper=True)


def submit_to_alpaca(client, signal: dict, sizing: dict, mode: str) -> dict:
    """Submit one order. Returns {'order_id', 'submitted_price', 'order_type', 'tif'}.

    Raises on submission failure — caller decides whether to record 'error'.
    """
    from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    qty = sizing["qty"]
    direction = signal["direction"]
    # For options the side is always BUY (you buy a call for bullish, a put
    # for bearish — the option_symbol encodes which). For stocks, bullish→BUY,
    # bearish→SELL (short).
    if sizing["instrument"] == "option":
        side = OrderSide.BUY
    else:
        side = OrderSide.BUY if direction == "bullish" else OrderSide.SELL

    if sizing["instrument"] == "option":
        sym = signal["option_symbol"]
        ask = pa._to_decimal(signal.get("option_ask")) or pa._to_decimal(signal.get("option_mid"))
        if ask is None or ask <= 0:
            raise ValueError("option has no ask/mid price to cross")
        req = LimitOrderRequest(
            symbol=sym,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,
            limit_price=float(ask),
        )
        order = client.submit_order(req)
        return {
            "order_id": str(order.id),
            "submitted_price": Decimal(str(ask)),
            "order_type": "limit",
            "tif": "day",
            "symbol": sym,
        }
    else:
        sym = signal["symbol"]
        req = MarketOrderRequest(
            symbol=sym,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,
        )
        order = client.submit_order(req)
        return {
            "order_id": str(order.id),
            "submitted_price": None,  # market order — fill price comes via reconcile
            "order_type": "market",
            "tif": "day",
            "symbol": sym,
        }


# ---------------------------------------------------------------------------
# DB state transitions
# ---------------------------------------------------------------------------

def lock_and_mark_executing(conn, signal_id: int) -> dict | None:
    """SELECT FOR UPDATE the row, flip to 'executing', commit. Returns the
    row dict if we won the race, None if it was already taken / not approved.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT * FROM market.signal_alerts
                WHERE id = %s
                  AND status = 'approved'
                  AND executed_at IS NULL
                FOR UPDATE""",
            (signal_id,),
        )
        row = cur.fetchone()
        if row is None:
            conn.rollback()
            return None
        cur.execute(
            """UPDATE market.signal_alerts
                  SET status = 'executing'
                WHERE id = %s""",
            (signal_id,),
        )
        conn.commit()
    return row


def record_execution(
    conn, signal_id: int, order_id: str, submitted_price: Decimal | None,
) -> None:
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE market.signal_alerts
                  SET alpaca_order_id = %s,
                      executed_at     = %s,
                      error_message   = NULL
                WHERE id = %s""",
            (order_id, now, signal_id),
        )
    conn.commit()


def record_error(conn, signal_id: int, message: str) -> None:
    """Move a row from executing → error and store the failure reason.

    Trimmed to fit reasonably in error_message (TEXT can hold more, but
    long stack traces aren't useful here — the log has the full detail).
    """
    msg = (message or "")[:1000]
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE market.signal_alerts
                  SET status = 'error',
                      error_message = %s
                WHERE id = %s""",
            (msg, signal_id),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Hard-fail gates (preflight CHECK_FAIL + extras specific to live submission)
# ---------------------------------------------------------------------------

def hard_fail_reason(signal: dict, sizing: dict, checks: list[tuple[str, str]]) -> str | None:
    """Return a string explaining why the row must be skipped, or None to proceed.

    Preflight WARN entries do not block — the user already approved the alert
    knowing those numbers. FAIL entries block.
    """
    for status, msg in checks:
        if status == pa.CHECK_FAIL:
            return msg

    # Stock shorts are off-limits in this slice. Paper supports them but the
    # tooling around margin / locate / buying-power isn't built out, and
    # nearly every bearish strategy in this repo attaches a PUT option.
    if sizing["instrument"] == "stock" and signal["direction"] == "bearish":
        return "stock-short not supported in this slice (no option_symbol on a bearish signal)"

    if sizing["qty"] <= 0:
        return f"qty sized to 0 ({sizing.get('limiting_factor', '?')} bound)"

    return None


# ---------------------------------------------------------------------------
# Main per-row flow
# ---------------------------------------------------------------------------

def execute_one(conn, client, signal: dict, equity: Decimal, verbose: bool) -> str:
    """With --confirm: actually submit. Returns a human-readable status line."""
    sid = signal["id"]
    risk_mode = signal.get("risk_mode") or "standard"
    mode = pa.infer_trade_mode(signal.get("strategy"), signal.get("timeframe"),
                                risk_mode=risk_mode)

    opt_mid = pa._to_decimal(signal.get("option_mid"))
    if opt_mid and opt_mid > 0:
        sizing = pa.size_option_position(equity, mode, opt_mid, risk_mode=risk_mode)
    else:
        entry = pa._to_decimal(signal.get("trigger_price"))
        stop  = pa._to_decimal(signal.get("stop_price"))
        if entry is None or stop is None:
            return f"#{sid} SKIP — no trigger_price/stop_price for stock fallback"
        sizing = pa.size_stock_position(equity, mode, entry, stop, signal["direction"],
                                        risk_mode=risk_mode)

    checks = pa.preflight(signal, sizing, mode, conn=conn, equity=equity)
    blocker = hard_fail_reason(signal, sizing, checks)
    if blocker:
        log.warning("#%s SKIP — %s", sid, blocker)
        if verbose:
            print(pa.render_plan(signal, mode, equity, sizing, checks, verbose=True))
        return f"#{sid} {signal['symbol']} SKIP — {blocker}"

    # Race-safe transition to 'executing'.
    locked = lock_and_mark_executing(conn, sid)
    if locked is None:
        return f"#{sid} {signal['symbol']} SKIP — row no longer approved (race lost)"

    # Submit.
    try:
        result = submit_to_alpaca(client, locked, sizing, mode)
    except Exception as e:
        log.exception("Alpaca submission failed for signal #%s", sid)
        record_error(conn, sid, f"{type(e).__name__}: {e}")
        return f"#{sid} {signal['symbol']} ERROR — {type(e).__name__}: {e}"

    record_execution(conn, sid, result["order_id"], result["submitted_price"])
    price_str = (f" @ ${result['submitted_price']}"
                 if result["submitted_price"] is not None else "")
    log.info(
        "#%s %s %s %s x%s (%s%s) → order_id=%s",
        sid, locked["symbol"], result["order_type"], result["symbol"],
        sizing["qty"], result["tif"], price_str, result["order_id"],
    )
    return (f"#{sid} {locked['symbol']} SUBMITTED — "
            f"{result['order_type']} {result['symbol']} x{sizing['qty']}{price_str} "
            f"→ order_id={result['order_id']}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true",
                        help="Actually submit orders. Without this, behaves like "
                             "process_approved.py (dry-run only, no DB writes).")
    parser.add_argument("--id", type=int, default=None,
                        help="Process a single signal_alerts row by id.")
    parser.add_argument("--limit", type=int, default=1,
                        help="Max rows to act on (default 1 — safer with --confirm).")
    parser.add_argument("--no-alpaca", action="store_true",
                        help="Skip live Alpaca equity lookup; use the $100K default. "
                             "Equity is still queried with --confirm because real "
                             "sizing needs the real number — this flag affects dry "
                             "runs only.")
    parser.add_argument("--verbose", action="store_true",
                        help="Show passing preflight checks too.")
    args = parser.parse_args()

    if args.confirm and args.no_alpaca:
        # With --confirm we MUST size off real equity. Refuse the combination
        # rather than silently using the default and over/under-sizing.
        log.error("--no-alpaca is incompatible with --confirm (need real equity to size).")
        return 2

    equity = pa.DEFAULT_EQUITY if args.no_alpaca else pa.get_alpaca_equity()

    conn = pa.get_connection()
    try:
        rows = pa.fetch_approved(conn, args.id, args.limit)
    except Exception:
        conn.close()
        raise

    if not rows:
        if args.id is not None:
            print(f"No signal_alerts row with id={args.id}.")
        else:
            print("No approved signals waiting for execution.")
        conn.close()
        return 0

    if not args.confirm:
        # Dry-run: identical output to process_approved.py.
        print(f"DRY RUN — {len(rows)} signal(s).  Equity: ${equity:,.2f}\n")
        for r in rows:
            print(pa.process_one(r, equity, args.verbose))
            print()
        print(f"DRY RUN — no orders submitted, no DB rows modified. "
              f"Pass --confirm to execute.")
        conn.close()
        return 0

    # --confirm path.
    log.info("LIVE PAPER EXECUTION — %d row(s).  Equity: $%s", len(rows), f"{equity:,.2f}")
    client = _alpaca_client()
    results: list[str] = []
    try:
        for r in rows:
            try:
                results.append(execute_one(conn, client, r, equity, args.verbose))
            except Exception:
                # Defensive: a bug in execute_one shouldn't take down the whole loop.
                log.exception("Unhandled error processing signal #%s", r["id"])
                try:
                    record_error(conn, r["id"], "internal error — see logs")
                except Exception:
                    log.exception("Could not even record_error for #%s", r["id"])
                results.append(f"#{r['id']} {r['symbol']} ERROR — internal")
    finally:
        conn.close()

    print()
    for line in results:
        print(line)
    submitted = sum(1 for line in results if "SUBMITTED" in line)
    errored   = sum(1 for line in results if "ERROR" in line)
    skipped   = sum(1 for line in results if "SKIP" in line)
    print(f"\nDONE — submitted={submitted}, error={errored}, skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
