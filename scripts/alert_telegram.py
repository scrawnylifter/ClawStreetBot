#!/usr/bin/env python3
"""Telegram Alert Dispatcher — v2

Reads new (unsent) signals from market.signal_alerts, formats them per
strategy, and sends them to Telegram with a 4-button approval keyboard.

Usage:
    python alert_telegram.py                       # send all unsent
    python alert_telegram.py --strategy orb        # only ORB
    python alert_telegram.py --dry-run             # print, don't send
"""
import argparse
import html as html_mod
import json
import logging
import sys
import urllib.error
import urllib.request
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent))
from constants import (  # noqa: E402
    DB_CONFIG,
    DEFAULT_SIGNAL_TTL_MINUTES,
    MAX_SPREAD_PCT,
    SIGNAL_TTL_MINUTES,
    load_env,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Telegram config
# ---------------------------------------------------------------------------

def get_telegram_config() -> dict:
    """Load Telegram bot token and chat ID from .env.telegram."""
    env_path = Path("/app/.env.telegram")
    config: dict = {}
    if not env_path.exists():
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


# ---------------------------------------------------------------------------
# Telegram API
# ---------------------------------------------------------------------------

def build_approval_keyboard(signal_id: int) -> dict:
    """Inline keyboard: Approve / Deny on row 1, Conservative / Aggressive on row 2."""
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
    """Send a message via Telegram Bot API. Returns response JSON or None."""
    if allowed_chat_id and str(chat_id) != str(allowed_chat_id):
        log.warning("BLOCKED: send to unauthorized chat_id=%s (allowed=%s)", chat_id, allowed_chat_id)
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
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
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


def _telegram_edit(token: str, method: str, payload: dict, allowed_chat_id: str = "") -> dict | None:
    chat_id = payload.get("chat_id")
    if allowed_chat_id and str(chat_id) != str(allowed_chat_id):
        log.warning("BLOCKED: edit to unauthorized chat_id=%s", chat_id)
        return None
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else ""
        if e.code == 400 and "not modified" in body:
            return {"ok": True, "no_change": True}
        log.error("Telegram %s error %d: %s", method, e.code, body[:200])
        return None
    except Exception as e:
        log.error("Telegram %s failed: %s", method, e)
        return None


def clear_message_keyboard(token: str, chat_id: str, message_id: int, allowed_chat_id: str = "") -> dict | None:
    return _telegram_edit(
        token, "editMessageReplyMarkup",
        {"chat_id": chat_id, "message_id": int(message_id), "reply_markup": {}},
        allowed_chat_id=allowed_chat_id,
    )


def edit_message_text(token: str, chat_id: str, message_id: int, text: str, allowed_chat_id: str = "") -> dict | None:
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
# Lifecycle: TTL expirer
# ---------------------------------------------------------------------------

def _ttl_line(strategy: str) -> str:
    ttl = SIGNAL_TTL_MINUTES.get(strategy, DEFAULT_SIGNAL_TTL_MINUTES)
    if ttl >= 60:
        hours, mins = divmod(ttl, 60)
        if mins:
            return f"⏱ Expires in {hours}h {mins}m"
        return f"⏱ Expires in {hours}h"
    return f"⏱ Expires in {ttl}m"


def expire_stale_new(conn, token: str | None, chat_id: str | None) -> int:
    """Flip status='new' rows past their strategy-specific TTL to 'expired'.

    Strips the inline keyboard from each expiring row's message so the user
    can't tap a dead button. Keyboard removal failure does not block the DB
    flip — the state machine still needs to advance."""
    total_expired = 0
    with conn.cursor() as cur:
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
                for _sid, msg_id in rows:
                    if msg_id:
                        clear_message_keyboard(token, chat_id, msg_id, allowed_chat_id=chat_id)
            if rows:
                log.info("Expired %d stale '%s' signal(s) (> %d min)", len(rows), strategy, ttl_minutes)
            total_expired += len(rows)

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
            for _sid, msg_id in default_rows:
                if msg_id:
                    clear_message_keyboard(token, chat_id, msg_id, allowed_chat_id=chat_id)
        if default_rows:
            log.info("Expired %d stale signal(s) (> %d min default TTL)",
                     len(default_rows), DEFAULT_SIGNAL_TTL_MINUTES)
        total_expired += len(default_rows)
    conn.commit()
    log.info("Total expired: %d signal_alerts row(s)", total_expired)
    return total_expired


# ---------------------------------------------------------------------------
# Formatter helpers
# ---------------------------------------------------------------------------

def _format_quote_line(opt_bid, opt_ask, opt_mid, spread_pct) -> str:
    """Quote line with a 🚩 when spread exceeds MAX_SPREAD_PCT."""
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


def _append_option_block(lines: list, signal: dict, direction: str, symbol: str):
    """Append option contract details with greeks and quote to alert lines."""
    opt_sym = signal.get("option_symbol")
    if not opt_sym:
        return
    opt_strike = signal.get("option_strike", 0)
    opt_expiry = signal.get("option_expiry", "?")
    opt_delta = signal.get("option_delta", 0)
    opt_theta = signal.get("option_theta")
    opt_mid = signal.get("option_mid")
    opt_bid = signal.get("option_bid")
    opt_ask = signal.get("option_ask")
    contract_type = "C" if direction == "bullish" else "P"

    lines.append("")
    # Header line: symbol strike C/P expiry delta
    lines.append(
        f"<b>Option:</b> {symbol} ${float(opt_strike):.0f}{contract_type} "
        f"exp {opt_expiry} (Δ{float(opt_delta):.2f})"
    )
    # Greeks line: theta if available
    greek_bits = []
    if opt_theta is not None:
        greek_bits.append(f"Θ{float(opt_theta):.3f}")
    if greek_bits:
        lines.append(f"Greeks: {' | '.join(greek_bits)}")
    # Quote line with spread
    if opt_bid is not None and opt_ask is not None:
        lines.append(_format_quote_line(opt_bid, opt_ask, opt_mid,
                                        signal.get("spread_pct")))
    elif opt_mid is not None:
        lines.append(f"Mid: ${float(opt_mid):.2f}")


def _trend_english(val) -> str:
    if val == "bull":
        return "up"
    if val == "bear":
        return "down"
    return "flat"


# ---------------------------------------------------------------------------
# Per-strategy formatters
# ---------------------------------------------------------------------------

def format_ema_crossover_alert(signal: dict) -> str:
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

    lines = [
        f"{emoji} <b>{symbol} — {action} Signal</b>",
        f"{'─' * 30}",
        f"Strategy: EMA 9/21 Crossover (daily) | Timeframe: {signal.get('timeframe', '1d')}",
    ]

    risk_d = price - stop if direction == "bullish" else stop - price
    reward_d = tp1 - price if direction == "bullish" else price - tp1
    rr_check = "✅" if rr >= 3 else "⚠️"
    lines.append(f"Entry: ${price:.2f} | Stop: ${stop:.2f} | Target: ${tp1:.2f} / ${tp2:.2f}")
    lines.append(f"Risk ${risk_d:.2f} → Reward ${reward_d:.2f} ({rr:.1f}:1) {rr_check}")

    cross_dir = "above" if direction == "bullish" else "below"
    lines.append(f"\n9-day moving average crossed {cross_dir} 21-day — trend forming (ADX {adx:.0f}).")

    micro = _trend_english(signal.get("micro_trend", "?"))
    inter = _trend_english(signal.get("intermediate_trend", "?"))
    prim = _trend_english(signal.get("primary_trend", "?"))
    regime_emoji = "🟢" if regime == "bull" else ("🔴" if regime == "bear" else "⚪")
    lines.append(f"Short-term {micro}, mid-term {inter}, long-term {prim}. Market regime: {regime_emoji} {regime}")

    # --- Option contract ---
    _append_option_block(lines, signal, direction, symbol)

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


def format_15m_crossover_alert(signal: dict) -> str:
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

    lines = [
        f"{emoji} <b>{symbol} — {action} Signal (15m)</b>",
        f"{'─' * 30}",
        f"Strategy: EMA 9/21 Crossover (intraday) | Timeframe: {signal.get('timeframe', '15m')}",
    ]

    risk_d = price - stop if direction == "bullish" else stop - price
    reward_d = tp1 - price if direction == "bullish" else price - tp1
    rr_check = "✅" if rr >= 3 else "⚠️"
    lines.append(f"Entry: ${price:.2f} | Stop: ${stop:.2f} | Target: ${tp1:.2f} / ${tp2:.2f}")
    lines.append(f"Risk ${risk_d:.2f} → Reward ${reward_d:.2f} ({rr:.1f}:1) {rr_check}")

    cross_dir = "above" if direction == "bullish" else "below"
    daily_pos = signal.get("daily_ema_position", "?")
    daily_label = "up" if daily_pos == "above" else "down"
    lines.append(f"\n15-min EMA crossed {cross_dir} 21-period — trend forming (ADX {adx:.0f}).")
    lines.append(f"Daily trend: EMA9 {'above' if daily_pos == 'above' else 'below'} EMA21 → daily {daily_label} ✅")

    micro = _trend_english(signal.get("micro_trend", "?"))
    inter = _trend_english(signal.get("intermediate_trend", "?"))
    prim = _trend_english(signal.get("primary_trend", "?"))
    regime_emoji = "🟢" if regime == "bull" else ("🔴" if regime == "bear" else "⚪")
    lines.append(f"Short-term {micro}, mid-term {inter}, long-term {prim}. Market regime: {regime_emoji} {regime}")

    # --- Option contract ---
    _append_option_block(lines, signal, direction, symbol)

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


def format_setup_scanner_alert(signal: dict) -> str:
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

    lines = [
        f"{emoji} <b>{action} Signal: {symbol}</b>",
        f"{'─' * 30}",
        f"Strategy: Setup Scanner (8-gate) | Timeframe: {signal.get('timeframe', '1d')}",
    ]
    composite = signal.get("composite_score")
    if composite is not None:
        lines.append(f"Composite: {float(composite):.1f}/100 | Regime: {regime}")
    else:
        lines.append(f"Regime: {regime}")

    micro = _trend_english(signal.get("micro_trend", "?"))
    inter = _trend_english(signal.get("intermediate_trend", "?"))
    prim = _trend_english(signal.get("primary_trend", "?"))
    lines.append(f"Stock: ${float(price):.2f} | RSI: {float(rsi):.0f} | ADX: {float(adx):.0f}")
    lines.append(f"Trend: short {micro}, mid {inter}, long {prim}")

    risk_d = float(price) - float(stop) if direction == "bullish" else float(stop) - float(price)
    reward_d = float(tp1) - float(price) if direction == "bullish" else float(price) - float(tp1)
    rr_check = "✅" if float(rr) >= 3 else "⚠️"
    lines.append("")
    lines.append(f"Entry: ${float(price):.2f} | Stop: ${float(stop):.2f} | Target: ${float(tp1):.2f} / ${float(tp2):.2f}")
    lines.append(f"Risk ${risk_d:.2f} → Reward ${reward_d:.2f} ({float(rr):.1f}:1) {rr_check}")

    # --- Option contract ---
    _append_option_block(lines, signal, direction, symbol)

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

    lines.append(_ttl_line("setup_scanner"))
    return "\n".join(lines)


def format_liquidity_sweep_alert(signal: dict) -> str:
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
    atr = signal.get("atr_14")
    if atr is not None:
        lines.append(f"ATR: ${float(atr):.2f}")

    risk_d = float(price) - float(stop) if direction == "bullish" else float(stop) - float(price)
    reward_d = float(tp1) - float(price) if direction == "bullish" else float(price) - float(tp1)
    rr_check = "✅" if float(rr) >= 3 else "⚠️"
    lines.append("")
    lines.append(f"Entry: ${float(price):.2f} | Stop: ${float(stop):.2f} | Target: ${float(tp1):.2f} / ${float(tp2):.2f}")
    lines.append(f"Risk ${risk_d:.2f} → Reward ${reward_d:.2f} ({float(rr):.1f}:1) {rr_check}")

    # --- Option contract ---
    _append_option_block(lines, signal, direction, symbol)

    lines.append("")
    lines.append("⚡ Close-beyond confirmation passed")
    lines.append("Backtest: PF 1.56 | WR 32.4% | avg +0.16R (259 trades)")

    lines.append(_ttl_line("liquidity_sweep"))
    return "\n".join(lines)


def format_orb_alert(signal: dict) -> str:
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
    orb_low = inv.get("orb_range_low")
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
    cross_word = "above ORB high" if direction == "bullish" else "below ORB low"
    breakout_word = "retest entry" if breakout_type == "retest" else "direct breakout"
    lines.append(f"Breakout: 5-min close {cross_word} ({breakout_word})")

    if atr is not None:
        lines.append(f"ATR: ${float(atr):.2f}")

    risk_d = float(price) - float(stop) if direction == "bullish" else float(stop) - float(price)
    reward_d = float(tp1) - float(price) if direction == "bullish" else float(price) - float(tp1)
    rr_check = "✅" if float(rr) >= 3 else "⚠️"
    lines.append("")
    lines.append(
        f"Entry: ${float(price):.2f} | Stop: ${float(stop):.2f} | "
        f"Target: ${float(tp1):.2f} / ${float(tp2):.2f}"
    )
    lines.append(
        f"Risk ${risk_d:.2f} → Reward ${reward_d:.2f} ({float(rr):.1f}:1) {rr_check}"
    )

    # --- Option contract ---
    _append_option_block(lines, signal, direction, symbol)

    if near_pdh or near_pdl:
        lines.append("")
        which = "high" if near_pdh else "low"
        lines.append(
            f"⚠️ Underlying near previous day's {which} — "
            "liquidity magnet, higher invalidation risk"
        )

    reasons = inv.get("reasons") or []
    if reasons:
        lines.append("")
        lines.append("<b>Bail if:</b>")
        for rule in reasons:
            lines.append(f"  ⛔ {html_mod.escape(str(rule))}")

    lines.append(_ttl_line("orb"))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Strategy dispatch table
# ---------------------------------------------------------------------------

STRATEGY_FORMATTERS = {
    "ema_crossover": format_ema_crossover_alert,
    "ema_crossover_15m": format_15m_crossover_alert,
    "setup_scanner": format_setup_scanner_alert,
    "liquidity_sweep": format_liquidity_sweep_alert,
    "orb": format_orb_alert,
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

SELECT_COLUMNS = [
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Dispatch Telegram alerts for new signals")
    parser.add_argument("--strategy", type=str, default=None,
                        help=f"Only dispatch this strategy (one of: {', '.join(STRATEGY_FORMATTERS)})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print formatted alerts to stdout instead of sending")
    parser.add_argument("--limit", type=int, default=10,
                        help="Max alerts to dispatch this run (default: 10)")
    parser.add_argument("--skip-lifecycle", action="store_true",
                        help="Skip expire_stale_new() prelude")
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)

    tg_config = get_telegram_config()
    tg_token = tg_config.get("TELEGRAM_BOT_TOKEN")
    tg_chat_id = tg_config.get("TELEGRAM_CHAT_ID")

    if not args.dry_run and (not tg_token or not tg_chat_id):
        log.error("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in .env.telegram")
        print("Can't send: missing Telegram config. Use --dry-run to preview.")
        conn.close()
        return

    if not args.skip_lifecycle and not args.dry_run:
        try:
            expire_stale_new(conn, tg_token, tg_chat_id)
        except Exception:
            log.exception("expire_stale_new failed — continuing")
            conn.rollback()

    select_cols = ", ".join(SELECT_COLUMNS)
    fetch_query = f"""
        SELECT {select_cols}
          FROM market.signal_alerts
         WHERE telegram_sent = FALSE
           AND status = 'new'
           AND id <> ALL(%s::int[])
    """
    if args.strategy:
        fetch_query += " AND strategy = %s"
    fetch_query += " ORDER BY created_at DESC LIMIT 1 FOR UPDATE SKIP LOCKED"

    seen_ids: list[int] = []
    sent_count = 0
    processed = 0

    cur = conn.cursor()
    while processed < args.limit:
        params: list = [seen_ids]
        if args.strategy:
            params.append(args.strategy)
        cur.execute(fetch_query, params)
        row = cur.fetchone()
        if row is None:
            conn.commit()
            break
        processed += 1
        signal = dict(zip(SELECT_COLUMNS, row))
        seen_ids.append(signal["id"])

        strategy = signal["strategy"]
        formatter = STRATEGY_FORMATTERS.get(strategy)
        if formatter is not None:
            alert_text = formatter(signal)
        else:
            emoji = "📈" if signal["direction"] == "bullish" else "📉"
            alert_text = (
                f"{emoji} {strategy.upper()} — {signal['symbol']}\n"
                f"Direction: {signal['direction']}\n"
                f"Price: ${float(signal['trigger_price']):.2f}\n"
            )

        if args.dry_run:
            print(f"\n{'=' * 60}")
            print(f"[id={signal['id']}] strategy={strategy} symbol={signal['symbol']}")
            print(f"{'=' * 60}")
            print(alert_text)
            sent_count += 1
            conn.rollback()
            continue

        keyboard = build_approval_keyboard(signal["id"])
        result = send_telegram_message(
            tg_token, tg_chat_id, alert_text,
            allowed_chat_id=tg_chat_id,
            reply_markup=keyboard,
        )
        if result and result.get("ok"):
            msg_id = result["result"]["message_id"]
            cur.execute(
                """UPDATE market.signal_alerts
                      SET telegram_sent = TRUE,
                          telegram_msg_id = %s
                    WHERE id = %s""",
                (msg_id, signal["id"]),
            )
            conn.commit()
            sent_count += 1
            log.info("Sent alert id=%s %s %s (msg_id=%s)",
                     signal["id"], signal["symbol"], strategy, msg_id)
        else:
            conn.rollback()
            log.error("Failed to send alert id=%s %s %s",
                      signal["id"], signal["symbol"], strategy)

    cur.close()
    conn.close()

    verb = "previewed" if args.dry_run else "sent"
    print(f"\n{sent_count} alert(s) {verb}.")


if __name__ == "__main__":
    main()
