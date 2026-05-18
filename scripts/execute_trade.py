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
from alert_telegram import send_telegram_message  # noqa: E402

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

    A deterministic client_order_id (`csb-entry-<signal_id>`) is attached so
    that a process crash between Alpaca submit and DB commit doesn't lead
    to a duplicate BUY on the next reconcile pass — Alpaca rejects the
    second submission with a duplicate-client-order-id error.
    """
    from alpaca.trading.requests import (
        LimitOrderRequest, MarketOrderRequest,
        TakeProfitRequest, StopLossRequest,
    )
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

    qty = sizing["qty"]
    direction = signal["direction"]
    client_order_id = f"csb-entry-{signal['id']}"

    # For options the side is always BUY (you buy a call for bullish, a put
    # for bearish — the option_symbol encodes which). For stocks, bullish→BUY,
    # bearish→SELL (short).
    if sizing["instrument"] == "option":
        side = OrderSide.BUY
    else:
        side = OrderSide.BUY if direction == "bullish" else OrderSide.SELL

    if sizing["instrument"] == "option":
        sym = signal["option_symbol"]
        # Submit at mid (or option_mid fallback) instead of ask. Crossing the
        # full ask guarantees the worst fill; mid gives room for negotiation
        # and a non-fill on a wide spread is information — the trade isn't
        # economic at those prices and we shouldn't be in it. The
        # MAX_SPREAD_PCT preflight in process_approved.py prevents stupid-wide
        # spreads from reaching here in the first place.
        bid = pa._to_decimal(signal.get("option_bid"))
        ask = pa._to_decimal(signal.get("option_ask"))
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            limit = (bid + ask) / Decimal("2")
        else:
            limit = pa._to_decimal(signal.get("option_mid"))
        if limit is None or limit <= 0:
            raise ValueError("option has no bid/ask/mid price to compute limit")
        # Round to a penny — Alpaca rejects sub-penny limit prices on options.
        limit = limit.quantize(Decimal("0.01"))
        req = LimitOrderRequest(
            symbol=sym,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,
            limit_price=float(limit),
            client_order_id=client_order_id,
        )
        order = client.submit_order(req)
        return {
            "order_id": str(order.id),
            "submitted_price": Decimal(str(limit)),
            "order_type": "limit",
            "tif": "day",
            "symbol": sym,
        }
    else:
        # Stock entry — submit as BRACKET so the stop_loss + take_profit legs
        # live at Alpaca even if exit_monitor goes down. The parent is a
        # market BUY (bullish only here; hard_fail blocks stock-short).
        # Children:
        #   - SELL stop  @ signal.stop_price  (loss cap; matches exit_monitor 'stop')
        #   - SELL limit @ signal.tp2_price   (profit cap; matches exit_monitor 'tp2')
        # exit_monitor still owns TP1 partials and trail logic (Alpaca brackets
        # only have ONE take-profit leg, so the partial-then-trail-then-final
        # ladder can't be encoded in the bracket alone). When exit_monitor
        # decides to exit early it MUST cancel the bracket legs first
        # (see exit_monitor.cancel_open_orders_for_symbol).
        # Bracket is bullish-only on Alpaca for short equities; bearish stock
        # entries are already blocked upstream by hard_fail_reason.
        sym = signal["symbol"]
        stop_price = pa._to_decimal(signal.get("stop_price"))
        tp2_price  = pa._to_decimal(signal.get("tp2_price"))

        if (stop_price is not None and stop_price > 0
                and tp2_price is not None and tp2_price > 0
                and direction == "bullish"
                and tp2_price > stop_price):
            req = MarketOrderRequest(
                symbol=sym,
                qty=qty,
                side=side,
                time_in_force=TimeInForce.DAY,
                client_order_id=client_order_id,
                order_class=OrderClass.BRACKET,
                stop_loss=StopLossRequest(stop_price=float(stop_price)),
                take_profit=TakeProfitRequest(limit_price=float(tp2_price)),
            )
            order_type_label = "market_bracket"
        else:
            # Missing stop/tp or non-standard direction — fall back to plain
            # market. exit_monitor remains the only line of defense; no
            # broker-side stop is in place. Log loudly so the operator notices.
            log.warning(
                "Stock entry for #%s %s missing stop/tp or bearish — "
                "submitting plain market (no bracket safety net): "
                "stop=%s tp2=%s dir=%s",
                signal["id"], sym, stop_price, tp2_price, direction,
            )
            req = MarketOrderRequest(
                symbol=sym,
                qty=qty,
                side=side,
                time_in_force=TimeInForce.DAY,
                client_order_id=client_order_id,
            )
            order_type_label = "market"

        order = client.submit_order(req)
        return {
            "order_id": str(order.id),
            "submitted_price": None,  # market order — fill price comes via reconcile
            "order_type": order_type_label,
            "tif": "day",
            "symbol": sym,
        }


# ---------------------------------------------------------------------------
# Risk-mode-aware option re-selection (M1)
# ---------------------------------------------------------------------------

def reselect_option_for_risk_mode(
    conn, signal: dict, risk_mode: str,
) -> dict:
    """If risk_mode requires a different delta band than 'standard' and the
    signal carries an option contract, re-query Alpaca for the best matching
    option in that band and overwrite the option_* fields on signal_alerts.

    Returns the (possibly-updated) signal dict.

    Why this exists: the scanner binds a 0.50–0.70 delta contract at scan
    time. The user picks risk mode AFTER (Approve / Conservative / Aggressive
    in Telegram). Conservative wants a tighter delta (0.55–0.65, higher
    probability) and aggressive wants a wider band (0.40–0.80, more
    leverage). Without this re-selection every approval submits the same
    scanner-chosen contract regardless of risk mode — audit M1.

    Standard is the no-op case: the scanner's delta band already matches
    standard's 0.50–0.70, so we keep the row's existing option_* fields and
    save the Alpaca round-trip.

    If the re-selection fails (no contract in the new band, Alpaca down,
    etc.) we LOG and fall back to the scanner's original contract — better
    to execute the standard-band trade than refuse to trade at all.
    """
    if risk_mode == "standard" or not signal.get("option_symbol"):
        return signal

    try:
        import fetch_alpaca_snapshot as fas
    except Exception:
        log.exception("reselect_option_for_risk_mode: import fas failed; "
                      "falling back to scanner-chosen option")
        return signal

    want_type = "C" if signal.get("direction") == "bullish" else "P"
    sym = signal["symbol"]
    try:
        best = fas.select_best_option(
            sym, want_type=want_type, min_dte=fas.MIN_DTE,
            max_dte=120, risk_mode=risk_mode,
        )
    except Exception:
        log.exception("reselect_option_for_risk_mode: Alpaca query failed for %s; "
                      "falling back to scanner-chosen option", sym)
        return signal

    if not best:
        log.warning("reselect_option_for_risk_mode: no %s contract in %s band "
                    "for %s; keeping scanner option %s",
                    want_type, risk_mode, sym, signal.get("option_symbol"))
        return signal

    if best["occ_symbol"] == signal.get("option_symbol"):
        # Same contract — scanner already picked the right one.
        return signal

    # Different contract — persist the swap. The scanner's stored
    # option_strike/option_expiry/option_delta/option_theta/option_bid/
    # option_ask/option_mid all get overwritten so reconcile_exits and
    # exit_monitor see the contract actually traded.
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE market.signal_alerts
                  SET option_symbol = %s,
                      option_strike = %s,
                      option_expiry = %s,
                      option_delta  = %s,
                      option_theta  = %s,
                      option_bid    = %s,
                      option_ask    = %s,
                      option_mid    = %s,
                      spread_pct    = %s
                WHERE id = %s""",
            (best["occ_symbol"], best["strike"], best["expiry"],
             best["delta"], best["theta"], best["bid"], best["ask"],
             best["mid"], best.get("spread_pct"), signal["id"]),
        )
    conn.commit()

    log.info(
        "Re-selected option for #%s (%s): %s (Δ%.2f) → %s (Δ%.2f) [risk_mode=%s]",
        signal["id"], sym,
        signal.get("option_symbol"), float(signal.get("option_delta") or 0),
        best["occ_symbol"], best["delta"], risk_mode,
    )

    # Reflect the swap on the in-memory dict so the caller's sizing /
    # preflight / submit_to_alpaca see the new contract.
    signal["option_symbol"] = best["occ_symbol"]
    signal["option_strike"] = best["strike"]
    signal["option_expiry"] = best["expiry"]
    signal["option_delta"]  = best["delta"]
    signal["option_theta"]  = best["theta"]
    signal["option_bid"]    = best["bid"]
    signal["option_ask"]    = best["ask"]
    signal["option_mid"]    = best["mid"]
    signal["spread_pct"]    = best.get("spread_pct")
    return signal


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
        # Stamp executed_at HERE (not in record_execution). The orphan
        # reaper recover_orphan_executing requires executed_at to be set so
        # it can age rows past the 5-min grace window. Without this stamp,
        # a SIGKILL between this UPDATE and record_execution leaves a row
        # with status='executing' AND alpaca_order_id IS NULL AND
        # executed_at IS NULL — invisible to BOTH fetch_executing (filters
        # by alpaca_order_id) AND recover_orphan_executing (filters by
        # executed_at), so it rots forever.
        # record_execution still UPDATEs executed_at to the post-submit
        # timestamp on success — that's fine and slightly more accurate
        # (the column is "when Alpaca confirmed the BUY"), but for
        # recovery purposes any non-NULL stamp old enough works.
        cur.execute(
            """UPDATE market.signal_alerts
                  SET status      = 'executing',
                      executed_at = NOW()
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
# Telegram confirmation notifications
# ---------------------------------------------------------------------------

def notify_execution(result: dict, signal: dict, *, suppress: bool = False) -> None:
    """Send a Telegram confirmation after order submission/error/skip.

    Fires for every execution attempt so the user sees what happened
    without checking Alpaca or the DB. Best-effort: failures to send
    are logged but never block the execution loop.

    If suppress=True, the per-signal notification is skipped — used for
    drawdown halts where the main loop sends one consolidated message
    instead of N duplicates.
    """
    if suppress:
        log.info("notify_execution: suppressed per-signal notification for %s (consolidated message sent by caller)",
                 signal.get("symbol", "?"))
        return

    try:
        cfg = pa.load_env("telegram")
        token = cfg.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = cfg.get("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            log.warning("notify_execution: missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID — skipping notification")
            return

        status = result.get("status", "unknown")
        sym = signal.get("symbol") or "—"
        direction = signal.get("direction") or "—"
        direction_emoji = {"bullish": "📈", "bearish": "📉"}.get(direction, "📊")
        strat = signal.get("strategy") or "—"
        option_sym = signal.get("option_symbol") or ""

        if status == "submitted":
            # Build the confirmation message with real execution details
            lines = [
                f"✅ <b>ORDER SUBMITTED</b>",
                f"{direction_emoji} {sym} {direction}",
            ]
            if option_sym:
                lines.append(f"📋 Contract: {option_sym}")
            qty = result.get("qty", "?")
            lines.append(f"📦 Qty: {qty}")

            submitted_price = result.get("submitted_price")
            if submitted_price is not None:
                cost = Decimal(str(submitted_price)) * Decimal(str(qty)) if isinstance(qty, (int, float)) and qty else None
                lines.append(f"💰 Limit: ${submitted_price}")
                if cost:
                    lines.append(f"💵 Cost: ${cost:,.2f}")

            order_id = result.get("order_id", "?")
            lines.append(f"🔑 Order: {order_id[:8]}…")
            lines.append(f"📋 Strategy: {strat}")

            # Risk/reward context from the signal
            risk_reward = signal.get("risk_reward")
            stop = signal.get("stop_price")
            tp1 = signal.get("tp1_price")
            tp2 = signal.get("tp2_price")
            if risk_reward:
                lines.append(f"📊 R:R {risk_reward}:1")
            if stop:
                lines.append(f"🛑 Stop: ${stop}")
            if tp1:
                lines.append(f"🎯 TP1: ${tp1}")

            text = "\n".join(lines)

        elif status == "error":
            lines = [
                f"❌ <b>ORDER FAILED</b>",
                f"{direction_emoji} {sym} {direction}",
                f"⚠️ {result.get('message', 'Unknown error')}",
            ]
            text = "\n".join(lines)

        elif status == "skipped":
            lines = [
                f"⏭️ <b>ORDER SKIPPED</b>",
                f"{direction_emoji} {sym} {direction}",
                f"⚠️ {result.get('reason', 'Unknown reason')}",
            ]
            text = "\n".join(lines)

        else:
            lines = [
                f"ℹ️ <b>ORDER {status.upper()}</b>",
                f"{direction_emoji} {sym} {direction}",
            ]
            text = "\n".join(lines)

        send_telegram_message(token, chat_id, text, allowed_chat_id=chat_id)

    except Exception:
        # Never let a notification failure block the execution path
        log.exception("notify_execution: failed to send Telegram notification — continuing")


# ---------------------------------------------------------------------------
# Main per-row flow
# ---------------------------------------------------------------------------

def execute_one(conn, client, signal: dict, equity: Decimal, verbose: bool) -> dict:
    """With --confirm: actually submit. Returns a result dict with keys:
       status: 'submitted' | 'skipped' | 'error'
       message: human-readable status line
       plus status-specific keys like order_id, submitted_price, qty, etc.
    """
    sid = signal["id"]
    risk_mode = signal.get("risk_mode") or "standard"
    mode = pa.infer_trade_mode(signal.get("strategy"), signal.get("timeframe"),
                                risk_mode=risk_mode)

    # M1: re-select the option contract for non-standard risk modes BEFORE
    # sizing, so the qty / preflight / submit all reflect the contract we
    # actually trade. No-op for standard mode and for stock-only signals.
    signal = reselect_option_for_risk_mode(conn, signal, risk_mode)

    opt_mid = pa._to_decimal(signal.get("option_mid"))
    if opt_mid and opt_mid > 0:
        sizing = pa.size_option_position(equity, mode, opt_mid, risk_mode=risk_mode)
    else:
        entry = pa._to_decimal(signal.get("trigger_price"))
        stop  = pa._to_decimal(signal.get("stop_price"))
        if entry is None or stop is None:
            msg = f"no trigger_price/stop_price for stock fallback"
            notify_execution({"status": "skipped", "reason": msg}, signal)
            return {"status": "skipped", "message": f"#{sid} SKIP — {msg}", "reason": msg}
        sizing = pa.size_stock_position(equity, mode, entry, stop, signal["direction"],
                                        risk_mode=risk_mode)

    checks = pa.preflight(signal, sizing, mode, conn=conn, equity=equity)
    blocker = hard_fail_reason(signal, sizing, checks)
    if blocker:
        log.warning("#%s SKIP — %s", sid, blocker)
        if verbose:
            print(pa.render_plan(signal, mode, equity, sizing, checks, verbose=True))
        # Drawdown halts get a consolidated notification from the main loop,
        # not per-signal duplicates.  Check the reason string so we can
        # flag it without sending a duplicate Telegram message here.
        reason = blocker
        is_drawdown = "Drawdown halt" in reason
        notify_execution({"status": "skipped", "reason": reason}, signal, suppress=is_drawdown)
        return {"status": "skipped", "message": f"#{sid} {signal['symbol']} SKIP — {reason}", "reason": reason, "is_drawdown": is_drawdown}

    # Race-safe transition to 'executing'.
    locked = lock_and_mark_executing(conn, sid)
    if locked is None:
        msg = "row no longer approved (race lost)"
        notify_execution({"status": "skipped", "reason": msg}, signal)
        return {"status": "skipped", "message": f"#{sid} {signal['symbol']} SKIP — {msg}", "reason": msg}

    # Submit.
    try:
        result = submit_to_alpaca(client, locked, sizing, mode)
    except Exception as e:
        log.exception("Alpaca submission failed for signal #%s", sid)
        record_error(conn, sid, f"{type(e).__name__}: {e}")
        err_msg = f"{type(e).__name__}: {e}"
        notify_execution({"status": "error", "message": err_msg}, signal)
        return {"status": "error", "message": f"#{sid} {signal['symbol']} ERROR — {err_msg}"}

    record_execution(conn, sid, result["order_id"], result["submitted_price"])
    price_str = (f" @ ${result['submitted_price']}"
                 if result["submitted_price"] is not None else "")
    log.info(
        "#%s %s %s %s x%s (%s%s) → order_id=%s",
        sid, locked["symbol"], result["order_type"], result["symbol"],
        sizing["qty"], result["tif"], price_str, result["order_id"],
    )
    notify_result = {
        "status": "submitted",
        "order_id": result["order_id"],
        "submitted_price": float(result["submitted_price"]) if result["submitted_price"] else None,
        "order_type": result["order_type"],
        "qty": sizing["qty"],
        "symbol": result["symbol"],
    }
    notify_execution(notify_result, signal)
    msg = (f"#{sid} {locked['symbol']} SUBMITTED — "
           f"{result['order_type']} {result['symbol']} x{sizing['qty']}{price_str} "
           f"→ order_id={result['order_id']}")
    return {"status": "submitted", "message": msg, **notify_result}


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
    results: list[dict] = []
    drawdown_halt_notified = False
    try:
        for r in rows:
            try:
                result = execute_one(conn, client, r, equity, args.verbose)
            except Exception:
                # Defensive: a bug in execute_one shouldn't take down the whole loop.
                log.exception("Unhandled error processing signal #%s", r["id"])
                try:
                    record_error(conn, r["id"], "internal error — see logs")
                except Exception:
                    log.exception("Could not even record_error for #%s", r["id"])
                result = {"status": "error", "message": f"#{r['id']} {r['symbol']} ERROR — internal"}

            # Suppress per-signal duplicate "drawdown halt" notifications.
            # If multiple signals are all blocked by drawdown, send ONE
            # consolidated message instead of N individual "SKIPPED" alerts.
            if result.get("status") == "skipped" and result.get("is_drawdown"):
                if not drawdown_halt_notified:
                    notify_execution(
                        {"status": "skipped",
                         "reason": f"Drawdown halt — all {len(rows)} order(s) blocked. "
                                   f"Account equity ${equity:,.2f}"},
                        r,
                    )
                    drawdown_halt_notified = True

            results.append(result)
    finally:
        conn.close()

    print()
    for r in results:
        print(r.get("message", str(r)))
    submitted = sum(1 for r in results if r.get("status") == "submitted")
    errored   = sum(1 for r in results if r.get("status") == "error")
    skipped   = sum(1 for r in results if r.get("status") == "skipped")
    print(f"\nDONE — submitted={submitted}, error={errored}, skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
