#!/usr/bin/env python3
"""Composite signal scoring engine.

For each active watchlist symbol, compute a 0-100 composite score from the
six analytics components (IV regime, IV-RV spread, GEX/DEX, technicals,
sentiment, IV outlier) and write the result to trading.signals.

Scoring weights (see CLAUDE.md → Greeks Strategy + Risk Management):
    IV regime          0-25
    IV-RV spread       0-15
    GEX/DEX            0-20
    Technicals         0-20
    Sentiment          0-10
    IV outlier flag    0-10
                      ----
    Composite          0-100

Signal type:
    composite > 55     -> bullish
    40 <= comp <= 55   -> neutral
    composite < 40     -> bearish

Recommended strategy:
    >=70 aggressive
    50-69 moderate
    30-49 cautious
    <30   no_trade

Usage:
    python scripts/generate_signals.py            # all active watchlist symbols
    python scripts/generate_signals.py --all
    python scripts/generate_signals.py --symbol NVDA
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import Json, RealDictCursor

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env(filename: str) -> None:
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
    """Proportional 0-10. Cap at 20 mentions -> 10 points."""
    if mention_count <= 0:
        return 0.0
    return min(10.0, mention_count * 0.5)


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


def fetch_inputs(conn, symbol: str, asof: date) -> dict[str, Any]:
    """Pull latest available analytics for `symbol` as of `asof`."""
    out: dict[str, Any] = {"symbol": symbol, "asof": asof}
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id FROM market.assets
            WHERE symbol = %s
            """,
            (symbol,),
        )
        row = cur.fetchone()
        out["asset_id"] = row["id"] if row else None

        cur.execute(
            """
            SELECT iv_rank_52w, current_iv, date
            FROM market.iv_rank
            WHERE symbol = %s AND date <= %s
            ORDER BY date DESC
            LIMIT 1
            """,
            (symbol, asof),
        )
        r = cur.fetchone()
        out["iv_rank"] = float(r["iv_rank_52w"]) if r and r["iv_rank_52w"] is not None else None
        out["current_iv"] = float(r["current_iv"]) if r and r["current_iv"] is not None else None

        cur.execute(
            """
            SELECT rv_20d
            FROM market.realized_vol
            WHERE symbol = %s AND date <= %s
            ORDER BY date DESC
            LIMIT 1
            """,
            (symbol, asof),
        )
        r = cur.fetchone()
        rv_20d = float(r["rv_20d"]) if r and r["rv_20d"] is not None else None
        out["rv_20d"] = rv_20d
        if out["current_iv"] is not None and rv_20d is not None:
            out["iv_rv_spread"] = out["current_iv"] - rv_20d
        else:
            out["iv_rv_spread"] = None

        cur.execute(
            """
            SELECT total_net_gex, total_net_dex, date
            FROM market.gex_dex_overview
            WHERE underlying = %s AND date <= %s
            ORDER BY date DESC
            LIMIT 1
            """,
            (symbol, asof),
        )
        r = cur.fetchone()
        out["net_gex"] = float(r["total_net_gex"]) if r and r["total_net_gex"] is not None else None
        out["net_dex"] = float(r["total_net_dex"]) if r and r["total_net_dex"] is not None else None
        gex_date = r["date"] if r else None

        cur.execute(
            """
            SELECT close
            FROM market.ohlcv o
            JOIN market.assets a ON a.id = o.asset_id
            WHERE a.symbol = %s
              AND o.timeframe = '1d'
              AND o.timestamp::date <= %s
            ORDER BY o.timestamp DESC
            LIMIT 1
            """,
            (symbol, asof),
        )
        r = cur.fetchone()
        close = float(r["close"]) if r and r["close"] is not None else None
        out["close"] = close

        has_nearby = False
        if close is not None and gex_date is not None:
            cur.execute(
                """
                SELECT 1
                FROM market.gex_dex
                WHERE underlying = %s
                  AND date = %s
                  AND ABS(strike - %s) / %s <= 0.05
                LIMIT 1
                """,
                (symbol, gex_date, close, close),
            )
            has_nearby = cur.fetchone() is not None
        out["has_nearby_strike"] = has_nearby

        cur.execute(
            """
            SELECT rsi_14, ema_21, macd_hist, date
            FROM market.technical_indicators
            WHERE symbol = %s AND date <= %s
            ORDER BY date DESC
            LIMIT 2
            """,
            (symbol, asof),
        )
        rows = cur.fetchall()
        if rows:
            latest = rows[0]
            out["rsi_14"] = float(latest["rsi_14"]) if latest["rsi_14"] is not None else None
            out["ema_21"] = float(latest["ema_21"]) if latest["ema_21"] is not None else None
            out["macd_hist"] = float(latest["macd_hist"]) if latest["macd_hist"] is not None else None
            if len(rows) > 1 and rows[1]["macd_hist"] is not None:
                out["macd_hist_prev"] = float(rows[1]["macd_hist"])
            else:
                out["macd_hist_prev"] = None
        else:
            out["rsi_14"] = out["ema_21"] = out["macd_hist"] = out["macd_hist_prev"] = None

        since = asof - timedelta(days=1)
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

        cur.execute(
            """
            SELECT z_score, direction, date
            FROM market.iv_outliers
            WHERE symbol = %s AND date <= %s
            ORDER BY date DESC
            LIMIT 1
            """,
            (symbol, asof),
        )
        r = cur.fetchone()
        # An outlier row is only stored when |z| > 3, but require it to be recent
        # (within 5 days of asof) so stale flags don't dominate the score.
        out["iv_outlier_flag"] = False
        out["iv_outlier_z"] = None
        out["iv_outlier_direction"] = None
        if r and r["date"] >= asof - timedelta(days=5):
            out["iv_outlier_flag"] = True
            out["iv_outlier_z"] = float(r["z_score"]) if r["z_score"] is not None else None
            out["iv_outlier_direction"] = r["direction"]

    return out


def score_symbol(inputs: dict[str, Any]) -> dict[str, Any]:
    iv_pts, regime = score_iv_regime(inputs["iv_rank"])
    iv_rv_pts = score_iv_rv_spread(inputs["iv_rv_spread"])
    gex_pts = score_gex_dex(
        inputs["net_gex"], inputs["net_dex"], inputs["has_nearby_strike"]
    )
    tech_pts = score_technicals(
        inputs["rsi_14"],
        inputs["close"],
        inputs["ema_21"],
        inputs["macd_hist"],
        inputs["macd_hist_prev"],
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
        "signal_type": signal_type_for(composite),
        "recommended_strategy": strategy_for(composite),
    }


def write_signal(
    conn,
    inputs: dict[str, Any],
    scored: dict[str, Any],
) -> None:
    if inputs["asset_id"] is None:
        print(f"  skip {inputs['symbol']}: not in market.assets")
        return

    details = {
        "inputs": {
            "iv_rank": inputs["iv_rank"],
            "current_iv": inputs["current_iv"],
            "rv_20d": inputs["rv_20d"],
            "iv_rv_spread": inputs["iv_rv_spread"],
            "net_gex": inputs["net_gex"],
            "net_dex": inputs["net_dex"],
            "has_nearby_strike": inputs["has_nearby_strike"],
            "rsi_14": inputs["rsi_14"],
            "ema_21": inputs["ema_21"],
            "macd_hist": inputs["macd_hist"],
            "macd_hist_prev": inputs["macd_hist_prev"],
            "close": inputs["close"],
            "mention_count": inputs["mention_count"],
            "iv_outlier_z": inputs["iv_outlier_z"],
            "iv_outlier_direction": inputs["iv_outlier_direction"],
        },
        "breakdown": {
            "iv_regime": scored["iv_regime_score"],
            "iv_rv_spread": scored["iv_rv_score"],
            "gex_dex": scored["gex_score"],
            "technicals": scored["tech_score"],
            "sentiment": scored["sentiment_score"],
            "iv_outlier": scored["iv_outlier_score"],
        },
    }

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO trading.signals (
                asset_id, symbol, signal_type, strategy,
                confidence, price_at_signal, metadata,
                composite_score, iv_regime, iv_regime_score,
                iv_rv_spread, iv_rv_score, gex_score, tech_score,
                sentiment_score, iv_outlier_flag, iv_outlier_score,
                recommended_strategy, details, signal_date
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s
            )
            ON CONFLICT (symbol, signal_date) DO UPDATE SET
                signal_type = EXCLUDED.signal_type,
                strategy = EXCLUDED.strategy,
                confidence = EXCLUDED.confidence,
                price_at_signal = EXCLUDED.price_at_signal,
                metadata = EXCLUDED.metadata,
                composite_score = EXCLUDED.composite_score,
                iv_regime = EXCLUDED.iv_regime,
                iv_regime_score = EXCLUDED.iv_regime_score,
                iv_rv_spread = EXCLUDED.iv_rv_spread,
                iv_rv_score = EXCLUDED.iv_rv_score,
                gex_score = EXCLUDED.gex_score,
                tech_score = EXCLUDED.tech_score,
                sentiment_score = EXCLUDED.sentiment_score,
                iv_outlier_flag = EXCLUDED.iv_outlier_flag,
                iv_outlier_score = EXCLUDED.iv_outlier_score,
                recommended_strategy = EXCLUDED.recommended_strategy,
                details = EXCLUDED.details
            """,
            (
                inputs["asset_id"],
                inputs["symbol"],
                scored["signal_type"],
                scored["recommended_strategy"],
                round(scored["composite_score"] / 100.0, 2),
                inputs["close"],
                Json(details),
                scored["composite_score"],
                scored["iv_regime"],
                scored["iv_regime_score"],
                inputs["iv_rv_spread"],
                scored["iv_rv_score"],
                scored["gex_score"],
                scored["tech_score"],
                scored["sentiment_score"],
                inputs["iv_outlier_flag"],
                scored["iv_outlier_score"],
                scored["recommended_strategy"],
                Json(details),
                inputs["asof"],
            ),
        )


def active_symbols(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol FROM market.assets
            WHERE active = TRUE
            ORDER BY symbol
            """
        )
        return [r[0] for r in cur.fetchall()]


def main() -> int:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group()
    g.add_argument("--symbol", help="Generate a signal for a single symbol")
    g.add_argument(
        "--all",
        action="store_true",
        help="Generate signals for all active watchlist symbols (default)",
    )
    p.add_argument(
        "--asof",
        type=date.fromisoformat,
        default=date.today(),
        help="As-of date for inputs (YYYY-MM-DD, default: today)",
    )
    args = p.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        if args.symbol:
            symbols = [args.symbol.upper()]
        else:
            symbols = active_symbols(conn)

        print(f"Generating signals as of {args.asof} for {len(symbols)} symbols")
        for sym in symbols:
            inputs = fetch_inputs(conn, sym, args.asof)
            scored = score_symbol(inputs)
            print(
                f"  {sym:6s}  composite={scored['composite_score']:6.2f}  "
                f"type={scored['signal_type']:7s}  "
                f"strategy={scored['recommended_strategy']:10s}  "
                f"regime={scored['iv_regime']}"
            )
            write_signal(conn, inputs, scored)
        conn.commit()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
