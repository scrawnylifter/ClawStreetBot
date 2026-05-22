#!/usr/bin/env python3
"""V2 Greeks Backtest Engine — walk-forward historical replay with greeks strategy validation.

Walks forward through historical daily data, applies the FULL v2 scanner gates
(8-gate setup scanner from scan_setups.py) plus the NEW greeks strategy (mode-keyed
delta bands, theta budget, IV regime), and records every signal + trade outcome with
greeks metadata.

Two-pass mode (--compare):
  Pass 1: OLD greeks rules — flat delta band 0.50-0.70 for ALL modes, theta budget 0.03.
  Pass 2: NEW greeks rules — mode-keyed delta bands (MODE_DELTA_BANDS), per-mode
           theta budgets (THETA_BUDGETS), IV regime gating.
  Compare: does the new greeks strategy improve win rate / R:R / drawdown?

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

DB tables (must exist — see db/init/032_backtest_greeks.sql):
  - trading.backtest_runs           — run metadata
  - trading.backtest_trades         — individual trades with greeks columns
  - trading.backtest_greeks_summary — per-run per-mode aggregate stats

Usage:
  python archive/backtests/backtest_greeks.py --start 2025-09-08 --end 2026-05-21
  python archive/backtests/backtest_greeks.py --start 2025-09-08 --end 2026-05-21 --mode swing
  python archive/backtests/backtest_greeks.py --start 2025-09-08 --end 2026-05-21 --compare
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
IV_RANK_MAX = 75.0        # gate 4: don't buy in sell_premium regime (iv_rank 0..75)
IV_RV_SPREAD_MAX = 50.0   # gate 5 (as percentage points, matching task spec: iv_rank - rv < 50)
EMA_GAP_MIN_PCT = 0.5     # gate 1: EMA gap > 0.5%
MIN_DTE = 30
MAX_DTE = 120
ATR_STOP_MULT = {"day": 1.5, "swing": 2.0, "long_term": 2.0}
ATR_TP1_MULT = {"day": 4.5, "swing": 6.0, "long_term": 6.0}
ATR_TP2_MULT = {"day": 7.5, "swing": 10.0, "long_term": 10.0}
RR_MIN = 3.0

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
    hold_days: int = 0
    partial_exits: list[dict[str, Any]] = field(default_factory=list)
    pdt_flag: bool = False

    @property
    def r_multiple(self) -> float:
        if self.risk_per_share <= 0:
            return 0.0
        return self.gross_pnl / (self.risk_per_share * self.quantity)


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
                iv.iv_rank_52w, iv.current_iv,
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

    # Gate 4: IV rank — 0 < iv_rank < 75 (don't trade in sell_premium)
    if iv_rank is None:
        fail_reasons.append("IV rank missing")
        return None
    if iv_rank >= IV_RANK_MAX:
        fail_reasons.append(f"IV rank {iv_rank:.1f} >= {IV_RANK_MAX}")
        return None
    if iv_rank <= 0:
        fail_reasons.append(f"IV rank {iv_rank:.1f} <= 0")
        return None

    # Gate 5: IV-RV spread — (iv_rank - rv) < 50
    # NOTE: The spec says iv_rank - rv < 50 pct points. scan_setups.py uses
    # IV - RV20d <= 0.05 (5pp). We use the greeks backtest spec (50pp) since
    # iv_rank is already a 0-100 percentile, not raw IV.
    if rv20 is None:
        fail_reasons.append("RV20 missing")
        return None
    assert iv_rank is not None  # already checked above
    rv20_pct = rv20 * 100.0  # rv is 0-1 decimal, convert to pct
    iv_rv_spread = iv_rank - rv20_pct
    if iv_rv_spread > IV_RV_SPREAD_MAX:
        fail_reasons.append(f"IV-RV spread {iv_rv_spread:.1f} > {IV_RV_SPREAD_MAX}")
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

        # Stop-loss check
        if direction == "bullish":
            if bar.l <= trail_stop:
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
            # TP1 partial
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
        else:  # bearish
            if bar.h >= trail_stop:
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
            # TP1 partial
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

        # Trailing stop on remainder
        if rules.get("trail") and remaining > 0:
            if direction == "bullish":
                high_water = max(high_water, bar.h)
                new_trail = high_water - atr * rules.get("trail_atr_mult", 2.0)
                trail_stop = max(trail_stop, new_trail)
            else:
                # For bearish, track lowest water mark and trail stop UP
                low_water = bar.l  # bar low for trailing
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
                t.gross_pnl, t.gross_pnl, round(t.r_multiple, 4), t.hold_days,
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

            entry_price = signal_bar.c  # Use close of signal day as proxy

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
                greeks_pass_count += 1
                # Simulate the trade
                sim_trade = simulate_trade(
                    signal, greeks, bars, mode, capital
                )
                if sim_trade is not None:
                    executed_trades.append(sim_trade)
                    capital += sim_trade.gross_pnl
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

    pdt_violations = annotate_pdt(executed_trades) if mode == "day" else 0
    metrics = compute_metrics(executed_trades, initial_capital, start, end)

    print(f"  Signals through scanner: {signal_count}")
    print(f"  Greeks passed: {greeks_pass_count}  |  Greeks failed: {greeks_fail_count}")
    print(f"  Executed trades: {len(executed_trades)}")
    for k, v in reject_counts.items():
        if v > 0:
            print(f"    {k}: {v}")

    # Write to DB
    run_id = write_run(conn, run_name, mode, start, end, initial_capital, use_new_rules)
    write_trades(conn, run_id, all_signals)
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
    p.add_argument("--name", help="Run name prefix (default: auto-generated)")
    args = p.parse_args()

    # Validate date range against data availability
    # IV rank data starts 2025-09-09, so enforce that as min start
    min_iv_date = date(2025, 9, 9)
    if args.start < min_iv_date:
        print(f"⚠️  IV rank data starts {min_iv_date}, adjusting start from {args.start} to {min_iv_date}")
        args.start = min_iv_date

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        if args.compare:
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

    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())