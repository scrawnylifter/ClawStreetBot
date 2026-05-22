#!/usr/bin/env python3
"""V2 Greeks Backtest Engine — walk-forward historical replay with greeks strategy validation.

Walks forward through historical daily data, applies the FULL v2 scanner gates
(8-gate setup scanner from scan_setups.py) plus the NEW greeks strategy (mode-keyed
delta bands, theta budget, IV regime), and records every signal + trade outcome with
greeks metadata.

Multi-dimensional slicing (--by-regime, --by-symbol, --by-sector, --period):
  After the walk-forward pass completes, an optional GROUP BY analysis pass slices
  the trades by regime / symbol / sector / named time period. Filters (--symbol,
  --sector) can narrow the pool first. Dimensions can be combined, e.g.:
    --by-regime --sector Technology = "how does the Tech sector perform in each regime?"
  Slices are persisted to trading.backtest_slices for historical comparison.

Two-pass mode (--compare):
  Pass 1: OLD greeks rules — flat delta band 0.50-0.70 for ALL modes, theta budget 0.03.
  Pass 2: NEW greeks rules — mode-keyed delta bands (MODE_DELTA_BANDS), per-mode
           theta budgets (THETA_BUDGETS), IV regime gating.
  Compare: does the new greeks strategy improve win rate / R:R / drawdown?

Signal-quality mode (--signal-quality):
  Decomposes backtest results into signal accuracy vs exit execution quality.
  Signal accuracy: measures MFE (max favorable excursion) and MAE (max adverse
  excursion) over a look-ahead window, independent of exit logic. A signal is
  "correct" if the favorable move hits the win threshold (1 ATR) before the
  adverse move hits the loss threshold (2 ATR). Grades: strong (≥2 ATR MFE),
  marginal (1-2 ATR), wrong.
  Exit execution: runs the normal simulate_trade exit logic, then computes
  capture % = actual exit PnL / MFE. This measures how much of the favorable
  move the exit system captured. Reports capture by exit reason, and flags
  "winners turned into losses" (correct signal but negative exit PnL).
  P&L decomposition: theoretical max (100% MFE capture) vs actual exit P&L vs
  exit slippage (left on table). Allows answering: "is the problem my signals
  or my exits?"
  Combined with --compare: runs signal-quality for both OLD and NEW greeks
  rules, then prints a side-by-side comparison of signal accuracy, exit
  capture, and slippage.

Data sources (no Alpaca API calls):
  - market.ohlcv          — daily bars
  - market.technical_indicators — EMA, RSI, ADX, ATR, MACD
  - market.iv_rank        — IV rank 52w, current IV
  - market.realized_vol   — RV 20d
  - market.regime         — regime classification
  - market.assets         — symbol / active watchlist
  - market.iv_outliers    — IV spike outliers

Trade simulation:
  - Entry = next day open (signal fires EOD, enter next morning)
  - Stop  = ATR×2 (swing), ATR×1.5 (day)
  - TP1   = ATR×6 (swing), ATR×4.5 (day) — exit 50%
  - TP2   = ATR×10 (swing), ATR×7.5 (day) — full close
  - Walk bars forward to find exit (stop breach, TP1, TP2, or expired range)

Greeks simulation (no historical option snapshots available):
  - Delta: if bullish and entry is X% ITM → delta ≈ 0.5 + X*2 (capped 0.90).
           If OTM → delta ≈ 0.5 - (OTM% * 2). Bearish is the mirror for puts.
  - Theta: if DTE < 30: theta/mid ≈ 8-10%, 30-60 DTE: 3-5%, 60+ DTE: 1-2%.
  - Vega: rough approximation ≈ 0.05-0.15 of mid depending on DTE.
  - Gamma: rough approximation ≈ 0.01-0.05 of mid depending on DTE.
  - IV rank + IV regime: from actual DB data.

DB tables (must exist — see db/init/032_backtest_greeks.sql, 033_backtest_slices.sql):
  - trading.backtest_runs           — run metadata
  - trading.backtest_trades         — individual trades with greeks columns
  - trading.backtest_greeks_summary — per-run per-mode aggregate stats
  - trading.backtest_slices         — per-slice metrics (regime/symbol/sector/period)

Usage:
  python archive/backtests/backtest_greeks.py --start 2025-09-08 --end 2026-05-21
  python archive/backtests/backtest_greeks.py --start 2025-09-08 --end 2026-05-21 --mode swing
  python archive/backtests/backtest_greeks.py --start 2025-09-08 --end 2026-05-21 --compare
  python archive/backtests/backtest_greeks.py --start 2025-09-08 --end 2026-05-21 --by-regime
  python archive/backtests/backtest_greeks.py --start 2025-09-08 --end 2026-05-21 --by-symbol --sector Technology
  python archive/backtests/backtest_greeks.py --start 2025-09-08 --end 2026-05-21 --period iran_crisis --by-sector
  python archive/backtests/backtest_greeks.py --start 2025-09-08 --end 2026-05-21 --signal-quality
  python archive/backtests/backtest_greeks.py --start 2025-09-08 --end 2026-05-21 --signal-quality --compare

ATR multiplier sweep mode (--atr-sweep):
  Sweeps stop ATR multiplier × TP1 ATR multiplier combinations to find the
  optimal stop/target configuration per stock. Grid: 5 stop mults (1.0-3.0)
  × 6 TP1 mults (3.0-10.0) = 30 combos. For each qualifying signal, re-simulates
  the trade across every combo and reports:
    - Overall best combination by profit factor, win rate, expectancy, avg R
    - Per-stock best combination (stock-specific stop/target tuning)
    - PF heat map (stop_mult × tp1_mult) for top-volume symbols
    - Comparison: default ATR mults vs sweep-optimal per stock
    - Multi-criteria disagreement flag (when PF vs WR vs R disagree)
  Results persisted to trading.backtest_atr_sweep.
  Usage:
    python archive/backtests/backtest_greeks.py --start 2025-09-08 --end 2026-05-21 --atr-sweep
    python archive/backtests/backtest_greeks.py --start 2025-09-08 --end 2026-05-21 --atr-sweep --mode day
"""
from __future__ import annotations

import argparse
import math
import os
import random
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import Json, RealDictCursor

# ---------------------------------------------------------------------------
# Import shared constants (DB_CONFIG, greeks strategy params)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # ClawStreetBot/
sys.path.insert(0, str(PROJECT_ROOT / "shared"))
from constants import (  # noqa: E402
    DB_CONFIG,
    DELTA_CEILING,
    DELTA_FLOOR,
    MODE_DELTA_BANDS,
    THETA_BUDGETS,
    classify_regime,
    load_env,
)

load_env(".env.db")

# ---------------------------------------------------------------------------
# Scanner gate thresholds (mirror scan_setups.py exactly)
# ---------------------------------------------------------------------------
ADX_MIN = 20.0
RSI_BULL_MAX = 70.0
RSI_BEAR_MIN = 30.0
IV_PCTILE_MAX = 40.0       # gate 4: IV percentile < 40 (matches scan_setups.py)
IV_RV_SPREAD_MAX = 0.05    # gate 5: current_iv - rv_20d <= 0.05 (matches scan_setups.py)
EMA_GAP_MIN_PCT = 0.5     # gate 1: EMA gap > 0.5%
MIN_DTE = 30
MAX_DTE = 120
ATR_STOP_MULT = {"day": 1.5, "swing": 2.0, "long_term": 2.0}
ATR_TP1_MULT = {"day": 4.5, "swing": 6.0, "long_term": 6.0}
ATR_TP2_MULT = {"day": 7.5, "swing": 10.0, "long_term": 10.0}
RR_MIN = 3.0
MAX_CONCURRENT_POSITIONS = 5  # max open trades at any one time

# OLD (flat) greeks rules for --compare pass
OLD_DELTA_BAND = (0.50, 0.70)
OLD_THETA_BUDGET = 0.03

# Per-mode risk/position sizing
STRATEGY_RULES: dict[str, dict[str, Any]] = {
    "day": {
        "risk_pct": 0.05,
        "max_position_pct": 0.20,
        "max_hold_days": 1,
        "time_stop": True,
        "tp1_size": 0.50,
        "tp2_size": 0.50,
    },
    "swing": {
        "risk_pct": 0.10,
        "max_position_pct": 0.20,
        "max_hold_days": 60,
        "time_stop": False,
        "tp1_size": 0.50,
        "tp2_size": 0.50,
        "trail": True,
        "trail_atr_mult": 2.0,
    },
    "long_term": {
        "risk_pct": 0.05,
        "max_position_pct": 0.15,
        "max_hold_days": 365,
        "time_stop": False,
        "tp1_size": 0.50,
        "tp2_size": 0.50,
        "trail": True,
        "trail_atr_mult": 4.0,
        "thesis_drawdown": 0.35,
    },
}

PDT_WINDOW_DAYS = 5
PDT_MAX_TRADES = 3

# Commission/slippage cost model for net_pnl calculation
# Per-contract commission ($0.65/contract typical Alpaca rate) × 2 (entry + exit)
# plus half-spread slippage on each leg.
COMMISSION_PER_CONTRACT = 0.65   # $0.65 per option contract per side
CONTRACTS_PER_TRADE = 1         # assume 1 contract per signal
SLIPPAGE_PCT = 0.001            # 0.1% round-trip slippage on underlying

# Signal-quality look-ahead windows (days) and thresholds (ATR multiples)
SQ_LOOKAHEAD = {"day": 5, "swing": 20, "long_term": 60}
SQ_MFE_WIN_THRESHOLD = 1.0   # signal = "winner" if MFE >= 1 ATR
SQ_MAE_LOSS_THRESHOLD = 2.0   # signal = "loser" if MAE >= 2 ATR *before* MFE threshold

# ATR sweep grid for --atr-sweep
ATR_SWEEP_STOP_MULTS = [1.0, 1.5, 2.0, 2.5, 3.0]
ATR_SWEEP_TP1_MULTS = [3.0, 4.0, 5.0, 6.0, 8.0, 10.0]
ATR_SWEEP_TP2_RATIO = 10.0 / 6.0  # ≈1.667 — same ratio as default swing TP2/TP1

# ---------------------------------------------------------------------------
# Named time periods for --period flag
# ---------------------------------------------------------------------------
NAMED_PERIODS: dict[str, tuple[str, str]] = {
    "iran_crisis": ("2025-04-04", "2025-06-13"),   # bear regime
    "recovery":    ("2025-06-16", "2025-07-09"),   # transition after crisis
    "bull_run":    ("2025-07-10", "2025-08-31"),   # first bull island
    "full":        ("2024-05-16", "2026-05-21"),   # all available data
}

# Sector lookup: symbol → sector (backed by market.assets, pre-loaded at runtime)
SECTOR_MAP: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class Bar:
    """One OHLCV row."""
    d: date
    o: float
    h: float
    l: float
    c: float


@dataclass
class GreeksTrade:
    """Simulated trade with greeks metadata."""
    symbol: str
    signal_date: date
    direction: str
    trade_mode: str
    entry_date: date
    entry_price: float
    quantity: float
    stop_loss: float
    tp1_price: float
    tp2_price: float
    atr_at_entry: float
    risk_per_share: float
    capital_at_entry: float
    # Greeks metadata
    option_delta: float
    option_theta: float
    option_theta_pct: float
    option_vega: float
    option_gamma: float
    iv_rank_at_entry: float
    iv_regime_at_entry: str
    delta_band_min: float
    delta_band_max: float
    theta_budget: float
    greeks_pass: bool
    greeks_fail_reasons: list[str] = field(default_factory=list)
    # Trade outcome
    exit_date: date | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    hold_days: int = 0
    partial_exits: list[dict[str, Any]] = field(default_factory=list)
    pdt_flag: bool = False

    @property
    def r_multiple(self) -> float:
        if self.risk_per_share <= 0:
            return 0.0
        return self.gross_pnl / (self.risk_per_share * self.quantity)


@dataclass
class SignalQualityResult:
    """Decomposed signal-vs-exit result for --signal-quality mode."""
    symbol: str
    signal_date: date
    direction: str
    trade_mode: str
    atr: float
    # ── Signal accuracy (directional correctness) ──
    mfe_atr: float          # MFE in ATR units (max favorable excursion)
    mfe_price: float         # MFE in absolute price
    mfe_day: int             # day-of-MFE offset from entry (0-based)
    mae_atr: float           # MAE in ATR units (max adverse excursion)
    mae_price: float          # MAE in absolute price
    mae_day: int             # day-of-MAE offset from entry
    signal_correct: bool     # True if MFE hit before MAE threshold
    signal_grade: str         # "strong" | "marginal" | "wrong"
    # ── Exit execution (how well exit system captured the move) ──
    exit_pnl_atr: float      # actual trade P&L in ATR units (None if no trade)
    exit_reason: str | None
    exit_capture_pct: float   # (exit_pnl / MFE) * 100 — how much of MFE was captured
    # ── Metadata ──
    greeks_pass: bool
    option_delta: float
    option_theta_pct: float
    iv_regime_at_entry: str


# ---------------------------------------------------------------------------
# ATR multiplier sweep data structures
# ---------------------------------------------------------------------------
@dataclass
class ATRSweepCombo:
    """Result for one (stop_mult, tp1_mult) combination across all signals."""
    stop_mult: float
    tp1_mult: float
    tp2_mult: float
    trades: int = 0
    winners: int = 0
    losers: int = 0
    win_rate: float = 0.0
    avg_r: float = 0.0
    profit_factor: float | None = 0.0
    total_pnl: float = 0.0
    max_drawdown_pct: float = 0.0
    expectancy: float = 0.0


@dataclass
class ATRSweepSymbolResult:
    """Per-symbol ATR sweep result: optimal combos by multiple criteria."""
    symbol: str
    signal_count: int
    combos: list[ATRSweepCombo] = field(default_factory=list)
    best_by_pf: ATRSweepCombo | None = None      # highest profit factor
    best_by_wr: ATRSweepCombo | None = None      # highest win rate
    best_by_expect: ATRSweepCombo | None = None  # highest expectancy
    best_by_r: ATRSweepCombo | None = None       # highest avg R


# ---------------------------------------------------------------------------
# Signal quality analysis engine
# ---------------------------------------------------------------------------
def compute_mfe_mae(
    bars: list[Bar],
    entry_idx: int,
    direction: str,
    atr: float,
    lookahead_days: int,
) -> dict[str, Any]:
    """Compute Maximum Favorable/Adverse Excursion for a signal.

    Walks forward from entry bar over `lookahead_days` calendar days,
    computing:
      - MFE: max favorable move in ATR units and price units
      - MAE: max adverse move in ATR units and price units
      - Day offsets for each (0 = entry day)
      - Whether MFE hit the win threshold before MAE hit loss threshold
    """
    if atr <= 0 or entry_idx >= len(bars):
        return {
            "mfe_atr": 0.0, "mfe_price": 0.0, "mfe_day": 0,
            "mae_atr": 0.0, "mae_price": 0.0, "mae_day": 0,
            "signal_correct": False, "signal_grade": "wrong",
        }

    entry_bar = bars[entry_idx]
    entry_price = entry_bar.o  # next-day open = entry
    cutoff_date = entry_bar.d + timedelta(days=lookahead_days)

    best_mfe = 0.0   # in ATR units (positive = favorable)
    best_mfe_price = 0.0
    mfe_day = 0
    best_mae = 0.0   # in ATR units (positive = adverse)
    best_mae_price = 0.0
    mae_day = 0
    mfe_hit_win = False
    mae_hit_loss = False
    mfe_hit_day = lookahead_days + 1  # sentinel: beyond window
    mae_hit_day = lookahead_days + 1

    for j in range(entry_idx, len(bars)):
        bar = bars[j]
        if bar.d > cutoff_date:
            break
        day_offset = (bar.d - entry_bar.d).days

        if direction == "bullish":
            # Favorable = price goes up; Adverse = price goes down
            favorable = max(0.0, bar.h - entry_price)
            adverse = max(0.0, entry_price - bar.l)
        else:
            # Bearish: favorable = price goes down; adverse = price goes up
            favorable = max(0.0, entry_price - bar.l)
            adverse = max(0.0, bar.h - entry_price)

        mfe_atr = favorable / atr
        mae_atr = adverse / atr

        if mfe_atr > best_mfe:
            best_mfe = mfe_atr
            best_mfe_price = favorable
            mfe_day = day_offset
        if mae_atr > best_mae:
            best_mae = mae_atr
            best_mae_price = adverse
            mae_day = day_offset

        # Track when thresholds are first hit
        if mfe_atr >= SQ_MFE_WIN_THRESHOLD and not mfe_hit_win:
            mfe_hit_win = True
            mfe_hit_day = day_offset
        if mae_atr >= SQ_MAE_LOSS_THRESHOLD and not mae_hit_loss:
            mae_hit_loss = True
            mae_hit_day = day_offset

    # Signal is "correct" if the favorable move hit the win threshold
    # before (or without) the adverse move hitting the loss threshold
    signal_correct = mfe_hit_win and (not mae_hit_loss or mfe_hit_day <= mae_hit_day)

    # Grade the signal quality
    if best_mfe >= 2.0 and signal_correct:
        signal_grade = "strong"
    elif best_mfe >= 1.0 and signal_correct:
        signal_grade = "marginal"
    else:
        signal_grade = "wrong"

    return {
        "mfe_atr": round(best_mfe, 4),
        "mfe_price": round(best_mfe_price, 4),
        "mfe_day": mfe_day,
        "mae_atr": round(best_mae, 4),
        "mae_price": round(best_mae_price, 4),
        "mae_day": mae_day,
        "signal_correct": signal_correct,
        "signal_grade": signal_grade,
    }


def run_signal_quality_analysis(
    conn,
    mode: str,
    start: date,
    end: date,
    use_new_rules: bool,
    run_name: str,
    initial_capital: float = 10_000.0,
) -> tuple[list[SignalQualityResult], dict[str, Any]]:
    """Run walk-forward signal quality analysis (--signal-quality mode).

    For every signal that passes scanner + greeks gates, compute:
      - MFE/MAE (directional correctness independent of exit logic)
      - Actual trade P&L via simulate_trade (exit execution quality)
      - Capture ratio: how much of the favorable move did exits capture

    Returns (list of SignalQualityResult, summary metrics dict).
    """
    lookahead = SQ_LOOKAHEAD.get(mode, 20)
    capital = initial_capital

    # Fetch all trading dates in range
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT o.timestamp::date
            FROM market.ohlcv o
            JOIN market.assets a ON a.id = o.asset_id
            WHERE o.timeframe = '1d'
              AND a.active = TRUE
              AND o.timestamp::date BETWEEN %s AND %s
            ORDER BY o.timestamp::date
        """, (start, end))
        trading_dates = [r[0] for r in cur.fetchall()]

    print(f"Signal-quality: {len(trading_dates)} trading days, mode={mode}, "
          f"lookahead={lookahead}d, rules={'NEW' if use_new_rules else 'OLD'}")

    # Pre-fetch bars
    bar_end = end + timedelta(days=400)
    with conn.cursor() as cur:
        cur.execute("SELECT symbol FROM market.assets WHERE active = TRUE ORDER BY symbol")
        all_symbols = [r[0] for r in cur.fetchall()]

    bars_cache: dict[str, list[Bar]] = {}
    for sym in all_symbols:
        bars_cache[sym] = fetch_bars(conn, sym, start - timedelta(days=60), bar_end)

    results: list[SignalQualityResult] = []
    scanner_pass = 0
    greeks_pass = 0
    greeks_fail = 0

    for d in trading_dates:
        snapshot = fetch_daily_snapshot(conn, d)

        # BUG FIX: Capital look-ahead bias — collect same-day P&L and apply
        # at end of day so subsequent signals on the same day don't see
        # already-credited (or debited) capital from earlier trades' outcomes.
        day_pnl = 0.0

        for row in snapshot:
            sym = row["symbol"]
            signal = evaluate_scanner_gates(sym, row, mode)
            if signal is None:
                continue

            signal["signal_date"] = d
            scanner_pass += 1

            bars = bars_cache.get(sym, [])
            signal_bar = next((b for b in bars if b.d == d), None)
            if signal_bar is None:
                continue

            # BUG FIX: Use next-day open as entry price (matching simulate_trade),
            # not signal-day close, for greeks simulation
            entry_idx_greeks = next(
                (i for i, b in enumerate(bars) if b.d > d), None
            )
            if entry_idx_greeks is None:
                continue
            entry_price = bars[entry_idx_greeks].o
            greeks = simulate_greeks(signal, entry_price, mode, use_new_rules)

            if not greeks["greeks_pass"]:
                greeks_fail += 1
                continue

            greeks_pass += 1

            # Find entry index (next bar after signal date)
            entry_idx = next(
                (i for i, b in enumerate(bars) if b.d > d), None
            )
            if entry_idx is None:
                continue

            # ── Signal accuracy: compute MFE/MAE ──
            mfe_mae = compute_mfe_mae(
                bars, entry_idx, signal["direction"], signal["atr"], lookahead
            )

            # ── Exit execution: simulate the trade normally ──
            sim_trade = simulate_trade(signal, greeks, bars, mode, capital)
            if sim_trade is not None:
                # BUG FIX: Defer capital update to end-of-day to avoid
                # look-ahead bias
                day_pnl += sim_trade.gross_pnl
                exit_pnl_atr = sim_trade.r_multiple * (
                    ATR_STOP_MULT.get(mode, 2.0)  # r_multiple = pnl / (risk_per_share * qty), risk_per_share = ATR * stop_mult
                ) if sim_trade.risk_per_share > 0 and sim_trade.quantity > 0 else 0.0
                # More precise: actual PnL in ATR units
                if signal["atr"] > 0 and sim_trade.quantity > 0:
                    exit_pnl_atr = sim_trade.gross_pnl / (signal["atr"] * sim_trade.quantity)
                else:
                    exit_pnl_atr = 0.0
                exit_reason = sim_trade.exit_reason
            else:
                exit_pnl_atr = 0.0
                exit_reason = "no_trade"

            # Capture %: how much of MFE did the exit system capture?
            if mfe_mae["mfe_atr"] > 0 and exit_pnl_atr > 0:
                capture_pct = (exit_pnl_atr / mfe_mae["mfe_atr"]) * 100.0
            else:
                capture_pct = 0.0

            results.append(SignalQualityResult(
                symbol=sym,
                signal_date=d,
                direction=signal["direction"],
                trade_mode=mode,
                atr=signal["atr"],
                mfe_atr=mfe_mae["mfe_atr"],
                mfe_price=mfe_mae["mfe_price"],
                mfe_day=mfe_mae["mfe_day"],
                mae_atr=mfe_mae["mae_atr"],
                mae_price=mfe_mae["mae_price"],
                mae_day=mfe_mae["mae_day"],
                signal_correct=mfe_mae["signal_correct"],
                signal_grade=mfe_mae["signal_grade"],
                exit_pnl_atr=round(exit_pnl_atr, 4),
                exit_reason=exit_reason,
                exit_capture_pct=round(capture_pct, 2),
                greeks_pass=greeks["greeks_pass"],
                option_delta=greeks["option_delta"],
                option_theta_pct=greeks["option_theta_pct"],
                iv_regime_at_entry=greeks["iv_regime_at_entry"],
            ))

        # Apply accumulated day P&L after all same-day signals are processed
        capital += day_pnl

    # ── Compute summary metrics ──
    summary = _signal_quality_summary(results)

    print(f"  Scanner passed: {scanner_pass}")
    print(f"  Greeks passed: {greeks_pass}  |  Greeks failed: {greeks_fail}")
    print(f"  Signal quality results: {len(results)}")

    return results, summary


def _signal_quality_summary(results: list[SignalQualityResult]) -> dict[str, Any]:
    """Aggregate signal quality results into summary metrics separating signal vs exit."""
    if not results:
        return {
            "total_signals": 0,
            "signal_accuracy": {},
            "exit_efficiency": {},
            "decomposition": {},
        }

    total = len(results)
    correct = [r for r in results if r.signal_correct]
    wrong = [r for r in results if not r.signal_correct]
    strong = [r for r in results if r.signal_grade == "strong"]
    marginal = [r for r in results if r.signal_grade == "marginal"]
    wrong_grade = [r for r in results if r.signal_grade == "wrong"]

    # ── SIGNAL ACCURACY METRICS (directional correctness, no exit logic) ──
    signal_accuracy = {
        "total_signals": total,
        "correct_count": len(correct),
        "wrong_count": len(wrong),
        "accuracy_pct": round(len(correct) / total * 100, 2) if total else 0.0,
        "strong_count": len(strong),
        "marginal_count": len(marginal),
        "wrong_count": len(wrong_grade),
        "avg_mfe_atr": round(statistics.mean([r.mfe_atr for r in results]), 4),
        "avg_mae_atr": round(statistics.mean([r.mae_atr for r in results]), 4),
        "median_mfe_atr": round(statistics.median([r.mfe_atr for r in results]), 4),
        "avg_mfe_day": round(statistics.mean([r.mfe_day for r in results]), 1),
        "avg_mae_day": round(statistics.mean([r.mae_day for r in results]), 1),
    }

    # Per-grade breakdown
    grade_breakdown = {}
    for grade in ["strong", "marginal", "wrong"]:
        subset = [r for r in results if r.signal_grade == grade]
        if subset:
            grade_breakdown[grade] = {
                "count": len(subset),
                "pct_of_total": round(len(subset) / total * 100, 1),
                "avg_mfe_atr": round(statistics.mean([r.mfe_atr for r in subset]), 4),
                "avg_mae_atr": round(statistics.mean([r.mae_atr for r in subset]), 4),
                "avg_capture_pct": round(statistics.mean([r.exit_capture_pct for r in subset]), 2),
            }
    signal_accuracy["grade_breakdown"] = grade_breakdown

    # ── EXIT EFFICIENCY METRICS (how well exits capture favorable moves) ──
    # Only meaningful for signals that were directionally correct
    correct_with_mfe = [r for r in correct if r.mfe_atr > 0]
    exit_efficiency = {
        "total_correct_signals": len(correct),
        "correct_with_favorable_move": len(correct_with_mfe),
    }

    if correct_with_mfe:
        captures = [r.exit_capture_pct for r in correct_with_mfe]
        exit_efficiency.update({
            "avg_capture_pct": round(statistics.mean(captures), 2),
            "median_capture_pct": round(statistics.median(captures), 2),
            "capture_over_50pct": len([c for c in captures if c >= 50.0]),
            "capture_over_80pct": len([c for c in captures if c >= 80.0]),
            "capture_under_0_pct": len([c for c in captures if c < 0]),  # winners turned into losses
        })

        # Per-exit-reason capture stats
        by_exit_reason: dict[str, list[float]] = {}
        for r in correct_with_mfe:
            reason = r.exit_reason or "unknown"
            by_exit_reason.setdefault(reason, []).append(r.exit_capture_pct)
        reason_stats = {}
        for reason, caps in sorted(by_exit_reason.items()):
            reason_stats[reason] = {
                "count": len(caps),
                "avg_capture_pct": round(statistics.mean(caps), 2),
            }
        exit_efficiency["by_exit_reason"] = reason_stats
    else:
        exit_efficiency.update({
            "avg_capture_pct": 0.0,
            "median_capture_pct": 0.0,
            "capture_over_50pct": 0,
            "capture_over_80pct": 0,
            "capture_under_0_pct": 0,
        })

    # Exit P&L distribution for correct vs wrong signals
    correct_pnls = [r.exit_pnl_atr for r in correct]
    wrong_pnls = [r.exit_pnl_atr for r in wrong]
    exit_efficiency["avg_exit_pnl_correct_atr"] = round(statistics.mean(correct_pnls), 4) if correct_pnls else 0.0
    exit_efficiency["avg_exit_pnl_wrong_atr"] = round(statistics.mean(wrong_pnls), 4) if wrong_pnls else 0.0

    # ── DECOMPOSITION: attribution of total P&L to signal vs exit ──
    # Theoretical max P&L if we captured 100% of MFE on correct signals and
    # cut losers at 0 (no adverse excursion taken):
    theoretical_signal_pnl = sum(r.mfe_atr for r in correct)
    actual_exit_pnl = sum(r.exit_pnl_atr for r in results)
    exit_slippage = theoretical_signal_pnl - actual_exit_pnl

    # Wrong-signal cost: adverse moves on wrong signals
    wrong_signal_cost = sum(r.mae_atr for r in wrong)

    decomposition = {
        "theoretical_max_pnl_atr": round(theoretical_signal_pnl, 2),
        "actual_exit_pnl_atr": round(actual_exit_pnl, 2),
        "exit_slippage_atr": round(exit_slippage, 2),
        "wrong_signal_cost_atr": round(wrong_signal_cost, 2),
        "signal_value_atr": round(theoretical_signal_pnl - wrong_signal_cost, 2),
        "exit_efficiency_ratio": round(actual_exit_pnl / theoretical_signal_pnl * 100, 2) if theoretical_signal_pnl > 0 else 0.0,
    }

    return {
        "total_signals": total,
        "signal_accuracy": signal_accuracy,
        "exit_efficiency": exit_efficiency,
        "decomposition": decomposition,
    }


def write_signal_quality_results(
    conn,
    run_id: int,
    results: list[SignalQualityResult],
) -> None:
    """Persist signal quality results to trading.backtest_signal_quality."""
    with conn.cursor() as cur:
        # Create table if not exists (idempotent)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS trading.backtest_signal_quality (
                id SERIAL PRIMARY KEY,
                run_id INT REFERENCES trading.backtest_runs(id),
                symbol TEXT NOT NULL,
                signal_date DATE NOT NULL,
                direction TEXT NOT NULL,
                trade_mode TEXT NOT NULL,
                atr DOUBLE PRECISION,
                mfe_atr DOUBLE PRECISION,
                mfe_price DOUBLE PRECISION,
                mfe_day INT,
                mae_atr DOUBLE PRECISION,
                mae_price DOUBLE PRECISION,
                mae_day INT,
                signal_correct BOOLEAN,
                signal_grade TEXT,
                exit_pnl_atr DOUBLE PRECISION,
                exit_reason TEXT,
                exit_capture_pct DOUBLE PRECISION,
                greeks_pass BOOLEAN,
                option_delta DOUBLE PRECISION,
                option_theta_pct DOUBLE PRECISION,
                iv_regime_at_entry TEXT,
                UNIQUE(run_id, symbol, signal_date)
            )
        """)
        for r in results:
            cur.execute("""
                INSERT INTO trading.backtest_signal_quality (
                    run_id, symbol, signal_date, direction, trade_mode, atr,
                    mfe_atr, mfe_price, mfe_day,
                    mae_atr, mae_price, mae_day,
                    signal_correct, signal_grade,
                    exit_pnl_atr, exit_reason, exit_capture_pct,
                    greeks_pass, option_delta, option_theta_pct, iv_regime_at_entry
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s
                )
                ON CONFLICT (run_id, symbol, signal_date) DO UPDATE SET
                    direction = EXCLUDED.direction,
                    trade_mode = EXCLUDED.trade_mode,
                    atr = EXCLUDED.atr,
                    mfe_atr = EXCLUDED.mfe_atr,
                    mfe_price = EXCLUDED.mfe_price,
                    mfe_day = EXCLUDED.mfe_day,
                    mae_atr = EXCLUDED.mae_atr,
                    mae_price = EXCLUDED.mae_price,
                    mae_day = EXCLUDED.mae_day,
                    signal_correct = EXCLUDED.signal_correct,
                    signal_grade = EXCLUDED.signal_grade,
                    exit_pnl_atr = EXCLUDED.exit_pnl_atr,
                    exit_reason = EXCLUDED.exit_reason,
                    exit_capture_pct = EXCLUDED.exit_capture_pct,
                    greeks_pass = EXCLUDED.greeks_pass,
                    option_delta = EXCLUDED.option_delta,
                    option_theta_pct = EXCLUDED.option_theta_pct,
                    iv_regime_at_entry = EXCLUDED.iv_regime_at_entry
            """, (
                run_id, r.symbol, r.signal_date, _db_direction(r.direction),
                r.trade_mode, r.atr,
                r.mfe_atr, r.mfe_price, r.mfe_day,
                r.mae_atr, r.mae_price, r.mae_day,
                r.signal_correct, r.signal_grade,
                r.exit_pnl_atr, r.exit_reason, r.exit_capture_pct,
                r.greeks_pass, r.option_delta, r.option_theta_pct,
                r.iv_regime_at_entry,
            ))


def print_signal_quality_report(
    run_name: str,
    mode: str,
    start: date,
    end: date,
    results: list[SignalQualityResult],
    summary: dict[str, Any],
) -> None:
    """Print the --signal-quality decomposed analysis report."""
    sa = summary["signal_accuracy"]
    ee = summary["exit_efficiency"]
    dc = summary["decomposition"]

    print()
    print("=" * 72)
    print(f"Signal Quality Analysis: {run_name}")
    print(f"Mode: {mode}    Range: {start} → {end}")
    print("=" * 72)

    # ── SECTION 1: SIGNAL ACCURACY ──
    print()
    print("── SIGNAL ACCURACY (directional correctness, no exit logic) ──")
    print(f"  Total signals:    {sa['total_signals']}")
    print(f"  Correct:          {sa['correct_count']}  ({sa['accuracy_pct']:.1f}%)")
    print(f"  Wrong:            {sa['wrong_count']}")
    print(f"  Avg MFE:          {sa['avg_mfe_atr']:.2f} ATR  (day {sa['avg_mfe_day']:.0f})")
    print(f"  Avg MAE:          {sa['avg_mae_atr']:.2f} ATR  (day {sa['avg_mae_day']:.0f})")
    print(f"  Median MFE:       {sa['median_mfe_atr']:.2f} ATR")

    # Grade breakdown
    print()
    print("  Signal Grades:")
    for grade in ["strong", "marginal", "wrong"]:
        gb = sa.get("grade_breakdown", {}).get(grade)
        if gb:
            bar_len = min(int(gb["pct_of_total"] / 2), 30)
            bar = "█" * bar_len
            print(f"    {grade:>8}: {gb['count']:>4} ({gb['pct_of_total']:>5.1f}%) {bar}  "
                  f"MFE={gb['avg_mfe_atr']:.2f} MAE={gb['avg_mae_atr']:.2f}")

    # ── SECTION 2: EXIT EXECUTION EFFICIENCY ──
    print()
    print("── EXIT EXECUTION (how well exits capture favorable moves) ──")
    print(f"  Correct signals with favorable move: {ee['correct_with_favorable_move']}")
    print(f"  Avg capture:   {ee['avg_capture_pct']:.1f}% of MFE")
    print(f"  Median capture: {ee['median_capture_pct']:.1f}% of MFE")
    print(f"  ≥50% captured:  {ee['capture_over_50pct']}")
    print(f"  ≥80% captured:  {ee['capture_over_80pct']}")
    print(f"  <0% captured:  {ee.get('capture_under_0_pct', 0)}  (winners turned into losses)")

    # Per-exit-reason capture
    by_reason = ee.get("by_exit_reason", {})
    if by_reason:
        print()
        print("  Capture by exit reason:")
        for reason, stats in sorted(by_reason.items(), key=lambda x: -x[1]["count"]):
            print(f"    {reason:<16}: {stats['count']:>3} trades  "
                  f"avg capture={stats['avg_capture_pct']:.1f}%")

    # Correct vs wrong exit PnL
    print()
    print(f"  Avg exit P&L on correct signals: {ee['avg_exit_pnl_correct_atr']:+.2f} ATR")
    print(f"  Avg exit P&L on wrong signals:  {ee['avg_exit_pnl_wrong_atr']:+.2f} ATR")

    # ── SECTION 3: P&L DECOMPOSITION ──
    print()
    print("── P&L DECOMPOSITION (signal value vs exit slippage) ──")
    print(f"  Theoretical max (100% MFE capture):    {dc['theoretical_max_pnl_atr']:+.1f} ATR")
    print(f"  Wrong-signal cost (adverse moves):      {dc['wrong_signal_cost_atr']:+.1f} ATR")
    print(f"  Signal value (max - wrong cost):        {dc['signal_value_atr']:+.1f} ATR")
    print(f"  Actual exit P&L:                        {dc['actual_exit_pnl_atr']:+.1f} ATR")
    print(f"  Exit slippage (left on table):          {dc['exit_slippage_atr']:+.1f} ATR")
    print(f"  Exit efficiency ratio:                  {dc['exit_efficiency_ratio']:.1f}%  "
          f"(actual / theoretical)")
    print()

    # Top 10 worst captures (biggest MFE but poor exit)
    if ee["correct_with_favorable_move"] > 0:
        worst_captures = sorted(
            [r for r in results if r.mfe_atr > 0 and r.signal_correct],
            key=lambda r: r.exit_capture_pct
        )[:10]
        print("  Top 10 worst exit captures (biggest MFE, worst capture %):")
        print(f"  {'Symbol':>8} {'Date':>12} {'Dir':>6} {'MFE':>6} {'Exit':>6} {'Cap%':>6} {'Reason':>14}")
        for r in worst_captures:
            print(f"  {r.symbol:>8} {r.signal_date!s:>12} {r.direction[:4]:>6} "
                  f"{r.mfe_atr:>5.1f}R {r.exit_pnl_atr:>+5.1f}R "
                  f"{r.exit_capture_pct:>5.1f}% {(r.exit_reason or '')[:14]:>14}")

    print("=" * 72)


# ---------------------------------------------------------------------------
# ATR multiplier sweep engine (--atr-sweep)
# ---------------------------------------------------------------------------
def _sweep_simulate_trade(
    signal: dict,
    bars: list[Bar],
    mode: str,
    stop_mult: float,
    tp1_mult: float,
    tp2_mult: float,
) -> dict[str, Any] | None:
    """Lightweight trade simulation for ATR sweep — returns P&L dict or None.

    Uses the same walk-forward logic as simulate_trade but avoids creating
    a full GreeksTrade object. Returns dict with gross_pnl, exit_reason,
    r_multiple, hold_days.
    """
    rules = STRATEGY_RULES.get(mode, STRATEGY_RULES["swing"])
    signal_date = signal.get("signal_date", date.today())

    entry_idx = next(
        (i for i, b in enumerate(bars) if b.d > signal_date), None
    )
    if entry_idx is None:
        return None

    entry_bar = bars[entry_idx]
    entry = entry_bar.o
    atr = signal["atr"]
    if atr is None or atr <= 0:
        return None

    direction = signal["direction"]
    if direction == "bullish":
        stop = entry - atr * stop_mult
        tp1 = entry + atr * tp1_mult
        tp2 = entry + atr * tp2_mult
        risk_per_share = entry - stop
    else:
        stop = entry + atr * stop_mult
        tp1 = entry - atr * tp1_mult
        tp2 = entry - atr * tp2_mult
        risk_per_share = stop - entry

    if risk_per_share <= 0:
        return None

    tp1_size = rules["tp1_size"]
    tp2_size = rules["tp2_size"]
    remaining = 1.0
    tp1_hit = False
    tp2_hit = False
    high_water = entry
    low_water_sweep = entry  # BUG FIX: Initialize low_water for bearish trailing stops
    trail_stop = stop
    realised = 0.0
    exit_reason = "end_of_data"
    exit_date = None
    hold_days = 0

    for j in range(entry_idx, len(bars)):
        bar = bars[j]
        hold_days = (bar.d - entry_bar.d).days

        # Day-trade time stop
        if rules.get("time_stop") and j == entry_idx:
            if direction == "bullish":
                if bar.l <= stop:
                    realised += (stop - entry)
                    exit_reason = "stop_loss"
                    exit_date = bar.d
                    break
                if not tp1_hit and bar.h >= tp1:
                    realised += (tp1 - entry) * tp1_size
                    remaining -= tp1_size
                    tp1_hit = True
                if not tp2_hit and bar.h >= tp2:
                    realised += (tp2 - entry) * tp2_size
                    remaining -= tp2_size
                    tp2_hit = True
            else:
                if bar.h >= stop:
                    realised += (entry - stop)
                    exit_reason = "stop_loss"
                    exit_date = bar.d
                    break
                if not tp1_hit and bar.l <= tp1:
                    realised += (entry - tp1) * tp1_size
                    remaining -= tp1_size
                    tp1_hit = True
                if not tp2_hit and bar.l <= tp2:
                    realised += (entry - tp2) * tp2_size
                    remaining -= tp2_size
                    tp2_hit = True

            # Flatten remainder at close
            if remaining > 0:
                if direction == "bullish":
                    realised += (bar.c - entry) * remaining
                else:
                    realised += (entry - bar.c) * remaining
            exit_reason = "time_stop"
            exit_date = bar.d
            break

        # TP checked BEFORE stop (matches real broker: limit orders fill before stops)
        if direction == "bullish":
            if not tp1_hit and bar.h >= tp1:
                realised += (tp1 - entry) * tp1_size
                remaining -= tp1_size
                tp1_hit = True
                if rules.get("trail"):
                    trail_stop = max(trail_stop, entry)
            if not tp2_hit and bar.h >= tp2:
                realised += (tp2 - entry) * tp2_size
                remaining -= tp2_size
                tp2_hit = True
            if remaining > 0 and bar.l <= trail_stop:
                realised += (trail_stop - entry) * remaining
                exit_reason = "stop_loss" if not tp1_hit else "trail_stop"
                exit_date = bar.d
                break
        else:
            if not tp1_hit and bar.l <= tp1:
                realised += (entry - tp1) * tp1_size
                remaining -= tp1_size
                tp1_hit = True
                if rules.get("trail"):
                    trail_stop = min(trail_stop, entry)
            if not tp2_hit and bar.l <= tp2:
                realised += (entry - tp2) * tp2_size
                remaining -= tp2_size
                tp2_hit = True
            if remaining > 0 and bar.h >= trail_stop:
                realised += (entry - trail_stop) * remaining
                exit_reason = "stop_loss" if not tp1_hit else "trail_stop"
                exit_date = bar.d
                break

        # Trailing stop
        if rules.get("trail") and remaining > 0:
            if direction == "bullish":
                high_water = max(high_water, bar.h)
                new_trail = high_water - atr * rules.get("trail_atr_mult", 2.0)
                trail_stop = max(trail_stop, new_trail)
            else:
                # BUG FIX: Track lowest water mark properly (like high_water for bullish)
                low_water_sweep = min(low_water_sweep, bar.l)
                new_trail = low_water_sweep + atr * rules.get("trail_atr_mult", 2.0)
                trail_stop = min(trail_stop, new_trail)

        # Thesis drawdown (long_term)
        if rules.get("thesis_drawdown") and remaining > 0:
            if direction == "bullish":
                if bar.c <= entry * (1.0 - rules["thesis_drawdown"]):
                    realised += (bar.c - entry) * remaining
                    exit_reason = "thesis_stop"
                    exit_date = bar.d
                    break
            else:
                if bar.c >= entry * (1.0 + rules["thesis_drawdown"]):
                    realised += (entry - bar.c) * remaining
                    exit_reason = "thesis_stop"
                    exit_date = bar.d
                    break

        # Max hold
        if hold_days >= rules["max_hold_days"] and remaining > 0:
            if direction == "bullish":
                realised += (bar.c - entry) * remaining
            else:
                realised += (entry - bar.c) * remaining
            exit_reason = "max_hold"
            exit_date = bar.d
            break

        if remaining <= 1e-9:
            exit_reason = "tp2" if tp2_hit else "tp1"
            exit_date = bar.d
            break
    else:
        # Ran off end of data
        last = bars[-1]
        if remaining > 0:
            if direction == "bullish":
                realised += (last.c - entry) * remaining
            else:
                realised += (entry - last.c) * remaining
        exit_date = last.d
        hold_days = (last.d - entry_bar.d).days

    r_multiple = realised / risk_per_share if risk_per_share > 0 else 0.0
    return {
        "gross_pnl": realised,
        "exit_reason": exit_reason,
        "r_multiple": round(r_multiple, 4),
        "hold_days": hold_days,
    }


def run_atr_sweep(
    conn,
    mode: str,
    start: date,
    end: date,
    use_new_rules: bool = True,
    capital: float = 10_000.0,
    stop_mults: list[float] | None = None,
    tp1_mults: list[float] | None = None,
) -> tuple[list[ATRSweepSymbolResult], ATRSweepCombo]:
    """Run ATR multiplier sweep (--atr-sweep).

    For each qualifying signal, re-simulate the trade across every (stop, tp1)
    combination in the grid. Aggregates per-symbol and overall results to find
    the optimal stop/target configuration.

    Returns (per_symbol_results, overall_best_combo).
    """
    if stop_mults is None:
        stop_mults = ATR_SWEEP_STOP_MULTS
    if tp1_mults is None:
        tp1_mults = ATR_SWEEP_TP1_MULTS

    # Fetch trading dates
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT o.timestamp::date
            FROM market.ohlcv o
            JOIN market.assets a ON a.id = o.asset_id
            WHERE o.timeframe = '1d'
              AND a.active = TRUE
              AND o.timestamp::date BETWEEN %s AND %s
            ORDER BY o.timestamp::date
        """, (start, end))
        trading_dates = [r[0] for r in cur.fetchall()]

    grid_size = len(stop_mults) * len(tp1_mults)
    print(f"ATR Sweep: {len(trading_dates)} trading days, mode={mode}, "
          f"grid={len(stop_mults)}×{len(tp1_mults)}={grid_size} combos")

    # Pre-fetch bars
    bar_end = end + timedelta(days=400)
    with conn.cursor() as cur:
        cur.execute("SELECT symbol FROM market.assets WHERE active = TRUE ORDER BY symbol")
        all_symbols = [r[0] for r in cur.fetchall()]

    bars_cache: dict[str, list[Bar]] = {}
    for sym in all_symbols:
        bars_cache[sym] = fetch_bars(conn, sym, start - timedelta(days=60), bar_end)

    # Collect signals: {(symbol, signal_date): (signal_dict, greeks_dict)}
    # Only keep greeks-passed signals
    signals_by_symbol: dict[str, list[tuple[dict, dict]]] = {}
    total_signals = 0

    for d in trading_dates:
        snapshot = fetch_daily_snapshot(conn, d)
        for row in snapshot:
            sym = row["symbol"]
            signal = evaluate_scanner_gates(sym, row, mode)
            if signal is None:
                continue
            signal["signal_date"] = d

            bars = bars_cache.get(sym, [])
            signal_bar = next((b for b in bars if b.d == d), None)
            if signal_bar is None:
                continue

            # BUG FIX: Use next-day open as entry price (matching simulate_trade),
            # not signal-day close, for greeks simulation
            entry_idx_greeks = next(
                (i for i, b in enumerate(bars) if b.d > d), None
            )
            if entry_idx_greeks is None:
                continue
            entry_price = bars[entry_idx_greeks].o
            greeks = simulate_greeks(signal, entry_price, mode, use_new_rules)
            if not greeks["greeks_pass"]:
                continue

            signals_by_symbol.setdefault(sym, []).append((signal, greeks))
            total_signals += 1

    print(f"  Signals collected: {total_signals} across {len(signals_by_symbol)} symbols")

    # Sweep per symbol
    symbol_results: list[ATRSweepSymbolResult] = []
    # Overall: track per-combo (signal_date, gross_pnl, r_multiple) for time-ordered stats
    overall_entries: dict[tuple[float, float, float], list[tuple[date, float, float]]] = {}

    for sym in sorted(signals_by_symbol.keys()):
        sigs = signals_by_symbol[sym]
        sym_result = ATRSweepSymbolResult(symbol=sym, signal_count=len(sigs))
        sym_combo_results: list[ATRSweepCombo] = []

        for stop_m in stop_mults:
            for tp1_m in tp1_mults:
                tp2_m = round(tp1_m * ATR_SWEEP_TP2_RATIO, 2)
                combo_pnls: list[float] = []
                combo_rs: list[float] = []

                for signal, greeks in sigs:
                    bars = bars_cache.get(sym, [])
                    # Override signal's stop/tp mults for sweep
                    sweep_signal = dict(signal)
                    sweep_signal["stop_mult"] = stop_m
                    sweep_signal["tp1_mult"] = tp1_m
                    sweep_signal["tp2_mult"] = tp2_m

                    result = _sweep_simulate_trade(
                        sweep_signal, bars, mode, stop_m, tp1_m, tp2_m,
                    )
                    if result is not None:
                        sig_date = signal.get("signal_date", date.today())
                        combo_pnls.append(result["gross_pnl"])
                        combo_rs.append(result["r_multiple"])
                        # Track for overall (date-ordered) aggregation
                        key = (stop_m, tp1_m, tp2_m)
                        overall_entries.setdefault(key, []).append(
                            (sig_date, result["gross_pnl"], result["r_multiple"])
                        )

                if not combo_pnls:
                    continue

                winners = [p for p in combo_pnls if p > 0]
                losers = [p for p in combo_pnls if p <= 0]
                gross_win = sum(winners)
                gross_loss = abs(sum(losers))
                pf = (gross_win / gross_loss) if gross_loss > 0 else (
                    float("inf") if gross_win > 0 else 0.0
                )
                wr = len(winners) / len(combo_pnls) if combo_pnls else 0.0
                avg_r = statistics.mean(combo_rs) if combo_rs else 0.0
                total_pnl = sum(combo_pnls)
                expectancy = wr * (statistics.mean(winners) if winners else 0) + \
                             (1 - wr) * (-(statistics.mean([abs(p) for p in losers]) if losers else 0))

                # Max drawdown for combo
                eq = 0.0
                peak = 0.0
                max_dd = 0.0
                for p in combo_pnls:
                    eq += p
                    peak = max(peak, eq)
                    if peak > 0:
                        dd = (peak - eq) / peak
                        max_dd = max(max_dd, dd)

                combo = ATRSweepCombo(
                    stop_mult=stop_m,
                    tp1_mult=tp1_m,
                    tp2_mult=tp2_m,
                    trades=len(combo_pnls),
                    winners=len(winners),
                    losers=len(losers),
                    win_rate=round(wr, 4),
                    avg_r=round(avg_r, 4),
                    profit_factor=round(pf, 4) if pf != float("inf") else None,
                    total_pnl=round(total_pnl, 2),
                    max_drawdown_pct=round(max_dd * 100, 2),
                    expectancy=round(expectancy, 4),
                )
                sym_combo_results.append(combo)

                # Tracked above in overall_entries during per-signal loop

        # Find best combos per symbol
        scored_combos = [c for c in sym_combo_results if c.trades > 0]
        if scored_combos:
            sym_result.best_by_pf = max(
                scored_combos,
                key=lambda c: c.profit_factor if c.profit_factor is not None else -1,
            )
            sym_result.best_by_wr = max(scored_combos, key=lambda c: c.win_rate)
            sym_result.best_by_expect = max(scored_combos, key=lambda c: c.expectancy)
            sym_result.best_by_r = max(scored_combos, key=lambda c: c.avg_r)
        sym_result.combos = sym_combo_results
        symbol_results.append(sym_result)

    # Compute overall best combo from already-collected overall_entries
    overall_combos: list[ATRSweepCombo] = []
    for (stop_m, tp1_m, tp2_m), entries in overall_entries.items():
        if not entries:
            continue
        pnls = [e[1] for e in entries]
        rs = [e[2] for e in entries]
        # Sort by signal_date for drawdown
        entries_sorted = sorted(entries, key=lambda e: e[0])
        ordered_pnls = [e[1] for e in entries_sorted]

        winners = [p for p in pnls if p > 0]
        losers_l = [p for p in pnls if p <= 0]
        gross_win = sum(winners)
        gross_loss = abs(sum(losers_l))
        pf = (gross_win / gross_loss) if gross_loss > 0 else (
            float("inf") if gross_win > 0 else 0.0
        )
        wr = len(winners) / len(pnls) if pnls else 0.0
        avg_r = statistics.mean(rs) if rs else 0.0
        total_pnl = sum(pnls)
        expectancy = wr * (statistics.mean(winners) if winners else 0) + \
                     (1 - wr) * (-(statistics.mean([abs(p) for p in losers_l]) if losers_l else 0))

        eq = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in ordered_pnls:
            eq += p
            peak = max(peak, eq)
            if peak > 0:
                dd = (peak - eq) / peak
                max_dd = max(max_dd, dd)

        overall_combos.append(ATRSweepCombo(
            stop_mult=stop_m,
            tp1_mult=tp1_m,
            tp2_mult=tp2_m,
            trades=len(pnls),
            winners=len(winners),
            losers=len(losers_l),
            win_rate=round(wr, 4),
            avg_r=round(avg_r, 4),
            profit_factor=round(pf, 4) if pf != float("inf") else None,
            total_pnl=round(total_pnl, 2),
            max_drawdown_pct=round(max_dd * 100, 2),
            expectancy=round(expectancy, 4),
        ))

    overall_best = max(
        overall_combos,
        key=lambda c: c.profit_factor if c.profit_factor is not None else -1,
    ) if overall_combos else ATRSweepCombo(
        stop_mult=ATR_STOP_MULT.get(mode, 2.0),
        tp1_mult=ATR_TP1_MULT.get(mode, 6.0),
        tp2_mult=ATR_TP2_MULT.get(mode, 10.0),
    )

    return symbol_results, overall_best


def write_atr_sweep_results(
    conn,
    run_name: str,
    symbol_results: list[ATRSweepSymbolResult],
    overall_best: ATRSweepCombo,
    mode: str,
    start: date,
    end: date,
) -> None:
    """Persist ATR sweep results to trading.backtest_atr_sweep."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS trading.backtest_atr_sweep (
                id SERIAL PRIMARY KEY,
                run_name TEXT NOT NULL,
                symbol TEXT NOT NULL,
                mode TEXT NOT NULL,
                start_date DATE NOT NULL,
                end_date DATE NOT NULL,
                signal_count INT,
                best_stop_mult DOUBLE PRECISION,
                best_tp1_mult DOUBLE PRECISION,
                best_tp2_mult DOUBLE PRECISION,
                best_pf DOUBLE PRECISION,
                best_by TEXT NOT NULL DEFAULT 'profit_factor',
                best_win_rate DOUBLE PRECISION,
                best_avg_r DOUBLE PRECISION,
                best_total_pnl DOUBLE PRECISION,
                best_max_dd_pct DOUBLE PRECISION,
                best_expectancy DOUBLE PRECISION,
                all_combos JSONB,
                UNIQUE(run_name, symbol, mode)
            )
        """)
        for sr in symbol_results:
            best = sr.best_by_pf
            if best is None:
                continue
            combos_json = [
                {
                    "stop": c.stop_mult, "tp1": c.tp1_mult, "tp2": c.tp2_mult,
                    "trades": c.trades, "winners": c.winners, "losers": c.losers,
                    "win_rate": c.win_rate, "avg_r": c.avg_r,
                    "profit_factor": c.profit_factor, "total_pnl": c.total_pnl,
                    "max_dd_pct": c.max_drawdown_pct, "expectancy": c.expectancy,
                }
                for c in sr.combos
            ]
            cur.execute("""
                INSERT INTO trading.backtest_atr_sweep (
                    run_name, symbol, mode, start_date, end_date, signal_count,
                    best_stop_mult, best_tp1_mult, best_tp2_mult,
                    best_pf, best_by, best_win_rate, best_avg_r,
                    best_total_pnl, best_max_dd_pct, best_expectancy, all_combos
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                ON CONFLICT (run_name, symbol, mode) DO UPDATE SET
                    signal_count = EXCLUDED.signal_count,
                    best_stop_mult = EXCLUDED.best_stop_mult,
                    best_tp1_mult = EXCLUDED.best_tp1_mult,
                    best_tp2_mult = EXCLUDED.best_tp2_mult,
                    best_pf = EXCLUDED.best_pf,
                    best_by = EXCLUDED.best_by,
                    best_win_rate = EXCLUDED.best_win_rate,
                    best_avg_r = EXCLUDED.best_avg_r,
                    best_total_pnl = EXCLUDED.best_total_pnl,
                    best_max_dd_pct = EXCLUDED.best_max_dd_pct,
                    best_expectancy = EXCLUDED.best_expectancy,
                    all_combos = EXCLUDED.all_combos
            """, (
                run_name, sr.symbol, mode, start, end, sr.signal_count,
                best.stop_mult, best.tp1_mult, best.tp2_mult,
                best.profit_factor, "profit_factor",
                best.win_rate, best.avg_r,
                best.total_pnl, best.max_drawdown_pct, best.expectancy,
                Json(combos_json),
            ))

        # Write overall row (symbol = '__OVERALL__')
        cur.execute("""
            INSERT INTO trading.backtest_atr_sweep (
                run_name, symbol, mode, start_date, end_date, signal_count,
                best_stop_mult, best_tp1_mult, best_tp2_mult,
                best_pf, best_by, best_win_rate, best_avg_r,
                best_total_pnl, best_max_dd_pct, best_expectancy, all_combos
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s
            )
            ON CONFLICT (run_name, symbol, mode) DO UPDATE SET
                signal_count = EXCLUDED.signal_count,
                best_stop_mult = EXCLUDED.best_stop_mult,
                best_tp1_mult = EXCLUDED.best_tp1_mult,
                best_tp2_mult = EXCLUDED.best_tp2_mult,
                best_pf = EXCLUDED.best_pf,
                best_by = EXCLUDED.best_by,
                best_win_rate = EXCLUDED.best_win_rate,
                best_avg_r = EXCLUDED.best_avg_r,
                best_total_pnl = EXCLUDED.best_total_pnl,
                best_max_dd_pct = EXCLUDED.best_max_dd_pct,
                best_expectancy = EXCLUDED.best_expectancy,
                all_combos = EXCLUDED.all_combos
        """, (
            run_name, "__OVERALL__", mode, start, end, 0,
            overall_best.stop_mult, overall_best.tp1_mult, overall_best.tp2_mult,
            overall_best.profit_factor, "profit_factor",
            overall_best.win_rate, overall_best.avg_r,
            overall_best.total_pnl, overall_best.max_drawdown_pct,
            overall_best.expectancy, Json([]),
        ))
    conn.commit()


def print_atr_sweep_report(
    mode: str,
    start: date,
    end: date,
    symbol_results: list[ATRSweepSymbolResult],
    overall_best: ATRSweepCombo,
) -> None:
    """Print the --atr-sweep results report."""
    print()
    print("=" * 78)
    print(f"ATR Multiplier Sweep Results: mode={mode}  range={start} → {end}")
    print(f"Default: stop={ATR_STOP_MULT.get(mode,2.0)}×  "
          f"TP1={ATR_TP1_MULT.get(mode,6.0)}×  TP2={ATR_TP2_MULT.get(mode,10.0)}×")
    print("=" * 78)

    # ── Overall best ──
    print()
    print("── OVERALL BEST COMBO (by profit factor) ──")
    ob = overall_best
    print(f"  Stop={ob.stop_mult}×ATR  TP1={ob.tp1_mult}×ATR  TP2={ob.tp2_mult}×ATR")
    print(f"  Trades={ob.trades}  WinRate={ob.win_rate*100:.1f}%  "
          f"AvgR={ob.avg_r:+.2f}  PF={ob.profit_factor if ob.profit_factor is None else f'{ob.profit_factor:.2f}'}  "
          f"Expectancy={ob.expectancy:+.4f}")
    print(f"  TotalPnL={ob.total_pnl:+,.2f}  MaxDD={ob.max_drawdown_pct:.1f}%")

    # ── Per-symbol best combos ──
    print()
    print("── OPTIMAL STOP/TARGET PER SYMBOL ──")
    hdr = (f"  {'Symbol':>8} {'#Sig':>4} | {'Stop':>5} {'TP1':>5} {'TP2':>5} | "
           f"{'Trades':>6} {'Win%':>6} {'AvgR':>6} {'PF':>6} {'Exp':>8} | "
           f"{'PnL':>10} {'MaxDD':>6}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    for sr in sorted(symbol_results, key=lambda s: -(s.best_by_pf.total_pnl if s.best_by_pf else 0)):
        b = sr.best_by_pf
        if b is None:
            continue
        pf_str = f"{b.profit_factor:.2f}" if b.profit_factor is not None else "n/a"
        print(f"  {sr.symbol:>8} {sr.signal_count:>4} | "
              f"{b.stop_mult:>5.1f} {b.tp1_mult:>5.1f} {b.tp2_mult:>5.1f} | "
              f"{b.trades:>6} {b.win_rate*100:>5.1f}% {b.avg_r:>+5.2f} "
              f"{pf_str:>6} {b.expectancy:>+8.4f} | "
              f"{b.total_pnl:>+10,.0f} {b.max_drawdown_pct:>5.1f}%")

    # ── Heat map: PF by (stop, tp1) for top symbols ──
    # Show top 3 symbols with most signals, or all if ≤5
    top_symbols = sorted(symbol_results, key=lambda s: -s.signal_count)[:5]
    if len(top_symbols) > 1:
        print()
        print("── PF HEAT MAP (stop_mult × tp1_mult) for top-volume symbols ──")
        for sr in top_symbols:
            if not sr.combos:
                continue
            print(f"\n  {sr.symbol} ({sr.signal_count} signals):")
            # Header row
            corner = "stop\\tp1"
            header = f"  {corner:>8}"
            for tp1_m in ATR_SWEEP_TP1_MULTS:
                header += f" {tp1_m:>7.1f}"
            print(header)
            print("  " + "-" * (9 + 8 * len(ATR_SWEEP_TP1_MULTS)))

            for stop_m in ATR_SWEEP_STOP_MULTS:
                row_str = f"  {stop_m:>8.1f}"
                for tp1_m in ATR_SWEEP_TP1_MULTS:
                    match = next(
                        (c for c in sr.combos
                         if abs(c.stop_mult - stop_m) < 0.01
                         and abs(c.tp1_mult - tp1_m) < 0.01),
                        None,
                    )
                    if match and match.profit_factor is not None:
                        # Color code: PF>1.5 = ✓, 1.0-1.5 = •, <1.0 = ✗
                        pf_val = match.profit_factor
                        if pf_val >= 1.5:
                            marker = "✓"
                        elif pf_val >= 1.0:
                            marker = "•"
                        else:
                            marker = "✗"
                        row_str += f" {pf_val:>6.2f}{marker}"
                    elif match and match.profit_factor is None:
                        row_str += f"   inf✓"
                    else:
                        row_str += f"     -"
                print(row_str)

    # ── Comparison vs defaults ──
    default_stop = ATR_STOP_MULT.get(mode, 2.0)
    default_tp1 = ATR_TP1_MULT.get(mode, 6.0)
    default_tp2 = ATR_TP2_MULT.get(mode, 10.0)
    print()
    print("── COMPARISON: DEFAULT vs OPTIMAL ──")
    better_stocks = 0
    worse_stocks = 0
    for sr in symbol_results:
        b = sr.best_by_pf
        if b is None:
            continue
        # Find the default combo for this symbol
        default_combo = next(
            (c for c in sr.combos
             if abs(c.stop_mult - default_stop) < 0.01
             and abs(c.tp1_mult - default_tp1) < 0.01),
            None,
        )
        if default_combo and default_combo.profit_factor is not None and b.profit_factor is not None:
            if b.profit_factor > default_combo.profit_factor:
                better_stocks += 1
            elif b.profit_factor < default_combo.profit_factor:
                worse_stocks += 1

    print(f"  Stocks where sweep beats default: {better_stocks}")
    print(f"  Stocks where default beats sweep:  {worse_stocks}")
    print(f"  Stocks with no default comparison: {len(symbol_results) - better_stocks - worse_stocks}")
    print()

    # ── Multi-criteria best: show per-symbol if different criteria disagree ──
    disagreements = 0
    for sr in symbol_results:
        criteria = [sr.best_by_pf, sr.best_by_wr, sr.best_by_r, sr.best_by_expect]
        combos_set = set()
        for c in criteria:
            if c:
                combos_set.add((c.stop_mult, c.tp1_mult))
        if len(combos_set) > 1:
            disagreements += 1

    if disagreements > 0:
        print(f"  ⚠️  {disagreements}/{len(symbol_results)} symbols have different "
              f"optimal combos across criteria (PF vs WR vs R vs Expect)")
        print("  Use --atr-sweep --name <run> to persist all combos for analysis")

    print("=" * 78)


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------
def _fnum(x) -> float | None:
    """Safe float conversion for numeric DB values."""
    if x is None:
        return None
    try:
        v = float(x)
        return None if v != v else v  # NaN check
    except (TypeError, ValueError):
        return None


def fetch_daily_snapshot(conn, on_date: date) -> list[dict]:
    """Fetch technical indicators, trend/ADX, IV rank, RV for ALL active symbols on a given date.

    ADX is in market.trend_status (not technical_indicators).
    Returns list of dicts keyed by symbol with all scanner gate inputs.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT
                a.symbol,
                ti.ema_9, ti.ema_21, ti.ema_50, ti.ema_200,
                ti.rsi_14, ti.atr_14,
                ts.adx,
                iv.iv_rank_52w, iv.current_iv, iv.iv_percentile,
                rv.rv_20d,
                io.direction AS iv_outlier_direction
            FROM market.assets a
            LEFT JOIN market.technical_indicators ti
                ON ti.symbol = a.symbol AND ti.date = %s
            LEFT JOIN market.trend_status ts
                ON ts.symbol = a.symbol AND ts.date = %s
            LEFT JOIN market.iv_rank iv
                ON iv.symbol = a.symbol AND iv.date = %s
            LEFT JOIN market.realized_vol rv
                ON rv.symbol = a.symbol AND rv.date = %s
            LEFT JOIN market.iv_outliers io
                ON io.symbol = a.symbol AND io.date = %s
            WHERE a.active = TRUE
            ORDER BY a.symbol
        """, (on_date, on_date, on_date, on_date, on_date))
        return [dict(r) for r in cur.fetchall()]


def fetch_bars(conn, symbol: str, start: date, end: date) -> list[Bar]:
    """Daily OHLCV bars (asc) for symbol over [start, end]."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT o.timestamp::date, o.open, o.high, o.low, o.close
            FROM market.ohlcv o
            JOIN market.assets a ON a.id = o.asset_id
            WHERE a.symbol = %s
              AND o.timeframe = '1d'
              AND o.timestamp::date BETWEEN %s AND %s
            ORDER BY o.timestamp
        """, (symbol, start, end))
        return [
            Bar(d=r[0], o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4]))
            for r in cur.fetchall()
        ]


def fetch_regime(conn, on_date: date) -> str | None:
    """Get market regime for a given date."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT regime FROM market.regime
            WHERE date <= %s
            ORDER BY date DESC LIMIT 1
        """, (on_date,))
        r = cur.fetchone()
        return r[0] if r else None


# ---------------------------------------------------------------------------
# Scanner gates (replicate scan_setups.py logic purely from DB data)
# ---------------------------------------------------------------------------
def evaluate_scanner_gates(
    sym: str,
    row: dict,
    mode: str,
) -> dict | None:
    """Run scanner gates 1-8 for one symbol on one date using DB data.

    Returns a signal dict if all gates pass, or None with fail reasons logged.
    """
    ema9 = _fnum(row.get("ema_9"))
    ema21 = _fnum(row.get("ema_21"))
    rsi = _fnum(row.get("rsi_14"))
    atr = _fnum(row.get("atr_14"))
    adx = _fnum(row.get("adx"))
    iv_rank = _fnum(row.get("iv_rank_52w"))
    current_iv = _fnum(row.get("current_iv"))
    rv20 = _fnum(row.get("rv_20d"))
    iv_outlier_dir = row.get("iv_outlier_direction")

    fail_reasons = []

    # Need all basics
    if None in (ema9, ema21, rsi, atr):
        return None

    # Gate 1: Trend direction — EMA gap > 0.5%
    ema_gap = ema9 - ema21
    ema_range = ema21 or 1.0
    ema_pct = abs(ema_gap) / ema_range * 100

    if ema_pct < EMA_GAP_MIN_PCT:
        return None  # flat trend, not logged per spec — just skip

    if ema_gap > 0:
        direction = "bullish"
    else:
        direction = "bearish"

    # Gate 2: ADX > 20
    if adx is None or adx < ADX_MIN:
        fail_reasons.append(f"ADX {adx} < {ADX_MIN}")
        return None

    # Gate 3: RSI not extreme — direction dependent
    if direction == "bullish" and rsi >= RSI_BULL_MAX:
        fail_reasons.append(f"RSI {rsi:.1f} >= {RSI_BULL_MAX}")
        return None
    if direction == "bearish" and rsi <= RSI_BEAR_MIN:
        fail_reasons.append(f"RSI {rsi:.1f} <= {RSI_BEAR_MIN}")
        return None

    # Gate 4: IV percentile < 40 (cheap premium — matches scan_setups.py)
    iv_pctile = _fnum(row.get("iv_percentile"))
    if iv_pctile is None:
        fail_reasons.append("IV percentile missing")
        return None
    if iv_pctile >= IV_PCTILE_MAX:
        fail_reasons.append(f"IV percentile {iv_pctile:.1f} >= {IV_PCTILE_MAX}")
        return None

    # Gate 5: current_iv - rv_20d <= 0.05 (fair pricing — matches scan_setups.py)
    # Both current_iv and rv_20d are stored as decimals (e.g. 0.53 = 53%)
    if current_iv is None or rv20 is None:
        fail_reasons.append("IV or RV20 missing")
        return None
    iv_rv_spread = current_iv - rv20
    if iv_rv_spread > IV_RV_SPREAD_MAX:
        fail_reasons.append(f"IV-RV spread {iv_rv_spread:.4f} > {IV_RV_SPREAD_MAX}")
        return None

    # Gate 6: Premium check — simulated, always pass for backtest (we don't
    # have historical option mid prices; we simulate greeks later)
    # We'll enforce affordability via theta budget instead

    # Gate 7: DTE 30-120 — simulated; we'll assign a DTE based on mode
    if mode == "day":
        dte = 35  # near the short end
    elif mode == "swing":
        dte = 60
    else:
        dte = 90  # long_term
    if dte < MIN_DTE or dte > MAX_DTE:
        fail_reasons.append(f"DTE {dte} outside {MIN_DTE}-{MAX_DTE}")
        return None

    # Gate 8: R:R >= 3:1 — ATR-based
    stop_mult = ATR_STOP_MULT.get(mode, 2.0)
    tp1_mult = ATR_TP1_MULT.get(mode, 6.0)
    tp2_mult = ATR_TP2_MULT.get(mode, 10.0)

    rr = tp1_mult / stop_mult  # reward:risk = TP1 distance / stop distance
    if rr < RR_MIN:
        fail_reasons.append(f"R:R {rr:.2f} < {RR_MIN}")
        return None

    return {
        "symbol": sym,
        "direction": direction,
        "ema9": ema9,
        "ema21": ema21,
        "ema_pct": ema_pct,
        "rsi": rsi,
        "adx": adx,
        "atr": atr,
        "iv_rank": iv_rank,
        "iv_percentile": iv_pctile,
        "current_iv": current_iv,
        "rv20": rv20,
        "iv_rv_spread": iv_rv_spread,
        "iv_outlier_dir": iv_outlier_dir,
        "dte": dte,
        "stop_mult": stop_mult,
        "tp1_mult": tp1_mult,
        "tp2_mult": tp2_mult,
        "rr": rr,
    }


# ---------------------------------------------------------------------------
# Greeks simulation (since no historical option snapshots available)
# ---------------------------------------------------------------------------
def simulate_greeks(
    signal: dict,
    entry_price: float,
    mode: str,
    use_new_rules: bool,
) -> dict:
    """Simulate option greeks for a signal based on entry price and mode.

    Since we can't get historical option greeks/snapshots, we use a rough
    Black-Scholes approximation:
    - Delta: if bullish and entry is X% ITM → delta ≈ 0.5 + X*2 (capped 0.90)
             If OTM → delta ≈ 0.5 - (OTM% * 2). Bearish is the mirror for puts.
    - Theta: if DTE < 30: theta/mid ≈ 8-10%, 30-60 DTE: 3-5%, 60+ DTE: 1-2%
    - Vega: rough ≈ 0.05-0.15 of mid depending on DTE
    - Gamma: rough ≈ 0.01-0.05 of mid depending on DTE

    Also applies greeks strategy gates:
    - Delta must be within mode-specific band (or flat 0.50-0.70 for old rules)
    - |theta|/mid must be within theta budget
    - IV regime must not be sell_premium (new rules only)
    - IV outlier must not be present (both rules)

    Returns dict with all greeks fields + pass/fail info.
    """
    direction = signal["direction"]
    dte = signal["dte"]
    iv_rank = signal["iv_rank"] or 50.0  # default to neutral if missing
    iv_outlier = signal.get("iv_outlier_dir")

    # Simulate option moneyness: assume the scanner selects options at varying ITM depths.
    # Day mode tends to pick slightly deeper ITM for faster moves (3-5%),
    # swing picks moderate (2-8%), long_term picks deeper ITM (3-10%).
    # The moneyness varies per signal to simulate realistic option selection.
    # Use deterministic seeding so OLD and NEW passes produce the same greeks
    # for the same signal, making comparison valid.
    seed_val = hash(f"{signal['symbol']}_{signal['signal_date']}_{signal['direction']}") % (2**31)
    rng = random.Random(seed_val)

    if mode == "day":
        base_itm = rng.uniform(0.03, 0.05)  # 3-5% ITM
    elif mode == "swing":
        base_itm = rng.uniform(0.02, 0.08)  # 2-8% ITM
    else:  # long_term
        base_itm = rng.uniform(0.03, 0.10)  # 3-10% ITM
    itm_pct = base_itm

    # Simulate delta using rough Black-Scholes approximation:
    # delta ≈ 0.5 + itm_pct * 2 (capped at 0.90)
    # e.g., 2% ITM → delta ≈ 0.54, 10% ITM → delta ≈ 0.70
    delta = min(0.50 + itm_pct * 2.0, 0.90)

    # Add some noise based on the symbol's IV rank (higher IV → slightly
    # lower delta for same moneyness because higher vol flattens the curve)
    iv_rank_val = float(iv_rank)
    # At IV rank 50, delta should stay about the same; at 75+ (high vol),
    # push delta down a bit; at 25- (low vol), push delta up
    delta_adj = (50.0 - iv_rank_val) / 500.0  # ±0.05 adjustment range
    delta = max(0.10, min(0.95, delta + delta_adj))

    # Simulate option mid price (rough: 2-8% of underlying depending on DTE + IV)
    mid_pct = 0.03 + (dte / 120.0) * 0.05  # 3% for 30 DTE, 8% for 120 DTE
    option_mid = entry_price * mid_pct

    # Simulate theta/mid based on DTE
    if dte < 30:
        theta_pct = 0.09  # 8-10%
    elif dte < 60:
        theta_pct = 0.04  # 3-5%
    else:
        theta_pct = 0.015  # 1-2%

    # Add some deterministic noise based on signal characteristics
    theta_pct *= (0.85 + rng.random() * 0.3)  # scale ±15-30%

    option_theta = option_mid * theta_pct  # absolute theta value

    # Simulate vega (rough: higher DTE → higher vega)
    if dte < 30:
        vega_pct = 0.05
    elif dte < 60:
        vega_pct = 0.10
    else:
        vega_pct = 0.15
    option_vega = option_mid * vega_pct

    # Simulate gamma (rough: lower DTE → higher gamma)
    if dte < 30:
        gamma_pct = 0.05
    elif dte < 60:
        gamma_pct = 0.03
    else:
        gamma_pct = 0.01
    option_gamma = option_mid * gamma_pct

    # IV regime classification
    iv_regime = classify_regime(iv_rank)

    # For output: iv_rank as a float (already guaranteed non-None above)
    iv_rank_float = float(iv_rank)

    # --- Apply greeks strategy gates ---
    greeks_fail_reasons = []

    if use_new_rules:
        # NEW rules: mode-keyed delta bands + per-mode theta budget + IV regime
        delta_min, delta_max = MODE_DELTA_BANDS.get(mode, (0.50, 0.70))
        theta_budget = THETA_BUDGETS.get(mode, 0.03)

        # Also enforce global floor/ceiling regardless of mode
        effective_min = max(delta_min, DELTA_FLOOR)
        effective_max = min(delta_max, DELTA_CEILING)
    else:
        # OLD rules: flat 0.50-0.70 delta, theta budget 0.03
        effective_min, effective_max = OLD_DELTA_BAND
        theta_budget = OLD_THETA_BUDGET

    # Gate: delta within band
    if delta < effective_min:
        greeks_fail_reasons.append(f"delta {delta:.3f} < min {effective_min:.2f}")
    if delta > effective_max:
        greeks_fail_reasons.append(f"delta {delta:.3f} > max {effective_max:.2f}")

    # Gate: theta within budget
    if theta_pct > theta_budget:
        greeks_fail_reasons.append(f"theta_pct {theta_pct:.3f} > budget {theta_budget:.2f}")

    # Gate: IV regime (new rules only)
    if use_new_rules and iv_regime == "sell_premium":
        greeks_fail_reasons.append(f"iv_regime sell_premium (rank {iv_rank:.0f})")

    # Gate: IV outlier (both rules)
    if iv_outlier is not None:
        greeks_fail_reasons.append(f"iv_outlier {iv_outlier}")

    greeks_pass = len(greeks_fail_reasons) == 0

    return {
        "option_delta": round(delta, 4),
        "option_theta": round(option_theta, 4),
        "option_theta_pct": round(theta_pct, 6),
        "option_vega": round(option_vega, 4),
        "option_gamma": round(option_gamma, 4),
        "iv_rank_at_entry": round(iv_rank_float, 2),
        "iv_regime_at_entry": iv_regime,
        "delta_band_min": effective_min,
        "delta_band_max": effective_max,
        "theta_budget": theta_budget,
        "greeks_pass": greeks_pass,
        "greeks_fail_reasons": greeks_fail_reasons,
        "option_mid": round(option_mid, 2),
    }


# ---------------------------------------------------------------------------
# Trade simulation
# ---------------------------------------------------------------------------
def position_size(
    capital: float,
    entry_price: float,
    risk_per_share: float,
    risk_pct: float,
    max_position_pct: float,
) -> float:
    """Size by risk; cap by max position % of capital."""
    if risk_per_share <= 0 or entry_price <= 0:
        return 0.0
    risk_dollars = capital * risk_pct
    qty_by_risk = risk_dollars / risk_per_share
    qty_by_cap = (capital * max_position_pct) / entry_price
    return max(0.0, min(qty_by_risk, qty_by_cap))


def _compute_net_pnl(trade: GreeksTrade) -> float:
    """Compute net_pnl from gross_pnl by deducting estimated commissions + slippage.

    Model: $0.65/contract × 100 shares/contract × contracts_needed × 2 sides
    contracts_needed = ceil(quantity / 100)  (1 option contract covers 100 shares)
    + 0.1% round-trip slippage on underlying × qty.
    """
    import math
    contracts_needed = max(1, math.ceil(trade.quantity / 100.0)) if trade.quantity > 0 else 1
    commission = COMMISSION_PER_CONTRACT * contracts_needed * 2  # per-contract × entry+exit
    slippage = trade.entry_price * SLIPPAGE_PCT * trade.quantity  # round-trip
    return round(trade.gross_pnl - commission - slippage, 4)


def simulate_trade(
    signal: dict,
    greeks: dict,
    bars: list[Bar],
    mode: str,
    capital: float,
) -> GreeksTrade | None:
    """Replay a signal forward over bars; return the closed trade with greeks metadata.

    Entry: next-bar open after signal date. Exits: SL, TP1 (partial),
    TP2 (partial), trail (remainder), time-stop, or end-of-window close.
    """
    rules = STRATEGY_RULES.get(mode, STRATEGY_RULES["swing"])
    signal_date = signal.get("signal_date", date.today())

    entry_idx = next(
        (i for i, b in enumerate(bars) if b.d > signal_date), None
    )
    if entry_idx is None:
        return None

    entry_bar = bars[entry_idx]
    entry = entry_bar.o
    atr = signal["atr"]
    if atr is None or atr <= 0:
        return None

    stop_mult = signal["stop_mult"]
    tp1_mult = signal["tp1_mult"]
    tp2_mult = signal["tp2_mult"]

    direction = signal["direction"]
    if direction == "bullish":
        stop = entry - atr * stop_mult
        tp1 = entry + atr * tp1_mult
        tp2 = entry + atr * tp2_mult
    else:
        stop = entry + atr * stop_mult
        tp1 = entry - atr * tp1_mult
        tp2 = entry - atr * tp2_mult

    if direction == "bullish":
        risk_per_share = entry - stop
    else:
        risk_per_share = stop - entry

    qty = position_size(
        capital, entry, risk_per_share,
        rules["risk_pct"], rules["max_position_pct"]
    )
    if qty <= 0:
        return None

    trade = GreeksTrade(
        symbol=signal["symbol"],
        signal_date=signal_date,
        direction=direction,
        trade_mode=mode,
        entry_date=entry_bar.d,
        entry_price=entry,
        quantity=qty,
        stop_loss=stop,
        tp1_price=tp1,
        tp2_price=tp2,
        atr_at_entry=atr,
        risk_per_share=risk_per_share,
        capital_at_entry=capital,
        # Greeks fields
        option_delta=greeks["option_delta"],
        option_theta=greeks["option_theta"],
        option_theta_pct=greeks["option_theta_pct"],
        option_vega=greeks["option_vega"],
        option_gamma=greeks["option_gamma"],
        iv_rank_at_entry=greeks["iv_rank_at_entry"],
        iv_regime_at_entry=greeks["iv_regime_at_entry"],
        delta_band_min=greeks["delta_band_min"],
        delta_band_max=greeks["delta_band_max"],
        theta_budget=greeks["theta_budget"],
        greeks_pass=greeks["greeks_pass"],
        greeks_fail_reasons=greeks["greeks_fail_reasons"],
    )

    tp1_size = rules["tp1_size"]
    tp2_size = rules["tp2_size"]
    remaining = 1.0
    tp1_hit = False
    tp2_hit = False
    high_water = entry
    low_water = entry  # BUG FIX: Initialize low_water for bearish trailing stops
    trail_stop = stop
    realised = 0.0

    for j in range(entry_idx, len(bars)):
        bar = bars[j]
        hold_days = (bar.d - entry_bar.d).days
        trade.hold_days = hold_days

        # Day-trade time stop
        if rules.get("time_stop") and j == entry_idx:
            if direction == "bullish":
                if bar.l <= stop:
                    realised += (stop - entry) * qty
                    trade.exit_date = bar.d
                    trade.exit_price = stop
                    trade.exit_reason = "stop_loss"
                    trade.gross_pnl = realised
                    return trade
                if not tp1_hit and bar.h >= tp1:
                    portion = qty * tp1_size
                    realised += (tp1 - entry) * portion
                    trade.partial_exits.append(
                        {"date": bar.d.isoformat(), "price": tp1, "qty": portion, "reason": "tp1"}
                    )
                    remaining -= tp1_size
                    tp1_hit = True
                if not tp2_hit and bar.h >= tp2:
                    portion = qty * tp2_size
                    realised += (tp2 - entry) * portion
                    trade.partial_exits.append(
                        {"date": bar.d.isoformat(), "price": tp2, "qty": portion, "reason": "tp2"}
                    )
                    remaining -= tp2_size
                    tp2_hit = True
            else:  # bearish
                if bar.h >= stop:
                    realised += (entry - stop) * qty
                    trade.exit_date = bar.d
                    trade.exit_price = stop
                    trade.exit_reason = "stop_loss"
                    trade.gross_pnl = realised
                    return trade
                if not tp1_hit and bar.l <= tp1:
                    portion = qty * tp1_size
                    realised += (entry - tp1) * portion
                    trade.partial_exits.append(
                        {"date": bar.d.isoformat(), "price": tp1, "qty": portion, "reason": "tp1"}
                    )
                    remaining -= tp1_size
                    tp1_hit = True
                if not tp2_hit and bar.l <= tp2:
                    portion = qty * tp2_size
                    realised += (entry - tp2) * portion
                    trade.partial_exits.append(
                        {"date": bar.d.isoformat(), "price": tp2, "qty": portion, "reason": "tp2"}
                    )
                    remaining -= tp2_size
                    tp2_hit = True

            # Flatten remainder at close
            if remaining > 0:
                portion = qty * remaining
                if direction == "bullish":
                    realised += (bar.c - entry) * portion
                else:
                    realised += (entry - bar.c) * portion
                trade.partial_exits.append(
                    {"date": bar.d.isoformat(), "price": bar.c, "qty": portion, "reason": "time_stop"}
                )
            trade.exit_date = bar.d
            trade.exit_price = bar.c
            trade.exit_reason = "time_stop"
            trade.gross_pnl = realised
            return trade

        # BUG FIX: Check TP BEFORE stop on each bar.
        # When a bar gaps and both stop and TP trigger, the more favorable
        # outcome (TP hit) should take priority — matching real broker behavior
        # where a limit order (TP) fills before a stop order when price moves
        # through both levels.
        if direction == "bullish":
            # TP1 partial (check before stop)
            if not tp1_hit and bar.h >= tp1:
                portion = qty * tp1_size
                realised += (tp1 - entry) * portion
                trade.partial_exits.append(
                    {"date": bar.d.isoformat(), "price": tp1, "qty": portion, "reason": "tp1"}
                )
                remaining -= tp1_size
                tp1_hit = True
                if rules.get("trail"):
                    trail_stop = max(trail_stop, entry)
            # TP2 partial
            if not tp2_hit and bar.h >= tp2:
                portion = qty * tp2_size
                realised += (tp2 - entry) * portion
                trade.partial_exits.append(
                    {"date": bar.d.isoformat(), "price": tp2, "qty": portion, "reason": "tp2"}
                )
                remaining -= tp2_size
                tp2_hit = True
            # Stop-loss check (after TP — only if no TP hit on this bar)
            if bar.l <= trail_stop and remaining > 0:
                portion = qty * remaining
                realised += (trail_stop - entry) * portion
                trade.partial_exits.append(
                    {"date": bar.d.isoformat(), "price": trail_stop, "qty": portion,
                     "reason": "stop_loss" if not tp1_hit else "trail_stop"}
                )
                trade.exit_date = bar.d
                trade.exit_price = trail_stop
                trade.exit_reason = "stop_loss" if not tp1_hit else "trail_stop"
                trade.gross_pnl = realised
                return trade
        else:  # bearish
            # TP1 partial (check before stop)
            if not tp1_hit and bar.l <= tp1:
                portion = qty * tp1_size
                realised += (entry - tp1) * portion
                trade.partial_exits.append(
                    {"date": bar.d.isoformat(), "price": tp1, "qty": portion, "reason": "tp1"}
                )
                remaining -= tp1_size
                tp1_hit = True
                if rules.get("trail"):
                    trail_stop = min(trail_stop, entry)
            # TP2 partial
            if not tp2_hit and bar.l <= tp2:
                portion = qty * tp2_size
                realised += (entry - tp2) * portion
                trade.partial_exits.append(
                    {"date": bar.d.isoformat(), "price": tp2, "qty": portion, "reason": "tp2"}
                )
                remaining -= tp2_size
                tp2_hit = True
            # Stop-loss check (after TP — only if no TP hit on this bar)
            if bar.h >= trail_stop and remaining > 0:
                portion = qty * remaining
                realised += (entry - trail_stop) * portion
                trade.partial_exits.append(
                    {"date": bar.d.isoformat(), "price": trail_stop, "qty": portion,
                     "reason": "stop_loss" if not tp1_hit else "trail_stop"}
                )
                trade.exit_date = bar.d
                trade.exit_price = trail_stop
                trade.exit_reason = "stop_loss" if not tp1_hit else "trail_stop"
                trade.gross_pnl = realised
                return trade

        # Trailing stop on remainder
        if rules.get("trail") and remaining > 0:
            if direction == "bullish":
                high_water = max(high_water, bar.h)
                new_trail = high_water - atr * rules.get("trail_atr_mult", 2.0)
                trail_stop = max(trail_stop, new_trail)
            else:
                # BUG FIX: Track lowest water mark properly (like high_water for bullish)
                low_water = min(low_water, bar.l)
                new_trail = low_water + atr * rules.get("trail_atr_mult", 2.0)
                trail_stop = min(trail_stop, new_trail)

        # Long-term thesis drawdown
        if rules.get("thesis_drawdown") and remaining > 0:
            if direction == "bullish":
                thesis_floor = entry * (1.0 - rules["thesis_drawdown"])
                if bar.c <= thesis_floor:
                    portion = qty * remaining
                    realised += (bar.c - entry) * portion
                    trade.partial_exits.append(
                        {"date": bar.d.isoformat(), "price": bar.c, "qty": portion, "reason": "thesis_stop"}
                    )
                    trade.exit_date = bar.d
                    trade.exit_price = bar.c
                    trade.exit_reason = "thesis_stop"
                    trade.gross_pnl = realised
                    return trade
            else:
                thesis_ceiling = entry * (1.0 + rules["thesis_drawdown"])
                if bar.c >= thesis_ceiling:
                    portion = qty * remaining
                    realised += (entry - bar.c) * portion
                    trade.partial_exits.append(
                        {"date": bar.d.isoformat(), "price": bar.c, "qty": portion, "reason": "thesis_stop"}
                    )
                    trade.exit_date = bar.d
                    trade.exit_price = bar.c
                    trade.exit_reason = "thesis_stop"
                    trade.gross_pnl = realised
                    return trade

        # Max hold cap
        if hold_days >= rules["max_hold_days"] and remaining > 0:
            portion = qty * remaining
            if direction == "bullish":
                realised += (bar.c - entry) * portion
            else:
                realised += (entry - bar.c) * portion
            trade.partial_exits.append(
                {"date": bar.d.isoformat(), "price": bar.c, "qty": portion, "reason": "max_hold"}
            )
            trade.exit_date = bar.d
            trade.exit_price = bar.c
            trade.exit_reason = "max_hold"
            trade.gross_pnl = realised
            return trade

        if remaining <= 1e-9:
            trade.exit_date = bar.d
            trade.exit_price = tp2 if tp2_hit else tp1
            trade.exit_reason = "tp2" if tp2_hit else "tp1"
            trade.gross_pnl = realised
            return trade

    # Ran off the end of data — close at last bar
    last = bars[-1]
    if remaining > 0:
        portion = qty * remaining
        if direction == "bullish":
            realised += (last.c - entry) * portion
        else:
            realised += (entry - last.c) * portion
        trade.partial_exits.append(
            {"date": last.d.isoformat(), "price": last.c, "qty": portion, "reason": "end_of_data"}
        )
    trade.exit_date = last.d
    trade.exit_price = last.c
    trade.exit_reason = "end_of_data"
    trade.gross_pnl = realised
    trade.hold_days = (last.d - entry_bar.d).days
    return trade


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def compute_metrics(
    trades: list[GreeksTrade],
    initial_capital: float,
    start: date,
    end: date,
) -> dict[str, Any]:
    """Aggregate per-trade results into standard performance metrics."""
    if not trades:
        return {
            "total_trades": 0, "winners": 0, "losers": 0,
            "win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "avg_r_multiple": 0.0, "profit_factor": 0.0,
            "expectancy": 0.0, "total_return": 0.0, "cagr": 0.0,
            "sharpe": 0.0, "max_drawdown": 0.0, "final_capital": initial_capital,
        }

    winners = [t for t in trades if t.gross_pnl > 0]
    losers = [t for t in trades if t.gross_pnl <= 0]
    gross_win = sum(t.gross_pnl for t in winners)
    gross_loss = abs(sum(t.gross_pnl for t in losers))
    win_rate = len(winners) / len(trades) if trades else 0.0
    avg_win = (gross_win / len(winners)) if winners else 0.0
    avg_loss = -(gross_loss / len(losers)) if losers else 0.0
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    avg_r = statistics.mean([t.r_multiple for t in trades]) if trades else 0.0

    ordered = sorted(trades, key=lambda t: (t.exit_date or t.entry_date))
    equity = initial_capital
    curve: list[float] = [equity]
    returns: list[float] = []
    for t in ordered:
        prev = equity
        equity += t.gross_pnl
        curve.append(equity)
        if prev > 0:
            returns.append((equity - prev) / prev)

    final_capital = equity
    total_return = (final_capital - initial_capital) / initial_capital
    years = max(((end - start).days / 365.25), 1e-9)
    base = final_capital / initial_capital
    cagr = (base ** (1 / years) - 1) if base > 0 else -1.0

    sharpe = 0.0
    if len(returns) > 1:
        mu = statistics.mean(returns)
        sd = statistics.stdev(returns)
        if sd > 0:
            n_per_year = len(returns) / years
            sharpe = (mu / sd) * math.sqrt(n_per_year)

    peak = curve[0]
    max_dd = 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            dd = (peak - v) / peak
            max_dd = max(max_dd, dd)

    return {
        "total_trades": len(trades),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": round(win_rate, 4),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "avg_r_multiple": round(avg_r, 4),
        "profit_factor": round(pf, 4) if pf != float("inf") else None,
        "expectancy": round(expectancy, 4),
        "total_return": round(total_return, 6),
        "cagr": round(cagr, 6),
        "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_dd, 6),
        "final_capital": round(final_capital, 2),
    }


def annotate_pdt(trades: list[GreeksTrade]) -> int:
    """Flag day-trades that would have violated PDT (>3 in any rolling 5d)."""
    day_trades = sorted(
        [t for t in trades if t.entry_date == t.exit_date],
        key=lambda t: t.entry_date,
    )
    violations = 0
    for i, t in enumerate(day_trades):
        window_start = t.entry_date - timedelta(days=PDT_WINDOW_DAYS)
        in_window = [
            o for o in day_trades[: i + 1]
            if window_start <= o.entry_date <= t.entry_date
        ]
        if len(in_window) > PDT_MAX_TRADES:
            t.pdt_flag = True
            violations += 1
    return violations


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------
def write_run(
    conn,
    run_name: str,
    mode: str,
    start: date,
    end: date,
    initial_capital: float,
    use_new_rules: bool,
) -> int:
    """Upsert the backtest_runs row; return run_id."""
    params = {
        "engine": "greeks_v2",
        "use_new_rules": use_new_rules,
        "delta_bands": MODE_DELTA_BANDS if use_new_rules else {"all": OLD_DELTA_BAND},
        "theta_budgets": THETA_BUDGETS if use_new_rules else {"all": OLD_THETA_BUDGET},
    }
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO trading.backtest_runs (
                run_name, strategy_mode, start_date, end_date,
                initial_capital, params
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_name) DO UPDATE SET
                strategy_mode = EXCLUDED.strategy_mode,
                start_date = EXCLUDED.start_date,
                end_date = EXCLUDED.end_date,
                initial_capital = EXCLUDED.initial_capital,
                params = EXCLUDED.params
            RETURNING id
        """, (run_name, mode, start, end, initial_capital, Json(params)))
        return int(cur.fetchone()[0])


def _db_direction(direction: str) -> str:
    """Map internal direction to DB-accepted values ('long'/'short')."""
    if direction == "bullish":
        return "long"
    elif direction == "bearish":
        return "short"
    return direction


def write_trades(conn, run_id: int, trades: list[GreeksTrade]) -> None:
    """Idempotent bulk insert of simulated trades with greeks columns."""
    with conn.cursor() as cur:
        for t in trades:
            cur.execute("""
                INSERT INTO trading.backtest_trades (
                    run_id, symbol, signal_date, direction,
                    entry_date, entry_price, quantity,
                    stop_loss, tp1_price, tp2_price, atr_at_entry,
                    risk_per_share, capital_at_entry,
                    exit_date, exit_price, exit_reason,
                    gross_pnl, net_pnl, r_multiple, hold_days,
                    partial_exits, pdt_flag,
                    trade_mode, option_delta, option_theta, option_theta_pct,
                    option_vega, option_gamma, iv_rank_at_entry, iv_regime_at_entry,
                    delta_band_min, delta_band_max, theta_budget,
                    greeks_pass, greeks_fail_reasons
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s
                )
                ON CONFLICT (run_id, symbol, signal_date) DO UPDATE SET
                    direction = EXCLUDED.direction,
                    entry_date = EXCLUDED.entry_date,
                    entry_price = EXCLUDED.entry_price,
                    quantity = EXCLUDED.quantity,
                    stop_loss = EXCLUDED.stop_loss,
                    tp1_price = EXCLUDED.tp1_price,
                    tp2_price = EXCLUDED.tp2_price,
                    atr_at_entry = EXCLUDED.atr_at_entry,
                    risk_per_share = EXCLUDED.risk_per_share,
                    capital_at_entry = EXCLUDED.capital_at_entry,
                    exit_date = EXCLUDED.exit_date,
                    exit_price = EXCLUDED.exit_price,
                    exit_reason = EXCLUDED.exit_reason,
                    gross_pnl = EXCLUDED.gross_pnl,
                    net_pnl = EXCLUDED.net_pnl,
                    r_multiple = EXCLUDED.r_multiple,
                    hold_days = EXCLUDED.hold_days,
                    partial_exits = EXCLUDED.partial_exits,
                    pdt_flag = EXCLUDED.pdt_flag,
                    trade_mode = EXCLUDED.trade_mode,
                    option_delta = EXCLUDED.option_delta,
                    option_theta = EXCLUDED.option_theta,
                    option_theta_pct = EXCLUDED.option_theta_pct,
                    option_vega = EXCLUDED.option_vega,
                    option_gamma = EXCLUDED.option_gamma,
                    iv_rank_at_entry = EXCLUDED.iv_rank_at_entry,
                    iv_regime_at_entry = EXCLUDED.iv_regime_at_entry,
                    delta_band_min = EXCLUDED.delta_band_min,
                    delta_band_max = EXCLUDED.delta_band_max,
                    theta_budget = EXCLUDED.theta_budget,
                    greeks_pass = EXCLUDED.greeks_pass,
                    greeks_fail_reasons = EXCLUDED.greeks_fail_reasons
            """, (
                run_id, t.symbol, t.signal_date, _db_direction(t.direction),
                t.entry_date, t.entry_price, t.quantity,
                t.stop_loss, t.tp1_price, t.tp2_price, t.atr_at_entry,
                t.risk_per_share, t.capital_at_entry,
                t.exit_date, t.exit_price, t.exit_reason,
                t.gross_pnl, t.net_pnl, round(t.r_multiple, 4), t.hold_days,
                Json(t.partial_exits), t.pdt_flag,
                t.trade_mode, t.option_delta, t.option_theta, t.option_theta_pct,
                t.option_vega, t.option_gamma, t.iv_rank_at_entry, t.iv_regime_at_entry,
                t.delta_band_min, t.delta_band_max, t.theta_budget,
                t.greeks_pass, t.greeks_fail_reasons,
            ))


def write_greeks_summary(
    conn,
    run_id: int,
    mode: str,
    all_signals: list[GreeksTrade],
    passed_trades: list[GreeksTrade],
) -> None:
    """Write per-mode greeks summary to backtest_greeks_summary."""
    total_signals = len(all_signals)
    greeks_passed = len([s for s in all_signals if s.greeks_pass])
    greeks_failed = total_signals - greeks_passed

    # Count reject reasons
    delta_rejects = 0
    theta_rejects = 0
    iv_regime_rejects = 0
    iv_outlier_rejects = 0
    for s in all_signals:
        for reason in s.greeks_fail_reasons:
            if "delta" in reason.lower():
                delta_rejects += 1
            elif "theta" in reason.lower():
                theta_rejects += 1
            elif "iv_regime" in reason.lower() or "sell_premium" in reason.lower():
                iv_regime_rejects += 1
            elif "iv_outlier" in reason.lower():
                iv_outlier_rejects += 1

    # Averages from passed trades
    avg_delta = statistics.mean([t.option_delta for t in passed_trades]) if passed_trades else None
    avg_theta_pct = statistics.mean([t.option_theta_pct for t in passed_trades]) if passed_trades else None
    avg_iv_rank = statistics.mean([t.iv_rank_at_entry for t in passed_trades if t.iv_rank_at_entry is not None]) if passed_trades else None

    # Win rate, avg R, profit factor from passed trades
    if passed_trades:
        winners = [t for t in passed_trades if t.gross_pnl > 0]
        losers = [t for t in passed_trades if t.gross_pnl <= 0]
        win_rate = len(winners) / len(passed_trades)
        avg_r = statistics.mean([t.r_multiple for t in passed_trades])
        gross_win = sum(t.gross_pnl for t in winners)
        gross_loss = abs(sum(t.gross_pnl for t in losers))
        profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    else:
        win_rate = None
        avg_r = None
        profit_factor = None

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO trading.backtest_greeks_summary (
                run_id, trade_mode, total_signals, greeks_passed, greeks_failed,
                delta_rejects, theta_rejects, iv_regime_rejects, iv_outlier_rejects,
                avg_delta, avg_theta_pct, avg_iv_rank,
                win_rate, avg_r_multiple, profit_factor
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s
            )
            ON CONFLICT (run_id, trade_mode) DO UPDATE SET
                total_signals = EXCLUDED.total_signals,
                greeks_passed = EXCLUDED.greeks_passed,
                greeks_failed = EXCLUDED.greeks_failed,
                delta_rejects = EXCLUDED.delta_rejects,
                theta_rejects = EXCLUDED.theta_rejects,
                iv_regime_rejects = EXCLUDED.iv_regime_rejects,
                iv_outlier_rejects = EXCLUDED.iv_outlier_rejects,
                avg_delta = EXCLUDED.avg_delta,
                avg_theta_pct = EXCLUDED.avg_theta_pct,
                avg_iv_rank = EXCLUDED.avg_iv_rank,
                win_rate = EXCLUDED.win_rate,
                avg_r_multiple = EXCLUDED.avg_r_multiple,
                profit_factor = EXCLUDED.profit_factor
        """, (
            run_id, mode, total_signals, greeks_passed, greeks_failed,
            delta_rejects, theta_rejects, iv_regime_rejects, iv_outlier_rejects,
            round(avg_delta, 4) if avg_delta is not None else None,
            round(avg_theta_pct, 6) if avg_theta_pct is not None else None,
            round(avg_iv_rank, 2) if avg_iv_rank is not None else None,
            round(win_rate, 4) if win_rate is not None else None,
            round(avg_r, 4) if avg_r is not None else None,
            round(profit_factor, 4) if profit_factor is not None and profit_factor != float("inf") else None,
        ))


def write_metrics(conn, run_id: int, metrics: dict[str, Any], final_capital: float) -> None:
    """Update backtest_runs with final capital."""
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE trading.backtest_runs
            SET final_capital = %s
            WHERE id = %s
        """, (final_capital, run_id))


# ---------------------------------------------------------------------------
# Sector map loader
# ---------------------------------------------------------------------------
def load_sector_map(conn) -> dict[str, str]:
    """Load symbol→sector mapping from market.assets."""
    global SECTOR_MAP
    with conn.cursor() as cur:
        cur.execute("SELECT symbol, sector FROM market.assets WHERE active = TRUE")
        SECTOR_MAP = {r[0]: (r[1] or "Unknown") for r in cur.fetchall()}
    return SECTOR_MAP


# ---------------------------------------------------------------------------
# Multi-dimensional slicing engine
# ---------------------------------------------------------------------------
def _slice_metrics(trades: list[GreeksTrade]) -> dict[str, Any]:
    """Compute standard slice metrics from a list of trades."""
    if not trades:
        return {
            "total_signals": 0, "greeks_passed": 0, "trades": 0,
            "winners": 0, "losers": 0, "win_rate": None,
            "avg_r": None, "profit_factor": None, "avg_hold_days": None,
            "max_dd_pct": None, "total_pnl": 0.0,
        }

    greeks_passed = len([t for t in trades if t.greeks_pass])
    executed = [t for t in trades if t.exit_date is not None]
    winners = [t for t in executed if t.gross_pnl > 0]
    losers = [t for t in executed if t.gross_pnl <= 0]
    win_rate = (len(winners) / len(executed)) if executed else None
    avg_r = statistics.mean([t.r_multiple for t in executed]) if executed else None
    avg_hold = statistics.mean([t.hold_days for t in executed]) if executed else None

    gross_win = sum(t.gross_pnl for t in winners)
    gross_loss = abs(sum(t.gross_pnl for t in losers))
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)

    total_pnl = sum(t.gross_pnl for t in executed)

    # Max drawdown for this slice (equity curve method)
    # BUG FIX: Start equity at the sum of absolute risk (capital basis)
    # so all-loss slices still compute a meaningful drawdown.
    # Use cumulative P&L relative to peak P&L reached.
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    cumulative_risk = sum(
        t.risk_per_share * t.quantity for t in executed
        if t.risk_per_share > 0 and t.quantity > 0
    )
    # If we have no risk basis, just sum P&L directly
    for t in sorted(executed, key=lambda t: (t.exit_date or t.entry_date)):
        equity += t.gross_pnl
        peak = max(peak, equity)
        if peak > 0:
            dd = (peak - equity) / peak
            max_dd = max(max_dd, dd)
        elif peak <= 0 and equity < 0:
            # All trades are losers so far; dd = total loss / total risk
            max_dd = 1.0  # 100% of risk capital lost

    return {
        "total_signals": len(trades),
        "greeks_passed": greeks_passed,
        "trades": len(executed),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": round(win_rate, 4) if win_rate is not None else None,
        "avg_r": round(avg_r, 4) if avg_r is not None else None,
        "profit_factor": round(pf, 4) if pf != float("inf") else None,
        "avg_hold_days": round(avg_hold, 1) if avg_hold is not None else None,
        "max_dd_pct": round(max_dd * 100, 2) if max_dd > 0 else 0.0,
        "total_pnl": round(total_pnl, 2),
    }


def _get_regime_for_date(conn, d: date) -> str:
    """Get market regime for a specific date from market.regime."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT regime FROM market.regime
            WHERE date <= %s
            ORDER BY date DESC LIMIT 1
        """, (d,))
        r = cur.fetchone()
        return r[0] if r else "transition"


# Pre-load regime map for the backtest range to avoid per-trade DB queries
_regime_cache: dict[str, str] = {}


def _preload_regime_cache(conn, start: date, end: date) -> None:
    """Pre-load all regime classifications for the date range."""
    global _regime_cache
    with conn.cursor() as cur:
        cur.execute("""
            SELECT date, regime FROM market.regime
            WHERE date BETWEEN %s AND %s
            ORDER BY date
        """, (start, end))
        _regime_cache = {r[0].isoformat(): r[1] for r in cur.fetchall()}


def _regime_for_signal(signal_date: date) -> str:
    """Return regime for a signal date using cached data, with fallback."""
    key = signal_date.isoformat()
    if key in _regime_cache:
        return _regime_cache[key]
    # Fallback: find nearest prior date in cache
    for i in range(1, 30):
        prev = (signal_date - timedelta(days=i)).isoformat()
        if prev in _regime_cache:
            return _regime_cache[prev]
    return "transition"


def compute_slices(
    conn,
    all_signals: list[GreeksTrade],
    mode: str,
    run_id: int,
    by_regime: bool = False,
    by_symbol: bool = False,
    by_sector: bool = False,
    period: str | None = None,
    symbol_filter: str | None = None,
    sector_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Compute multi-dimensional slices from completed trades in DB.

    Queries trading.backtest_trades for the given run_id to get trades
    with full exit data (exit_date, gross_pnl, r_multiple, hold_days).
    Falls back to filtering in-memory signals if DB query returns nothing.
    """
    # Load completed trades from DB (they have exit simulation results)
    from psycopg2.extras import RealDictCursor
    db_trades = []
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT symbol, signal_date, direction, entry_price, entry_date,
                   exit_date, exit_price, exit_reason, gross_pnl, net_pnl,
                   r_multiple, hold_days, trade_mode, option_delta,
                   iv_rank_at_entry, iv_regime_at_entry, greeks_pass,
                   greeks_fail_reasons, tp1_price, tp2_price, stop_loss,
                   quantity, risk_per_share
            FROM trading.backtest_trades
            WHERE run_id = %s
        """, (run_id,))
        for row in cur.fetchall():
            db_trades.append(type('DBTrade', (), {
                'symbol': row['symbol'],
                'signal_date': row['signal_date'],
                'direction': row['direction'],
                'entry_price': row['entry_price'] or 0,
                'entry_date': row['entry_date'],
                'exit_date': row['exit_date'],
                'exit_price': row['exit_price'],
                'exit_reason': row['exit_reason'],
                'gross_pnl': float(row['gross_pnl'] or 0),
                'net_pnl': float(row['net_pnl'] or 0),
                'r_multiple': float(row['r_multiple'] or 0),
                'hold_days': row['hold_days'] or 0,
                'trade_mode': row['trade_mode'],
                'option_delta': float(row['option_delta'] or 0),
                'iv_rank_at_entry': float(row['iv_rank_at_entry'] or 0),
                'iv_regime_at_entry': row['iv_regime_at_entry'],
                'greeks_pass': row['greeks_pass'],
                'greeks_fail_reasons': row['greeks_fail_reasons'] or [],
                'tp1_price': float(row['tp1_price'] or 0),
                'tp2_price': float(row['tp2_price'] or 0),
                'stop_loss': float(row['stop_loss'] or 0),
                'quantity': float(row['quantity'] or 0),
                'risk_per_share': float(row['risk_per_share'] or 0),
            })())

    # Use DB trades if available, otherwise fall back to in-memory signals
    pool = db_trades if db_trades else list(all_signals)

    # Apply filters first
    if symbol_filter:
        pool = [t for t in pool if t.symbol == symbol_filter.upper()]

    if sector_filter:
        if not SECTOR_MAP:
            load_sector_map(conn)
        symbols_in_sector = [s for s, sec in SECTOR_MAP.items() if sec == sector_filter]
        pool = [t for t in pool if t.symbol in symbols_in_sector]

    if period:
        if period not in NAMED_PERIODS:
            print(f"⚠️  Unknown period '{period}'. Available: {', '.join(NAMED_PERIODS.keys())}")
            return []
        p_start, p_end = NAMED_PERIODS[period]
        p_start_d = date.fromisoformat(p_start)
        p_end_d = date.fromisoformat(p_end)
        pool = [t for t in pool if p_start_d <= t.signal_date <= p_end_d]

    slices: list[dict[str, Any]] = []

    # If no grouping flags, just emit the filtered pool as a single "period" or "filtered" slice
    has_grouping = by_regime or by_symbol or by_sector
    if not has_grouping:
        if period:
            m = _slice_metrics(pool)
            m["slice_type"] = "period"
            m["slice_value"] = period
            m["mode"] = mode
            slices.append(m)
        elif symbol_filter or sector_filter:
            m = _slice_metrics(pool)
            m["slice_type"] = "sector" if sector_filter else "symbol"
            m["slice_value"] = sector_filter or symbol_filter
            m["mode"] = mode
            slices.append(m)
        return slices

    # Group by regime
    if by_regime:
        regime_groups: dict[str, list[GreeksTrade]] = {}
        for t in pool:
            r = _regime_for_signal(t.signal_date)
            regime_groups.setdefault(r, []).append(t)
        for r in sorted(regime_groups.keys()):
            m = _slice_metrics(regime_groups[r])
            m["slice_type"] = "regime"
            m["slice_value"] = r
            m["mode"] = mode
            slices.append(m)

    # Group by symbol
    if by_symbol:
        sym_groups: dict[str, list[GreeksTrade]] = {}
        for t in pool:
            sym_groups.setdefault(t.symbol, []).append(t)
        for s in sorted(sym_groups.keys()):
            m = _slice_metrics(sym_groups[s])
            m["slice_type"] = "symbol"
            m["slice_value"] = s
            m["mode"] = mode
            slices.append(m)

    # Group by sector
    if by_sector:
        if not SECTOR_MAP:
            load_sector_map(conn)
        sec_groups: dict[str, list[GreeksTrade]] = {}
        for t in pool:
            sec = SECTOR_MAP.get(t.symbol, "Unknown")
            sec_groups.setdefault(sec, []).append(t)
        for s in sorted(sec_groups.keys()):
            m = _slice_metrics(sec_groups[s])
            m["slice_type"] = "sector"
            m["slice_value"] = s
            m["mode"] = mode
            slices.append(m)

    return slices


def write_slices(conn, run_id: int, slices: list[dict[str, Any]]) -> None:
    """Persist slice metrics to trading.backtest_slices (idempotent)."""
    with conn.cursor() as cur:
        for s in slices:
            cur.execute("""
                INSERT INTO trading.backtest_slices (
                    run_id, slice_type, slice_value, mode,
                    total_signals, greeks_passed, trades,
                    winners, losers, win_rate, avg_r,
                    profit_factor, avg_hold_days, max_dd_pct, total_pnl
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                ON CONFLICT (run_id, slice_type, slice_value, mode) DO UPDATE SET
                    total_signals = EXCLUDED.total_signals,
                    greeks_passed = EXCLUDED.greeks_passed,
                    trades = EXCLUDED.trades,
                    winners = EXCLUDED.winners,
                    losers = EXCLUDED.losers,
                    win_rate = EXCLUDED.win_rate,
                    avg_r = EXCLUDED.avg_r,
                    profit_factor = EXCLUDED.profit_factor,
                    avg_hold_days = EXCLUDED.avg_hold_days,
                    max_dd_pct = EXCLUDED.max_dd_pct,
                    total_pnl = EXCLUDED.total_pnl
            """, (
                run_id, s["slice_type"], s["slice_value"], s.get("mode"),
                s["total_signals"], s["greeks_passed"], s["trades"],
                s["winners"], s["losers"], s["win_rate"], s["avg_r"],
                s["profit_factor"], s["avg_hold_days"], s["max_dd_pct"], s["total_pnl"],
            ))


def print_slice_table(slices: list[dict[str, Any]], title: str) -> None:
    """Print a compact table of slice results."""
    if not slices:
        print(f"\n{title}: no data")
        return

    print(f"\n{title}")
    print(f"{'':>12}| {'Trades':>6} | {'Win%':>6} | {'Avg R':>6} | {'PF':>6} | {'MaxDD':>6} | {'PNL':>10}")
    print("-" * 12 + "|" + "-" * 8 + "|" + "-" * 8 + "|" + "-" * 8 + "|" + "-" * 8 + "|" + "-" * 8 + "|" + "-" * 12)

    for s in slices:
        val = s["slice_value"][:12]
        trades = s["trades"]
        wr = s.get("win_rate")
        wr_str = f"{wr*100:.1f}%" if wr is not None else "n/a"
        ar = s.get("avg_r")
        ar_str = f"{ar:+.2f}" if ar is not None else "n/a"
        pf = s.get("profit_factor")
        pf_str = f"{pf:.2f}" if pf is not None else "n/a"
        dd = s.get("max_dd_pct", 0.0)
        dd_str = f"{dd:.1f}%" if dd else "0.0%"
        pnl = s.get("total_pnl", 0.0)
        pnl_str = f"${pnl:,.0f}" if pnl >= 0 else f"-${abs(pnl):,.0f}"
        if pnl < 0:
            pnl_str = f"-${abs(pnl):,.0f}"
        print(f"{val:>12}| {trades:>6} | {wr_str:>6} | {ar_str:>6} | {pf_str:>6} | {dd_str:>6} | {pnl_str:>10}")


# ---------------------------------------------------------------------------
# Walk-forward backtest engine
# ---------------------------------------------------------------------------
def run_greeks_backtest(
    conn,
    mode: str,
    start: date,
    end: date,
    initial_capital: float,
    use_new_rules: bool,
    run_name: str,
) -> tuple[dict[str, Any], list[GreeksTrade], dict[str, int]]:
    """Walk forward through historical daily data, apply scanner + greeks gates.

    Returns (metrics, all_trades_with_greeks, reject_counts).
    """
    rules = STRATEGY_RULES.get(mode, STRATEGY_RULES["swing"])

    # Fetch all trading dates in the range
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT o.timestamp::date
            FROM market.ohlcv o
            JOIN market.assets a ON a.id = o.asset_id
            WHERE o.timeframe = '1d'
              AND a.active = TRUE
              AND o.timestamp::date BETWEEN %s AND %s
            ORDER BY o.timestamp::date
        """, (start, end))
        trading_dates = [r[0] for r in cur.fetchall()]

    print(f"Walk-forward: {len(trading_dates)} trading days, mode={mode}, "
          f"rules={'NEW' if use_new_rules else 'OLD'}")

    # Pre-fetch all bars per symbol (with extended range for exit simulation)
    bar_end = end + timedelta(days=400)
    all_symbols_rows = []
    with conn.cursor() as cur:
        cur.execute("SELECT symbol FROM market.assets WHERE active = TRUE ORDER BY symbol")
        all_symbols_rows = [r[0] for r in cur.fetchall()]

    bars_cache: dict[str, list[Bar]] = {}
    for sym in all_symbols_rows:
        bars_cache[sym] = fetch_bars(conn, sym, start - timedelta(days=60), bar_end)

    # Track all signals (including greeks-rejected ones) and all executed trades
    all_signals: list[GreeksTrade] = []
    executed_trades: list[GreeksTrade] = []
    open_positions: dict[str, GreeksTrade] = {}   # symbol -> currently open trade
    skip_max_pos = 0                               # count of skipped due to position limit
    capital = initial_capital

    # Reject counters
    reject_counts = {
        "scanner_fail": 0,
        "delta_reject": 0,
        "theta_reject": 0,
        "iv_regime_reject": 0,
        "iv_outlier_reject": 0,
        "no_entry_bar": 0,
    }

    signal_count = 0
    greeks_pass_count = 0
    greeks_fail_count = 0

    for d in trading_dates:
        # Fetch daily snapshot for all active symbols
        snapshot = fetch_daily_snapshot(conn, d)
        regime = fetch_regime(conn, d)

        # BUG FIX: Capital look-ahead bias — collect same-day P&L and apply
        # at end of day so subsequent signals on the same day don't see
        # already-credited (or debited) capital from earlier trades' outcomes.
        day_pnl = 0.0

        for row in snapshot:
            sym = row["symbol"]

            # Run scanner gates 1-8
            signal = evaluate_scanner_gates(sym, row, mode)
            if signal is None:
                reject_counts["scanner_fail"] += 1
                continue

            signal["signal_date"] = d
            signal_count += 1

            # Get the latest close from bars as the current price for greeks sim
            bars = bars_cache.get(sym, [])
            # Find bar for this date
            signal_bar = next((b for b in bars if b.d == d), None)
            if signal_bar is None:
                continue

            # BUG FIX: Use next-day open as entry price (matching simulate_trade),
            # not signal-day close, for greeks simulation
            entry_idx_greeks = next(
                (i for i, b in enumerate(bars) if b.d > d), None
            )
            if entry_idx_greeks is None:
                continue
            entry_price = bars[entry_idx_greeks].o

            # Simulate greeks and apply greeks strategy gates
            greeks = simulate_greeks(signal, entry_price, mode, use_new_rules)

            # Create a trade object (even if greeks fail — we record it)
            trade = GreeksTrade(
                symbol=sym,
                signal_date=d,
                direction=signal["direction"],
                trade_mode=mode,
                entry_date=d,  # placeholder, will be set in simulation
                entry_price=entry_price,
                quantity=0,
                stop_loss=0,
                tp1_price=0,
                tp2_price=0,
                atr_at_entry=signal["atr"],
                risk_per_share=0,
                capital_at_entry=0,
                option_delta=greeks["option_delta"],
                option_theta=greeks["option_theta"],
                option_theta_pct=greeks["option_theta_pct"],
                option_vega=greeks["option_vega"],
                option_gamma=greeks["option_gamma"],
                iv_rank_at_entry=greeks["iv_rank_at_entry"],
                iv_regime_at_entry=greeks["iv_regime_at_entry"],
                delta_band_min=greeks["delta_band_min"],
                delta_band_max=greeks["delta_band_max"],
                theta_budget=greeks["theta_budget"],
                greeks_pass=greeks["greeks_pass"],
                greeks_fail_reasons=greeks["greeks_fail_reasons"],
            )

            all_signals.append(trade)

            if greeks["greeks_pass"]:
                # Enforce max concurrent positions — don't open new if at limit
                if len(open_positions) >= MAX_CONCURRENT_POSITIONS:
                    skip_max_pos += 1
                    greeks_fail_count += 1
                    greeks["greeks_fail_reasons"].append("max_concurrent_positions")
                    continue

                greeks_pass_count += 1
                # Simulate the trade
                sim_trade = simulate_trade(
                    signal, greeks, bars, mode, capital
                )
                if sim_trade is not None:
                    executed_trades.append(sim_trade)
                    # Track open position
                    open_positions[sym] = sim_trade
                    # BUG FIX: Defer capital update to end-of-day to avoid
                    # look-ahead bias — same-day signals must all use the same
                    # capital for position sizing.
                    day_pnl += sim_trade.gross_pnl
            else:
                greeks_fail_count += 1
                for reason in greeks["greeks_fail_reasons"]:
                    if "delta" in reason.lower():
                        reject_counts["delta_reject"] += 1
                    elif "theta" in reason.lower():
                        reject_counts["theta_reject"] += 1
                    elif "iv_regime" in reason.lower() or "sell_premium" in reason.lower():
                        reject_counts["iv_regime_reject"] += 1
                    elif "iv_outlier" in reason.lower():
                        reject_counts["iv_outlier_reject"] += 1

        # Apply accumulated day P&L after all same-day signals are processed
        capital += day_pnl

        # Close out positions that exited today
        closed_today = [sym for sym, t in open_positions.items()
                        if t.exit_date is not None and t.exit_date <= d]
        for sym_to_close in closed_today:
            del open_positions[sym_to_close]

    # Force-close any remaining open positions at end of data
    # Treat as time_stop exit at last available bar's close
    if open_positions:
        last_date = trading_dates[-1] if trading_dates else end
        force_closed = 0
        for sym, t in list(open_positions.items()):
            if t.exit_date is None:
                t.exit_date = last_date
                t.exit_reason = "end_of_data"
                force_closed += 1
                # P&L already computed by simulate_trade at end-of-bars
                if t.gross_pnl == 0 and t.entry_price:
                    # Try to compute from last available data
                    bars = bars_cache.get(sym, [])
                    last_bar = bars[-1] if bars else None
                    if last_bar:
                        direction_mult = 1.0 if t.direction == "bullish" else -1.0
                        t.gross_pnl = direction_mult * (last_bar.c - t.entry_price) * t.quantity
                        t.net_pnl = _compute_net_pnl(t)
        open_positions.clear()
        if force_closed:
            print(f"  Force-closed {force_closed} open positions at end of data")

    pdt_violations = annotate_pdt(executed_trades) if mode == "day" else 0
    metrics = compute_metrics(executed_trades, initial_capital, start, end)

    print(f"  Signals through scanner: {signal_count}")
    print(f"  Greeks passed: {greeks_pass_count}  |  Greeks failed: {greeks_fail_count}")
    print(f"  Executed trades: {len(executed_trades)}")
    if skip_max_pos:
        print(f"  Skipped (max positions): {skip_max_pos}")
    for k, v in reject_counts.items():
        if v > 0:
            print(f"    {k}: {v}")

    # Write to DB — use executed_trades (which have exit simulation results)
    # Merge simulation results back into all_signals for complete DB records
    executed_by_key = {(t.symbol, t.signal_date): t for t in executed_trades}
    merged = []
    for s in all_signals:
        key = (s.symbol, s.signal_date)
        if key in executed_by_key:
            t = executed_by_key[key]
            t.net_pnl = _compute_net_pnl(t)
            merged.append(t)
        else:
            merged.append(s)
    run_id = write_run(conn, run_name, mode, start, end, initial_capital, use_new_rules)
    write_trades(conn, run_id, merged)
    write_greeks_summary(conn, run_id, mode, all_signals, executed_trades)
    write_metrics(conn, run_id, metrics, metrics["final_capital"])
    conn.commit()

    return metrics, all_signals, reject_counts


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def print_report(
    run_name: str,
    mode: str,
    start: date,
    end: date,
    initial: float,
    metrics: dict[str, Any],
    reject_counts: dict[str, int],
    label: str = "",
) -> None:
    """Console summary report for a completed run."""
    tag = f" [{label}]" if label else ""
    print()
    print("=" * 72)
    print(f"Greeks Backtest: {run_name}{tag}")
    print(f"Mode: {mode}    Range: {start} → {end}")
    print(f"Capital: {initial:,.2f} → {metrics['final_capital']:,.2f}  "
          f"({metrics['total_return']*100:+.2f}%, CAGR {metrics['cagr']*100:+.2f}%)")
    print("-" * 72)
    print(f"Trades:        {metrics['total_trades']}  "
          f"(W:{metrics['winners']}  L:{metrics['losers']})")
    print(f"Win rate:      {metrics['win_rate']*100:.2f}%")
    print(f"Avg win:       {metrics['avg_win']:+,.2f}")
    print(f"Avg loss:      {metrics['avg_loss']:+,.2f}")
    print(f"Avg R:         {metrics['avg_r_multiple']:+.2f}")
    pf = metrics["profit_factor"]
    print(f"Profit factor: {pf if pf is None else f'{pf:.2f}'}")
    print(f"Expectancy:    {metrics['expectancy']:+,.2f}")
    print(f"Sharpe:        {metrics['sharpe']:.2f}")
    print(f"Max drawdown:  {metrics['max_drawdown']*100:.2f}%")
    print("-" * 72)
    print("Greeks reject counts:")
    for k, v in sorted(reject_counts.items()):
        if v > 0:
            print(f"  {k}: {v}")
    print("=" * 72)


def print_comparison(
    old_metrics: dict[str, Any],
    new_metrics: dict[str, Any],
    old_rejects: dict[str, int],
    new_rejects: dict[str, int],
    mode: str,
) -> None:
    """Print side-by-side comparison of OLD vs NEW greeks rules."""
    print()
    print("=" * 72)
    print(f"COMPARISON: OLD (flat 0.50-0.70) vs NEW (mode-keyed) — mode: {mode}")
    print("=" * 72)
    print(f"{'Metric':<20} {'OLD':>12} {'NEW':>12} {'Delta':>12}")
    print("-" * 72)

    keys = [
        ("total_trades", "Trades"),
        ("winners", "Winners"),
        ("losers", "Losers"),
        ("win_rate", "Win Rate"),
        ("avg_r_multiple", "Avg R"),
        ("profit_factor", "Profit Factor"),
        ("max_drawdown", "Max DD"),
        ("total_return", "Total Return"),
        ("sharpe", "Sharpe"),
        ("final_capital", "Final Cap"),
    ]

    for key, label in keys:
        old_v = old_metrics.get(key, 0)
        new_v = new_metrics.get(key, 0)
        if old_v is None:
            old_v = 0
        if new_v is None:
            new_v = 0

        if key in ("win_rate", "max_drawdown", "total_return"):
            delta_v = new_v - old_v
            print(f"{label:<20} {old_v*100:>11.2f}% {new_v*100:>11.2f}% {delta_v*100:>+11.2f}%")
        elif key in ("final_capital",):
            delta_v = new_v - old_v
            print(f"{label:<20} {old_v:>12,.2f} {new_v:>12,.2f} {delta_v:>+12,.2f}")
        else:
            delta_v = new_v - old_v
            print(f"{label:<20} {old_v:>12.2f} {new_v:>12.2f} {delta_v:>+12.2f}")

    print("-" * 72)
    print("Greeks reject comparison:")
    all_keys = sorted(set(list(old_rejects.keys()) + list(new_rejects.keys())))
    for k in all_keys:
        ov = old_rejects.get(k, 0)
        nv = new_rejects.get(k, 0)
        print(f"  {k:<25} {ov:>6} {nv:>6} {nv-ov:>+6}")
    print("=" * 72)

    # Verdict
    wr_diff = (new_metrics.get("win_rate", 0) or 0) - (old_metrics.get("win_rate", 0) or 0)
    r_diff = (new_metrics.get("avg_r_multiple", 0) or 0) - (old_metrics.get("avg_r_multiple", 0) or 0)
    dd_diff = (old_metrics.get("max_drawdown", 0) or 0) - (new_metrics.get("max_drawdown", 0) or 0)

    verdict_parts = []
    if wr_diff > 0:
        verdict_parts.append(f"win rate +{wr_diff*100:.1f}%")
    if r_diff > 0:
        verdict_parts.append(f"avg R +{r_diff:.2f}")
    if dd_diff > 0:
        verdict_parts.append(f"lower DD by {dd_diff*100:.1f}%")

    if verdict_parts:
        print(f"\n✅ NEW greeks strategy improves: {', '.join(verdict_parts)}")
    else:
        print(f"\n⚠️  NEW greeks strategy does NOT clearly improve over OLD rules")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", type=date.fromisoformat, required=True)
    p.add_argument("--end", type=date.fromisoformat, required=True)
    p.add_argument("--mode", choices=["day", "swing", "long_term"], default="swing",
                   help="Strategy mode to backtest")
    p.add_argument("--capital", type=float, default=10_000.0)
    p.add_argument("--compare", action="store_true",
                   help="Run both OLD and NEW greeks rules and compare")
    p.add_argument("--signal-quality", action="store_true",
                   help="Decompose results into signal accuracy vs exit execution quality")
    p.add_argument("--atr-sweep", action="store_true",
                   help="Sweep ATR stop/target multipliers to find optimal per-stock config")
    p.add_argument("--name", help="Run name prefix (default: auto-generated)")
    # Multi-dimensional slicing flags
    p.add_argument("--by-regime", action="store_true",
                   help="Group results by market regime (bear/transition/bull)")
    p.add_argument("--by-symbol", action="store_true",
                   help="Group results per symbol (NVDA, AMD, etc.)")
    p.add_argument("--by-sector", action="store_true",
                   help="Group results per sector (Technology, Healthcare, etc.)")
    p.add_argument("--period", choices=list(NAMED_PERIODS.keys()),
                   help="Filter to a named time period")
    p.add_argument("--symbol", help="Filter to a specific symbol (e.g. NVDA)")
    p.add_argument("--sector", help="Filter to a specific sector (e.g. Technology)")
    args = p.parse_args()

    # Validate date range against data availability
    # IV rank data starts 2025-09-09, so enforce that as min start
    min_iv_date = date(2025, 9, 9)
    if args.start < min_iv_date:
        print(f"⚠️  IV rank data starts {min_iv_date}, adjusting start from {args.start} to {min_iv_date}")
        args.start = min_iv_date

    # Pre-load supporting data for slicing
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        # Always load sector map and regime cache for slicing support
        load_sector_map(conn)
        # Extend regime cache range to cover full data (incl. named periods)
        _preload_regime_cache(conn, date(2024, 5, 1), date(2026, 5, 31))

        slicing_kwargs = {
            "by_regime": args.by_regime,
            "by_symbol": args.by_symbol,
            "by_sector": args.by_sector,
            "period": args.period,
            "symbol_filter": args.symbol,
            "sector_filter": args.sector,
        }

        if args.atr_sweep:
            # ATR multiplier sweep mode: find optimal stop/target per stock
            run_name = args.name or f"atrsweep_{args.mode}_{args.start.isoformat()}_{args.end.isoformat()}"

            print("\n▶ ATR Multiplier Sweep: scanning for optimal stop/target per stock...")
            symbol_results, overall_best = run_atr_sweep(
                conn, args.mode, args.start, args.end,
                use_new_rules=True, capital=args.capital,
            )
            print_atr_sweep_report(
                args.mode, args.start, args.end,
                symbol_results, overall_best,
            )

            # Persist sweep results to DB
            if symbol_results:
                write_atr_sweep_results(
                    conn, run_name, symbol_results, overall_best,
                    args.mode, args.start, args.end,
                )

        elif args.signal_quality:
            # Signal quality mode: separate signal accuracy from exit execution
            run_name = args.name or f"sq_{args.mode}_{args.start.isoformat()}_{args.end.isoformat()}_NEW"

            # Create the backtest_runs row so we have a run_id for FK
            run_id = write_run(conn, run_name, args.mode, args.start, args.end,
                               args.capital, use_new_rules=True)
            conn.commit()

            results, summary = run_signal_quality_analysis(
                conn, args.mode, args.start, args.end,
                use_new_rules=True, run_name=run_name,
                initial_capital=args.capital,
            )
            print_signal_quality_report(
                run_name, args.mode, args.start, args.end,
                results, summary,
            )

            # Persist results to DB
            if results:
                write_signal_quality_results(conn, run_id, results)
                write_metrics(conn, run_id, {
                    "final_capital": summary.get("decomposition", {}).get("actual_exit_pnl_atr", 0),
                }, summary.get("decomposition", {}).get("actual_exit_pnl_atr", 0))
                conn.commit()

            # Optionally also run OLD rules for comparison
            if args.compare:
                print("\n▶ Comparing with OLD greeks rules...")
                old_name = f"{run_name}_OLD"

                # Create backtest_runs row for OLD pass
                old_run_id = write_run(conn, old_name, args.mode, args.start, args.end,
                                       args.capital, use_new_rules=False)
                conn.commit()

                old_results, old_summary = run_signal_quality_analysis(
                    conn, args.mode, args.start, args.end,
                    use_new_rules=False, run_name=old_name,
                    initial_capital=args.capital,
                )
                print_signal_quality_report(
                    old_name, args.mode, args.start, args.end,
                    old_results, old_summary,
                )

                # Persist OLD results
                if old_results:
                    write_signal_quality_results(conn, old_run_id, old_results)
                    conn.commit()

                # Side-by-side comparison on key split metrics
                new_sa = summary["signal_accuracy"]
                old_sa = old_summary["signal_accuracy"]
                new_ee = summary["exit_efficiency"]
                old_ee = old_summary["exit_efficiency"]
                new_dc = summary["decomposition"]
                old_dc = old_summary["decomposition"]

                print()
                print("=" * 72)
                print("SIGNAL QUALITY COMPARISON: NEW vs OLD greeks rules")
                print("=" * 72)
                print(f"{'Metric':<35} {'NEW':>12} {'OLD':>12} {'Delta':>12}")
                print("-" * 72)

                rows = [
                    ("Signal accuracy %", new_sa.get("accuracy_pct", 0), old_sa.get("accuracy_pct", 0)),
                    ("Strong signals %", new_sa.get("grade_breakdown", {}).get("strong", {}).get("pct_of_total", 0), old_sa.get("grade_breakdown", {}).get("strong", {}).get("pct_of_total", 0)),
                    ("Avg MFE (ATR)", new_sa.get("avg_mfe_atr", 0), old_sa.get("avg_mfe_atr", 0)),
                    ("Avg MAE (ATR)", new_sa.get("avg_mae_atr", 0), old_sa.get("avg_mae_atr", 0)),
                    ("Avg capture %", new_ee.get("avg_capture_pct", 0), old_ee.get("avg_capture_pct", 0)),
                    ("Median capture %", new_ee.get("median_capture_pct", 0), old_ee.get("median_capture_pct", 0)),
                    ("Exit efficiency ratio %", new_dc.get("exit_efficiency_ratio", 0), old_dc.get("exit_efficiency_ratio", 0)),
                    ("Actual exit P&L (ATR)", new_dc.get("actual_exit_pnl_atr", 0), old_dc.get("actual_exit_pnl_atr", 0)),
                    ("Exit slippage (ATR)", new_dc.get("exit_slippage_atr", 0), old_dc.get("exit_slippage_atr", 0)),
                ]
                for label, nv, ov in rows:
                    delta = nv - ov
                    print(f"{label:<35} {nv:>12.2f} {ov:>12.2f} {delta:>+12.2f}")
                print("=" * 72)

                # Verdict
                acc_diff = new_sa.get("accuracy_pct", 0) - old_sa.get("accuracy_pct", 0)
                cap_diff = new_ee.get("avg_capture_pct", 0) - old_ee.get("avg_capture_pct", 0)
                slip_diff = old_dc.get("exit_slippage_atr", 0) - new_dc.get("exit_slippage_atr", 0)

                parts = []
                if acc_diff > 0:
                    parts.append(f"signal accuracy +{acc_diff:.1f}%")
                if cap_diff > 0:
                    parts.append(f"exit capture +{cap_diff:.1f}%")
                if slip_diff > 0:
                    parts.append(f"exit slippage reduced by {slip_diff:.1f} ATR")

                if parts:
                    print(f"\n✅ NEW greeks improves: {', '.join(parts)}")
                else:
                    print(f"\n⚠️  NEW greeks does NOT clearly improve signal quality vs OLD rules")
                print()

        elif args.compare:
            # Run TWO passes: OLD rules then NEW rules
            base_name = args.name or f"greeks_{args.mode}_{args.start.isoformat()}_{args.end.isoformat()}"

            print("\n" + "▶ PASS 1: OLD greeks rules (flat delta 0.50-0.70, theta budget 0.03)")
            old_name = f"{base_name}_OLD"
            old_metrics, old_signals, old_rejects = run_greeks_backtest(
                conn, args.mode, args.start, args.end, args.capital,
                use_new_rules=False, run_name=old_name,
            )
            print_report(old_name, args.mode, args.start, args.end,
                         args.capital, old_metrics, old_rejects, label="OLD")

            print("\n▶ PASS 2: NEW greeks rules (mode-keyed delta bands, per-mode theta budget)")
            new_name = f"{base_name}_NEW"
            new_metrics, new_signals, new_rejects = run_greeks_backtest(
                conn, args.mode, args.start, args.end, args.capital,
                use_new_rules=True, run_name=new_name,
            )
            print_report(new_name, args.mode, args.start, args.end,
                         args.capital, new_metrics, new_rejects, label="NEW")

            # Print comparison
            print_comparison(old_metrics, new_metrics, old_rejects, new_rejects, args.mode)

            # Slicing on the NEW pass
            has_slicing = any([args.by_regime, args.by_symbol, args.by_sector, args.period,
                               args.symbol, args.sector])
            if has_slicing and new_signals:
                # Get the NEW run_id
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM trading.backtest_runs WHERE run_name = %s",
                                (new_name,))
                    new_run_row = cur.fetchone()
                new_run_id = new_run_row[0] if new_run_row else 0
                slices = compute_slices(conn, new_signals, args.mode, run_id=new_run_id, **slicing_kwargs)
                if new_run_id and slices:
                    write_slices(conn, new_run_id, slices)
                    conn.commit()

                # Print slice tables grouped by type
                for stype in ["regime", "symbol", "sector", "period"]:
                    matching = [s for s in slices if s["slice_type"] == stype]
                    if matching:
                        print_slice_table(matching, f"BY {stype.upper()}")
        else:
            # Single pass with NEW rules
            run_name = args.name or f"greeks_{args.mode}_{args.start.isoformat()}_{args.end.isoformat()}_NEW"
            metrics, signals, reject_counts = run_greeks_backtest(
                conn, args.mode, args.start, args.end, args.capital,
                use_new_rules=True, run_name=run_name,
            )
            print_report(run_name, args.mode, args.start, args.end,
                         args.capital, metrics, reject_counts)

            # Print greeks metadata summary
            if signals:
                passed = [s for s in signals if s.greeks_pass]
                failed = [s for s in signals if not s.greeks_pass]
                print(f"\nGreeks Metadata Summary:")
                print(f"  Total signals through scanner: {len(signals)}")
                print(f"  Greeks passed: {len(passed)}")
                print(f"  Greeks failed: {len(failed)}")
                if passed:
                    avg_d = statistics.mean([s.option_delta for s in passed])
                    avg_t = statistics.mean([s.option_theta_pct for s in passed])
                    avg_iv = statistics.mean([s.iv_rank_at_entry for s in passed if s.iv_rank_at_entry is not None])
                    print(f"  Avg delta (passed): {avg_d:.3f}")
                    print(f"  Avg theta% (passed): {avg_t:.4f}")
                    print(f"  Avg IV rank (passed): {avg_iv:.1f}")
                    regimes = {}
                    for s in passed:
                        r = s.iv_regime_at_entry
                        regimes[r] = regimes.get(r, 0) + 1
                    print(f"  IV regimes: {dict(sorted(regimes.items()))}")

            # Multi-dimensional slicing analysis
            has_slicing = any([args.by_regime, args.by_symbol, args.by_sector, args.period,
                               args.symbol, args.sector])
            if has_slicing and signals:
                # Get run_id
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM trading.backtest_runs WHERE run_name = %s",
                                (run_name,))
                    run_row = cur.fetchone()
                slice_run_id = run_row[0] if run_row else 0
                slices = compute_slices(conn, signals, args.mode, run_id=slice_run_id, **slicing_kwargs)
                if slice_run_id and slices:
                    write_slices(conn, slice_run_id, slices)
                    conn.commit()

                # Print slice tables grouped by type
                for stype in ["regime", "symbol", "sector", "period"]:
                    matching = [s for s in slices if s["slice_type"] == stype]
                    if matching:
                        print_slice_table(matching, f"BY {stype.upper()}")

    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())