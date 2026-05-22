#!/usr/bin/env python3
"""Regime-conditional analysis + backtest for ClawStreetBot.

Pipeline (each step idempotent, ``ON CONFLICT`` everywhere):

1. ``classify``   Classify each trading day as ``bull`` / ``bear`` / ``transition``
                  using SPY 50d vs 200d SMA, a VIX proxy (or actual VIX if a
                  ``VIX`` asset is present in ``market.ohlcv``), and a breadth
                  proxy (% of sector-proxy symbols above their 50d SMA).
                  Writes to ``market.regime``.

2. ``analyze``    For each regime, correlate every factor score in
                  ``trading.signals`` with the underlying symbol's forward
                  5-day and 20-day return. Writes to
                  ``trading.regime_factor_analysis``.

3. ``optimize``   Convert per-regime positive correlations into a normalized
                  weight vector that sums to 100. Factors with non-positive
                  correlation get a small floor weight (5) so the score is
                  never blind to them. Writes to ``trading.regime_weights``
                  (scheme = ``optimized``).

4. ``backtest``   Replay ``trading.signals`` against ``market.ohlcv`` using
                  either the static (baseline) weights or the regime-aware
                  optimized weights, recomputing each signal's composite from
                  its per-factor scores on the fly. Stores a row in
                  ``trading.backtest_runs`` with ``params.weight_scheme`` set.

5. ``compare``    Run both schemes back-to-back and print a side-by-side
                  summary.

Usage::

    python scripts/regime_backtest.py classify --start 2024-01-01 --end 2026-05-01
    python scripts/regime_backtest.py analyze  --start 2024-01-01 --end 2026-05-01
    python scripts/regime_backtest.py optimize
    python scripts/regime_backtest.py backtest --mode swing --scheme optimized \\
        --start 2024-01-01 --end 2026-05-01
    python scripts/regime_backtest.py compare  --mode swing \\
        --start 2024-01-01 --end 2026-05-01

The ``all`` subcommand chains classify -> analyze -> optimize -> compare.
"""
from __future__ import annotations

import argparse
import math
import os
import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

import psycopg2
from psycopg2.extras import Json, RealDictCursor

# Reuse the existing backtest engine — only the scoring layer needs to change.
from backtest import (  # type: ignore  # noqa: E402
    STRATEGY_RULES,
    Bar,
    Trade,
    annotate_pdt,
    compute_metrics,
    fetch_atr,
    fetch_bars,
    print_report,
    simulate_trade,
    write_metrics,
    write_trades,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env(filename: str) -> None:
    """Populate ``os.environ`` from a dotenv-style file (no quoting)."""
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

# Max possible points for each factor in the existing static scoring engine.
# Used to normalize raw factor scores to [0, 1] before re-weighting.
FACTOR_MAX: dict[str, float] = {
    "iv_regime": 25.0,
    "gex": 20.0,
    "tech": 20.0,
    "iv_rv": 15.0,
    "sentiment": 10.0,
    "outlier": 10.0,
}

STATIC_WEIGHTS: dict[str, float] = {
    "iv_regime": 25.0,
    "gex": 20.0,
    "tech": 20.0,
    "iv_rv": 15.0,
    "sentiment": 10.0,
    "outlier": 10.0,
}

# Sector proxies used for the breadth signal when no full SPY500 breadth feed
# is available. Each entry is checked independently — the breadth_proxy is the
# fraction (0-1) of these symbols whose close is above their own 50d SMA.
BREADTH_PROXIES = ("NVDA", "AMD", "MU")

# VIX bands (CLAUDE.md → market regime classifier).
VIX_CALM = 18.0
VIX_PANIC = 30.0


# ---------------------------------------------------------------------------
# Step 1: classify
# ---------------------------------------------------------------------------

@dataclass
class SpyRow:
    """SPY daily close with rolling 50d / 200d SMAs already pre-computed."""

    d: date
    close: float
    sma_50: float | None
    sma_200: float | None


def fetch_spy_series(conn, start: date, end: date) -> list[SpyRow]:
    """Pull SPY closes plus 50/200 SMAs from ``market.ohlcv``.

    Pulls 250 calendar days before ``start`` so the 200d SMA is defined on the
    first in-range row. Falls back to ``NULL`` SMA where insufficient history
    exists.
    """
    window_start = start - timedelta(days=320)
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH spy AS (
                SELECT o.timestamp::date AS d, o.close::float AS close
                FROM market.ohlcv o
                JOIN market.assets a ON a.id = o.asset_id
                WHERE a.symbol = 'SPY'
                  AND o.timeframe = '1d'
                  AND o.timestamp::date BETWEEN %s AND %s
            )
            SELECT d, close,
                   AVG(close) OVER (
                       ORDER BY d ROWS BETWEEN 49 PRECEDING AND CURRENT ROW
                   ) AS sma_50,
                   AVG(close) OVER (
                       ORDER BY d ROWS BETWEEN 199 PRECEDING AND CURRENT ROW
                   ) AS sma_200,
                   COUNT(*) OVER (
                       ORDER BY d ROWS BETWEEN 49 PRECEDING AND CURRENT ROW
                   ) AS n_50,
                   COUNT(*) OVER (
                       ORDER BY d ROWS BETWEEN 199 PRECEDING AND CURRENT ROW
                   ) AS n_200
            FROM spy
            ORDER BY d
            """,
            (window_start, end),
        )
        rows: list[SpyRow] = []
        for d, close, s50, s200, n50, n200 in cur.fetchall():
            if d < start:
                continue
            rows.append(
                SpyRow(
                    d=d,
                    close=float(close),
                    sma_50=float(s50) if n50 >= 50 else None,
                    sma_200=float(s200) if n200 >= 200 else None,
                )
            )
        return rows


def fetch_vix_series(conn, start: date, end: date) -> dict[date, float]:
    """Return a ``{date: vix_value}`` mapping if a VIX asset exists.

    Looks for any of ``VIX`` / ``^VIX`` / ``I:VIX`` in ``market.assets``.
    If none is present an empty mapping is returned and the caller will fall
    back to a SPY realized-volatility proxy.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT o.timestamp::date, o.close::float
            FROM market.ohlcv o
            JOIN market.assets a ON a.id = o.asset_id
            WHERE a.symbol IN ('VIX','^VIX','I:VIX')
              AND o.timeframe = '1d'
              AND o.timestamp::date BETWEEN %s AND %s
            ORDER BY o.timestamp
            """,
            (start, end),
        )
        return {d: float(c) for d, c in cur.fetchall()}


def spy_realized_vol_proxy(spy: list[SpyRow]) -> dict[date, float]:
    """Compute SPY 20d annualized realized vol (×100) as a VIX stand-in.

    Annualization uses sqrt(252). The returned scale is roughly comparable to
    the VIX index so the same band thresholds (18 / 30) apply.
    """
    proxy: dict[date, float] = {}
    closes = [r.close for r in spy]
    dates = [r.d for r in spy]
    rets: list[float] = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            rets.append(math.log(closes[i] / closes[i - 1]))
        else:
            rets.append(0.0)
    # rets[i-1] corresponds to dates[i]
    for i in range(20, len(rets) + 1):
        window = rets[i - 20:i]
        if len(window) < 2:
            continue
        sd = statistics.stdev(window)
        rv = sd * math.sqrt(252) * 100.0
        proxy[dates[i]] = rv
    return proxy


def fetch_breadth_series(conn, start: date, end: date) -> dict[date, float]:
    """Fraction of proxy symbols whose close is above their own 50d SMA."""
    if not BREADTH_PROXIES:
        return {}
    window_start = start - timedelta(days=120)
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH px AS (
                SELECT a.symbol, o.timestamp::date AS d, o.close::float AS close
                FROM market.ohlcv o
                JOIN market.assets a ON a.id = o.asset_id
                WHERE a.symbol = ANY(%s)
                  AND o.timeframe = '1d'
                  AND o.timestamp::date BETWEEN %s AND %s
            ),
            sma AS (
                SELECT symbol, d, close,
                       AVG(close) OVER (
                           PARTITION BY symbol ORDER BY d
                           ROWS BETWEEN 49 PRECEDING AND CURRENT ROW
                       ) AS sma_50,
                       COUNT(*) OVER (
                           PARTITION BY symbol ORDER BY d
                           ROWS BETWEEN 49 PRECEDING AND CURRENT ROW
                       ) AS n
                FROM px
            )
            SELECT d,
                   SUM(CASE WHEN n >= 50 AND close > sma_50 THEN 1 ELSE 0 END)::float
                       / NULLIF(SUM(CASE WHEN n >= 50 THEN 1 ELSE 0 END), 0)::float
                       AS above_pct
            FROM sma
            WHERE d BETWEEN %s AND %s
            GROUP BY d
            ORDER BY d
            """,
            (list(BREADTH_PROXIES), window_start, end, start, end),
        )
        return {d: float(p) for d, p in cur.fetchall() if p is not None}


def classify_day(
    spy: SpyRow,
    vix: float | None,
    breadth: float | None,
) -> tuple[str, str, str]:
    """Combine the three components into ``(regime, spy_trend, vix_level)``.

    Rules (see CLAUDE.md):
      * spy_trend: golden_cross if SMA50 > SMA200 by >2%, death_cross if
        SMA50 < SMA200 by >2%, otherwise neutral.
      * vix_level: <18 calm, 18-30 elevated, >30 panic.
      * regime:
          - bull if golden_cross and not panic and breadth >= 0.5
          - bear if death_cross or panic
          - transition otherwise
    """
    spy_trend = "neutral"
    if spy.sma_50 is not None and spy.sma_200 is not None and spy.sma_200 > 0:
        gap = (spy.sma_50 - spy.sma_200) / spy.sma_200
        if gap > 0.02:
            spy_trend = "golden_cross"
        elif gap < -0.02:
            spy_trend = "death_cross"

    if vix is None:
        vix_level = "unknown"
    elif vix < VIX_CALM:
        vix_level = "calm"
    elif vix <= VIX_PANIC:
        vix_level = "elevated"
    else:
        vix_level = "panic"

    breadth_ok = breadth is not None and breadth >= 0.5

    if spy_trend == "death_cross" or vix_level == "panic":
        regime = "bear"
    elif spy_trend == "golden_cross" and vix_level != "panic" and breadth_ok:
        regime = "bull"
    else:
        regime = "transition"

    return regime, spy_trend, vix_level


def classify_command(conn, start: date, end: date) -> int:
    """Write a row to ``market.regime`` for every SPY trading day in range."""
    spy = fetch_spy_series(conn, start, end)
    if not spy:
        print("No SPY rows found. Backfill SPY into market.ohlcv first.")
        return 1
    vix = fetch_vix_series(conn, start, end)
    vix_source = "vix"
    if not vix:
        print("No VIX asset found — falling back to SPY 20d realized-vol proxy.")
        vix = spy_realized_vol_proxy(spy)
        vix_source = "rv_proxy"
    breadth = fetch_breadth_series(conn, start, end)

    counts = {"bull": 0, "bear": 0, "transition": 0}
    with conn.cursor() as cur:
        for row in spy:
            v = vix.get(row.d)
            b = breadth.get(row.d)
            regime, spy_trend, vix_level = classify_day(row, v, b)
            counts[regime] += 1
            cur.execute(
                """
                INSERT INTO market.regime (
                    date, regime, spy_trend, spy_sma_50, spy_sma_200, spy_close,
                    vix_level, vix_value, breadth_proxy, details
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (date) DO UPDATE SET
                    regime         = EXCLUDED.regime,
                    spy_trend      = EXCLUDED.spy_trend,
                    spy_sma_50     = EXCLUDED.spy_sma_50,
                    spy_sma_200    = EXCLUDED.spy_sma_200,
                    spy_close      = EXCLUDED.spy_close,
                    vix_level      = EXCLUDED.vix_level,
                    vix_value      = EXCLUDED.vix_value,
                    breadth_proxy  = EXCLUDED.breadth_proxy,
                    details        = EXCLUDED.details,
                    computed_at    = NOW()
                """,
                (
                    row.d, regime, spy_trend,
                    row.sma_50, row.sma_200, row.close,
                    vix_level, v, b,
                    Json({"vix_source": vix_source}),
                ),
            )
    conn.commit()
    print(
        f"Classified {len(spy)} days  "
        f"bull={counts['bull']} bear={counts['bear']} transition={counts['transition']}"
    )
    return 0


# ---------------------------------------------------------------------------
# Step 2: analyze (per-regime factor → forward-return correlation)
# ---------------------------------------------------------------------------

def fetch_regime_map(conn, start: date, end: date) -> dict[date, str]:
    """Return ``{date: regime}`` for every classified day in range."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT date, regime FROM market.regime "
            "WHERE date BETWEEN %s AND %s",
            (start, end),
        )
        return {d: r for d, r in cur.fetchall()}


def fetch_signal_factors(
    conn, start: date, end: date
) -> list[dict[str, Any]]:
    """Pull every signal in range with its six per-factor sub-scores."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT symbol, signal_date, composite_score,
                   iv_regime_score, gex_score, tech_score,
                   iv_rv_score, sentiment_score, iv_outlier_score
            FROM trading.signals
            WHERE signal_date BETWEEN %s AND %s
            ORDER BY signal_date, symbol
            """,
            (start, end),
        )
        return [dict(r) for r in cur.fetchall()]


def forward_return(
    bars: list[Bar], signal_date: date, horizon: int
) -> float | None:
    """Return ``(close[t+horizon] / close[t] - 1)`` if both bars exist."""
    entry_idx = next(
        (i for i, b in enumerate(bars) if b.d > signal_date), None
    )
    if entry_idx is None:
        return None
    exit_idx = entry_idx + horizon
    if exit_idx >= len(bars):
        return None
    entry = bars[entry_idx].c
    exit_ = bars[exit_idx].c
    if entry <= 0:
        return None
    return (exit_ / entry) - 1.0


def pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation coefficient. Returns ``None`` if undefined."""
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mx = statistics.mean(xs)
    my = statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


FACTOR_COLUMNS: dict[str, str] = {
    "iv_regime": "iv_regime_score",
    "gex": "gex_score",
    "tech": "tech_score",
    "iv_rv": "iv_rv_score",
    "sentiment": "sentiment_score",
    "outlier": "iv_outlier_score",
    "composite": "composite_score",
}


def analyze_command(conn, start: date, end: date) -> int:
    """Compute per-regime factor-to-forward-return correlations."""
    regime_map = fetch_regime_map(conn, start, end)
    if not regime_map:
        print("No regime rows. Run `classify` first.")
        return 1

    signals = fetch_signal_factors(conn, start, end)
    if not signals:
        print("No signals in range.")
        return 1

    # Cache OHLCV per symbol to compute forward returns.
    bars_by_symbol: dict[str, list[Bar]] = {}

    # Bucket: regime -> factor -> [(score, fwd_return)]
    buckets: dict[str, dict[int, dict[str, list[tuple[float, float]]]]] = {
        r: {5: {f: [] for f in FACTOR_COLUMNS}, 20: {f: [] for f in FACTOR_COLUMNS}}
        for r in ("bull", "bear", "transition")
    }

    for sig in signals:
        regime = regime_map.get(sig["signal_date"])
        if regime is None:
            continue
        sym = sig["symbol"]
        if sym not in bars_by_symbol:
            bars_by_symbol[sym] = fetch_bars(
                conn, sym, start - timedelta(days=5), end + timedelta(days=60)
            )
        bars = bars_by_symbol[sym]
        for horizon in (5, 20):
            ret = forward_return(bars, sig["signal_date"], horizon)
            if ret is None:
                continue
            for factor, col in FACTOR_COLUMNS.items():
                score = sig.get(col)
                if score is None:
                    continue
                buckets[regime][horizon][factor].append((float(score), ret))

    with conn.cursor() as cur:
        for regime, horizons in buckets.items():
            for horizon, by_factor in horizons.items():
                for factor, pairs in by_factor.items():
                    if not pairs:
                        continue
                    xs = [p[0] for p in pairs]
                    ys = [p[1] for p in pairs]
                    corr = pearson(xs, ys)
                    wins = sum(1 for y in ys if y > 0)
                    cur.execute(
                        """
                        INSERT INTO trading.regime_factor_analysis (
                            regime, factor, horizon_days,
                            correlation, sample_size, avg_return, win_rate
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (regime, factor, horizon_days) DO UPDATE SET
                            correlation = EXCLUDED.correlation,
                            sample_size = EXCLUDED.sample_size,
                            avg_return  = EXCLUDED.avg_return,
                            win_rate    = EXCLUDED.win_rate,
                            computed_at = NOW()
                        """,
                        (
                            regime, factor, horizon,
                            round(corr, 5) if corr is not None else None,
                            len(pairs),
                            round(statistics.mean(ys), 6),
                            round(wins / len(ys), 4),
                        ),
                    )
    conn.commit()

    print("Per-regime factor → forward-return correlations:")
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT regime, factor, horizon_days, correlation, sample_size, win_rate
            FROM trading.regime_factor_analysis
            ORDER BY regime, horizon_days, factor
            """
        )
        for r in cur.fetchall():
            c = r["correlation"]
            c_s = f"{c:+.3f}" if c is not None else "  n/a"
            print(
                f"  {r['regime']:11s} h={r['horizon_days']:>2d}d  "
                f"{r['factor']:9s}  corr={c_s}  "
                f"n={r['sample_size']:>4d}  win={r['win_rate']*100:5.2f}%"
            )
    return 0


# ---------------------------------------------------------------------------
# Step 3: optimize (per-regime weights)
# ---------------------------------------------------------------------------

FLOOR_WEIGHT = 5.0
HORIZON_FOR_OPTIMIZER = 20  # 20-day forward return is more stable than 5-day


def optimize_command(conn) -> int:
    """Convert per-regime correlations into a weight vector summing to 100.

    Positive correlations get proportional weight (above the FLOOR). Non-positive
    correlations get the FLOOR weight only. Final vector is rescaled so the six
    weights sum to 100, matching the static scoring scale.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT regime, factor, correlation, sample_size
            FROM trading.regime_factor_analysis
            WHERE horizon_days = %s
              AND factor <> 'composite'
            """,
            (HORIZON_FOR_OPTIMIZER,),
        )
        rows = cur.fetchall()

    if not rows:
        print("No analysis rows. Run `analyze` first.")
        return 1

    by_regime: dict[str, dict[str, tuple[float | None, int]]] = {}
    for r in rows:
        by_regime.setdefault(r["regime"], {})[r["factor"]] = (
            float(r["correlation"]) if r["correlation"] is not None else None,
            int(r["sample_size"]),
        )

    factors = list(FACTOR_MAX.keys())

    with conn.cursor() as cur:
        for regime, fmap in by_regime.items():
            raw: dict[str, float] = {}
            sample = 0
            for f in factors:
                corr, n = fmap.get(f, (None, 0))
                sample = max(sample, n)
                if corr is None or corr <= 0:
                    raw[f] = FLOOR_WEIGHT
                else:
                    raw[f] = FLOOR_WEIGHT + corr * 100.0

            total = sum(raw.values())
            scale = 100.0 / total if total > 0 else 1.0
            w = {f: round(raw[f] * scale, 2) for f in factors}

            cur.execute(
                """
                INSERT INTO trading.regime_weights (
                    regime, scheme,
                    iv_regime_weight, gex_weight, tech_weight,
                    iv_rv_weight, sentiment_weight, outlier_weight,
                    sample_size, notes
                ) VALUES (%s,'optimized',%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (regime, scheme) DO UPDATE SET
                    iv_regime_weight = EXCLUDED.iv_regime_weight,
                    gex_weight       = EXCLUDED.gex_weight,
                    tech_weight      = EXCLUDED.tech_weight,
                    iv_rv_weight     = EXCLUDED.iv_rv_weight,
                    sentiment_weight = EXCLUDED.sentiment_weight,
                    outlier_weight   = EXCLUDED.outlier_weight,
                    sample_size      = EXCLUDED.sample_size,
                    notes            = EXCLUDED.notes,
                    computed_at      = NOW()
                """,
                (
                    regime,
                    w["iv_regime"], w["gex"], w["tech"],
                    w["iv_rv"], w["sentiment"], w["outlier"],
                    sample,
                    f"Optimized from 20d forward-return correlations (floor={FLOOR_WEIGHT})",
                ),
            )
            print(
                f"  {regime:11s}  iv_regime={w['iv_regime']:5.2f}  "
                f"gex={w['gex']:5.2f}  tech={w['tech']:5.2f}  "
                f"iv_rv={w['iv_rv']:5.2f}  sentiment={w['sentiment']:5.2f}  "
                f"outlier={w['outlier']:5.2f}  (n={sample})"
            )
    conn.commit()
    return 0


# ---------------------------------------------------------------------------
# Step 4: backtest (static vs regime-weighted scoring)
# ---------------------------------------------------------------------------

def load_weights(conn, scheme: str) -> dict[str, dict[str, float]]:
    """Return ``{regime: {factor: weight}}`` for the requested scheme."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT regime, iv_regime_weight, gex_weight, tech_weight,
                   iv_rv_weight, sentiment_weight, outlier_weight
            FROM trading.regime_weights
            WHERE scheme = %s
            """,
            (scheme,),
        )
        out: dict[str, dict[str, float]] = {}
        for r in cur.fetchall():
            out[r["regime"]] = {
                "iv_regime": float(r["iv_regime_weight"]),
                "gex":       float(r["gex_weight"]),
                "tech":      float(r["tech_weight"]),
                "iv_rv":     float(r["iv_rv_weight"]),
                "sentiment": float(r["sentiment_weight"]),
                "outlier":   float(r["outlier_weight"]),
            }
        return out


def fetch_signals_with_factors(
    conn, start: date, end: date, symbols: list[str] | None
) -> list[dict[str, Any]]:
    """Pull every signal in range, with the columns needed to re-score it."""
    sql = """
        SELECT symbol, signal_date, composite_score, iv_regime, signal_type,
               iv_regime_score, gex_score, tech_score,
               iv_rv_score, sentiment_score, iv_outlier_score
        FROM trading.signals
        WHERE signal_date BETWEEN %s AND %s
    """
    params: list[Any] = [start, end]
    if symbols:
        sql += " AND symbol = ANY(%s)"
        params.append(symbols)
    sql += " ORDER BY signal_date, symbol"
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def reweight_composite(
    sig: dict[str, Any], weights: dict[str, float]
) -> float:
    """Recompute composite = sum(normalized_factor_score * weight).

    Each factor score is divided by its FACTOR_MAX to project it onto [0, 1],
    then multiplied by the regime-specific weight. The resulting composite
    lives on the same 0-100 scale as the static engine.
    """
    total = 0.0
    for factor, col in (
        ("iv_regime", "iv_regime_score"),
        ("gex",       "gex_score"),
        ("tech",      "tech_score"),
        ("iv_rv",     "iv_rv_score"),
        ("sentiment", "sentiment_score"),
        ("outlier",   "iv_outlier_score"),
    ):
        score = sig.get(col)
        if score is None:
            continue
        norm = float(score) / FACTOR_MAX[factor]
        total += norm * weights[factor]
    return total


def upsert_run(
    conn,
    run_name: str,
    mode: str,
    start: date,
    end: date,
    initial_capital: float,
    threshold: float,
    symbols: list[str] | None,
    rules: dict[str, Any],
    scheme: str,
    weights: dict[str, dict[str, float]],
) -> int:
    """Insert / update the backtest_runs row, stamping the weight scheme used."""
    params = dict(rules)
    params["weight_scheme"] = scheme
    params["regime_weights"] = weights
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO trading.backtest_runs (
                run_name, strategy_mode, start_date, end_date,
                initial_capital, signal_threshold, symbols, params
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (run_name) DO UPDATE SET
                strategy_mode    = EXCLUDED.strategy_mode,
                start_date       = EXCLUDED.start_date,
                end_date         = EXCLUDED.end_date,
                initial_capital  = EXCLUDED.initial_capital,
                signal_threshold = EXCLUDED.signal_threshold,
                symbols          = EXCLUDED.symbols,
                params           = EXCLUDED.params
            RETURNING id
            """,
            (
                run_name, mode, start, end,
                initial_capital, threshold, symbols, Json(params),
            ),
        )
        return int(cur.fetchone()[0])


def run_regime_backtest(
    conn,
    mode: str,
    start: date,
    end: date,
    initial_capital: float,
    threshold: float,
    symbols: list[str] | None,
    run_name: str,
    scheme: str,
) -> dict[str, Any]:
    """Replay signals using regime-conditional weights; return metrics."""
    if mode not in STRATEGY_RULES:
        raise ValueError(f"Unknown strategy mode: {mode}")
    rules = STRATEGY_RULES[mode]

    weights_by_regime = load_weights(conn, scheme)
    if not weights_by_regime:
        raise RuntimeError(
            f"No weights found for scheme={scheme!r}. "
            "Run `optimize` (or check seed rows for scheme=static)."
        )
    regime_map = fetch_regime_map(conn, start, end)

    signals = fetch_signals_with_factors(conn, start, end, symbols)
    print(f"Loaded {len(signals)} raw signals  scheme={scheme}")

    # Re-score each signal under the regime's weight vector, then keep
    # only the bullish-equivalent ones that clear the threshold.
    qualifying: list[dict[str, Any]] = []
    for sig in signals:
        regime = regime_map.get(sig["signal_date"], "transition")
        weights = weights_by_regime.get(regime) or weights_by_regime.get("transition")
        if weights is None:
            continue
        new_composite = reweight_composite(sig, weights)
        if new_composite < threshold:
            continue
        sig = dict(sig)
        sig["composite_score"] = new_composite
        sig["market_regime"] = regime
        qualifying.append(sig)

    print(f"  {len(qualifying)} signals clear threshold {threshold} after reweighting")

    by_symbol: dict[str, list[Bar]] = {}
    trades: list[Trade] = []
    capital = initial_capital
    for sig in qualifying:
        sym = sig["symbol"]
        if sym not in by_symbol:
            by_symbol[sym] = fetch_bars(
                conn, sym, start, end + timedelta(days=400)
            )
        atr = fetch_atr(conn, sym, sig["signal_date"])
        trade = simulate_trade(sym, sig, by_symbol[sym], atr, rules, capital)
        if trade is None:
            continue
        trades.append(trade)
        capital += trade.gross_pnl

    pdt_violations = annotate_pdt(trades) if mode == "day" else 0
    metrics = compute_metrics(trades, initial_capital, start, end)

    run_id = upsert_run(
        conn, run_name, mode, start, end,
        initial_capital, threshold, symbols, rules, scheme, weights_by_regime,
    )
    write_trades(conn, run_id, trades)
    write_metrics(conn, run_id, metrics, pdt_violations)
    conn.commit()

    print_report(run_name, mode, start, end, initial_capital, metrics, pdt_violations)
    return metrics


def backtest_command(
    conn,
    mode: str,
    start: date,
    end: date,
    initial_capital: float,
    threshold: float,
    symbols: list[str] | None,
    scheme: str,
    run_name: str | None,
) -> int:
    """CLI entrypoint for a single regime-aware backtest run."""
    name = run_name or (
        f"regime_{scheme}_{mode}_{start.isoformat()}_{end.isoformat()}"
    )
    run_regime_backtest(
        conn, mode, start, end, initial_capital, threshold,
        symbols, name, scheme,
    )
    return 0


def compare_command(
    conn,
    mode: str,
    start: date,
    end: date,
    initial_capital: float,
    threshold: float,
    symbols: list[str] | None,
) -> int:
    """Run the static and optimized schemes back-to-back and print a diff."""
    name_s = f"regime_static_{mode}_{start.isoformat()}_{end.isoformat()}"
    name_o = f"regime_optimized_{mode}_{start.isoformat()}_{end.isoformat()}"
    print("\n--- STATIC SCHEME ---")
    m_static = run_regime_backtest(
        conn, mode, start, end, initial_capital, threshold, symbols, name_s, "static"
    )
    print("\n--- OPTIMIZED SCHEME ---")
    m_opt = run_regime_backtest(
        conn, mode, start, end, initial_capital, threshold, symbols, name_o, "optimized"
    )
    print()
    print("=" * 72)
    print(f"{'Metric':<22} {'STATIC':>20} {'OPTIMIZED':>20}   Δ")
    print("-" * 72)
    keys = (
        ("total_trades",   "{:d}",      lambda a, b: f"{b - a:+d}"),
        ("win_rate",       "{:.2%}",    lambda a, b: f"{(b - a) * 100:+.2f}pp"),
        ("avg_r_multiple", "{:+.2f}",   lambda a, b: f"{b - a:+.2f}"),
        ("profit_factor",  "{}",        lambda a, b: ""),
        ("expectancy",     "{:+,.2f}",  lambda a, b: f"{b - a:+,.2f}"),
        ("total_return",   "{:+.2%}",   lambda a, b: f"{(b - a) * 100:+.2f}pp"),
        ("cagr",           "{:+.2%}",   lambda a, b: f"{(b - a) * 100:+.2f}pp"),
        ("sharpe",         "{:+.2f}",   lambda a, b: f"{b - a:+.2f}"),
        ("max_drawdown",   "{:.2%}",    lambda a, b: f"{(b - a) * 100:+.2f}pp"),
        ("final_capital",  "${:,.2f}",  lambda a, b: f"${b - a:+,.2f}"),
    )
    for key, fmt, dfn in keys:
        a, b = m_static.get(key), m_opt.get(key)
        if a is None or b is None:
            print(f"{key:<22} {'n/a':>20} {'n/a':>20}")
            continue
        sa = fmt.format(a)
        sb = fmt.format(b)
        delta = dfn(a, b) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else ""
        print(f"{key:<22} {sa:>20} {sb:>20}   {delta}")
    print("=" * 72)
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _add_range_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--start", type=date.fromisoformat, required=True)
    p.add_argument("--end", type=date.fromisoformat, required=True)


def _add_run_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--mode", choices=list(STRATEGY_RULES.keys()), default="swing")
    p.add_argument("--capital", type=float, default=10_000.0)
    p.add_argument("--threshold", type=float, default=50.0,
                   help="Minimum composite score to take a signal (default 50)")
    p.add_argument("--symbol", action="append",
                   help="Restrict to symbol (repeatable)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("classify", help="Classify daily market regime")
    _add_range_args(pc)

    pa = sub.add_parser("analyze", help="Per-regime factor → return correlations")
    _add_range_args(pa)

    sub.add_parser("optimize", help="Optimize per-regime weights from analysis")

    pb = sub.add_parser("backtest", help="Run a regime-weighted backtest")
    _add_range_args(pb)
    _add_run_args(pb)
    pb.add_argument("--scheme", choices=("static", "optimized"), default="optimized")
    pb.add_argument("--name", help="Run name (default: auto-generated)")

    pcm = sub.add_parser("compare", help="Run static and optimized side-by-side")
    _add_range_args(pcm)
    _add_run_args(pcm)

    pall = sub.add_parser("all", help="classify → analyze → optimize → compare")
    _add_range_args(pall)
    _add_run_args(pall)

    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        symbols = (
            [s.upper() for s in args.symbol]
            if getattr(args, "symbol", None)
            else None
        )
        if args.cmd == "classify":
            return classify_command(conn, args.start, args.end)
        if args.cmd == "analyze":
            return analyze_command(conn, args.start, args.end)
        if args.cmd == "optimize":
            return optimize_command(conn)
        if args.cmd == "backtest":
            return backtest_command(
                conn, args.mode, args.start, args.end,
                args.capital, args.threshold, symbols, args.scheme, args.name,
            )
        if args.cmd == "compare":
            return compare_command(
                conn, args.mode, args.start, args.end,
                args.capital, args.threshold, symbols,
            )
        if args.cmd == "all":
            rc = classify_command(conn, args.start, args.end)
            if rc:
                return rc
            rc = analyze_command(conn, args.start, args.end)
            if rc:
                return rc
            rc = optimize_command(conn)
            if rc:
                return rc
            return compare_command(
                conn, args.mode, args.start, args.end,
                args.capital, args.threshold, symbols,
            )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
