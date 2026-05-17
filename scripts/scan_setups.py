#!/usr/bin/env python3
"""Setup Scanner — Phase 5

Scans the watchlist on a 15-minute cadence and emits a Telegram alert
only when a symbol passes ALL of the following gates:

BULLISH (calls):
  1. Trend            EMA9 > EMA21 (daily, bullish)
  2. Trend strength   ADX > 20
  3. Not overbought   RSI_14 < 70
  4. Cheap premium    IV percentile < 40
  5. Fair pricing     IV - RV_20d <= 0.05
  6. Affordable       option mid <= --risk-budget (default 2000)
  7. Time decay       DTE >= 30
  8. Risk/Reward      >= 3:1 using daily ATR (stop = ATRx2, TP1 = ATRx6)

BEARISH (puts):
  1. Trend            EMA9 < EMA21 (daily, bearish)
  2. Trend strength   ADX > 20
  3. Not oversold     RSI_14 > 30 (not bouncing)
  4. Cheap premium    IV percentile < 40
  5. Fair pricing     IV - RV_20d <= 0.05
  6. Affordable       option mid <= --risk-budget (default 2000)
  7. Time decay       DTE >= 30
  8. Risk/Reward      >= 3:1 using daily ATR (stop = ATRx2, TP1 = ATRx6)

Sources: market.technical_indicators, market.iv_rank, market.realized_vol,
market.ohlcv, market.assets, plus live Alpaca option/stock snapshots.

Silence means no signal: nothing is sent to Telegram when no symbol passes.

Usage:
    python scan_setups.py                          # write + alert
    python scan_setups.py --dry-run                # stdout only
    python scan_setups.py --risk-budget 1000       # tighter affordability
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

# Allow direct import of sibling scripts regardless of CWD (n8n runs from /).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_alpaca_snapshot import (  # noqa: E402
    get_underlying_price,
    select_best_option,
)
from alert_telegram import (  # noqa: E402
    get_telegram_config,
    send_telegram_message,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Gate thresholds (tweak via CLI where appropriate)
# ---------------------------------------------------------------------------

ADX_MIN = 20.0
RSI_MAX = 70.0
IV_PCTILE_MAX = 40.0
IV_RV_SPREAD_MAX = 0.05
MIN_DTE = 30
MAX_DTE = 120
ATR_STOP_MULT = 2.0
ATR_TP1_MULT = 6.0
ATR_TP2_MULT = 9.0
DELTA_MIN = 0.50
DELTA_MAX = 0.70
RR_MIN = 3.0


# ---------------------------------------------------------------------------
# DB connection (matches sibling scripts)
# ---------------------------------------------------------------------------

def get_connection():
    """Open a Postgres connection using /app/.env.db (worker container path)."""
    env_path = Path("/app/.env.db")
    if not env_path.exists():
        # Local-dev fallback: project root .env.db
        env_path = Path(__file__).resolve().parent.parent / ".env.db"
    conn_params: dict[str, str] = {}
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
# Scanner
# ---------------------------------------------------------------------------

def _fnum(x) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
        return None if v != v else v
    except (TypeError, ValueError):
        return None


def fetch_candidates(conn) -> list[dict]:
    """Read latest indicators per active symbol and return raw candidate rows."""
    cur = conn.cursor()
    cur.execute("""
        WITH latest_ti AS (
            SELECT DISTINCT ON (symbol)
                symbol, date, ema_9, ema_21, rsi_14, atr_14
            FROM market.technical_indicators
            ORDER BY symbol, date DESC
        ),
        latest_iv AS (
            SELECT DISTINCT ON (symbol)
                symbol, date, current_iv, iv_percentile, iv_rank_52w
            FROM market.iv_rank
            ORDER BY symbol, date DESC
        ),
        latest_rv AS (
            SELECT DISTINCT ON (symbol)
                symbol, date, rv_20d
            FROM market.realized_vol
            ORDER BY symbol, date DESC
        )
        SELECT a.symbol,
               ti.ema_9, ti.ema_21, ti.rsi_14, ti.atr_14,
               iv.current_iv, iv.iv_percentile, iv.iv_rank_52w,
               rv.rv_20d
        FROM market.assets a
        LEFT JOIN latest_ti ti ON ti.symbol = a.symbol
        LEFT JOIN latest_iv iv ON iv.symbol = a.symbol
        LEFT JOIN latest_rv rv ON rv.symbol = a.symbol
        WHERE a.active = TRUE
        ORDER BY a.symbol
    """)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.close()
    return rows


def fetch_daily_adx(conn, symbol: str) -> float | None:
    """Pull latest ADX from market.trend_status (daily)."""
    cur = conn.cursor()
    cur.execute(
        "SELECT adx FROM market.trend_status WHERE symbol = %s "
        "ORDER BY date DESC LIMIT 1",
        (symbol,),
    )
    row = cur.fetchone()
    cur.close()
    return _fnum(row[0]) if row else None


def fetch_current_regime(conn) -> str:
    cur = conn.cursor()
    cur.execute("SELECT regime FROM market.regime ORDER BY date DESC LIMIT 1")
    row = cur.fetchone()
    cur.close()
    return row[0] if row else "unknown"


def evaluate_symbol(
    conn,
    cand: dict,
    risk_budget: float,
    regime: str,
) -> dict | None:
    """Run gates 1-8 for one candidate. Returns a signal dict or None.

    Determines direction automatically:
    - EMA9 > EMA21 → bullish (calls)
    - EMA9 < EMA21 → bearish (puts)
    - EMA9 ≈ EMA21 → no trade (flat trend)

    Args:
        conn: live psycopg2 connection (for ADX lookup).
        cand: raw row from `fetch_candidates`.
        risk_budget: max option mid premium in dollars.
        regime: current market regime tag (annotation only — not a gate here).

    Returns:
        Fully-populated signal dict ready for persistence/alerting, or None
        if any gate fails. Reason for failure is logged at INFO.
    """
    sym = cand["symbol"]
    ema9 = _fnum(cand["ema_9"])
    ema21 = _fnum(cand["ema_21"])
    rsi = _fnum(cand["rsi_14"])
    atr = _fnum(cand["atr_14"])
    iv = _fnum(cand["current_iv"])
    iv_pct = _fnum(cand["iv_percentile"])
    iv_rank = _fnum(cand["iv_rank_52w"])
    rv20 = _fnum(cand["rv_20d"])

    # Sanity — need all the basics to even evaluate.
    if None in (ema9, ema21, rsi, atr):
        log.info("%s: missing daily indicators, skip", sym)
        return None

    # Determine direction from EMA crossover.
    ema_gap = ema9 - ema21        # positive = bullish, negative = bearish
    ema_range = ema21 or 1.0      # avoid div-by-zero
    ema_pct = abs(ema_gap) / ema_range * 100  # % separation

    if ema_pct < 0.5:
        # EMAs too close — flat / indecisive trend.
        log.info("%s: gate1 trend fail (EMA9/EMA21 within 0.5%%, flat)", sym)
        return None

    if ema_gap > 0:
        direction = "bullish"
        opt_type = "C"
    else:
        direction = "bearish"
        opt_type = "P"

    # Gate 1: trend direction (already decided above, log it).
    if direction == "bullish":
        log.info("%s: trend BULLISH (EMA9=%.2f > EMA21=%.2f, gap %.1f%%)",
                 sym, ema9, ema21, ema_pct)
    else:
        log.info("%s: trend BEARISH (EMA9=%.2f < EMA21=%.2f, gap %.1f%%)",
                 sym, ema9, ema21, ema_pct)

    # Gate 2: ADX > 20
    adx = fetch_daily_adx(conn, sym)
    if adx is None or adx < ADX_MIN:
        log.info("%s: gate2 ADX fail (%s)", sym, adx)
        return None

    # Gate 3: RSI check — direction-dependent.
    if direction == "bullish":
        if rsi >= RSI_MAX:                    # not overbought
            log.info("%s: gate3 RSI fail (%.1f >= %.0f)", sym, rsi, RSI_MAX)
            return None
    else:
        if rsi <= 30.0:                       # not oversold (could bounce)
            log.info("%s: gate3 RSI fail (%.1f <= 30, may bounce)", sym, rsi)
            return None

    # Gate 4: IV percentile < 40
    if iv_pct is None or iv_pct >= IV_PCTILE_MAX:
        log.info("%s: gate4 IV pctile fail (%s)", sym, iv_pct)
        return None

    # Gate 5: IV - RV20 <= 0.05
    if iv is None or rv20 is None:
        log.info("%s: gate5 IV/RV missing", sym)
        return None
    iv_rv_spread = iv - rv20
    if iv_rv_spread > IV_RV_SPREAD_MAX:
        log.info("%s: gate5 IV-RV fail (+%.3f)", sym, iv_rv_spread)
        return None

    # Live underlying price (fall back to latest close if snapshot fails).
    try:
        price = get_underlying_price(sym)
    except Exception as e:
        log.warning("%s: snapshot price error (%s)", sym, e)
        price = None
    if price is None:
        cur = conn.cursor()
        cur.execute(
            "SELECT o.close FROM market.ohlcv o "
            "JOIN market.assets a ON a.id = o.asset_id "
            "WHERE a.symbol = %s AND o.timeframe = '1d' "
            "ORDER BY o.timestamp DESC LIMIT 1",
            (sym,),
        )
        row = cur.fetchone()
        cur.close()
        price = _fnum(row[0]) if row else None
    if price is None:
        log.info("%s: no price available, skip", sym)
        return None

    # Gates 6 + 7 require the live option chain (direction-aware).
    try:
        opt = select_best_option(sym, want_type=opt_type,
                                 min_dte=MIN_DTE, max_dte=MAX_DTE)
    except Exception as e:
        log.warning("%s: option snapshot error (%s)", sym, e)
        return None
    if not opt:
        log.info("%s: no qualifying option (delta %.2f-%.2f, DTE>=%d)",
                 sym, DELTA_MIN, DELTA_MAX, MIN_DTE)
        return None

    mid = _fnum(opt["mid"])
    if mid is None or mid <= 0:
        log.info("%s: bad option mid (%s)", sym, mid)
        return None
    # Gate 6: affordability — per-contract premium is mid * 100 shares.
    contract_cost = mid * 100.0
    if contract_cost > risk_budget:
        log.info("%s: gate6 affordability fail (contract $%.0f > budget $%.0f)",
                 sym, contract_cost, risk_budget)
        return None
    # Gate 7: DTE (select_best_option already enforces >= MIN_DTE)
    if opt["dte"] < MIN_DTE:
        log.info("%s: gate7 DTE fail (%d)", sym, opt["dte"])
        return None

    # Gate 8: ATR-based R:R on the underlying (direction-aware).
    if direction == "bullish":
        stop = round(price - atr * ATR_STOP_MULT, 2)
        tp1 = round(price + atr * ATR_TP1_MULT, 2)
        tp2 = round(price + atr * ATR_TP2_MULT, 2)
    else:
        # Bearish: stop above, targets below.
        stop = round(price + atr * ATR_STOP_MULT, 2)
        tp1 = round(price - atr * ATR_TP1_MULT, 2)
        tp2 = round(price - atr * ATR_TP2_MULT, 2)

    if direction == "bullish":
        risk_per_share = price - stop
        reward_per_share = tp1 - price
    else:
        risk_per_share = stop - price
        reward_per_share = price - tp1
    if risk_per_share <= 0:
        log.info("%s: gate8 zero risk", sym)
        return None
    stock_rr = round(reward_per_share / risk_per_share, 2)
    if stock_rr < RR_MIN:
        log.info("%s: gate8 R:R fail (%.2f)", sym, stock_rr)
        return None

    # Option-level dollar Risk/Reward for display (scaled by stock R:R).
    risk_dollars = round(mid, 2)
    reward_dollars = round(mid * stock_rr, 2)

    if direction == "bullish":
        invalidation = json.dumps([
            "EMA9 crosses back below EMA21 → EXIT",
            "Daily ADX drops below 20 → EXIT",
            "RSI prints > 75 with no follow-through → trim/exit",
            "Stock breaks below ATR stop → EXIT immediately",
        ])
    else:
        invalidation = json.dumps([
            "EMA9 crosses back above EMA21 → EXIT",
            "Daily ADX drops below 20 → EXIT",
            "RSI prints < 25 with no follow-through → trim/exit",
            "Stock breaks above ATR stop → EXIT immediately",
        ])

    return {
        "symbol": sym,
        "strategy": "setup_scanner",
        "direction": direction,
        "status": "new",
        "timeframe": "1d",
        "regime": regime,
        "trigger_price": round(price, 2),
        "ema_9": ema9,
        "ema_21": ema21,
        "adx": adx,
        "rsi": rsi,
        "atr_14": atr,
        "iv_current": iv,
        "iv_percentile": iv_pct,
        "iv_rank": iv_rank,
        "rv_20d": rv20,
        "iv_rv_spread": round(iv_rv_spread, 4),
        "stop_price": stop,
        "tp1_price": tp1,
        "tp2_price": tp2,
        "stock_risk_reward": stock_rr,
        "risk_dollars": risk_dollars,
        "reward_dollars": reward_dollars,
        "invalidation": invalidation,
        "option_symbol": opt["occ_symbol"],
        "option_strike": opt["strike"],
        "option_expiry": opt["expiry"],
        "option_dte": opt["dte"],
        "option_delta": opt["delta"],
        "option_theta": opt["theta"],
        "option_bid": opt["bid"],
        "option_ask": opt["ask"],
        "option_mid": mid,
        "option_iv": opt.get("iv"),
    }


# ---------------------------------------------------------------------------
# Persistence + alert formatting
# ---------------------------------------------------------------------------

def save_signal(conn, sig: dict) -> int | None:
    """Insert into market.signal_alerts; returns row id, or None on duplicate."""
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO market.signal_alerts (
                symbol, strategy, direction, status, regime, timeframe,
                trigger_price, ema_9, ema_21, adx, rsi, atr_14,
                stop_price, tp1_price, tp2_price, risk_reward,
                invalidation,
                option_symbol, option_strike, option_expiry,
                option_delta, option_theta,
                option_bid, option_ask, option_mid,
                iv_rank, iv_rv_spread
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s,
                %s, %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, %s
            )
            ON CONFLICT (symbol, strategy, direction, timeframe, created_at)
            DO NOTHING
            RETURNING id
        """, (
            sig["symbol"], sig["strategy"], sig["direction"], sig["status"],
            sig["regime"], sig["timeframe"],
            sig["trigger_price"], sig["ema_9"], sig["ema_21"],
            sig["adx"], sig["rsi"], sig["atr_14"],
            sig["stop_price"], sig["tp1_price"], sig["tp2_price"],
            sig["stock_risk_reward"],
            sig["invalidation"],
            sig["option_symbol"], sig["option_strike"], sig["option_expiry"],
            sig["option_delta"], sig["option_theta"],
            sig["option_bid"], sig["option_ask"], sig["option_mid"],
            sig["iv_percentile"], sig["iv_rv_spread"],
        ))
        row = cur.fetchone()
        conn.commit()
        return row[0] if row else None
    except Exception as e:
        log.error("save_signal error for %s: %s", sig["symbol"], e)
        conn.rollback()
        return None
    finally:
        cur.close()


def format_buy_alert(sig: dict) -> str:
    """Render the alert exactly to the Phase-5 scanner template (bullish + bearish)."""
    s = sig
    iv_pct = s["iv_percentile"] or 0
    iv_label = "cheap" if iv_pct < 25 else ("slightly rich" if iv_pct > 50 else "moderate")
    spread = s["iv_rv_spread"] or 0
    spread_label = (
        "options cheap vs realized" if spread < -0.05 else
        "options rich vs realized" if spread > 0.05 else
        "fair pricing"
    )
    contract_letter = "C" if s["direction"] == "bullish" else "P"
    direction_emoji = "🟢" if s["direction"] == "bullish" else "🔴"
    direction_word = "BUY" if s["direction"] == "bullish" else "SHORT"
    trend_label = "Bullish (EMA9 > EMA21)" if s["direction"] == "bullish" else "Bearish (EMA9 < EMA21)"
    iv_pct_disp = round((s["iv_current"] or 0) * 100)
    rv_pct_disp = round((s["rv_20d"] or 0) * 100)

    lines = [
        f"{direction_emoji} <b>{direction_word} Signal: {s['symbol']}</b>",
        f"{'─' * 30}",
        f"Stock: ${s['trigger_price']:.2f} | Trend: {trend_label} | RSI: {s['rsi']:.0f}",
        f"IV: {iv_pct_disp}% (rank {iv_pct:.0f}th pctl) — {iv_label}",
        f"RV: {rv_pct_disp}% | IV-RV spread: {spread:+.2f} — {spread_label}",
        "",
        f"Contract: {s['symbol']} {s['option_expiry']} ${s['option_strike']:.0f}{contract_letter} @ ${s['option_mid']:.2f}",
        f"Delta {s['option_delta']:.2f} | Theta {s['option_theta']:.2f} | DTE {s['option_dte']}",
        f"Risk ${s['risk_dollars']:.2f} → Reward ${s['reward_dollars']:.2f} ({s['stock_risk_reward']:.1f}:1) ✅",
        "",
        f"ATR Stop: ${s['stop_price']:.2f} | TP1: ${s['tp1_price']:.2f} | TP2: ${s['tp2_price']:.2f}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Scan watchlist for BUY setups")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print alerts to stdout; do not write DB or send Telegram")
    parser.add_argument("--risk-budget", type=float, default=2000.0,
                        help="Max per-contract premium in dollars (default 2000)")
    args = parser.parse_args()

    conn = get_connection()
    regime = fetch_current_regime(conn)
    candidates = fetch_candidates(conn)
    log.info("Evaluating %d active symbols (regime=%s, risk_budget=$%.0f)",
             len(candidates), regime, args.risk_budget)

    passed: list[dict] = []
    for cand in candidates:
        sig = evaluate_symbol(conn, cand, args.risk_budget, regime)
        if sig:
            passed.append(sig)
            log.info("%s: ALL gates PASS (%s, R:R %.1f, premium $%.2f)",
                     sig["symbol"], sig["direction"], sig["stock_risk_reward"],
                     sig["option_mid"])

    if not passed:
        log.info("No setups qualified — staying silent.")
        conn.close()
        return 0

    log.info("%d setup(s) qualified", len(passed))

    # Dry-run: print alerts and exit.
    if args.dry_run:
        for sig in passed:
            print("=" * 60)
            print(format_buy_alert(sig))
            print("=" * 60)
        conn.close()
        return 0

    # Real run: write to DB, then Telegram.
    tg_config = get_telegram_config()
    tg_token = tg_config.get("TELEGRAM_BOT_TOKEN")
    tg_chat_id = tg_config.get("TELEGRAM_CHAT_ID")
    if not tg_token or not tg_chat_id:
        log.error("Missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID — alerts not sent")
        conn.close()
        return 1

    cur = conn.cursor()
    for sig in passed:
        sig_id = save_signal(conn, sig)
        if sig_id is None:
            log.info("%s: duplicate signal for current minute, skipping send", sig["symbol"])
            continue
        text = format_buy_alert(sig)
        result = send_telegram_message(tg_token, tg_chat_id, text,
                                       allowed_chat_id=tg_chat_id)
        if result and result.get("ok"):
            msg_id = result["result"]["message_id"]
            cur.execute(
                "UPDATE market.signal_alerts "
                "SET telegram_sent = TRUE, telegram_msg_id = %s WHERE id = %s",
                (msg_id, sig_id),
            )
            conn.commit()
            log.info("Sent %s alert for %s (msg_id=%s)",
                     sig["direction"], sig["symbol"], msg_id)
        else:
            log.error("Telegram send failed for %s", sig["symbol"])
    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
