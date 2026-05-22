#!/usr/bin/env python3
"""EMA Crossover Detector — 15-Minute Intraday (Multi-Timeframe)

Detects EMA 9/21 crossovers on 15m candles, confirmed by:
  1. Daily trend alignment — EMA 9/21 on daily must agree with the signal direction
  2. ADX > 20 on daily (slightly looser than daily's 25, since intraday moves faster)

This is the primary signal for options swing trades:
  - 15m chart for entry timing (tighter stops, more signals)
  - Daily chart for trend direction (don't fight the bigger trend)
  - Hold overnight minimum (swing, not day-trade)

Trade plan (Laws of Trading — swing):
  - Stop: ATR(14) × 1.5 on 15m chart (tighter than daily's 2.0)
  - TP1: +30% from entry
  - TP2: +50% from entry, then trail
  - Risk: 10% of portfolio
  - Min DTE: 30 days for options

Schedule: Runs every 15 minutes during market hours (SESSION_OPEN–SESSION_CLOSE ET)
"""
import os
import sys
import logging
from pathlib import Path
import json
from datetime import date, datetime, time, timezone, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

import psycopg2
import numpy as np

# Allow `import fetch_alpaca_snapshot` regardless of CWD (n8n runs from /).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_alpaca_snapshot import select_best_option  # noqa: E402
from constants import is_market_day, is_market_hours  # noqa: E402

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
        dbname=conn_params.get("POSTGRES_DB", "clawstreet"),
    )


# ---------------------------------------------------------------------------
# EMA calculation
# ---------------------------------------------------------------------------

def compute_ema(prices: list[float], period: int) -> list[float | None]:
    """Calculate EMA over a price series. Returns None for warmup period."""
    if len(prices) < period:
        return [None] * len(prices)
    multiplier = 2 / (period + 1)
    ema = [None] * (period - 1)
    # Seed with SMA
    seed = sum(prices[:period]) / period
    ema.append(seed)
    for i in range(period, len(prices)):
        ema.append(prices[i] * multiplier + ema[-1] * (1 - multiplier))
    return ema


def compute_atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float | None]:
    """Calculate ATR over price series."""
    if len(highs) < period + 1:
        return [None] * len(highs)
    trs = []
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    # Simple ATR for first period, then exponential
    if len(trs) < period:
        return [None] * len(highs)
    atr = [None] * len(highs)
    first_atr = sum(trs[:period]) / period
    atr[period] = first_atr
    for i in range(period + 1, len(highs)):
        atr[i] = (atr[i - 1] * (period - 1) + trs[i - 1]) / period
    return atr


# ---------------------------------------------------------------------------
# Detection logic
# ---------------------------------------------------------------------------

def detect_15m_crossovers(conn) -> list[dict]:
    """Find EMA 9/21 crossovers on 15m candles, confirmed by daily trend."""
    cur = conn.cursor()

    # Get watchlist symbols
    cur.execute("SELECT id, symbol FROM market.assets WHERE active = TRUE ORDER BY symbol")
    watchlist = cur.fetchall()
    if not watchlist:
        log.warning("No active assets in watchlist")
        cur.close()
        return []

    # Current regime
    cur.execute("SELECT regime, spy_trend FROM market.regime ORDER BY date DESC LIMIT 1")
    regime_row = cur.fetchone()
    current_regime = regime_row[0] if regime_row else "unknown"

    signals = []
    for asset_id, symbol in watchlist:
        # --- 15m ohlcv data (last 2 trading days = ~52 candles, need more for EMA warmup) ---
        cur.execute("""
            SELECT timestamp, open, high, low, close, volume
            FROM market.ohlcv
            WHERE asset_id = %s AND timeframe = '15m'
            ORDER BY timestamp DESC
            LIMIT 200
        """, (asset_id,))
        bars = cur.fetchall()

        if len(bars) < 50:
            log.info("%s: only %d 15m bars, need 50+", symbol, len(bars))
            continue

        # Reverse to chronological order
        bars.reverse()
        timestamps = [b[0] for b in bars]
        closes = [float(b[4]) for b in bars]
        highs = [float(b[2]) for b in bars]
        lows = [float(b[3]) for b in bars]
        opens = [float(b[1]) for b in bars]
        volumes = [float(b[5]) for b in bars]

        # Calculate indicators on 15m data
        ema_9 = compute_ema(closes, 9)
        ema_21 = compute_ema(closes, 21)
        atr_14 = compute_atr(highs, lows, closes, 14)

        # Find crossovers in the LAST 3 candles (current + 2 previous)
        # This gives us very recent signals only
        crossovers = []
        for i in range(max(21, len(closes) - 3), len(closes)):
            if ema_9[i] is None or ema_21[i] is None:
                continue
            if i < 1 or ema_9[i - 1] is None or ema_21[i - 1] is None:
                continue

            # Bullish cross: EMA9 was below EMA21, now above
            if ema_9[i - 1] <= ema_21[i - 1] and ema_9[i] > ema_21[i]:
                crossovers.append((i, "bullish"))
            # Bearish cross: EMA9 was above EMA21, now below
            elif ema_9[i - 1] >= ema_21[i - 1] and ema_9[i] < ema_21[i]:
                crossovers.append((i, "bearish"))

        if not crossovers:
            continue

        # --- Daily trend filter ---
        # Check daily EMA position (today or most recent day)
        cur.execute("""
            SELECT ti.ema_9, ti.ema_21, ti.rsi_14, ti.atr_14,
                   ts.adx, ts.micro_trend, ts.intermediate_trend, ts.primary_trend,
                   ts.trend_score, ts.ema_stack
            FROM market.technical_indicators ti
            LEFT JOIN market.trend_status ts ON ts.symbol = ti.symbol AND ts.date = ti.date
            WHERE ti.symbol = %s
            ORDER BY ti.date DESC LIMIT 1
        """, (symbol,))
        daily_row = cur.fetchone()

        if not daily_row:
            log.warning("%s: no daily indicators, skipping", symbol)
            continue

        daily_ema9, daily_ema21, daily_rsi, daily_atr, \
            daily_adx, micro_trend, inter_trend, prim_trend, \
            trend_score, ema_stack = daily_row

        # Daily EMA position
        daily_ema_position = "above" if float(daily_ema9) > float(daily_ema21) else "below"
        daily_trend_dir = "bull" if float(daily_ema9) > float(daily_ema21) else "bear"

        # Daily ADX
        daily_adx_val = float(daily_adx) if daily_adx else 0.0

        # IV rank
        cur.execute("""
            SELECT iv_rank_52w FROM market.iv_rank
            WHERE symbol = %s ORDER BY date DESC LIMIT 1
        """, (symbol,))
        iv_row = cur.fetchone()
        iv_rank = float(iv_row[0]) if iv_row and iv_row[0] else None

        # IV-RV spread
        cur.execute("""
            SELECT ir.current_iv, rv.rv_20d
            FROM market.iv_rank ir
            JOIN market.realized_vol rv ON ir.symbol = rv.symbol AND DATE(ir.date) = DATE(rv.date)
            WHERE ir.symbol = %s ORDER BY ir.date DESC LIMIT 1
        """, (symbol,))
        iv_rv_row = cur.fetchone()
        if iv_rv_row and iv_rv_row[0] and iv_rv_row[1]:
            iv_rv_spread = round(float(iv_rv_row[0]) - float(iv_rv_row[1]), 4)
        else:
            iv_rv_spread = None

        # GEX
        cur.execute("""
            SELECT total_net_gex FROM market.gex_dex_overview
            WHERE underlying = %s ORDER BY date DESC LIMIT 1
        """, (symbol,))
        gex_row = cur.fetchone()
        net_gex = float(gex_row[0]) if gex_row and gex_row[0] else None

        # Process each crossover
        for bar_idx, direction in crossovers:
            trigger_price = closes[bar_idx]
            trigger_time = timestamps[bar_idx]
            atr_val = atr_14[bar_idx] if atr_14[bar_idx] else daily_atr or 1.0
            atr_val = float(atr_val) if atr_val else 1.0

            # --- Daily trend must align with signal ---
            if direction == "bullish" and daily_ema_position == "below":
                log.info("%s: 15m bullish cross BUT daily trend is bearish (EMA9<EMA21), skipping", symbol)
                continue
            if direction == "bearish" and daily_ema_position == "above":
                log.info("%s: 15m bearish cross BUT daily trend is bullish (EMA9>EMA21), skipping", symbol)
                continue

            # Daily ADX gate (looser threshold for intraday: 20 instead of 25)
            if daily_adx_val < 20:
                log.info("%s: 15m %s cross but daily ADX=%.1f (<20, skip)",
                         symbol, direction, daily_adx_val)
                continue

            # Regime filter
            if direction == "bullish" and current_regime == "bear":
                log.info("%s: 15m bullish cross in bear regime, skipping", symbol)
                continue
            if direction == "bearish" and current_regime == "bull":
                log.info("%s: 15m bearish cross in bull regime, skipping", symbol)
                continue

            # --- Trade plan: ATR-based stops and targets ---
            # Stop: ATR × 1.5 (tighter for 15m entries)
            # TP1: ATR × 4.5 (3:1 R:R on shares — your swing rule)
            # TP2: ATR × 7.5 (5:1 R:R — trailing territory)
            if direction == "bullish":
                stop_price = round(trigger_price - atr_val * 1.5, 2)
                risk_per_share = trigger_price - stop_price
                tp1_price = round(trigger_price + atr_val * 4.5, 2)
                tp2_price = round(trigger_price + atr_val * 7.5, 2)
            else:
                stop_price = round(trigger_price + atr_val * 1.5, 2)
                risk_per_share = stop_price - trigger_price
                tp1_price = round(trigger_price - atr_val * 4.5, 2)
                tp2_price = round(trigger_price - atr_val * 7.5, 2)

            if risk_per_share <= 0:
                log.warning("%s: zero risk, skipping", symbol)
                continue

            risk_reward = round((tp1_price - trigger_price) / risk_per_share, 1)

            # Volume ratio vs 20-period average on 15m
            avg_vol = np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes)
            volume_ratio = round(volumes[bar_idx] / avg_vol, 2) if avg_vol > 0 else 0.0

            # Invalidation conditions
            invalidation = json.dumps([
                "Cross reverses within 2 bars → EXIT",
                "Daily ADX drops below 20 → EXIT",
                "Volume dries up after entry → caution",
                "Price breaks below stop → EXIT immediately",
            ])

            # Best option contract — live snapshot from Alpaca (not stale daily greeks)
            contract_type = 'C' if direction == 'bullish' else 'P'
            try:
                live_opt = select_best_option(
                    symbol, want_type=contract_type, min_dte=30, max_dte=120,
                )
            except Exception as e:
                log.warning("%s: live option snapshot failed (%s), falling back to DB", symbol, e)
                live_opt = None

            signal = {
                "symbol": symbol,
                "strategy": "ema_crossover_15m",
                "direction": direction,
                "status": "new",
                "timeframe": "15m",
                "regime": current_regime,
                "trigger_price": trigger_price,
                "ema_9": float(ema_9[bar_idx]) if ema_9[bar_idx] else None,
                "ema_21": float(ema_21[bar_idx]) if ema_21[bar_idx] else None,
                "adx": daily_adx_val,
                "rsi": float(daily_rsi) if daily_rsi else None,
                "atr_14": atr_val,
                "volume_ratio": volume_ratio,
                "stop_price": stop_price,
                "tp1_price": tp1_price,
                "tp2_price": tp2_price,
                "risk_reward": risk_reward,
                "micro_trend": micro_trend,
                "intermediate_trend": inter_trend,
                "primary_trend": prim_trend,
                "trend_score": float(trend_score) if trend_score else None,
                "ema_stack": ema_stack,
                "invalidation": invalidation,
                "iv_rank": iv_rank,
                "iv_rv_spread": iv_rv_spread,
                "net_gex": net_gex,
                "regime_ok": True,
                # 15m-specific fields
                "daily_trend": daily_trend_dir,
                "daily_ema_position": daily_ema_position,
                "intraday_ema_9": float(ema_9[bar_idx]) if ema_9[bar_idx] else None,
                "intraday_ema_21": float(ema_21[bar_idx]) if ema_21[bar_idx] else None,
                "trigger_time": trigger_time,
            }

            if live_opt:
                signal.update({
                    "option_symbol": live_opt["occ_symbol"],
                    "option_strike": live_opt["strike"],
                    "option_expiry": live_opt["expiry"],
                    "option_delta": live_opt["delta"],
                    "option_theta": live_opt["theta"],
                    "option_bid": live_opt["bid"],
                    "option_ask": live_opt["ask"],
                    "option_mid": live_opt["mid"],
                    "spread_pct": live_opt.get("spread_pct"),
                })
            else:
                signal.update({
                    "option_symbol": None,
                    "option_strike": None,
                    "option_expiry": None,
                    "option_delta": None,
                    "option_theta": None,
                    "option_bid": None,
                    "option_ask": None,
                    "option_mid": None,
                    "spread_pct": None,
                })

            signals.append(signal)
            log.info("%s: 15m %s cross detected at %s (daily %s, regime %s, ADX %.1f)",
                     symbol, direction, trigger_time.strftime("%H:%M PDT"),
                     daily_ema_position, current_regime, daily_adx_val)

    cur.close()
    return signals


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_signals(conn, signals: list[dict]) -> int:
    """Insert new signals into market.signal_alerts, skipping duplicates."""
    if not signals:
        return 0

    cur = conn.cursor()
    inserted = 0

    for s in signals:
        try:
            # 4-hour cooldown: skip if same-direction alert fired within 4h.
            # The UNIQUE constraint uses created_at (default NOW()) so it never
            # collides in practice — this SELECT is the real dedup.
            cur.execute("""
                SELECT 1 FROM market.signal_alerts
                WHERE symbol = %s AND strategy = %s
                  AND direction = %s AND timeframe = %s
                  AND created_at > NOW() - INTERVAL '4 hours'
                LIMIT 1
            """, (s["symbol"], s["strategy"], s["direction"], s["timeframe"]))
            if cur.fetchone():
                log.info("Skipped (4h cooldown): %s %s %s %s",
                         s["symbol"], s["strategy"], s["direction"], s["timeframe"])
                continue

            cur.execute("""
                INSERT INTO market.signal_alerts (
                    symbol, strategy, direction, status, regime,
                    trigger_price, ema_9, ema_21, adx, rsi, atr_14, volume_ratio,
                    stop_price, tp1_price, tp2_price, risk_reward,
                    micro_trend, intermediate_trend, primary_trend,
                    trend_score, ema_stack, invalidation,
                    option_symbol, option_strike, option_expiry,
                    option_delta, option_theta,
                    option_bid, option_ask, option_mid,
                    spread_pct,
                    iv_rank, iv_rv_spread, net_gex,
                    timeframe, daily_trend, daily_ema_position,
                    intraday_ema_9, intraday_ema_21
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s
                ) ON CONFLICT (symbol, strategy, direction, timeframe, created_at) DO NOTHING
            """, (
                s["symbol"], s["strategy"], s["direction"], s["status"], s["regime"],
                s["trigger_price"], s["ema_9"], s["ema_21"], s["adx"], s["rsi"], s["atr_14"], s["volume_ratio"],
                s["stop_price"], s["tp1_price"], s["tp2_price"], s["risk_reward"],
                s["micro_trend"], s["intermediate_trend"], s["primary_trend"],
                s["trend_score"], s["ema_stack"], s["invalidation"],
                s["option_symbol"], s["option_strike"], s["option_expiry"],
                s["option_delta"], s["option_theta"],
                s["option_bid"], s["option_ask"], s["option_mid"],
                s.get("spread_pct"),
                s["iv_rank"], s["iv_rv_spread"], s["net_gex"],
                s["timeframe"], s["daily_trend"], s["daily_ema_position"],
                s["intraday_ema_9"], s["intraday_ema_21"],
            ))
            if cur.rowcount > 0:
                inserted += 1
                log.info("Inserted 15m signal: %s %s %s", s["symbol"], s["direction"], s["timeframe"])
            else:
                log.info("Skipped duplicate: %s %s %s", s["symbol"], s["direction"], s["timeframe"])

        except Exception as e:
            log.error("Error inserting signal for %s: %s", s["symbol"], e)
            conn.rollback()
            cur = conn.cursor()

    conn.commit()
    cur.close()
    return inserted


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Detect 15m EMA crossovers")
    parser.add_argument("--dry-run", action="store_true", help="Detect but don't save")
    args = parser.parse_args()

    # Market-hours gate: only fire during regular session (weekday + market hours via constants).
    now_et = datetime.now(ET)
    if not is_market_day(now_et.date()):
        log.info("Non-market day (%s ET) — market closed, exiting silent.",
                 now_et.strftime("%a %H:%M"))
        sys.exit(0)
    if not is_market_hours(now_et.time()):
        log.info("Outside market hours (%s ET) — exiting silent.",
                 now_et.strftime("%H:%M"))
        sys.exit(0)

    conn = get_connection()
    signals = detect_15m_crossovers(conn)

    if signals:
        log.info("Found %d 15m EMA crossover(s)", len(signals))
        for s in signals:
            log.info("  %s %s @ $%.2f (daily %s, ADX %.1f, R:R %.1f)",
                     s["symbol"], s["direction"], s["trigger_price"],
                     s["daily_ema_position"], s["adx"], s["risk_reward"])
    else:
        log.info("No 15m EMA crossovers detected")

    if not args.dry_run and signals:
        n = save_signals(conn, signals)
        log.info("Saved %d/%d signals", n, len(signals))

    conn.close()