#!/usr/bin/env python3
"""Telegram Alert Sender — Phase 5A

Reads new (unsent) signals from market.signal_alerts, formats them as
strategy-specific trade alerts matching the Phase 5A design, and sends
them via Telegram Bot API.

Usage:
    python alert_telegram.py                  # Send all unsent alerts
    python alert_telegram.py --strategy ema    # Only EMA crossover alerts
    python alert_telegram.py --dry-run         # Print alerts without sending
"""
import os
import sys
import json
import html as html_mod
import logging
from decimal import Decimal
from pathlib import Path
from datetime import datetime, timezone

import psycopg2
import urllib.request
import urllib.error

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from constants import MAX_SPREAD_PCT, AUTO_APPROVE_STRATEGIES  # noqa: E402


def _format_quote_line(opt_bid, opt_ask, opt_mid, spread_pct) -> str:
    """Build the 'Quote: bid X / ask Y | mid Z | spread W%' line.

    spread_pct is the (ask - bid) / mid fraction at signal time. We red-flag
    anything above MAX_SPREAD_PCT (0.15 today) so the user immediately sees
    when a stale or thin contract slipped past — preflight will block it,
    but seeing it in the alert avoids approval whiplash.
    """
    mid_str = f" | mid ${float(opt_mid):.2f}" if opt_mid is not None else ""
    if spread_pct is not None:
        pct = float(spread_pct) * 100
        if float(spread_pct) > MAX_SPREAD_PCT:
            spread_str = f" | spread {pct:.1f}% 🚩"
        else:
            spread_str = f" | spread {pct:.1f}%"
    else:
        spread_str = ""
    return (f"Quote: bid ${float(opt_bid):.2f} / ask ${float(opt_ask):.2f}"
            f"{mid_str}{spread_str}")

# ---------------------------------------------------------------------------
# DB connection
# ---------------------------------------------------------------------------

def get_connection():
    env_path = Path("/app/.env.db")
    conn_params = {}
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                conn_params[k.strip()] = v.strip()
    return psycopg2.connect(
        host=conn_params.get("POSTGRES_HOST", "postgres"),
        user=conn_params.get("POSTGRES_USER", "clawstreet"),
        password=conn_params.get("POSTGRES_PASSWORD", ""),
        dbname=conn_params.get("POSTGRES_DB", "clawstreet"),
    )


# ---------------------------------------------------------------------------
# Telegram Bot API
# ---------------------------------------------------------------------------

def get_telegram_config():
    """Load Telegram bot token and chat ID from .env.telegram."""
    env_path = Path("/app/.env.telegram")
    config = {}
    if not env_path.exists():
        # Try alternate path
        env_path = Path("/app/config/.env.telegram")
    if not env_path.exists():
        log.error("No .env.telegram file found")
        return config

    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                config[k.strip()] = v.strip()
    return config


def build_deny_only_keyboard(signal_id: int) -> dict:
    """Single-button keyboard for auto-approved alerts.

    Auto-approved strategies (ORB, liquidity_sweep) are already in the
    execution queue by the time the alert arrives — the Deny button is a
    last-chance veto that only succeeds before Alpaca confirms the BUY.
    Past that point, the callback handler reports "too late" without
    canceling the order.
    """
    return {
        "inline_keyboard": [
            [{"text": "❌ Deny (veto auto-approve)", "callback_data": f"deny:{signal_id}"}],
        ]
    }


def build_approval_keyboard(signal_id: int) -> dict:
    """Inline keyboard with Approve / Deny buttons for a signal_alerts row.

    Row 1: ✅ Approve (standard sizing) | ❌ Deny
    Row 2: 🔵 Conservative (half risk)  | 🟡 Aggressive (2x risk)

    callback_data format (read by telegram_callback_listener.py):
        approve:<id>:standard | approve:<id>:conservative | approve:<id>:aggressive | deny:<id>
    """
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Approve", "callback_data": f"approve:{signal_id}:standard"},
                {"text": "❌ Deny", "callback_data": f"deny:{signal_id}"},
            ],
            [
                {"text": "🔵 Conservative", "callback_data": f"approve:{signal_id}:conservative"},
                {"text": "🟡 Aggressive", "callback_data": f"approve:{signal_id}:aggressive"},
            ],
        ]
    }


def send_telegram_message(
    token: str,
    chat_id: str,
    text: str,
    allowed_chat_id: str = "",
    reply_markup: dict | None = None,
) -> dict | None:
    """Send a message via Telegram Bot API. Returns the response JSON or None.

    Security: only sends to the allowed_chat_id. Any other chat_id
    is rejected with a warning log. This prevents the bot from being
    used to send alerts to unauthorized chats.
    """
    # Security gate: only allow the authorized chat ID
    if allowed_chat_id and str(chat_id) != str(allowed_chat_id):
        log.warning("BLOCKED: attempt to send to unauthorized chat_id=%s (allowed=%s)", chat_id, allowed_chat_id)
        return None

    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else ""
        log.error("Telegram API error %d: %s", e.code, body[:200])
        return None
    except Exception as e:
        log.error("Telegram send failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Telegram message edits (keyboard removal + error surfacing)
# ---------------------------------------------------------------------------

def _telegram_edit(
    token: str, method: str, payload: dict, allowed_chat_id: str = "",
) -> dict | None:
    """Generic editMessage* helper. Honors the same chat allowlist as
    send_telegram_message."""
    chat_id = payload.get("chat_id")
    if allowed_chat_id and str(chat_id) != str(allowed_chat_id):
        log.warning("BLOCKED: edit attempt to unauthorized chat_id=%s", chat_id)
        return None
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else ""
        # 'message is not modified' is harmless — Telegram returns 400 if the
        # edit doesn't change anything (e.g. keyboard already cleared).
        if e.code == 400 and "not modified" in body:
            return {"ok": True, "no_change": True}
        log.error("Telegram %s error %d: %s", method, e.code, body[:200])
        return None
    except Exception as e:
        log.error("Telegram %s failed: %s", method, e)
        return None


def clear_message_keyboard(
    token: str, chat_id: str, message_id: int, allowed_chat_id: str = "",
) -> dict | None:
    """Remove the inline keyboard from a previously-sent alert. Used when
    expiring stale 'new' rows so the user can't tap a dead button."""
    return _telegram_edit(
        token, "editMessageReplyMarkup",
        {"chat_id": chat_id, "message_id": int(message_id), "reply_markup": {}},
        allowed_chat_id=allowed_chat_id,
    )


def edit_message_text(
    token: str, chat_id: str, message_id: int, text: str,
    allowed_chat_id: str = "",
) -> dict | None:
    """Replace the text of a previously-sent alert message (drops keyboard).
    Used to surface a status='error' failure in-place on the original
    alert."""
    return _telegram_edit(
        token, "editMessageText",
        {
            "chat_id": chat_id,
            "message_id": int(message_id),
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        allowed_chat_id=allowed_chat_id,
    )


# ---------------------------------------------------------------------------
# Lifecycle: expirer + error surfacer
# ---------------------------------------------------------------------------
# Per-strategy TTL (minutes) — imported from constants.py so the same
# values are available to telegram_callback_listener.py and the scanners.
from constants import SIGNAL_TTL_MINUTES, DEFAULT_SIGNAL_TTL_MINUTES


def _ttl_line(strategy: str) -> str:
    """Return a one-liner showing when this alert expires."""
    ttl = SIGNAL_TTL_MINUTES.get(strategy, DEFAULT_SIGNAL_TTL_MINUTES)
    if ttl >= 60:
        hours, mins = divmod(ttl, 60)
        if mins:
            return f"⏱ Expires in {hours}h {mins}m"
        return f"⏱ Expires in {hours}h"
    return f"⏱ Expires in {ttl}m"


def expire_stale_new(conn, token: str | None, chat_id: str | None) -> int:
    """Flip status='new' rows past their strategy-specific TTL to 'expired'.

    Each strategy has a freshness window (SIGNAL_TTL_MINUTES in constants.py):
    ORB signals expire after 60 min, 15m EMA crosses after 60 min, setup
    scanner after 120 min, etc. After the window elapses, the signal is stale
    — the market conditions that triggered it have changed, option quotes
    have moved, and executing on it would be contra-competitive.

    Also strips the inline keyboard from each expiring row's original
    message so the user can't tap a dead button. Keyboard removal failure
    does not block the DB flip — Telegram message_id may be missing or the
    message itself deleted; the state machine still needs to advance.

    Returns the number of rows expired (for logging)."""
    total_expired = 0
    strategies = set(SIGNAL_TTL_MINUTES.keys()) | {DEFAULT_SIGNAL_TTL_MINUTES}

    with conn.cursor() as cur:
        # Strategy-specific expiry: each strategy gets its own TTL window.
        for strategy, ttl_minutes in SIGNAL_TTL_MINUTES.items():
            cur.execute(
                """UPDATE market.signal_alerts
                      SET status = 'expired'
                    WHERE status = 'new'
                      AND strategy = %s
                      AND created_at < NOW() - make_interval(mins => %s)
                    RETURNING id, telegram_msg_id""",
                (strategy, ttl_minutes),
            )
            rows = cur.fetchall()
            if rows and token and chat_id:
                for sid, msg_id in rows:
                    if msg_id:
                        clear_message_keyboard(token, chat_id, msg_id,
                                               allowed_chat_id=chat_id)
            if rows:
                log.info("Expired %d stale '%s' signal(s) (> %d min old)",
                         len(rows), strategy, ttl_minutes)
            total_expired += len(rows)

        # Default TTL: catch any strategy not in the explicit dict.
        # Build the NOT IN clause from keys already processed above.
        known = list(SIGNAL_TTL_MINUTES.keys())
        cur.execute(
            """UPDATE market.signal_alerts
                  SET status = 'expired'
                WHERE status = 'new'
                  AND (strategy NOT IN %s OR strategy IS NULL)
                  AND created_at < NOW() - make_interval(mins => %s)
                RETURNING id, telegram_msg_id""",
            (tuple(known) if known else ("__none__",), DEFAULT_SIGNAL_TTL_MINUTES),
        )
        default_rows = cur.fetchall()
        if default_rows and token and chat_id:
            for sid, msg_id in default_rows:
                if msg_id:
                    clear_message_keyboard(token, chat_id, msg_id,
                                           allowed_chat_id=chat_id)
        if default_rows:
            log.info("Expired %d stale signal(s) (> %d min default TTL)",
                     len(default_rows), DEFAULT_SIGNAL_TTL_MINUTES)
        total_expired += len(default_rows)

    conn.commit()
    log.info("Total expired: %d signal_alerts row(s)", total_expired)
    return total_expired


def notify_errors(conn, token: str | None, chat_id: str | None, limit: int = 20) -> int:
    """Surface status='error' rows to Telegram by editing the original
    alert message in-place.

    Selects rows with error_notified_at IS NULL (migration 028) so each
    error is surfaced exactly once. The edit replaces the trade plan with
    an error notice and drops the keyboard. Rows without a telegram_msg_id
    (errors raised before the alert ever sent) are marked notified
    immediately to keep the queue from filling — the operator can grep
    logs by signal id.

    Returns the count of rows processed.

    One-row-at-a-time fetch + per-row commit. The batch-fetch
    FOR UPDATE SKIP LOCKED + single-commit-at-end pattern was unsafe:
      (a) the lock window stretched across all Telegram edits (up to 20×
          15s = 5 min on a network outage), starving concurrent crons;
      (b) an exception mid-loop rolled back every error_notified_at
          UPDATE that had succeeded earlier, so the next tick re-edited
          the same error messages with no progress recorded.
    Per-row commit fixes both — lock window is one edit call, and each
    success is durable independently."""
    notified = 0
    while notified < limit:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, symbol, strategy, direction, error_message,
                          telegram_msg_id
                     FROM market.signal_alerts
                    WHERE status = 'error'
                      AND error_notified_at IS NULL
                    ORDER BY id ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED""",
            )
            row = cur.fetchone()
            if row is None:
                conn.commit()
                break

            sid, sym, strat, direction, err_msg, msg_id = row
            err_text = (err_msg or "(no message)")[:500]
            text = (
                f"❌ <b>ERROR — {sym} {strat} {direction}</b>\n"
                f"<i>signal #{sid}</i>\n\n"
                f"<code>{err_text}</code>"
            )
            if token and chat_id and msg_id:
                edit_message_text(token, chat_id, msg_id, text,
                                  allowed_chat_id=chat_id)
            cur.execute(
                """UPDATE market.signal_alerts
                      SET error_notified_at = NOW()
                    WHERE id = %s""",
                (sid,),
            )
        # Per-row commit releases the FOR UPDATE lock + persists progress
        # so an exception on the NEXT row doesn't roll this one back.
        conn.commit()
        notified += 1

    if notified:
        log.info("Surfaced %d error row(s) to Telegram", notified)
    return notified


# ---------------------------------------------------------------------------
# Exit-fill notifications — fire-and-forget from reconcile_exits
# ---------------------------------------------------------------------------

# Exit reasons emit different lead emojis so the chat is glance-readable.
# Wins (TP hits, profitable trails) → 💰; losses / risk-management exits → ⛔;
# neutral mechanics (time stop, expiry, partial fills) → 📤.
_EXIT_EMOJI = {
    "stop":          "⛔",
    "premium_stop":  "⛔",
    "trail_stop":    "💰",
    "tp2":           "💰",
    "tp1_partial":   "📤",   # 50% close, position still open
    "time_stop":     "📤",
    "expiry":        "📤",
}


def format_exit_notification(
    *, symbol: str, strategy: str, direction: str, reason: str,
    entry_price: Decimal | float, exit_price: Decimal | float,
    qty: Decimal | float, realized_pnl: Decimal | float,
    is_option: bool, is_partial: bool = False, residual_qty: Decimal | float | None = None,
) -> str:
    """Build the Telegram message body for a position exit (full close or
    partial). Pure formatter — no I/O — so the caller controls when to
    actually send (must be AFTER the DB commit, so we never notify on a
    rolled-back close)."""
    from decimal import Decimal as D
    entry = D(str(entry_price)) if entry_price is not None else D("0")
    exit_ = D(str(exit_price)) if exit_price is not None else D("0")
    qty_d = D(str(qty)) if qty is not None else D("0")
    pnl = D(str(realized_pnl)) if realized_pnl is not None else D("0")

    emoji = _EXIT_EMOJI.get(reason, "📤")
    sign = "+" if pnl >= 0 else "-"
    pnl_str = f"{sign}${abs(pnl):,.2f}"

    # % move on the underlying / option premium for the closed slice.
    # For shorts (rare in this bot) we'd want (entry - exit) / entry — but
    # the realized_pnl already has the sign baked in by compute_realized_pnl,
    # so we just use that for the percent label on the close direction.
    if entry > 0:
        pct = (exit_ - entry) / entry * D("100")
        if direction != "bullish":
            pct = -pct
        pct_str = f"{'+' if pct >= 0 else ''}{pct:.1f}%"
    else:
        pct_str = "—"

    if is_partial and reason == "tp1_partial":
        verb = "💰 TP1 PARTIAL"
        emoji = "📤"
    elif is_partial:
        verb = "⚠️ PARTIAL CLOSE"
    elif reason == "stop" or reason == "premium_stop":
        verb = f"{emoji} EXITED (loss)"
    else:
        verb = f"{emoji} EXITED"

    lines = [
        f"{verb} — <b>{symbol}</b> {direction}",
        f"<i>via {strategy} · reason: {reason}</i>",
        f"",
        f"Entry: ${entry:,.2f} → Exit: ${exit_:,.2f} ({pct_str} on "
        f"{'option premium' if is_option else 'underlying'})",
        f"Quantity: {qty_d}{' contracts' if is_option else ' shares'}",
        f"Realized P&L: <b>{pnl_str}</b>",
    ]
    if is_partial and residual_qty is not None:
        lines.append(f"Residual still open: {D(str(residual_qty))}"
                     f"{' contracts' if is_option else ' shares'}")
    return "\n".join(lines)


def send_exit_notification(text: str) -> None:
    """Fire-and-forget notification. Loads creds from .env.telegram and
    sends. Any failure is logged but NEVER raises — reconcile_exits must
    not roll back a close because Telegram is down."""
    try:
        cfg = get_telegram_config()
        token = cfg.get("TELEGRAM_BOT_TOKEN")
        chat_id = cfg.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            log.warning("send_exit_notification: missing TELEGRAM_BOT_TOKEN/CHAT_ID")
            return
        send_telegram_message(token, chat_id, text, allowed_chat_id=chat_id)
    except Exception:
        log.exception("send_exit_notification failed — close already committed")


# ---------------------------------------------------------------------------
# Alert formatters (per strategy)
# ---------------------------------------------------------------------------

def _trend_english(val):
    """Convert trend value to plain English."""
    if val == "bull":
        return "up"
    elif val == "bear":
        return "down"
    return "flat"

def format_15m_crossover_alert(signal: dict) -> str:
    """Format a 15m EMA crossover signal — intraday entry with daily trend filter."""
    symbol = signal["symbol"]
    direction = signal["direction"]
    price = signal["trigger_price"]
    stop = signal["stop_price"]
    tp1 = signal["tp1_price"]
    tp2 = signal["tp2_price"]
    rr = signal["risk_reward"]
    adx = signal["adx"]
    regime = signal["regime"]

    emoji = "🟢" if direction == "bullish" else "🔴"
    action = "BUY" if direction == "bullish" else "SELL/PUT"

    # --- Header ---
    lines = [
        f"{emoji} <b>{symbol} — {action} Signal (15m)</b>",
        f"{'─' * 30}",
        f"Strategy: EMA 9/21 Crossover (intraday) | Timeframe: {signal.get('timeframe', '15m')}",
    ]

    # --- Trade Plan ---
    risk_dollars = price - stop if direction == "bullish" else stop - price
    reward_dollars = tp1 - price if direction == "bullish" else price - tp1
    rr_check = "✅" if rr >= 3 else "⚠️"
    lines.append(f"Entry: ${price:.2f} | Stop: ${stop:.2f} | Target: ${tp1:.2f} / ${tp2:.2f}")
    lines.append(f"Risk ${risk_dollars:.2f} → Reward ${reward_dollars:.2f} ({rr:.1f}:1) {rr_check}")

    # --- Why this signal fired ---
    cross_dir = "above" if direction == "bullish" else "below"
    daily_pos = signal.get("daily_ema_position", "?")
    daily_label = "up" if daily_pos == "above" else "down"
    lines.append(f"\n15-min EMA crossed {cross_dir} 21-period — trend forming (ADX {adx:.0f}).")
    lines.append(f"Daily trend: EMA9 {'above' if daily_pos == 'above' else 'below'} EMA21 → daily {daily_label} ✅")

    # Trend alignment in plain English
    micro = _trend_english(signal.get("micro_trend", "?"))
    inter = _trend_english(signal.get("intermediate_trend", "?"))
    prim = _trend_english(signal.get("primary_trend", "?"))
    regime_emoji = "🟢" if regime == "bull" else ("🔴" if regime == "bear" else "⚪")
    lines.append(f"Short-term {micro}, mid-term {inter}, long-term {prim}. Market regime: {regime_emoji} {regime}")

    # --- How to play it ---
    opt_sym = signal.get("option_symbol")
    if opt_sym:
        opt_strike = signal.get("option_strike", 0)
        opt_expiry = signal.get("option_expiry", "?")
        opt_delta = signal.get("option_delta", 0)
        contract_type = "C" if direction == "bullish" else "P"
        lines.append(f"\nSuggested: {symbol} ${opt_strike:.0f}{contract_type} exp {opt_expiry} (Δ{opt_delta:.2f})")
        opt_bid = signal.get("option_bid")
        opt_ask = signal.get("option_ask")
        opt_mid = signal.get("option_mid")
        if opt_bid is not None and opt_ask is not None:
            lines.append(_format_quote_line(opt_bid, opt_ask, opt_mid,
                                            signal.get("spread_pct")))

    # --- Vol & Gamma context ---
    context_bits = []
    iv_rank = signal.get("iv_rank")
    if iv_rank is not None:
        if iv_rank < 30:
            context_bits.append(f"IV rank {iv_rank:.0f}% → cheap premium, good time to buy options")
        elif iv_rank < 50:
            context_bits.append(f"IV rank {iv_rank:.0f}% → moderate premium")
        else:
            context_bits.append(f"IV rank {iv_rank:.0f}% → expensive premium (consider selling)")
    iv_rv = signal.get("iv_rv_spread")
    if iv_rv is not None:
        if iv_rv < -0.15:
            context_bits.append(f"Options cheap vs actual vol ({iv_rv:+.2f}) — good time to buy")
        elif iv_rv > 0.15:
            context_bits.append(f"Options pricey vs actual vol ({iv_rv:+.2f}) — consider credit spreads")
        else:
            context_bits.append(f"IV vs RV fairly priced ({iv_rv:+.2f})")
    net_gex = signal.get("net_gex")
    if net_gex is not None:
        if net_gex > 0:
            context_bits.append(f"Dealers long gamma (${net_gex:,.0f}) → price likely sticks near strikes")
        else:
            context_bits.append(f"Dealers short gamma (${net_gex:,.0f}) → expect wider moves")
    if context_bits:
        lines.append("")
        lines.append("<b>Vol & Gamma:</b>")
        for bit in context_bits:
            lines.append(f"  • {bit}")

    # --- When to bail ---
    invalidation = signal.get("invalidation")
    if invalidation:
        if isinstance(invalidation, str):
            invalidation = json.loads(invalidation)
        lines.append("")
        lines.append("<b>Bail if:</b>")
        for cond in invalidation:
            lines.append(f"  ⛔ {html_mod.escape(str(cond))}")

    lines.append(_ttl_line("ema_crossover_15m"))
    return "\n".join(lines)


def format_ema_crossover_alert(signal: dict) -> str:
    """Format an EMA crossover signal as a clean, readable trade alert."""
    symbol = signal["symbol"]
    direction = signal["direction"]
    price = signal["trigger_price"]
    stop = signal["stop_price"]
    tp1 = signal["tp1_price"]
    tp2 = signal["tp2_price"]
    rr = signal["risk_reward"]
    adx = signal["adx"]
    regime = signal["regime"]

    emoji = "🟢" if direction == "bullish" else "🔴"
    action = "BUY" if direction == "bullish" else "SELL/PUT"

    # --- Header ---
    lines = [
        f"{emoji} <b>{symbol} — {action} Signal</b>",
        f"{'─' * 30}",
        f"Strategy: EMA 9/21 Crossover (daily) | Timeframe: {signal.get('timeframe', '1d')}",
    ]

    # --- Trade Plan (the most important numbers upfront) ---
    risk_dollars = price - stop if direction == "bullish" else stop - price
    reward_dollars = tp1 - price if direction == "bullish" else price - tp1
    rr_check = "✅" if rr >= 3 else "⚠️"
    lines.append(f"Entry: ${price:.2f} | Stop: ${stop:.2f} | Target: ${tp1:.2f} / ${tp2:.2f}")
    lines.append(f"Risk ${risk_dollars:.2f} → Reward ${reward_dollars:.2f} ({rr:.1f}:1) {rr_check}")

    # --- Why this signal fired ---
    cross_dir = "above" if direction == "bullish" else "below"
    lines.append(f"\n9-day moving average crossed {cross_dir} 21-day — trend forming (ADX {adx:.0f}).")

    # Trend alignment in plain English
    micro = _trend_english(signal.get("micro_trend", "?"))
    inter = _trend_english(signal.get("intermediate_trend", "?"))
    prim = _trend_english(signal.get("primary_trend", "?"))
    regime_emoji = "🟢" if regime == "bull" else ("🔴" if regime == "bear" else "⚪")
    lines.append(f"Short-term {micro}, mid-term {inter}, long-term {prim}. Market regime: {regime_emoji} {regime}")

    # --- How to play it ---
    opt_sym = signal.get("option_symbol")
    if opt_sym:
        opt_strike = signal.get("option_strike", 0)
        opt_expiry = signal.get("option_expiry", "?")
        opt_delta = signal.get("option_delta", 0)
        contract_type = "C" if direction == "bullish" else "P"
        lines.append(f"\nSuggested: {symbol} ${opt_strike:.0f}{contract_type} exp {opt_expiry} (Δ{opt_delta:.2f})")
        opt_bid = signal.get("option_bid")
        opt_ask = signal.get("option_ask")
        opt_mid = signal.get("option_mid")
        if opt_bid is not None and opt_ask is not None:
            lines.append(_format_quote_line(opt_bid, opt_ask, opt_mid,
                                            signal.get("spread_pct")))

    # --- Vol & Gamma context ---
    context_bits = []

    iv_rank = signal.get("iv_rank")
    if iv_rank is not None:
        if iv_rank < 30:
            context_bits.append(f"IV rank {iv_rank:.0f}% → cheap premium, good time to buy options")
        elif iv_rank < 50:
            context_bits.append(f"IV rank {iv_rank:.0f}% → moderate premium")
        else:
            context_bits.append(f"IV rank {iv_rank:.0f}% → expensive premium (consider selling)")

    iv_rv = signal.get("iv_rv_spread")
    if iv_rv is not None:
        if iv_rv < -0.15:
            context_bits.append(f"Options cheap vs actual vol ({iv_rv:+.2f}) — good time to buy")
        elif iv_rv > 0.15:
            context_bits.append(f"Options pricey vs actual vol ({iv_rv:+.2f}) — consider credit spreads")
        else:
            context_bits.append(f"IV vs RV fairly priced ({iv_rv:+.2f})")

    net_gex = signal.get("net_gex")
    if net_gex is not None:
        if net_gex > 0:
            context_bits.append(f"Dealers long gamma (${net_gex:,.0f}) → price likely sticks near strikes")
        else:
            context_bits.append(f"Dealers short gamma (${net_gex:,.0f}) → expect wider moves")

    if context_bits:
        lines.append("")
        lines.append("<b>Vol & Gamma:</b>")
        for bit in context_bits:
            lines.append(f"  • {bit}")

    # --- When to bail ---
    invalidation = signal.get("invalidation")
    if invalidation:
        if isinstance(invalidation, str):
            invalidation = json.loads(invalidation)
        lines.append("")
        lines.append("<b>Bail if:</b>")
        for cond in invalidation:
            lines.append(f"  ⛔ {html_mod.escape(str(cond))}")

    lines.append(_ttl_line("ema_crossover"))
    return "\n".join(lines)


def format_setup_scanner_alert(signal: dict) -> str:
    """Format an 8-gate setup scanner signal (scan_setups.py)."""
    symbol = signal["symbol"]
    direction = signal["direction"]
    price = signal["trigger_price"]
    stop = signal["stop_price"]
    tp1 = signal["tp1_price"]
    tp2 = signal["tp2_price"]
    rr = signal["risk_reward"]
    adx = signal["adx"]
    rsi = signal["rsi"]
    regime = signal["regime"]

    emoji = "🟢" if direction == "bullish" else "🔴"
    action = "BUY" if direction == "bullish" else "SHORT"

    # --- Header ---
    lines = [
        f"{emoji} <b>{action} Signal: {symbol}</b>",
        f"{'─' * 30}",
        f"Strategy: Setup Scanner (8-gate) | Timeframe: {signal.get('timeframe', '1d')}",
    ]

    # --- Composite + Regime ---
    composite = signal.get("composite_score")
    if composite is not None:
        lines.append(f"Composite: {float(composite):.1f}/100 | Regime: {regime}")
    else:
        lines.append(f"Regime: {regime}")

    # --- Price + Trend ---
    micro = _trend_english(signal.get("micro_trend", "?"))
    inter = _trend_english(signal.get("intermediate_trend", "?"))
    prim = _trend_english(signal.get("primary_trend", "?"))
    lines.append(f"Stock: ${float(price):.2f} | RSI: {float(rsi):.0f} | ADX: {float(adx):.0f}")
    lines.append(f"Trend: short {micro}, mid {inter}, long {prim}")

    # --- Trade Plan ---
    risk_dollars = float(price) - float(stop) if direction == "bullish" else float(stop) - float(price)
    reward_dollars = float(tp1) - float(price) if direction == "bullish" else float(price) - float(tp1)
    rr_check = "✅" if float(rr) >= 3 else "⚠️"
    lines.append(f"")
    lines.append(f"Entry: ${float(price):.2f} | Stop: ${float(stop):.2f} | Target: ${float(tp1):.2f} / ${float(tp2):.2f}")
    lines.append(f"Risk ${risk_dollars:.2f} → Reward ${reward_dollars:.2f} ({float(rr):.1f}:1) {rr_check}")

    # --- Option contract ---
    opt_sym = signal.get("option_symbol")
    if opt_sym:
        opt_strike = signal.get("option_strike", 0)
        opt_expiry = signal.get("option_expiry", "?")
        opt_delta = signal.get("option_delta", 0)
        opt_mid = signal.get("option_mid")
        contract_type = "C" if direction == "bullish" else "P"
        lines.append(f"")
        lines.append(f"Suggested: {symbol} ${float(opt_strike):.0f}{contract_type} exp {opt_expiry} (Δ{float(opt_delta):.2f})")
        opt_bid = signal.get("option_bid")
        opt_ask = signal.get("option_ask")
        if opt_bid is not None and opt_ask is not None:
            lines.append(_format_quote_line(opt_bid, opt_ask, opt_mid,
                                            signal.get("spread_pct")))

    # --- Vol & Gamma context ---
    context_bits = []
    iv_rank = signal.get("iv_rank")
    if iv_rank is not None:
        iv_rank = float(iv_rank)
        if iv_rank < 30:
            context_bits.append(f"IV rank {iv_rank:.0f}% → cheap premium, good time to buy options")
        elif iv_rank < 50:
            context_bits.append(f"IV rank {iv_rank:.0f}% → moderate premium")
        else:
            context_bits.append(f"IV rank {iv_rank:.0f}% → expensive premium (consider selling)")

    iv_rv = signal.get("iv_rv_spread")
    if iv_rv is not None:
        iv_rv = float(iv_rv)
        if iv_rv < -0.15:
            context_bits.append(f"Options cheap vs realized ({iv_rv:+.2f}) — good time to buy")
        elif iv_rv > 0.15:
            context_bits.append(f"Options pricey vs realized ({iv_rv:+.2f}) — consider credit spreads")
        else:
            context_bits.append(f"IV vs RV fairly priced ({iv_rv:+.2f})")

    net_gex = signal.get("net_gex")
    if net_gex is not None:
        net_gex = float(net_gex)
        if net_gex > 0:
            context_bits.append(f"Dealers long gamma (${net_gex:,.0f}) → price sticks near strikes")
        else:
            context_bits.append(f"Dealers short gamma (${net_gex:,.0f}) → expect wider moves")

    volume_ratio = signal.get("volume_ratio")
    if volume_ratio is not None:
        context_bits.append(f"Volume: {float(volume_ratio):.1f}x average")

    if context_bits:
        lines.append("")
        lines.append("<b>Context:</b>")
        for bit in context_bits:
            lines.append(f"  • {bit}")

    # --- Bail conditions ---
    invalidation = signal.get("invalidation")
    if invalidation:
        if isinstance(invalidation, str):
            try:
                invalidation = json.loads(invalidation)
            except (json.JSONDecodeError, TypeError):
                invalidation = None
        if invalidation:
            # Handle both list format and dict with "rules" key
            rules = invalidation if isinstance(invalidation, list) else invalidation.get("rules", [])
            if rules:
                lines.append("")
                lines.append("<b>Bail if:</b>")
                for rule in rules:
                    lines.append(f"  ⛔ {html_mod.escape(str(rule))}")

    lines.append(_ttl_line("setup_scanner"))
    return "\n".join(lines)


def format_intraday_signal_alert(signal: dict) -> str:
    """Format an intraday composite-score signal (intraday_signal.py)."""
    symbol = signal["symbol"]
    direction = signal["direction"]
    price = signal["trigger_price"]
    stop = signal["stop_price"]
    tp1 = signal["tp1_price"]
    tp2 = signal["tp2_price"]
    rr = signal["risk_reward"]
    rsi = signal.get("rsi")
    regime = signal.get("regime", "unknown")
    composite = signal.get("composite_score")

    emoji = "🟢" if direction == "bullish" else "🔴"
    action = "BUY" if direction == "bullish" else "SHORT"

    lines = [
        f"{emoji} <b>{action} Signal: {symbol}</b>",
        f"{'─' * 30}",
        f"Strategy: Intraday 5m | Regime: {regime}",
    ]
    if composite is not None:
        lines.append(f"Composite: {float(composite):.1f}/100")

    if rsi is not None:
        lines.append(f"Stock: ${float(price):.2f} | RSI: {float(rsi):.0f}")
    else:
        lines.append(f"Stock: ${float(price):.2f}")

    inter = _trend_english(signal.get("intermediate_trend", "?"))
    trend_score = signal.get("trend_score")
    if trend_score is not None:
        lines.append(f"Mid-term trend: {inter} (score {float(trend_score):.0f})")
    elif signal.get("intermediate_trend"):
        lines.append(f"Mid-term trend: {inter}")

    risk_dollars = float(price) - float(stop) if direction == "bullish" else float(stop) - float(price)
    reward_dollars = float(tp1) - float(price) if direction == "bullish" else float(price) - float(tp1)
    rr_check = "✅" if float(rr) >= 3 else "⚠️"
    lines.append("")
    lines.append(f"Entry: ${float(price):.2f} | Stop: ${float(stop):.2f} | Target: ${float(tp1):.2f} / ${float(tp2):.2f}")
    lines.append(f"Risk ${risk_dollars:.2f} → Reward ${reward_dollars:.2f} ({float(rr):.1f}:1) {rr_check}")

    vwap_ratio = signal.get("volume_ratio")
    if vwap_ratio is not None:
        lines.append(f"VWAP ratio: {float(vwap_ratio):.3f}")

    invalidation = signal.get("invalidation")
    if invalidation:
        if isinstance(invalidation, str):
            try:
                invalidation = json.loads(invalidation)
            except (json.JSONDecodeError, TypeError):
                invalidation = None
        if invalidation:
            rules = invalidation if isinstance(invalidation, list) else invalidation.get("rules", [])
            if rules:
                lines.append("")
                lines.append("<b>Bail if:</b>")
                for rule in rules:
                    lines.append(f"  ⛔ {html_mod.escape(str(rule))}")

    lines.append(_ttl_line("intraday_signal"))
    return "\n".join(lines)


def format_liquidity_sweep_alert(signal: dict) -> str:
    """Format a liquidity sweep signal (detect_liquidity_sweep.py)."""
    symbol = signal["symbol"]
    direction = signal["direction"]
    price = signal["trigger_price"]
    stop = signal["stop_price"]
    tp1 = signal["tp1_price"]
    tp2 = signal["tp2_price"]
    rr = signal["risk_reward"]

    emoji = "🟢" if direction == "bullish" else "🔴"
    action = "BUY" if direction == "bullish" else "SHORT"

    lines = [
        f"{emoji} <b>{action} Signal: {symbol}</b>",
        f"{'─' * 30}",
        f"Strategy: Liquidity Sweep | Timeframe: {signal.get('timeframe', '5m')}",
        f"Stock: ${float(price):.2f}",
    ]

    # ATR-based stop/target
    atr = signal.get("atr_14")
    if atr is not None:
        lines.append(f"ATR: ${float(atr):.2f}")

    risk_dollars = float(price) - float(stop) if direction == "bullish" else float(stop) - float(price)
    reward_dollars = float(tp1) - float(price) if direction == "bullish" else float(price) - float(tp1)
    rr_check = "✅" if float(rr) >= 3 else "⚠️"
    lines.append(f"")
    lines.append(f"Entry: ${float(price):.2f} | Stop: ${float(stop):.2f} | Target: ${float(tp1):.2f} / ${float(tp2):.2f}")
    lines.append(f"Risk ${risk_dollars:.2f} → Reward ${reward_dollars:.2f} ({float(rr):.1f}:1) {rr_check}")

    # Option contract
    opt_sym = signal.get("option_symbol")
    if opt_sym:
        opt_strike = signal.get("option_strike", 0)
        opt_expiry = signal.get("option_expiry", "?")
        opt_delta = signal.get("option_delta", 0)
        opt_mid = signal.get("option_mid")
        contract_type = "C" if direction == "bullish" else "P"
        lines.append(f"")
        lines.append(f"Suggested: {symbol} ${float(opt_strike):.0f}{contract_type} exp {opt_expiry} (Δ{float(opt_delta):.2f})")
        if opt_mid is not None:
            lines.append(f"Mid: ${float(opt_mid):.2f}")

    # Sweep-specific context
    lines.append("")
    lines.append("⚡ Close-beyond confirmation passed")
    lines.append("Backtest: PF 1.56 | WR 32.4% | avg +0.16R (259 trades)")

    lines.append(_ttl_line("liquidity_sweep"))
    return "\n".join(lines)


def format_orb_alert(signal: dict) -> str:
    """Format an ORB (Opening Range Breakout) signal (detect_orb.py).

    Shows the ORB range, breakout direction, ATR-based stops, and an
    external-level warning if the underlying is near yesterday's high/low
    (the strategy's main invalidation risk)."""
    symbol = signal["symbol"]
    direction = signal["direction"]
    price = signal["trigger_price"]
    stop = signal["stop_price"]
    tp1 = signal["tp1_price"]
    tp2 = signal["tp2_price"]
    rr = signal["risk_reward"]
    atr = signal.get("atr_14")

    emoji = "🟢" if direction == "bullish" else "🔴"
    action = "BUY" if direction == "bullish" else "SHORT"

    # Pull ORB-specific fields from the invalidation JSONB blob.
    invalidation = signal.get("invalidation")
    inv: dict = {}
    if invalidation:
        if isinstance(invalidation, str):
            try:
                inv = json.loads(invalidation)
            except (json.JSONDecodeError, TypeError):
                inv = {}
        elif isinstance(invalidation, dict):
            inv = invalidation

    orb_high = inv.get("orb_range_high")
    orb_low  = inv.get("orb_range_low")
    breakout_type = inv.get("breakout_type", "direct")
    near_pdh = bool(inv.get("near_prev_day_high"))
    near_pdl = bool(inv.get("near_prev_day_low"))

    lines = [
        f"{emoji} <b>{action} Signal: {symbol}</b>",
        f"{'─' * 30}",
        f"Strategy: ORB | Timeframe: {signal.get('timeframe', '5m')}",
        f"Stock: ${float(price):.2f}",
    ]

    if orb_high is not None and orb_low is not None:
        lines.append(
            f"ORB Range: ${float(orb_low):.2f}–${float(orb_high):.2f} "
            f"(width ${float(orb_high) - float(orb_low):.2f})"
        )
    cross_word = ("above ORB high" if direction == "bullish"
                  else "below ORB low")
    breakout_word = "retest entry" if breakout_type == "retest" else "direct breakout"
    lines.append(f"Breakout: 5-min close {cross_word} ({breakout_word})")

    if atr is not None:
        lines.append(f"ATR: ${float(atr):.2f}")

    # Trade plan (matches the rest of the scanner formatters).
    risk_dollars = (float(price) - float(stop) if direction == "bullish"
                    else float(stop) - float(price))
    reward_dollars = (float(tp1) - float(price) if direction == "bullish"
                      else float(price) - float(tp1))
    rr_check = "✅" if float(rr) >= 3 else "⚠️"
    lines.append("")
    lines.append(
        f"Entry: ${float(price):.2f} | Stop: ${float(stop):.2f} | "
        f"Target: ${float(tp1):.2f} / ${float(tp2):.2f}"
    )
    lines.append(
        f"Risk ${risk_dollars:.2f} → Reward ${reward_dollars:.2f} "
        f"({float(rr):.1f}:1) {rr_check}"
    )

    # Suggested option contract.
    opt_sym = signal.get("option_symbol")
    if opt_sym:
        opt_strike = signal.get("option_strike", 0)
        opt_expiry = signal.get("option_expiry", "?")
        opt_delta = signal.get("option_delta", 0)
        opt_mid = signal.get("option_mid")
        contract_type = "C" if direction == "bullish" else "P"
        lines.append("")
        lines.append(
            f"Suggested: {symbol} ${float(opt_strike):.0f}{contract_type} "
            f"exp {opt_expiry} (Δ{float(opt_delta):.2f})"
        )
        if opt_mid is not None:
            lines.append(f"Mid: ${float(opt_mid):.2f}")

    # External-level warning. detect_orb refuses to fire when within 0.5%
    # of prev-day H/L, so these flags will normally be False — but the
    # JSONB blob records them defensively, so surface them if present.
    if near_pdh or near_pdl:
        lines.append("")
        which = "high" if near_pdh else "low"
        lines.append(
            f"⚠️ Underlying near previous day's {which} — "
            "liquidity magnet, higher invalidation risk"
        )

    # Bail conditions from the invalidation blob.
    reasons = inv.get("reasons") or []
    if reasons:
        lines.append("")
        lines.append("<b>Bail if:</b>")
        for rule in reasons:
            lines.append(f"  ⛔ {html_mod.escape(str(rule))}")

    lines.append(_ttl_line("orb"))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Send Telegram alerts for new signals")
    parser.add_argument("--strategy", type=str, default=None,
                        help="Only send alerts for this strategy (e.g., ema_crossover)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print alerts without sending")
    parser.add_argument("--limit", type=int, default=10,
                        help="Max alerts to send (default: 10)")
    parser.add_argument("--skip-lifecycle", action="store_true",
                        help="Skip the expire-stale + surface-errors prelude. "
                             "Used in tests; the cron always runs both.")
    args = parser.parse_args()

    conn = get_connection()
    cur = conn.cursor()

    # Pull one row at a time with FOR UPDATE SKIP LOCKED so two overlapping
    # alert_dispatch cron runs (1-min cadence, ~200ms per Telegram send)
    # never fetch the same row and double-send. The lock is released by
    # the conn.commit() that follows the UPDATE on each iteration.
    columns = [
        "id", "symbol", "strategy", "direction", "status", "regime",
        "trigger_price", "ema_9", "ema_21", "adx", "rsi", "atr_14", "volume_ratio",
        "composite_score",
        "stop_price", "tp1_price", "tp2_price", "risk_reward",
        "micro_trend", "intermediate_trend", "primary_trend",
        "trend_score", "ema_stack", "invalidation",
        "option_symbol", "option_strike", "option_expiry",
        "option_delta", "option_theta",
        "option_bid", "option_ask", "option_mid",
        "spread_pct",
        "iv_rank", "iv_rv_spread", "net_gex",
        "timeframe", "daily_trend", "daily_ema_position",
        "intraday_ema_9", "intraday_ema_21",
        "created_at",
    ]
    select_cols = ", ".join(columns)
    fetch_query = f"""
        SELECT {select_cols}
        FROM market.signal_alerts
        WHERE telegram_sent = FALSE AND status = 'new'
          AND id <> ALL(%s::int[])
    """
    fetch_strategy: str | None = args.strategy
    if fetch_strategy:
        fetch_query += " AND strategy = %s"
    fetch_query += " ORDER BY created_at DESC LIMIT 1 FOR UPDATE SKIP LOCKED"
    # Track rows already visited THIS run so dry-run / send-failure rollbacks
    # don't re-pick the same row in a busy loop.
    seen_ids: list[int] = []

    # Load Telegram config
    tg_config = get_telegram_config()
    tg_token = tg_config.get("TELEGRAM_BOT_TOKEN")
    tg_chat_id = tg_config.get("TELEGRAM_CHAT_ID")

    if not args.dry_run and (not tg_token or not tg_chat_id):
        log.error("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in .env.telegram")
        print("Can't send: missing Telegram config. Use --dry-run to preview.")
        conn.close()
        return

    # Lifecycle prelude: walk through age and failure terminals before the
    # main dispatch so the 1-min cron handles them on every tick. Both
    # helpers commit themselves; both are safe to call against an empty
    # backlog.
    if not args.skip_lifecycle and not args.dry_run:
        try:
            expire_stale_new(conn, tg_token, tg_chat_id)
        except Exception:
            log.exception("expire_stale_new failed — continuing")
            conn.rollback()
        try:
            notify_errors(conn, tg_token, tg_chat_id, limit=args.limit)
        except Exception:
            log.exception("notify_errors failed — continuing")
            conn.rollback()

    sent_count = 0
    processed = 0
    while processed < args.limit:
        params = [seen_ids]
        if fetch_strategy:
            params.append(fetch_strategy)
        cur.execute(fetch_query, params)
        row = cur.fetchone()
        if row is None:
            # Either nothing to send, or every remaining row is locked by
            # a concurrent run. Release any implicit transaction.
            conn.commit()
            break
        processed += 1
        signal = dict(zip(columns, row))
        seen_ids.append(signal["id"])

        # Format alert based on strategy
        if signal["strategy"] == "ema_crossover_15m":
            alert_text = format_15m_crossover_alert(signal)
        elif signal["strategy"] == "ema_crossover":
            alert_text = format_ema_crossover_alert(signal)
        elif signal["strategy"] == "setup_scanner":
            alert_text = format_setup_scanner_alert(signal)
        elif signal["strategy"] == "liquidity_sweep":
            alert_text = format_liquidity_sweep_alert(signal)
        elif signal["strategy"] == "intraday_signal":
            alert_text = format_intraday_signal_alert(signal)
        elif signal["strategy"] == "orb":
            alert_text = format_orb_alert(signal)
        else:
            # Generic fallback
            emoji = "📈" if signal["direction"] == "bullish" else "📉"
            alert_text = f"{emoji} {signal['strategy'].upper()} — {signal['symbol']}\n"
            alert_text += f"Direction: {signal['direction']}\n"
            alert_text += f"Price: ${signal['trigger_price']:.2f}\n"

        if args.dry_run:
            print(f"\n{'='*60}")
            print(alert_text)
            print(f"{'='*60}")
            sent_count += 1
            # Release the FOR UPDATE lock without persisting any change so a
            # real cron run can still pick this row up.
            conn.rollback()
            continue

        # Auto-approve momentum strategies (ORB, liquidity_sweep): skip the
        # human button, but ALWAYS still send the alert + run preflight in
        # execute_trade. Manual strategies keep the 4-button keyboard.
        is_auto_approve = signal["strategy"] in AUTO_APPROVE_STRATEGIES

        if is_auto_approve:
            # Prepend a notice so the chat reads top-down: this fired and is
            # already in flight; veto with the Deny button before fill.
            alert_text = (
                "⚡ <b>AUTO-APPROVED — order submitting</b>\n"
                f"<i>{signal['strategy']} bypasses manual approval — "
                "tap Deny within the TTL window to veto.</i>\n"
                f"{'─' * 30}\n"
                f"{alert_text}"
            )
            keyboard = build_deny_only_keyboard(signal["id"])
        else:
            keyboard = build_approval_keyboard(signal["id"])

        result = send_telegram_message(
            tg_token, tg_chat_id, alert_text,
            allowed_chat_id=tg_chat_id,
            reply_markup=keyboard,
        )
        if result and result.get("ok"):
            msg_id = result["result"]["message_id"]
            if is_auto_approve:
                # Single UPDATE flips status='approved' + records the auto
                # bypass + stores the message id, so the row is immediately
                # eligible for execute_signal_immediate below (and the cron
                # safety net if that fails).
                cur.execute(
                    """UPDATE market.signal_alerts
                          SET telegram_sent    = TRUE,
                              telegram_msg_id  = %s,
                              status           = 'approved',
                              user_action      = 'auto_approved',
                              approved_at      = NOW(),
                              risk_mode        = COALESCE(risk_mode, 'standard')
                        WHERE id = %s""",
                    (msg_id, signal["id"]),
                )
                conn.commit()
                sent_count += 1
                log.info("AUTO-APPROVED alert for %s %s (msg_id=%s) — "
                         "dispatching execute_signal_immediate",
                         signal["symbol"], signal["strategy"], msg_id)
                # Inline execution — same in-process trigger the listener
                # uses for manual approves. Best effort: a failure here just
                # leaves the row for the next execute_trade cron tick.
                try:
                    from execute_trade import execute_signal_immediate
                    exec_result = execute_signal_immediate(signal["id"])
                    log.info("Immediate exec #%s → %s", signal["id"],
                             exec_result.get("status"))
                except Exception:
                    log.exception("Immediate exec failed for #%s — cron will retry",
                                  signal["id"])
            else:
                cur.execute(
                    """UPDATE market.signal_alerts
                          SET telegram_sent = TRUE, telegram_msg_id = %s
                        WHERE id = %s""",
                    (msg_id, signal["id"]),
                )
                conn.commit()
                sent_count += 1
                log.info("Sent alert for %s %s (msg_id=%s)", signal["symbol"], signal["strategy"], msg_id)
        else:
            # Telegram send failed — release the lock so the next cron run
            # can retry. The seen_ids guard keeps us from busy-looping on the
            # same row within THIS run.
            conn.rollback()
            log.error("Failed to send alert for %s %s", signal["symbol"], signal["strategy"])

    cur.close()
    conn.close()

    print(f"\n{sent_count} alert(s) {'processed' if args.dry_run else 'sent'}.")


if __name__ == "__main__":
    main()