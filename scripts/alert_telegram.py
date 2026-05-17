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
import logging
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
        dbname=conn_params.get("POSTGRES_DB", "clawstreetbot"),
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


def build_approval_keyboard(signal_id: int) -> dict:
    """Inline keyboard with Approve / Deny buttons for a signal_alerts row.

    callback_data is read by telegram_callback_listener.py — keep the
    `approve:<id>` / `deny:<id>` format stable on both sides.
    """
    return {
        "inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": f"approve:{signal_id}"},
            {"text": "❌ Deny",    "callback_data": f"deny:{signal_id}"},
        ]]
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
            mid_str = f" | mid ${float(opt_mid):.2f}" if opt_mid is not None else ""
            lines.append(f"Quote: bid ${float(opt_bid):.2f} / ask ${float(opt_ask):.2f}{mid_str}")

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
            lines.append(f"  ⛔ {cond}")

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
            mid_str = f" | mid ${float(opt_mid):.2f}" if opt_mid is not None else ""
            lines.append(f"Quote: bid ${float(opt_bid):.2f} / ask ${float(opt_ask):.2f}{mid_str}")

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
            lines.append(f"  ⛔ {cond}")

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
    args = parser.parse_args()

    conn = get_connection()
    cur = conn.cursor()

    # Fetch unsent signals
    query = """
        SELECT id, symbol, strategy, direction, status, regime,
               trigger_price, ema_9, ema_21, adx, rsi, atr_14, volume_ratio,
               stop_price, tp1_price, tp2_price, risk_reward,
               micro_trend, intermediate_trend, primary_trend,
               trend_score, ema_stack, invalidation,
               option_symbol, option_strike, option_expiry,
               option_delta, option_theta,
               option_bid, option_ask, option_mid,
               iv_rank, iv_rv_spread, net_gex,
               timeframe, daily_trend, daily_ema_position,
               intraday_ema_9, intraday_ema_21,
               created_at
        FROM market.signal_alerts
        WHERE telegram_sent = FALSE AND status = 'new'
    """
    params = []
    if args.strategy:
        query += " AND strategy = %s"
        params.append(args.strategy)
    query += " ORDER BY created_at DESC LIMIT %s"
    params.append(args.limit)

    cur.execute(query, params)
    rows = cur.fetchall()

    if not rows:
        print("No new unsent signals found.")
        conn.close()
        return

    print(f"Found {len(rows)} unsent signal(s)")

    # Load Telegram config
    tg_config = get_telegram_config()
    tg_token = tg_config.get("TELEGRAM_BOT_TOKEN")
    tg_chat_id = tg_config.get("TELEGRAM_CHAT_ID")

    if not args.dry_run and (not tg_token or not tg_chat_id):
        log.error("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in .env.telegram")
        print("Can't send: missing Telegram config. Use --dry-run to preview.")
        conn.close()
        return

    columns = [
        "id", "symbol", "strategy", "direction", "status", "regime",
        "trigger_price", "ema_9", "ema_21", "adx", "rsi", "atr_14", "volume_ratio",
        "stop_price", "tp1_price", "tp2_price", "risk_reward",
        "micro_trend", "intermediate_trend", "primary_trend",
        "trend_score", "ema_stack", "invalidation",
        "option_symbol", "option_strike", "option_expiry",
        "option_delta", "option_theta",
        "option_bid", "option_ask", "option_mid",
        "iv_rank", "iv_rv_spread", "net_gex",
        "timeframe", "daily_trend", "daily_ema_position",
        "intraday_ema_9", "intraday_ema_21",
        "created_at",
    ]

    sent_count = 0
    for row in rows:
        signal = dict(zip(columns, row))

        # Format alert based on strategy
        if signal["strategy"] == "ema_crossover_15m":
            alert_text = format_15m_crossover_alert(signal)
        elif signal["strategy"] == "ema_crossover":
            alert_text = format_ema_crossover_alert(signal)
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
            continue

        # Send via Telegram with Approve / Deny buttons
        keyboard = build_approval_keyboard(signal["id"])
        result = send_telegram_message(
            tg_token, tg_chat_id, alert_text,
            allowed_chat_id=tg_chat_id,
            reply_markup=keyboard,
        )
        if result and result.get("ok"):
            msg_id = result["result"]["message_id"]
            # Mark as sent
            cur.execute("""
                UPDATE market.signal_alerts
                SET telegram_sent = TRUE, telegram_msg_id = %s
                WHERE id = %s
            """, (msg_id, signal["id"]))
            conn.commit()
            sent_count += 1
            log.info("Sent alert for %s %s (msg_id=%s)", signal["symbol"], signal["strategy"], msg_id)
        else:
            log.error("Failed to send alert for %s %s", signal["symbol"], signal["strategy"])

    cur.close()
    conn.close()

    print(f"\n{sent_count} alert(s) {'processed' if args.dry_run else 'sent'}.")


if __name__ == "__main__":
    main()