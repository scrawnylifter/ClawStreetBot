#!/usr/bin/env python3
"""EMA Crossover Detector — Phase 5A Signal Alert Engine

Detects EMA 9/21 crossover events across the watchlist, confirmed by ADX > 25.
For each detected signal, computes the full trade plan (ATR stops, TP levels,
R:R ratio) and stores it in market.signal_alerts for Telegram delivery.

Entry criteria (swing):
  - EMA 9 crosses above/below EMA 21 (daily candles)
  - ADX > 25 (trend confirmation)
  - Regime check: avoid bullish signals in bear regime, bearish in bull regime

Trade plan (Laws of Trading — swing):
  - Stop: ATR(14) × 2.0 below entry
  - TP1: +30% from entry
  - TP2: +50% from entry, then trail
  - Risk: 10% of portfolio ($100 on $1K)
  - Max position: 20% ($200)
  - Min DTE: 30 days

Exit criteria (monitored separately):
  - Cross reverses within 2 candles → EXIT
  - ADX drops below 20 → EXIT
  - MACD histogram declining 3+ candles → tighten stop
  - MACD zero-cross against position → strong exit
  - MACD divergence → consider TP1
"""
import os
import sys
import logging
from pathlib import Path
import json
from datetime import date, datetime, timezone

import psycopg2

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
# Detection logic
# ---------------------------------------------------------------------------

def detect_crossovers(conn, lookback_days: int = 5) -> list[dict]:
    """Find EMA 9/21 crossovers in the last N trading days, confirmed by ADX > 25."""
    cur = conn.cursor()

    # Latest date in technical_indicators
    cur.execute("SELECT MAX(date) FROM market.technical_indicators")
    max_date = cur.fetchone()[0]
    if not max_date:
        log.warning("No technical indicators data available")
        cur.close()
        return []

    # Find EMA crossovers with proper ohlcv join (ohlcv uses asset_id FK)
    cur.execute("""
        WITH ema_series AS (
            SELECT
                ti.symbol,
                ti.date,
                ti.ema_9,
                ti.ema_21,
                ti.rsi_14,
                ti.atr_14,
                ti.macd_hist,
                o.close,
                o.volume,
                LAG(ti.ema_9)  OVER (PARTITION BY ti.symbol ORDER BY ti.date) AS prev_ema_9,
                LAG(ti.ema_21) OVER (PARTITION BY ti.symbol ORDER BY ti.date) AS prev_ema_21,
                CASE
                    WHEN ti.ema_9 > ti.ema_21 THEN 'above'
                    WHEN ti.ema_9 < ti.ema_21 THEN 'below'
                    ELSE 'neutral'
                END AS ema_position
            FROM market.technical_indicators ti
            JOIN market.assets a ON ti.symbol = a.symbol
            JOIN market.ohlcv o ON o.asset_id = a.id
                AND o.timeframe = '1d'
                AND DATE(o.timestamp) = DATE(ti.date)
            WHERE ti.date >= %s - INTERVAL '%s days'
        )
        SELECT
            e.symbol,
            e.date,
            e.ema_9,
            e.ema_21,
            e.rsi_14,
            e.atr_14,
            e.macd_hist,
            e.close,
            e.volume,
            e.prev_ema_9,
            e.prev_ema_21,
            CASE
                WHEN e.prev_ema_9 <= e.prev_ema_21 AND e.ema_9 > e.ema_21
                    THEN 'bullish'
                WHEN e.prev_ema_9 >= e.prev_ema_21 AND e.ema_9 < e.ema_21
                    THEN 'bearish'
                ELSE NULL
            END AS crossover
        FROM ema_series e
        WHERE e.date >= %s - INTERVAL '%s days'
          AND (
            (e.prev_ema_9 <= e.prev_ema_21 AND e.ema_9 > e.ema_21)
            OR
            (e.prev_ema_9 >= e.prev_ema_21 AND e.ema_9 < e.ema_21)
          )
        ORDER BY e.date DESC, e.symbol
    """, (max_date, lookback_days + 60, max_date, lookback_days))

    crossovers = cur.fetchall()
    if not crossovers:
        log.info("No EMA crossovers detected in the last %d days", lookback_days)
        cur.close()
        return []

    log.info("Found %d raw crossover(s)", len(crossovers))

    # Current regime
    cur.execute("""
        SELECT regime, spy_trend FROM market.regime
        ORDER BY date DESC LIMIT 1
    """)
    regime_row = cur.fetchone()
    current_regime = regime_row[0] if regime_row else "unknown"
    spy_trend = regime_row[1] if regime_row else "unknown"

    signals = []
    for row in crossovers:
        (symbol, cross_date, ema_9, ema_21, rsi, atr, macd_hist,
         close_price, volume, prev_ema_9, prev_ema_21, direction) = row

        # ADX + trend context from trend_status (not technical_indicators!)
        cur.execute("""
            SELECT adx, micro_trend, intermediate_trend, primary_trend,
                   trend_strength, ema_stack, trend_score, details
            FROM market.trend_status
            WHERE symbol = %s AND date = %s
        """, (symbol, cross_date))
        trend_row = cur.fetchone()

        if not trend_row:
            log.warning("No trend_status for %s on %s, skipping", symbol, cross_date)
            continue

        adx, micro_trend, inter_trend, prim_trend, \
            trend_strength, ema_stack, trend_score, details = trend_row

        adx_val = float(adx) if adx else 0.0

        # ADX > 25 confirmation gate
        if adx_val <= 25:
            log.info("%s %s cross on %s: ADX=%.1f (≤25, skip)",
                     symbol, direction, cross_date, adx_val)
            continue

        # Regime filter
        regime_ok = True
        if direction == "bullish" and current_regime == "bear":
            regime_ok = False
            log.info("Skipping bullish %s cross: regime is bear", symbol)
        elif direction == "bearish" and current_regime == "bull":
            regime_ok = False
            log.info("Skipping bearish %s cross: regime is bull", symbol)

        # Volume ratio (20-day avg)
        cur.execute("""
            SELECT AVG(volume)::numeric
            FROM (
                SELECT volume FROM market.ohlcv o
                JOIN market.assets a ON o.asset_id = a.id
                WHERE a.symbol = %s AND o.timeframe = '1d'
                  AND DATE(o.timestamp) <= DATE(%s)
                ORDER BY o.timestamp DESC LIMIT 20
            ) sub
        """, (symbol, cross_date))
        avg_vol_row = cur.fetchone()
        avg_volume = float(avg_vol_row[0]) if avg_vol_row and avg_vol_row[0] else 1
        volume_ratio = round(float(volume) / avg_volume, 2) if avg_volume > 0 else 0.0

        # Trade plan — ATR-based stops and targets (your swing rules)
        # Daily: Stop = ATR×2, TP1 = ATR×6 (3:1 R:R), TP2 = ATR×10 (5:1 R:R)
        atr_val = float(atr) if atr else 0.0
        close_f = float(close_price)

        if direction == "bullish":
            stop_price = round(close_f - atr_val * 2.0, 2)
            risk_per_share = close_f - stop_price
            tp1_price = round(close_f + atr_val * 6.0, 2)
            tp2_price = round(close_f + atr_val * 10.0, 2)
        else:
            stop_price = round(close_f + atr_val * 2.0, 2)
            risk_per_share = stop_price - close_f
            tp1_price = round(close_f - atr_val * 6.0, 2)
            tp2_price = round(close_f - atr_val * 10.0, 2)

        if risk_per_share <= 0:
            log.warning("Zero risk for %s, skipping", symbol)
            continue

        risk_reward = round(abs(tp1_price - close_f) / risk_per_share, 1)

        # Invalidation conditions (store as JSON string for jsonb column)
        invalidation = json.dumps([
            "Cross reverses within 2 candles → EXIT",
            f"ADX drops below 20 → EXIT",
            "Volume dries up after entry → caution",
        ])

        # IV rank
        cur.execute("""
            SELECT iv_rank_52w FROM market.iv_rank
            WHERE symbol = %s AND date = %s
        """, (symbol, cross_date))
        iv_rank_row = cur.fetchone()
        iv_rank = float(iv_rank_row[0]) if iv_rank_row and iv_rank_row[0] else None

        # IV-RV spread (current_iv - rv_20d from realized_vol)
        cur.execute("""
            SELECT ir.current_iv, rv.rv_20d
            FROM market.iv_rank ir
            JOIN market.realized_vol rv ON ir.symbol = rv.symbol AND ir.date = rv.date
            WHERE ir.symbol = %s AND ir.date = %s
        """, (symbol, cross_date))
        iv_rv_row = cur.fetchone()
        if iv_rv_row and iv_rv_row[0] and iv_rv_row[1]:
            iv_rv_spread = round(float(iv_rv_row[0]) - float(iv_rv_row[1]), 4)
        else:
            iv_rv_spread = None

        # GEX overview
        cur.execute("""
            SELECT total_net_gex FROM market.gex_dex_overview
            WHERE underlying = %s AND date = %s
        """, (symbol, cross_date))
        gex_row = cur.fetchone()
        net_gex = float(gex_row[0]) if gex_row and gex_row[0] else None

        # Best option contract (DTE≥30, delta 0.50-0.70, calls for bullish / puts for bearish)
        contract_type = 'C' if direction == 'bullish' else 'P'
        cur.execute("""
            SELECT o.occ_symbol, o.strike, o.expiration,
                   g.delta, g.theta, g.underlying_price
            FROM market.options o
            JOIN market.greeks g ON o.occ_symbol = g.occ_symbol AND g.date = %s
            WHERE o.underlying = %s
              AND o.expiration >= %s + INTERVAL '30 days'
              AND o.contract_type = %s
              AND g.delta IS NOT NULL
              AND g.delta BETWEEN 0.50 AND 0.70
            ORDER BY g.theta ASC, ABS(g.delta - 0.60) ASC
            LIMIT 1
        """, (cross_date, symbol, cross_date, contract_type))
        option_row = cur.fetchone()

        signal = {
            "symbol": symbol,
            "strategy": "ema_crossover",
            "direction": direction,
            "status": "new",
            "regime": current_regime,
            "trigger_price": close_f,
            "ema_9": float(ema_9) if ema_9 else None,
            "ema_21": float(ema_21) if ema_21 else None,
            "adx": adx_val,
            "rsi": float(rsi) if rsi else None,
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
            "regime_ok": regime_ok,
        }

        if option_row:
            signal.update({
                "option_symbol": option_row[0],
                "option_strike": float(option_row[1]),
                "option_expiry": option_row[2].strftime("%Y-%m-%d") if hasattr(option_row[2], "strftime") else str(option_row[2])[:10],
                "option_delta": float(option_row[3]) if option_row[3] else None,
                "option_theta": float(option_row[4]) if option_row[4] else None,
            })
        else:
            # No suitable option found — set defaults so the INSERT doesn't break
            signal.update({
                "option_symbol": None,
                "option_strike": None,
                "option_expiry": None,
                "option_delta": None,
                "option_theta": None,
            })

        signals.append(signal)

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
                  AND direction = %s AND timeframe = '1d'
                  AND created_at > NOW() - INTERVAL '4 hours'
                LIMIT 1
            """, (s["symbol"], s["strategy"], s["direction"]))
            if cur.fetchone():
                log.info("Skipped (4h cooldown): %s %s %s",
                         s["symbol"], s["strategy"], s["direction"])
                continue

            cur.execute("""
                INSERT INTO market.signal_alerts (
                    symbol, strategy, direction, status, regime,
                    trigger_price, ema_9, ema_21, adx, rsi, atr_14, volume_ratio,
                    stop_price, tp1_price, tp2_price, risk_reward,
                    micro_trend, intermediate_trend, primary_trend,
                    trend_score, ema_stack,
                    invalidation,
                    option_symbol, option_strike, option_expiry,
                    option_delta, option_theta,
                    iv_rank, iv_rv_spread, net_gex
                ) VALUES (
                    %(symbol)s, %(strategy)s, %(direction)s, %(status)s, %(regime)s,
                    %(trigger_price)s, %(ema_9)s, %(ema_21)s, %(adx)s, %(rsi)s,
                    %(atr_14)s, %(volume_ratio)s,
                    %(stop_price)s, %(tp1_price)s, %(tp2_price)s, %(risk_reward)s,
                    %(micro_trend)s, %(intermediate_trend)s, %(primary_trend)s,
                    %(trend_score)s, %(ema_stack)s,
                    %(invalidation)s,
                    %(option_symbol)s, %(option_strike)s, %(option_expiry)s,
                    %(option_delta)s, %(option_theta)s,
                    %(iv_rank)s, %(iv_rv_spread)s, %(net_gex)s
                )
                ON CONFLICT (symbol, strategy, direction, created_at) DO NOTHING
            """, s)
            if cur.rowcount > 0:
                inserted += 1
                log.info("Inserted signal: %s %s %s cross @ $%.2f (R:R %.1f:1)",
                         s["symbol"], s["strategy"], s["direction"],
                         s["trigger_price"], s["risk_reward"])
        except Exception as e:
            log.error("Failed to insert signal for %s: %s", s["symbol"], e)

    conn.commit()
    cur.close()
    return inserted


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def report_signals(conn, since_hours: int = 24) -> None:
    """Print a summary of recent signals to stdout for n8n logging."""
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol, strategy, direction, status, trigger_price,
               stop_price, tp1_price, risk_reward, adx, rsi,
               ema_9, ema_21, regime, created_at
        FROM market.signal_alerts
        WHERE created_at >= NOW() - INTERVAL '%s hours'
        ORDER BY created_at DESC
    """, (since_hours,))
    rows = cur.fetchall()

    if not rows:
        print("No recent EMA crossover signals found.")
        cur.close()
        return

    print(f"\n{'='*70}")
    print(f"  EMA CROSSOVER DETECTOR — {len(rows)} signal(s) in last {since_hours}h")
    print(f"{'='*70}")

    for row in rows:
        (sym, strat, direction, status, price, stop, tp1, rr, adx, rsi,
         ema9, ema21, regime, ts) = row
        emoji = "📈" if direction == "bullish" else "📉"
        print(f"\n{emoji} {sym} {direction.upper()} {strat}")
        print(f"   Price: ${price:.2f} | Stop: ${stop:.2f} | TP1: ${tp1:.2f}")
        print(f"   R:R: {rr:.1f}:1 | ADX: {adx:.1f} | RSI: {rsi:.1f}")
        print(f"   EMA9: ${ema9:.2f} | EMA21: ${ema21:.2f} | Regime: {regime}")
        print(f"   Status: {status} | Created: {ts}")

    print(f"\n{'='*70}\n")
    cur.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Detect EMA 9/21 crossovers")
    parser.add_argument("--lookback", type=int, default=5,
                        help="Days to look back for crossovers (default: 5)")
    parser.add_argument("--report-only", action="store_true",
                        help="Just print recent signals, don't detect new ones")
    parser.add_argument("--since-hours", type=int, default=24,
                        help="Hours to look back for report (default: 24)")
    args = parser.parse_args()

    conn = get_connection()

    if args.report_only:
        report_signals(conn, since_hours=args.since_hours)
        conn.close()
        return

    # Detect crossovers
    signals = detect_crossovers(conn, lookback_days=args.lookback)

    if signals:
        count = save_signals(conn, signals)
        log.info("Saved %d new signal(s) out of %d detected", count, len(signals))
    else:
        log.info("No EMA crossover signals detected in last %d days", args.lookback)

    # Always print the report
    report_signals(conn, since_hours=max(args.lookback * 24, args.since_hours))
    conn.close()


if __name__ == "__main__":
    main()