#!/usr/bin/env python3
"""Intraday signal refresh — re-evaluate technicals every 5 minutes.

The daily signal pipeline (generate_signals.py) computes all 6 factors once
per day at 19:30 ET. This script runs every 5 minutes during market hours
and re-scores the **technical component only** (RSI, EMA proximity, MACD
crossover) from the latest 5m OHLCV bars, then combines it with the five
remaining daily factors (IV regime, IV-RV spread, GEX/DEX, sentiment, IV
outlier) to produce an updated composite score.

If the updated composite crosses an alert threshold (default 60), the
script prints an alert line suitable for downstream notification.

Usage::

    python scripts/intraday_signal.py                # all active symbols
    python scripts/intraday_signal.py --symbol NVDA   # single symbol
    python scripts/intraday_signal.py --threshold 55  # custom alert threshold
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import Json, RealDictCursor

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))


def load_env(filename: str) -> None:
    """Populate os.environ from a dotenv-style file."""
    path = PROJECT_ROOT / filename
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


load_env(".env.db")

DB_CONFIG = {
    "host": os.environ.get("POSTGRES_HOST", "localhost"),
    "port": int(os.environ.get("POSTGRES_PORT", 5432)),
    "dbname": os.environ["POSTGRES_DB"],
    "user": os.environ["POSTGRES_USER"],
    "password": os.environ["POSTGRES_PASSWORD"],
}

# Reuse the same scoring functions as generate_signals.py

def score_iv_regime(iv_rank: float | None) -> tuple[float, str]:
    """Map IV rank percentile to regime points + regime label."""
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
    """GEX (0-15) + DEX direction tailwind (0-5) = 0-20."""
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


def score_sentiment(mention_count: int) -> float:
    """Proportional 0-10. Cap at 20 mentions -> 10 points."""
    if mention_count <= 0:
        return 0.0
    return min(10.0, mention_count * 0.5)


# ---------- Intraday technical scoring ----------

def compute_intraday_technicals(
    conn, symbol: str, asof: date
) -> tuple[float, float | None, float | None, float | None, float | None, float, float | None]:
    """Compute RSI-14, EMA-21, and MACD from 5m bars up to current time.

    Returns (tech_score, rsi, close, ema_21, macd_hist, vwap_ratio, atr) where:
    - tech_score is 0-20 using the same scoring as generate_signals.py
    - vwap_ratio = close / vwap (1.0 = at VWAP, >1 = above)
    - atr is the daily ATR-14 from market.technical_indicators
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Fetch last ~300 bars of 5m data (enough for RSI-14 x 20 periods + EMA-21)
        cur.execute(
            """
            SELECT o.timestamp, o.open, o.high, o.low, o.close, o.volume
            FROM market.ohlcv o
            JOIN market.assets a ON a.id = o.asset_id
            WHERE a.symbol = %s
              AND o.timeframe = '5m'
              AND o.timestamp::date <= %s
            ORDER BY o.timestamp ASC
            LIMIT 300
            """,
            (symbol, asof),
        )
        bars = cur.fetchall()

    if len(bars) < 50:
        return 0.0, None, None, None, None, 1.0, None

    closes = [float(b["close"]) for b in bars]
    volumes = [float(b["volume"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    current_close = closes[-1]

    # RSI-14 from 5m closes
    rsi = _rsi(closes, 14)

    # EMA-21 from 5m closes
    ema_21 = _ema(closes, 21)

    # MACD (12, 26, 9) from 5m closes
    macd_hist, macd_hist_prev = _macd_hist(closes)

    # VWAP approximation (cumulative typical-price * volume / cumulative volume)
    vwap = _vwap(highs, lows, closes, volumes)
    vwap_ratio = current_close / vwap if vwap and vwap > 0 else 1.0

    # Fetch daily ATR for reference
    atr: float | None = None
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT atr_14 FROM market.technical_indicators
            WHERE symbol = %s AND date <= %s AND atr_14 IS NOT NULL
            ORDER BY date DESC LIMIT 1
            """,
            (symbol, asof),
        )
        row = cur.fetchone()
        atr = float(row[0]) if row and row[0] else None

    # Score using same logic as generate_signals.py score_technicals()
    tech_score = 0.0
    if rsi is not None:
        if rsi < 30:
            tech_score += 10.0
        elif rsi <= 70:
            tech_score += 5.0
        else:
            tech_score += 2.0
    if current_close is not None and ema_21 is not None:
        if current_close > ema_21:
            tech_score += 5.0
    if macd_hist is not None and macd_hist_prev is not None:
        if macd_hist_prev <= 0 < macd_hist:
            tech_score += 5.0

    return tech_score, rsi, current_close, ema_21, macd_hist, vwap_ratio, atr


def _rsi(prices: list[float], period: int = 14) -> float | None:
    """Simple RSI calculation."""
    if len(prices) < period + 1:
        return None
    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(c, 0) for c in changes]
    losses = [max(-c, 0) for c in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    if avg_loss == 0:
        return 100.0
    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _ema(prices: list[float], period: int) -> float | None:
    """Exponential moving average."""
    if len(prices) < period:
        return None
    k = 2.0 / (period + 1)
    ema_val = sum(prices[:period]) / period
    for p in prices[period:]:
        ema_val = p * k + ema_val * (1 - k)
    return ema_val


def _macd_hist(prices: list[float]) -> tuple[float | None, float | None]:
    """MACD histogram (current, previous). Returns (hist, hist_prev)."""
    ema12 = _ema(prices, 12)
    ema26 = _ema(prices, 26)
    if ema12 is None or ema26 is None:
        return None, None
    macd_line = ema12 - ema26

    # Compute signal line (9-period EMA of MACD values)
    if len(prices) < 35:
        return None, None
    # Build MACD series
    macd_vals = []
    for i in range(26, len(prices)):
        e12 = _ema(prices[: i + 1], 12)
        e26 = _ema(prices[: i + 1], 26)
        if e12 is not None and e26 is not None:
            macd_vals.append(e12 - e26)
    if len(macd_vals) < 9:
        return None, None
    signal = sum(macd_vals[-9:]) / 9
    hist = macd_vals[-1] - signal
    hist_prev = None
    if len(macd_vals) >= 10:
        signal_prev = sum(macd_vals[-10:-1]) / 9
        hist_prev = macd_vals[-2] - signal_prev
    return hist, hist_prev


def _vwap(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
) -> float | None:
    """Cumulative VWAP for the trading day."""
    cum_tp_vol = 0.0
    cum_vol = 0.0
    for h, l, c, v in zip(highs, lows, closes, volumes):
        if v > 0:
            cum_tp_vol += ((h + l + c) / 3) * v
            cum_vol += v
    return cum_tp_vol / cum_vol if cum_vol > 0 else None


# ---------- Daily factor retrieval ----------

def fetch_daily_factors(conn, symbol: str, asof: date) -> dict[str, Any]:
    """Pull the 5 non-technical factor scores from today's daily signal."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT iv_regime_score, iv_rv_score, gex_score,
                   sentiment_score, iv_outlier_score,
                   iv_regime, iv_rv_spread, composite_score as daily_composite
            FROM trading.signals
            WHERE symbol = %s AND signal_date = %s
            """,
            (symbol, asof),
        )
        row = cur.fetchone()
        if row:
            return dict(row)

    # No daily signal exists yet — compute from raw data
    factors: dict[str, Any] = {}
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT iv_rank_52w, current_iv
            FROM market.iv_rank
            WHERE symbol = %s AND date <= %s
            ORDER BY date DESC LIMIT 1
            """,
            (symbol, asof),
        )
        r = cur.fetchone()
        iv_rank = float(r["iv_rank_52w"]) if r and r["iv_rank_52w"] else None
        current_iv = float(r["current_iv"]) if r and r["current_iv"] else None
        iv_score, regime = score_iv_regime(iv_rank)
        factors["iv_regime_score"] = iv_score
        factors["iv_regime"] = regime

        cur.execute(
            """
            SELECT rv_20d FROM market.realized_vol
            WHERE symbol = %s AND date <= %s
            ORDER BY date DESC LIMIT 1
            """,
            (symbol, asof),
        )
        r = cur.fetchone()
        rv_20d = float(r["rv_20d"]) if r and r["rv_20d"] else None
        spread = (current_iv - rv_20d) if current_iv and rv_20d else None
        factors["iv_rv_score"] = score_iv_rv_spread(spread)
        factors["iv_rv_spread"] = spread

        cur.execute(
            """
            SELECT total_net_gex, total_net_dex FROM market.gex_dex_overview
            WHERE underlying = %s AND date <= %s
            ORDER BY date DESC LIMIT 1
            """,
            (symbol, asof),
        )
        r = cur.fetchone()
        net_gex = float(r["total_net_gex"]) if r and r["total_net_gex"] else None
        net_dex = float(r["total_net_dex"]) if r and r["total_net_dex"] else None

        cur.execute(
            """
            SELECT 1 FROM market.gex_dex
            WHERE underlying = %s AND date <= %s
              AND ABS(strike - %s) <= 5
            LIMIT 1
            """,
            (symbol, asof, current_iv or 0),
        )
        has_nearby = cur.fetchone() is not None
        factors["gex_score"] = score_gex_dex(net_gex, net_dex, has_nearby)

        cur.execute(
            """
            SELECT COUNT(*) as cnt FROM (
                SELECT 1 FROM scraper.articles
                WHERE %s = ANY(symbols) AND published_at::date <= %s
                UNION ALL
                SELECT 1 FROM scraper.posts
                WHERE %s = ANY(symbols) AND published_at::date <= %s
            ) sub
            """,
            (symbol, asof, symbol, asof),
        )
        mentions = int(cur.fetchone()["cnt"])  # type: ignore[index]
        factors["sentiment_score"] = score_sentiment(mentions)

        cur.execute(
            """
            SELECT 1 FROM market.iv_outliers
            WHERE symbol = %s AND date <= %s
            LIMIT 1
            """,
            (symbol, asof),
        )
        is_outlier = cur.fetchone() is not None
        factors["iv_outlier_score"] = 10.0 if is_outlier else 0.0

    factors["daily_composite"] = None
    return factors


def fetch_active_symbols(conn) -> list[str]:
    """Return all active watchlist symbols."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol FROM market.assets WHERE active = true ORDER BY symbol"
        )
        return [r[0] for r in cur.fetchall()]


def fetch_trend_status(conn, symbol: str, asof: date) -> dict[str, Any]:
    """Pull the latest trend status for swing trade validation."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT trend_score, intermediate_trend, primary_trend,
                   micro_trend, adx, ema_stack, price_structure, trend_strength
            FROM market.trend_status
            WHERE symbol = %s AND date <= %s
            ORDER BY date DESC LIMIT 1
            """,
            (symbol, asof),
        )
        row = cur.fetchone()
        if row:
            return {
                "trend_score": float(row["trend_score"]) if row["trend_score"] else 50.0,
                "intermediate_trend": row["intermediate_trend"] or "neutral",
                "primary_trend": row["primary_trend"] or "neutral",
                "micro_trend": row["micro_trend"] or "neutral",
                "adx": float(row["adx"]) if row["adx"] else None,
                "ema_stack": row["ema_stack"] or "partial",
                "price_structure": row["price_structure"] or "mixed",
                "trend_strength": row["trend_strength"] or "no_trend",
            }
    return {"trend_score": 50.0, "intermediate_trend": "neutral"}


def signal_type_for(composite: float) -> str:
    if composite > 55:
        return "bullish"
    if composite >= 40:
        return "neutral"
    return "bearish"


def strategy_for(composite: float) -> str:
    if composite >= 70:
        return "aggressive"
    if composite >= 50:
        return "moderate"
    if composite >= 30:
        return "cautious"
    return "no_trade"


# ATR multipliers for intraday (5m) timeframe
ATR_STOP_MULT_5M = 1.5
ATR_TP1_MULT_5M = 4.5
ATR_TP2_MULT_5M = 7.5


def write_signal_alert(
    conn,
    symbol: str,
    direction: str,
    composite: float,
    price: float,
    ema_21: float | None,
    rsi: float | None,
    atr: float | None,
    daily_factors: dict[str, Any],
    trend_info: dict[str, Any],
    details: dict[str, Any],
    strategy_tag: str = "intraday_signal",
) -> int | None:
    """Insert into market.signal_alerts when composite crosses threshold.

    Returns the row id, or None on duplicate/missing data.
    """
    if price is None or atr is None or atr <= 0:
        return None

    # ATR-based stops for intraday timeframe
    if direction == "bullish":
        stop = round(price - atr * ATR_STOP_MULT_5M, 2)
        tp1 = round(price + atr * ATR_TP1_MULT_5M, 2)
        tp2 = round(price + atr * ATR_TP2_MULT_5M, 2)
        risk = price - stop
        reward = tp1 - price
    else:
        stop = round(price + atr * ATR_STOP_MULT_5M, 2)
        tp1 = round(price - atr * ATR_TP1_MULT_5M, 2)
        tp2 = round(price - atr * ATR_TP2_MULT_5M, 2)
        risk = stop - price
        reward = price - tp1

    risk_reward = round(reward / risk, 2) if risk > 0 else 0.0
    regime = daily_factors.get("iv_regime", "unknown")
    iv = daily_factors.get("current_iv")
    iv_pct = daily_factors.get("iv_percentile")
    iv_rank = daily_factors.get("iv_rank_52w")
    rv20 = daily_factors.get("rv_20d")
    iv_rv_spread = daily_factors.get("iv_rv_spread")
    adx = daily_factors.get("adx")
    net_gex = daily_factors.get("net_gex")
    volume_ratio = details.get("vwap_ratio")  # approximate

    invalidation = {
        "context": {
            "source": "intraday_5m",
            "composite_score": round(composite, 2),
            "tech_score_intraday": details.get("tech_score_intraday"),
            "trend_adjustment": details.get("trend_adjustment"),
            "raw_composite": details.get("raw_composite"),
        },
        "rules": [
            "Composite drops below 40 → EXIT",
            "Price breaks ATR stop → EXIT immediately",
        ],
    }

    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO market.signal_alerts (
                symbol, strategy, direction, status, regime, timeframe,
                trigger_price, ema_21, adx, rsi, atr_14,
                stop_price, tp1_price, tp2_price, risk_reward,
                invalidation, iv_rank, iv_rv_spread, net_gex,
                volume_ratio, trend_score, intermediate_trend,
                composite_score
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s
            )
            ON CONFLICT (symbol, strategy, direction, timeframe, created_at)
            DO NOTHING
            RETURNING id
            """,
            (
                symbol, strategy_tag, direction, "new", regime, "5m",
                round(price, 2), ema_21, adx, rsi, atr,
                stop, tp1, tp2, risk_reward,
                Json(invalidation), iv_rank, iv_rv_spread, net_gex,
                volume_ratio, trend_info.get("trend_score"),
                trend_info.get("intermediate_trend"),
                round(composite, 2),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return row[0] if row else None
    except Exception:
        conn.rollback()
        return None
    finally:
        cur.close()


def write_intraday_signal(
    conn,
    symbol: str,
    signal_date: date,
    composite: float,
    tech_score: float,
    daily_factors: dict[str, Any],
    intraday_details: dict[str, Any],
) -> None:
    """Upsert the intraday signal update."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO trading.signals (
                symbol, signal_date, composite_score, signal_type, strategy,
                iv_regime_score, iv_rv_score, gex_score, tech_score,
                sentiment_score, iv_outlier_score,
                iv_regime, iv_rv_spread, recommended_strategy, details,
                asset_id, price_at_signal
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s,
                %s, %s, %s, %s,
                (SELECT id FROM market.assets WHERE symbol = %s),
                %s
            )
            ON CONFLICT (symbol, signal_date) DO UPDATE SET
                composite_score = EXCLUDED.composite_score,
                signal_type = EXCLUDED.signal_type,
                strategy = EXCLUDED.strategy,
                tech_score = EXCLUDED.tech_score,
                recommended_strategy = EXCLUDED.recommended_strategy,
                details = EXCLUDED.details,
                price_at_signal = EXCLUDED.price_at_signal
            """,
            (
                symbol,
                signal_date,
                composite,
                signal_type_for(composite),
                strategy_for(composite),
                daily_factors.get("iv_regime_score", 0),
                daily_factors.get("iv_rv_score", 0),
                daily_factors.get("gex_score", 0),
                tech_score,
                daily_factors.get("sentiment_score", 0),
                daily_factors.get("iv_outlier_score", 0),
                daily_factors.get("iv_regime", "unknown"),
                daily_factors.get("iv_rv_spread"),
                strategy_for(composite),
                Json(intraday_details),
                symbol,
                intraday_details.get("price"),
            ),
        )
    conn.commit()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", help="Single symbol to evaluate")
    p.add_argument("--threshold", type=float, default=60.0,
                    help="Alert threshold for composite score (default: 60)")
    p.add_argument("--quiet", action="store_true",
                    help="Only output alerts (no per-symbol lines)")
    p.add_argument("--dry-run", action="store_true",
                    help="No DB writes or Telegram sends; stdout only")
    args = p.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    today = date.today()

    symbols = [args.symbol.upper()] if args.symbol else fetch_active_symbols(conn)

    alerts: list[str] = []
    updated = 0

    for sym in symbols:
        # Compute intraday technical score
        tech_score, rsi, price, ema_21, macd_hist, vwap_ratio, atr = (
            compute_intraday_technicals(conn, sym, today)
        )

        if price is None:
            continue

        # Fetch the 5 daily factor scores
        daily = fetch_daily_factors(conn, sym, today)

        # Fetch trend status for swing trade validation
        trend_info = fetch_trend_status(conn, sym, today)
        trend_score = trend_info.get("trend_score", 50.0)
        intermediate_trend = trend_info.get("intermediate_trend", "neutral")

        # Recompute composite: replace tech_score with intraday version
        iv_regime_pts = float(daily.get("iv_regime_score") or 0)
        iv_rv_pts = float(daily.get("iv_rv_score") or 0)
        gex_pts = float(daily.get("gex_score") or 0)
        sentiment_pts = float(daily.get("sentiment_score") or 0)
        outlier_pts = float(daily.get("iv_outlier_score") or 0)
        raw_composite = iv_regime_pts + iv_rv_pts + gex_pts + tech_score + sentiment_pts + outlier_pts

        # Trend-aware adjustment: penalize counter-trend signals, boost alignment
        # Trend score 0-100 where <30 = strong bearish trend, >70 = strong bullish
        # If composite is bullish but trend is bearish → reduce confidence
        # If composite and trend agree → boost slightly
        if raw_composite >= 55:  # bullish signal
            if intermediate_trend == "bull":
                composite = min(100.0, raw_composite * 1.05)  # +5% trend alignment bonus
            elif intermediate_trend == "bear":
                composite = raw_composite * 0.80  # -20% counter-trend penalty
            else:
                composite = raw_composite  # neutral trend = no adjustment
        elif raw_composite <= 30:  # bearish signal
            if intermediate_trend == "bear":
                composite = raw_composite  # bear signal in bear trend = stay as-is (not buying)
            elif intermediate_trend == "bull":
                composite = max(0.0, raw_composite * 0.90)  # less bearish in bull trend
            else:
                composite = raw_composite
        else:
            composite = raw_composite

        daily_comp = daily.get("daily_composite")
        delta = None
        if daily_comp is not None:
            delta = composite - float(daily_comp)

        # Build details dict
        details: dict[str, Any] = {
            "source": "intraday_5m",
            "tech_score_intraday": tech_score,
            "rsi_5m": round(rsi, 2) if rsi else None,
            "ema_21_5m": round(ema_21, 4) if ema_21 else None,
            "macd_hist_5m": round(macd_hist, 4) if macd_hist else None,
            "vwap_ratio": round(vwap_ratio, 4),
            "atr_daily": atr,
            "daily_composite": float(daily_comp) if daily_comp else None,
            "delta_from_daily": round(delta, 2) if delta is not None else None,
            "raw_composite": round(raw_composite, 2),
            "trend_adjustment": round(composite - raw_composite, 2),
            "trend_score": trend_score,
            "intermediate_trend": intermediate_trend,
        }

        # Write updated signal
        write_intraday_signal(conn, sym, today, composite, tech_score,
                              daily, details)

        updated += 1

        if not args.quiet:
            delta_str = f" ({delta:+.1f} vs daily)" if delta is not None else ""
            trend_adj = round(composite - raw_composite, 1)
            trend_str = f" trend={intermediate_trend[:4]}({trend_score:.0f})"
            adj_str = f" adj={trend_adj:+.1f}" if trend_adj != 0 else ""
            print(
                f"  {sym:6s}  composite={composite:5.1f}  "
                f"tech_5m={tech_score:4.1f}  rsi={rsi:5.1f}  "
                f"vwap={vwap_ratio:.3f}{trend_str}{adj_str}  "
                f"price={price:.2f}{delta_str}"
            )

        # Alert if composite crosses threshold
        if composite >= args.threshold:
            sig_type = signal_type_for(composite)
            strat = strategy_for(composite)
            direction = "bullish" if composite >= 55 else "bearish"

            # Write to market.signal_alerts for production alert pipeline.
            # alert_telegram.py is the sole dispatcher — it reads
            # telegram_sent=FALSE rows and sends with the 4-button keyboard.
            if not args.dry_run:
                alert_id = write_signal_alert(
                    conn=conn,
                    symbol=sym,
                    direction=direction,
                    composite=composite,
                    price=price,
                    ema_21=ema_21,
                    rsi=rsi,
                    atr=atr,
                    daily_factors=daily,
                    trend_info=trend_info,
                    details=details,
                )
                if alert_id:
                    log.info("Saved intraday alert for %s (id=%s, composite=%.1f) "
                             "— awaiting alert_telegram dispatch",
                             sym, alert_id, composite)

            alert = (
                f"🚀 ALERT: {sym} composite={composite:.1f} ({sig_type}, {strat}) "
                f"price={price:.2f} rsi={rsi:.1f} vwap={vwap_ratio:.3f}"
            )
            alerts.append(alert)

    # Print alerts
    for a in alerts:
        print(a)

    if not args.quiet:
        print(f"\nUpdated {updated} symbols, {len(alerts)} alerts above {args.threshold}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())