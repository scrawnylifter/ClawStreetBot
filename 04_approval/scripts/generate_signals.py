#!/usr/bin/env python3
"""Daily composite signal generator — writes to market.signal_alerts.

For each active watchlist symbol, compute a 0-100 composite score from the
six analytics components (IV regime, IV-RV spread, GEX/DEX, technicals,
sentiment, IV outlier).  When the composite exceeds a threshold (default 55)
AND the EMA trend gives a clear direction, insert a row into
market.signal_alerts and optionally send a Telegram alert.

Scoring weights:
    IV regime          0-25
    IV-RV spread       0-15
    GEX/DEX            0-20
    Technicals         0-20
    Sentiment          0-10
    IV outlier flag    0-10
                      ----
    Composite          0-100

Direction logic:
    EMA-9 > EMA-21 → bullish (calls)
    EMA-9 < EMA-21 → bearish (puts)
    EMA gap < 0.5%  → no signal (flat)

ATR-based trade plan (daily swing):
    Stop  = ATR × 2
    TP1   = ATR × 6
    TP2   = ATR × 10

Usage:
    python 04_approval/scripts/generate_signals.py                # all active symbols
    python 04_approval/scripts/generate_signals.py --all
    python 04_approval/scripts/generate_signals.py --symbol NVDA
    python 04_approval/scripts/generate_signals.py --dry-run       # stdout only
    python 04_approval/scripts/generate_signals.py --threshold 60  # custom alert threshold
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timezone
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "shared"))
from constants import DB_CONFIG, load_env  # noqa: E402

load_env(".env.db")
load_env(".env.alpaca")
load_env(".env.telegram")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Allow shared imports regardless of CWD
# ---------------------------------------------------------------------------
sys.path.insert(0, str(PROJECT_ROOT / "shared"))
sys.path.insert(0, str(PROJECT_ROOT / "03_alert" / "scripts"))
from fetch_alpaca_snapshot import (  # noqa: E402
    get_underlying_price,
    select_best_option,
)
from alert_telegram import (  # noqa: E402
    get_telegram_config,
    send_telegram_message,
)

# ---------------------------------------------------------------------------
# Scoring functions (unchanged from original)
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLD = 55.0
ATR_STOP_MULT = 2.0
ATR_TP1_MULT = 6.0
ATR_TP2_MULT = 10.0
EMA_GAP_MIN_PCT = 0.5  # minimum % separation for a direction signal
MIN_DTE = 30
MAX_DTE = 120
DELTA_MIN = 0.50
DELTA_MAX = 0.70
RISK_BUDGET_DEFAULT = 2000.0


def score_iv_regime(iv_rank: float | None) -> tuple[float, str]:
    if iv_rank is None:
        return 0.0, "unknown"
    if iv_rank < 25:
        return 25.0, "buy_premium"
    if iv_rank < 50:
        return 15.0, "directional"
    if iv_rank < 75:
        return 5.0, "spreads_cautious"
    return 0.0, "sell_premium"


def score_iv_rv_spread(spread: float | None) -> float:
    if spread is None:
        return 0.0
    if spread < -0.10:
        return 15.0
    if spread < -0.05:
        return 10.0
    if spread < 0.05:
        return 5.0
    if spread < 0.15:
        return 3.0
    return 0.0


def score_gex_dex(
    net_gex: float | None,
    net_dex: float | None,
    has_nearby_strike: bool,
) -> float:
    """GEX (0–15) + DEX direction tailwind (0–5) = 0–20."""
    score = 0.0
    if net_gex is None:
        gex_pts = 0.0
    elif net_gex < 0:
        gex_pts = 15.0
    elif has_nearby_strike:
        gex_pts = 10.0
    else:
        gex_pts = 3.0
    score += gex_pts
    if net_dex is not None and net_dex > 0:
        score += 5.0
    return score


def score_technicals(
    rsi: float | None,
    close: float | None,
    ema_21: float | None,
    macd_hist: float | None,
    macd_hist_prev: float | None,
) -> float:
    score = 0.0
    if rsi is not None:
        if rsi < 30:
            score += 10.0
        elif rsi <= 70:
            score += 5.0
        else:
            score += 2.0
    if close is not None and ema_21 is not None:
        if close > ema_21:
            score += 5.0
    if macd_hist is not None and macd_hist_prev is not None:
        if macd_hist_prev <= 0 < macd_hist:
            score += 5.0
    return score


def score_sentiment(mention_count: int) -> float:
    if mention_count <= 0:
        return 0.0
    return min(10.0, mention_count * 0.5)


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def _fnum(x) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
        return None if v != v else v  # NaN guard
    except (TypeError, ValueError):
        return None


def fetch_inputs(conn, symbol: str, asof: date) -> dict[str, Any]:
    """Pull all analytics for `symbol` as-of `asof`."""
    out: dict[str, Any] = {"symbol": symbol, "asof": asof}
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # --- asset id ---
        cur.execute("SELECT id FROM market.assets WHERE symbol = %s", (symbol,))
        row = cur.fetchone()
        out["asset_id"] = row["id"] if row else None

        # --- IV rank ---
        cur.execute(
            """
            SELECT iv_rank_52w, current_iv, iv_percentile, date
            FROM market.iv_rank
            WHERE symbol = %s AND date <= %s
            ORDER BY date DESC LIMIT 1
            """,
            (symbol, asof),
        )
        r = cur.fetchone()
        out["iv_rank"] = _fnum(r["iv_rank_52w"]) if r else None
        out["current_iv"] = _fnum(r["current_iv"]) if r else None
        out["iv_percentile"] = _fnum(r["iv_percentile"]) if r else None

        # --- realized vol / IV-RV spread ---
        cur.execute(
            """
            SELECT rv_20d FROM market.realized_vol
            WHERE symbol = %s AND date <= %s
            ORDER BY date DESC LIMIT 1
            """,
            (symbol, asof),
        )
        r = cur.fetchone()
        rv_20d = _fnum(r["rv_20d"]) if r else None
        out["rv_20d"] = rv_20d
        if out["current_iv"] is not None and rv_20d is not None:
            out["iv_rv_spread"] = round(out["current_iv"] - rv_20d, 6)
        else:
            out["iv_rv_spread"] = None

        # --- GEX / DEX ---
        cur.execute(
            """
            SELECT total_net_gex, total_net_dex, date
            FROM market.gex_dex_overview
            WHERE underlying = %s AND date <= %s
            ORDER BY date DESC LIMIT 1
            """,
            (symbol, asof),
        )
        r = cur.fetchone()
        out["net_gex"] = _fnum(r["total_net_gex"]) if r else None
        out["net_dex"] = _fnum(r["total_net_dex"]) if r else None
        gex_date = r["date"] if r else None

        # --- Latest close + volume ratio from daily bars ---
        cur.execute(
            """
            SELECT o.close, o.volume, o.asset_id
            FROM market.ohlcv o
            JOIN market.assets a ON a.id = o.asset_id
            WHERE a.symbol = %s AND o.timeframe = '1d'
              AND o.timestamp::date <= %s
            ORDER BY o.timestamp DESC LIMIT 1
            """,
            (symbol, asof),
        )
        r = cur.fetchone()
        out["close"] = _fnum(r["close"]) if r else None
        asset_id = r["asset_id"] if r else None

        # Volume ratio (today's vol / 20-day avg)
        out["volume_ratio"] = None
        if asset_id:
            cur.execute(
                """
                SELECT AVG(vol) AS avg_vol FROM (
                    SELECT o.volume AS vol
                    FROM market.ohlcv o
                    WHERE o.asset_id = %s AND o.timeframe = '1d'
                      AND o.timestamp::date <= %s
                    ORDER BY o.timestamp DESC LIMIT 21
                ) sub
                """,
                (asset_id, asof),
            )
            vr = cur.fetchone()
            if r and r["volume"] and vr and vr["avg_vol"]:
                out["volume_ratio"] = round(float(r["volume"]) / float(vr["avg_vol"]), 4)

        # --- nearby GEX strike check ---
        has_nearby = False
        if out["close"] is not None and gex_date is not None:
            cur.execute(
                """
                SELECT 1 FROM market.gex_dex
                WHERE underlying = %s AND date = %s
                  AND ABS(strike - %s) / %s <= 0.05
                LIMIT 1
                """,
                (symbol, gex_date, out["close"], out["close"]),
            )
            has_nearby = cur.fetchone() is not None
        out["has_nearby_strike"] = has_nearby

        # --- Technical indicators (daily) ---
        cur.execute(
            """
            SELECT ema_9, ema_21, rsi_14, macd_hist, atr_14, date
            FROM market.technical_indicators
            WHERE symbol = %s AND date <= %s
            ORDER BY date DESC LIMIT 2
            """,
            (symbol, asof),
        )
        rows = cur.fetchall()
        if rows:
            latest = rows[0]
            out["ema_9"] = _fnum(latest["ema_9"])
            out["ema_21"] = _fnum(latest["ema_21"])
            out["rsi_14"] = _fnum(latest["rsi_14"])
            out["atr_14"] = _fnum(latest["atr_14"])
            out["macd_hist"] = _fnum(latest["macd_hist"])
            if len(rows) > 1 and rows[1]["macd_hist"] is not None:
                out["macd_hist_prev"] = _fnum(rows[1]["macd_hist"])
            else:
                out["macd_hist_prev"] = None
        else:
            out["ema_9"] = out["ema_21"] = out["rsi_14"] = None
            out["atr_14"] = out["macd_hist"] = out["macd_hist_prev"] = None

        # --- Sentiment ---
        since = asof - __import__("datetime").timedelta(days=1)
        cur.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM scraper.articles
                 WHERE %s = ANY(symbols) AND published_at >= %s) AS arts,
                (SELECT COUNT(*) FROM scraper.posts
                 WHERE %s = ANY(symbols) AND posted_at >= %s) AS posts
            """,
            (symbol, since, symbol, since),
        )
        r = cur.fetchone()
        out["mention_count"] = int((r["arts"] or 0) + (r["posts"] or 0)) if r else 0

        # --- IV outlier ---
        cur.execute(
            """
            SELECT z_score, direction, date FROM market.iv_outliers
            WHERE symbol = %s AND date <= %s
            ORDER BY date DESC LIMIT 1
            """,
            (symbol, asof),
        )
        r = cur.fetchone()
        out["iv_outlier_flag"] = False
        out["iv_outlier_z"] = None
        out["iv_outlier_direction"] = None
        if r and r["date"] >= asof - __import__("datetime").timedelta(days=5):
            out["iv_outlier_flag"] = True
            out["iv_outlier_z"] = _fnum(r["z_score"])
            out["iv_outlier_direction"] = r["direction"]

        # --- Trend status ---
        cur.execute(
            """
            SELECT adx, micro_trend, intermediate_trend, primary_trend,
                   trend_score, ema_stack
            FROM market.trend_status
            WHERE symbol = %s AND date <= %s
            ORDER BY date DESC LIMIT 1
            """,
            (symbol, asof),
        )
        r = cur.fetchone()
        if r:
            out["adx"] = _fnum(r["adx"])
            out["micro_trend"] = r["micro_trend"]
            out["intermediate_trend"] = r["intermediate_trend"]
            out["primary_trend"] = r["primary_trend"]
            out["trend_score"] = _fnum(r["trend_score"])
            out["ema_stack"] = r["ema_stack"]
        else:
            out["adx"] = None
            out["micro_trend"] = None
            out["intermediate_trend"] = None
            out["primary_trend"] = None
            out["trend_score"] = None
            out["ema_stack"] = None

    return out


def fetch_current_regime(conn) -> str:
    """Get the latest market regime label."""
    with conn.cursor() as cur:
        cur.execute("SELECT regime FROM market.regime ORDER BY date DESC LIMIT 1")
        row = cur.fetchone()
    return row[0] if row else "unknown"


def active_symbols(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol FROM market.assets WHERE active = TRUE ORDER BY symbol"
        )
        return [r[0] for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Composite scoring
# ---------------------------------------------------------------------------

def score_symbol(inputs: dict[str, Any]) -> dict[str, Any]:
    iv_pts, regime = score_iv_regime(inputs["iv_rank"])
    iv_rv_pts = score_iv_rv_spread(inputs["iv_rv_spread"])
    gex_pts = score_gex_dex(
        inputs["net_gex"], inputs["net_dex"], inputs["has_nearby_strike"]
    )
    tech_pts = score_technicals(
        inputs["rsi_14"], inputs["close"], inputs["ema_21"],
        inputs["macd_hist"], inputs["macd_hist_prev"],
    )
    sent_pts = score_sentiment(inputs["mention_count"])
    outlier_pts = 10.0 if inputs["iv_outlier_flag"] else 0.0

    composite = iv_pts + iv_rv_pts + gex_pts + tech_pts + sent_pts + outlier_pts

    return {
        "composite_score": round(composite, 2),
        "iv_regime": regime,
        "iv_regime_score": iv_pts,
        "iv_rv_score": iv_rv_pts,
        "gex_score": gex_pts,
        "tech_score": tech_pts,
        "sentiment_score": sent_pts,
        "iv_outlier_score": outlier_pts,
    }


# ---------------------------------------------------------------------------
# Evaluate + build signal dict
# ---------------------------------------------------------------------------

def evaluate_symbol(
    conn,
    inputs: dict[str, Any],
    scored: dict[str, Any],
    regime: str,
    risk_budget: float,
    threshold: float,
) -> dict | None:
    """Run direction + threshold filters and build a signal_alerts row dict.

    Returns None if the symbol is filtered out.
    """
    sym = inputs["symbol"]
    ema_9 = inputs.get("ema_9")
    ema_21 = inputs.get("ema_21")
    rsi = inputs.get("rsi_14")
    atr = inputs.get("atr_14")
    close = inputs.get("close")
    adx = inputs.get("adx")

    # Need price + EMA + ATR at minimum.
    if None in (close, ema_9, ema_21, atr):
        log.info("%s: missing core indicators (close/EMA/ATR), skip", sym)
        return None

    # --- Direction from EMA crossover (same logic as scan_setups.py) ---
    ema_gap = ema_9 - ema_21
    ema_range = ema_21 or 1.0
    ema_pct = abs(ema_gap) / ema_range * 100

    if ema_pct < EMA_GAP_MIN_PCT:
        log.info("%s: EMA9/EMA21 within %.1f%% — flat, no direction", sym, ema_pct)
        return None

    if ema_gap > 0:
        direction = "bullish"
        opt_type = "C"
    else:
        direction = "bearish"
        opt_type = "P"

    log.info("%s: direction=%s (EMA9=%.2f EMA21=%.2f gap %.1f%%)",
             sym, direction, ema_9, ema_21, ema_pct)

    # --- Composite threshold ---
    composite = scored["composite_score"]
    if composite < threshold:
        log.info("%s: composite %.1f < threshold %.1f, skip", sym, composite, threshold)
        return None

    # --- Live underlying price (fall back to latest close) ---
    try:
        price = get_underlying_price(sym)
    except Exception as e:
        log.warning("%s: Alpaca snapshot error (%s)", sym, e)
        price = None
    if price is None:
        price = close

    # --- ATR-based trade plan ---
    if direction == "bullish":
        stop = round(price - atr * ATR_STOP_MULT, 2)
        tp1 = round(price + atr * ATR_TP1_MULT, 2)
        tp2 = round(price + atr * ATR_TP2_MULT, 2)
        risk_per_share = price - stop
        reward_per_share = tp1 - price
    else:
        stop = round(price + atr * ATR_STOP_MULT, 2)
        tp1 = round(price - atr * ATR_TP1_MULT, 2)
        tp2 = round(price - atr * ATR_TP2_MULT, 2)
        risk_per_share = stop - price
        reward_per_share = price - tp1

    if risk_per_share <= 0:
        log.info("%s: zero risk, skip", sym)
        return None
    risk_reward = round(reward_per_share / risk_per_share, 2)

    # --- Option selection ---
    try:
        opt = select_best_option(sym, want_type=opt_type,
                                 min_dte=MIN_DTE, max_dte=MAX_DTE)
    except Exception as e:
        log.warning("%s: option snapshot error (%s)", sym, e)
        opt = None

    # Option fields (nullable)
    option_symbol = opt["occ_symbol"] if opt else None
    option_strike = opt["strike"] if opt else None
    option_expiry = opt["expiry"] if opt else None
    option_delta = opt["delta"] if opt else None
    option_theta = opt["theta"] if opt else None
    option_bid = opt["bid"] if opt else None
    option_ask = opt["ask"] if opt else None
    option_mid = opt["mid"] if opt else None
    spread_pct = opt.get("spread_pct") if opt else None

    # Affordability gate (only if we have an option)
    if opt:
        contract_cost = opt["mid"] * 100.0
        if contract_cost > risk_budget:
            log.info("%s: option premium $%.0f > budget $%.0f, skipping option",
                     sym, contract_cost, risk_budget)
            # Keep the signal but drop the option
            option_symbol = option_strike = option_expiry = None
            option_delta = option_theta = None
            option_bid = option_ask = option_mid = None
            spread_pct = None

    # --- Invalidation rules ---
    if direction == "bullish":
        invalidation = json.dumps({
            "rules": [
                "EMA9 crosses back below EMA21 → EXIT",
                "Daily ADX drops below 20 → EXIT",
                "RSI prints > 75 with no follow-through → trim/exit",
                "Stock breaks below ATR stop → EXIT immediately",
            ],
            "context": {
                "composite_score": composite,
                "iv_regime_score": scored["iv_regime_score"],
                "iv_rv_score": scored["iv_rv_score"],
                "gex_score": scored["gex_score"],
                "tech_score": scored["tech_score"],
                "sentiment_score": scored["sentiment_score"],
                "iv_outlier_score": scored["iv_outlier_score"],
                "iv_regime": scored["iv_regime"],
                "macd_hist": inputs.get("macd_hist"),
                "macd_hist_prev": inputs.get("macd_hist_prev"),
            },
        })
    else:
        invalidation = json.dumps({
            "rules": [
                "EMA9 crosses back above EMA21 → EXIT",
                "Daily ADX drops below 20 → EXIT",
                "RSI prints < 25 with no follow-through → trim/exit",
                "Stock breaks above ATR stop → EXIT immediately",
            ],
            "context": {
                "composite_score": composite,
                "iv_regime_score": scored["iv_regime_score"],
                "iv_rv_score": scored["iv_rv_score"],
                "gex_score": scored["gex_score"],
                "tech_score": scored["tech_score"],
                "sentiment_score": scored["sentiment_score"],
                "iv_outlier_score": scored["iv_outlier_score"],
                "iv_regime": scored["iv_regime"],
                "macd_hist": inputs.get("macd_hist"),
                "macd_hist_prev": inputs.get("macd_hist_prev"),
            },
        })

    # --- daily_ema_position ---
    if ema_9 > ema_21:
        daily_ema_position = "above"
    elif ema_9 < ema_21:
        daily_ema_position = "below"
    else:
        daily_ema_position = "neutral"

    return {
        "symbol": sym,
        "strategy": "daily_signal",
        "direction": direction,
        "status": "new",
        "regime": regime,
        "timeframe": "1d",
        "trigger_price": round(price, 2),
        "ema_9": ema_9,
        "ema_21": ema_21,
        "adx": adx,
        "rsi": rsi,
        "atr_14": atr,
        "volume_ratio": inputs.get("volume_ratio"),
        "stop_price": stop,
        "tp1_price": tp1,
        "tp2_price": tp2,
        "risk_reward": risk_reward,
        "invalidation": invalidation,
        "micro_trend": inputs.get("micro_trend"),
        "intermediate_trend": inputs.get("intermediate_trend"),
        "primary_trend": inputs.get("primary_trend"),
        "trend_score": inputs.get("trend_score"),
        "ema_stack": inputs.get("ema_stack"),
        "option_symbol": option_symbol,
        "option_strike": option_strike,
        "option_expiry": option_expiry,
        "option_delta": option_delta,
        "option_theta": option_theta,
        "option_bid": option_bid,
        "option_ask": option_ask,
        "option_mid": option_mid,
        "spread_pct": spread_pct,
        "iv_rank": inputs.get("iv_rank"),
        "iv_rv_spread": inputs.get("iv_rv_spread"),
        "net_gex": inputs.get("net_gex"),
        "daily_trend": inputs.get("primary_trend"),
        "daily_ema_position": daily_ema_position,
        # Keep composite + breakdown for alert formatting
        "_composite": composite,
        "_iv_current": inputs.get("current_iv"),
        "_rv_20d": inputs.get("rv_20d"),
        "_iv_percentile": inputs.get("iv_percentile"),
        "_scored": scored,
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_signal(conn, sig: dict) -> int | None:
    """Insert into market.signal_alerts; returns row id, or None on duplicate."""
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO market.signal_alerts (
                symbol, strategy, direction, status, regime, timeframe,
                trigger_price, ema_9, ema_21, adx, rsi, atr_14,
                volume_ratio,
                stop_price, tp1_price, tp2_price, risk_reward,
                composite_score,
                invalidation,
                micro_trend, intermediate_trend, primary_trend,
                trend_score, ema_stack,
                option_symbol, option_strike, option_expiry,
                option_delta, option_theta,
                option_bid, option_ask, option_mid,
                spread_pct,
                iv_rank, iv_rv_spread, net_gex,
                daily_trend, daily_ema_position
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s,
                %s, %s, %s, %s,
                %s,
                %s,
                %s, %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s, %s,
                %s,
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
            sig.get("volume_ratio"),
            sig["stop_price"], sig["tp1_price"], sig["tp2_price"],
            sig["risk_reward"],
            sig["_composite"],
            sig["invalidation"],
            sig.get("micro_trend"), sig.get("intermediate_trend"),
            sig.get("primary_trend"),
            sig.get("trend_score"), sig.get("ema_stack"),
            sig["option_symbol"], sig["option_strike"], sig["option_expiry"],
            sig["option_delta"], sig["option_theta"],
            sig["option_bid"], sig["option_ask"], sig["option_mid"],
            sig.get("spread_pct"),
            sig.get("iv_rank"), sig.get("iv_rv_spread"),
            sig.get("net_gex"),
            sig.get("daily_trend"), sig.get("daily_ema_position"),
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


# ---------------------------------------------------------------------------
# Telegram formatting (scan_setups style)
# ---------------------------------------------------------------------------

def format_daily_alert(sig: dict) -> str:
    """Format a daily composite signal as a Telegram trade alert."""
    direction = sig["direction"]
    emoji = "🟢" if direction == "bullish" else "🔴"
    action = "BUY" if direction == "bullish" else "SELL/PUT"
    contract_letter = "C" if direction == "bullish" else "P"

    composite = sig.get("_composite", 0)
    iv_current = sig.get("_iv_current")
    iv_pct = sig.get("_iv_percentile") or 0
    rv_20d = sig.get("_rv_20d")
    iv_rv_spread = sig.get("iv_rv_spread") or 0

    iv_label = "cheap" if iv_pct < 25 else ("slightly rich" if iv_pct > 50 else "moderate")
    spread_label = (
        "options cheap vs realized" if iv_rv_spread < -0.05 else
        "options rich vs realized" if iv_rv_spread > 0.05 else
        "fair pricing"
    )

    lines = [
        f"{emoji} <b>{action} Signal: {sig['symbol']}</b>",
        f"{'─' * 30}",
        f"Stock: ${sig['trigger_price']:.2f} | Trend: {sig.get('primary_trend', '?')} | RSI: {sig['rsi']:.0f}",
        f"Composite: {composite:.1f}/100 | Regime: {sig.get('regime', '?')}",
    ]

    if iv_current is not None:
        iv_pct_disp = round(iv_current * 100)
        lines.append(f"IV: {iv_pct_disp}% (rank {iv_pct:.0f}th pctl) — {iv_label}")

    if rv_20d is not None:
        rv_pct_disp = round(rv_20d * 100)
        lines.append(f"RV: {rv_pct_disp}% | IV-RV spread: {iv_rv_spread:+.2f} — {spread_label}")

    volume_ratio = sig.get("volume_ratio")
    if volume_ratio:
        lines.append(f"Vol: {volume_ratio:.1f}x avg")
    net_gex = sig.get("net_gex")
    if net_gex:
        lines.append(f"GEX: ${net_gex / 1e6:+.0f}M")

    lines.append("")
    lines.append(f"ATR Stop: ${sig['stop_price']:.2f} | TP1: ${sig['tp1_price']:.2f} | TP2: ${sig['tp2_price']:.2f}")
    lines.append(f"Risk/Reward: {sig['risk_reward']:.1f}:1")

    if sig.get("option_symbol"):
        lines.append("")
        lines.append(
            f"Contract: {sig['symbol']} {sig['option_expiry']} "
            f"${sig['option_strike']:.0f}{contract_letter} @ ${sig['option_mid']:.2f}"
        )
        lines.append(
            f"Delta {sig['option_delta']:.2f} | Theta {sig['option_theta']:.2f}"
        )

    # Bail conditions
    inv = sig.get("invalidation")
    if inv:
        if isinstance(inv, str):
            inv = json.loads(inv)
        rules = inv.get("rules", [])
        if rules:
            lines.append("")
            lines.append("<b>Bail if:</b>")
            for rule in rules:
                lines.append(f"  ⛔ {rule}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--symbol", help="Single symbol to evaluate")
    g.add_argument("--all", action="store_true",
                    help="All active watchlist symbols (default)")
    p.add_argument("--asof", type=date.fromisoformat, default=date.today(),
                   help="As-of date (YYYY-MM-DD, default: today)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print alerts to stdout; do not write DB or send Telegram")
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                   help=f"Minimum composite score to emit alert (default {DEFAULT_THRESHOLD})")
    p.add_argument("--risk-budget", type=float, default=RISK_BUDGET_DEFAULT,
                   help=f"Max per-contract option premium in dollars (default {RISK_BUDGET_DEFAULT})")
    args = p.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)

    try:
        if args.symbol:
            symbols = [args.symbol.upper()]
        else:
            symbols = active_symbols(conn)

        regime = fetch_current_regime(conn)

        print(f"Generating daily signals as of {args.asof} for {len(symbols)} symbols "
              f"(regime={regime}, threshold={args.threshold})")

        passed: list[dict] = []

        for sym in symbols:
            inputs = fetch_inputs(conn, sym, args.asof)
            scored = score_symbol(inputs)

            log.info("  %s: composite=%.2f  type=%s  regime=%s",
                     sym, scored["composite_score"],
                     "bullish" if scored["composite_score"] > 55 else
                     ("neutral" if scored["composite_score"] >= 40 else "bearish"),
                     scored["iv_regime"])

            sig = evaluate_symbol(conn, inputs, scored, regime,
                                  args.risk_budget, args.threshold)
            if sig:
                passed.append(sig)
                log.info("%s: PASS — %s composite=%.1f R:R=%.1f",
                         sym, sig["direction"], sig["_composite"], sig["risk_reward"])

        if not passed:
            print("No symbols qualified — staying silent.")
            return 0

        print(f"\n{len(passed)} symbol(s) qualified")

        # ---- Dry-run: print and exit ----
        if args.dry_run:
            for sig in passed:
                print("=" * 60)
                print(format_daily_alert(sig))
                print("=" * 60)
            return 0

        # ---- Real run: write DB rows, then Telegram ----
        tg_config = get_telegram_config()
        tg_token = tg_config.get("TELEGRAM_BOT_TOKEN")
        tg_chat_id = tg_config.get("TELEGRAM_CHAT_ID")
        if not tg_token or not tg_chat_id:
            log.error("Missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID — alerts not sent")
            return 1

        cur = conn.cursor()
        for sig in passed:
            sig_id = save_signal(conn, sig)
            if sig_id is None:
                log.info("%s: duplicate signal, skipping send", sig["symbol"])
                continue
            text = format_daily_alert(sig)
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

    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
