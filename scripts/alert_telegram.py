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


def send_telegram_message(token: str, chat_id: str, text: str) -> dict | None:
    """Send a message via Telegram Bot API. Returns the response JSON or None."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")

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

def format_ema_crossover_alert(signal: dict) -> str:
    """Format an EMA crossover signal as a Phase 5A trade alert."""
    symbol = signal["symbol"]
    direction = signal["direction"]
    price = signal["trigger_price"]
    stop = signal["stop_price"]
    tp1 = signal["tp1_price"]
    tp2 = signal["tp2_price"]
    rr = signal["risk_reward"]
    adx = signal["adx"]
    rsi = signal["rsi"]
    atr = signal["atr_14"]
    regime = signal["regime"]

    emoji = "📈" if direction == "bullish" else "📉"
    direction_label = "Bullish 9/21 cross confirmed" if direction == "bullish" else "Bearish 9/21 cross confirmed"

    lines = [
        f"{emoji} <b>EMA CROSSOVER — {symbol}</b>",
        "",
        f"Signal: {direction_label}",
        f"Price: ${price:.2f} | ATR(14): ${atr:.2f}",
        f"Stop: ${stop:.2f} (ATR × 2.0 {'below' if direction == 'bullish' else 'above'})",
        f"TP1: ${tp1:.2f} (+30%) | TP2: ${tp2:.2f} (+50%) | Trail after TP2",
        f"R:R: {rr:.1f}:1 ✅",
        f"ADX: {adx:.0f} (trending ✅) | RSI: {rsi:.0f}",
    ]

    # Trend context
    micro = signal.get("micro_trend", "?")
    inter = signal.get("intermediate_trend", "?")
    prim = signal.get("primary_trend", "?")
    trend_symbols = []
    for t in [micro, inter, prim]:
        if t == "bull":
            trend_symbols.append("↑")
        elif t == "bear":
            trend_symbols.append("↓")
        else:
            trend_symbols.append("→")
    trend_str = " ".join(trend_symbols)
    lines.append(f"Trend: micro{trend_symbols[0]} inter{trend_symbols[1]} primary{trend_symbols[2]}")

    # IV rank
    iv_rank = signal.get("iv_rank")
    if iv_rank is not None:
        if iv_rank < 30:
            iv_zone = "buy zone ✅"
        elif iv_rank < 50:
            iv_zone = "moderate"
        else:
            iv_zone = "expensive ⚠️"
        lines.append(f"IV Rank: {iv_rank:.0f}% ({iv_zone})")

    # IV-RV spread
    iv_rv = signal.get("iv_rv_spread")
    if iv_rv is not None:
        if iv_rv < -0.15:
            spread_note = "IV cheap vs RV ✅"
        elif iv_rv > 0.15:
            spread_note = "IV expensive vs RV ⚠️"
        else:
            spread_note = "fair value"
        lines.append(f"IV-RV Spread: {iv_rv:+.3f} ({spread_note})")

    # GEX
    net_gex = signal.get("net_gex")
    if net_gex is not None:
        gex_sign = "positive (dealer hedging suppresses vol)" if net_gex > 0 else "negative (dealer hedging amplifies vol)"
        lines.append(f"Net GEX: ${net_gex:,.0f} ({gex_sign})")

    # Regime
    lines.append(f"Regime: {regime}")

    # Best option contract
    opt_sym = signal.get("option_symbol")
    if opt_sym:
        opt_strike = signal.get("option_strike", 0)
        opt_expiry = signal.get("option_expiry", "?")
        opt_delta = signal.get("option_delta", 0)
        opt_theta = signal.get("option_theta", 0)
        lines.append("")
        lines.append(f"<b>Best Option</b> (DTE≥30, Delta 0.50-0.70):")
        lines.append(f"{opt_sym} C${opt_strike:.0f} exp {opt_expiry}")
        lines.append(f"Delta: {opt_delta:.2f} | Theta: {opt_theta:.3f}/day")

    # Invalidation conditions
    invalidation = signal.get("invalidation")
    if invalidation:
        if isinstance(invalidation, str):
            invalidation = json.loads(invalidation)
        lines.append("")
        lines.append("<b>Invalidation conditions:</b>")
        for cond in invalidation:
            lines.append(f"  • {cond}")

    # Approve/reject links (placeholder — will be interactive later)
    signal_id = signal.get("id", 0)
    lines.append("")
    lines.append(f"/approve EMA_{symbol}_{signal_id}")
    lines.append("/reject")

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
               iv_rank, iv_rv_spread, net_gex,
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
        "iv_rank", "iv_rv_spread", "net_gex",
        "created_at",
    ]

    sent_count = 0
    for row in rows:
        signal = dict(zip(columns, row))

        # Format alert based on strategy
        if signal["strategy"] == "ema_crossover":
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

        # Send via Telegram
        result = send_telegram_message(tg_token, tg_chat_id, alert_text)
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