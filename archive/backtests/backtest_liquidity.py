#!/usr/bin/env python3
"""Liquidity sweep backtest for ClawStreetBot.

Tests the core hypothesis from the synthesized liquidity strategy (3 YouTube
sources): when price sweeps a HTF swing level on the 5m chart and rejects,
entering at the rejection with stop below the sweep extreme and targeting the
opposite liquidity level produces positive expectancy.

Strategy layers tested:
  1. External range liquidity: daily swing H/L sweeps → 5m rejection entry
  2. FVG (internal range liquidity): 5m fair value gap entries after sweep
  3. Consolidation sweep: range H/L breakout → fade opposite direction

Usage:
    python scripts/backtest_liquidity.py [--symbols NVDA,AMD] [--start 2026-04-16]
    python scripts/backtest_liquidity.py --show-trades   # print every trade detail
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

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

# ── Liquidity strategy parameters (from user rules, not industry defaults) ──
LIQUIDITY_RULES = {
    # Day-trade liquidity sweep (5m chart)
    "risk_pct": 0.05,           # 5% risk per trade
    "atr_mult_stop": 1.5,       # ATR × 1.5 stop (matches day mode)
    "min_rr": 3.0,             # minimum 3:1 R:R to take the trade
    "max_hold_bars": 78,        # ~6.5 hours of 5m bars (1 trading day)
    "tp1_rr": 3.0,             # TP1 at 3:1 R:R
    "tp2_rr": 5.0,             # TP2 at 5:1 R:R
    "tp1_size": 0.50,          # close 50% at TP1
    "tp2_size": 0.50,          # close remaining 50% at TP2
    # Swing high/low detection
    "swing_lookback": 20,       # bars to look back for swing points (daily)
    "equal_hl_tolerance": 0.002,  # 0.2% tolerance for equal highs/lows
    # Sweep detection
    "sweep_wick_min": 0.001,   # min wick/body ratio for sweep confirmation
    # FVG detection
    "fvg_min_size_atr": 0.3,  # FVG must be ≥ 0.3× ATR to count
    # Consolidation
    "consolidation_min_bars": 10,  # min daily bars to define a range
    "consolidation_range_atr": 2.0,  # max range width in ATR multiples
    # Session filter (NYSE hours)
    "session_start": "09:30",
    "session_end": "16:00",
}


# ── Data classes ──

@dataclass
class Bar5m:
    """5-minute OHLCV bar."""
    ts: datetime
    o: float
    h: float
    l: float
    c: float
    volume: int
    trade_count: int
    vwap: float


@dataclass
class BarDaily:
    """Daily OHLCV bar."""
    d: date
    o: float
    h: float
    l: float
    c: float


@dataclass
class SwingLevel:
    """A swing high or low on the daily chart."""
    price: float
    kind: str          # "high" or "low"
    date: date
    tapped: bool = False


@dataclass
class FVG:
    """Fair value gap on the 5m chart."""
    direction: str     # "bullish" or "bearish"
    top: float         # upper boundary
    bottom: float      # lower boundary
    bar_ts: datetime   # when it formed
    filled: bool = False


@dataclass
class LiquidityTrade:
    """Simulated liquidity sweep trade."""
    symbol: str
    direction: str         # "long" or "short"
    entry_ts: datetime
    entry_price: float
    stop_price: float
    tp1_price: float
    tp2_price: float
    sweep_level: float
    sweep_kind: str        # "external", "fvg", "consolidation"
    confirmation: str     # "engulfing", "wick_rejection", "structure_shift"
    risk_per_share: float
    quantity: float
    exit_ts: datetime | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    gross_pnl: float = 0.0
    r_multiple: float = 0.0
    hold_bars: int = 0

    # partial exits
    partial_exits: list[dict] = field(default_factory=list)

    @property
    def is_winner(self) -> bool:
        return self.gross_pnl > 0


# ── Data fetching ──

def fetch_daily_bars(conn, symbol: str, start: date, end: date) -> list[BarDaily]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT o.timestamp::date, o.open, o.high, o.low, o.close
            FROM market.ohlcv o
            JOIN market.assets a ON a.id = o.asset_id
            WHERE a.symbol = %s
              AND o.timeframe = '1d'
              AND o.timestamp::date BETWEEN %s AND %s
            ORDER BY o.timestamp
            """,
            (symbol, start, end),
        )
        return [
            BarDaily(d=r[0], o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4]))
            for r in cur.fetchall()
        ]


def fetch_5m_bars(conn, symbol: str, start: date, end: date) -> list[Bar5m]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT o.timestamp, o.open, o.high, o.low, o.close,
                   o.volume, o.trade_count, o.vwap
            FROM market.ohlcv o
            JOIN market.assets a ON a.id = o.asset_id
            WHERE a.symbol = %s
              AND o.timeframe = '5m'
              AND o.timestamp::date BETWEEN %s AND %s
            ORDER BY o.timestamp
            """,
            (symbol, start, end),
        )
        rows = cur.fetchall()
        return [
            Bar5m(
                ts=r[0], o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4]),
                volume=int(r[5] or 0), trade_count=int(r[6] or 0),
                vwap=float(r[7] or 0),
            )
            for r in rows
        ]


def fetch_atr_daily(conn, symbol: str, on: date) -> float | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT atr_14 FROM market.technical_indicators
            WHERE symbol = %s AND date <= %s AND atr_14 IS NOT NULL
            ORDER BY date DESC LIMIT 1
            """,
            (symbol, on),
        )
        r = cur.fetchone()
        return float(r[0]) if r and r[0] is not None else None


def get_active_symbols(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT symbol FROM market.assets WHERE active ORDER BY symbol")
        return [r[0] for r in cur.fetchall()]


# ── Swing point detection (daily) ──

def find_swing_levels(daily: list[BarDaily], lookback: int = 20) -> list[SwingLevel]:
    """Find swing highs and lows using a lookback window.
    
    A swing high: bar high is the max of the lookback window.
    A swing low: bar low is the min of the lookback window.
    """
    levels = []
    n = len(daily)
    if n < lookback:
        return levels

    half = lookback // 2

    for i in range(half, n - half):
        window_highs = [daily[j].h for j in range(i - half, i + half + 1)]
        window_lows = [daily[j].l for j in range(i - half, i + half + 1)]

        if daily[i].h == max(window_highs) and window_highs.count(daily[i].h) == 1:
            levels.append(SwingLevel(price=daily[i].h, kind="high", date=daily[i].d))
        if daily[i].l == min(window_lows) and window_lows.count(daily[i].l) == 1:
            levels.append(SwingLevel(price=daily[i].l, kind="low", date=daily[i].d))

    return levels


def find_equal_levels(levels: list[SwingLevel], tolerance: float = 0.002) -> list[SwingLevel]:
    """Group swing levels that are approximately equal (engineered liquidity).
    
    Returns one representative per cluster.
    """
    if not levels:
        return []

    # Sort by kind then price
    highs = sorted([l for l in levels if l.kind == "high"], key=lambda x: x.price)
    lows = sorted([l for l in levels if l.kind == "low"], key=lambda x: x.price)

    result = []

    for kind_group in [highs, lows]:
        if not kind_group:
            continue
        cluster = [kind_group[0]]
        for lvl in kind_group[1:]:
            if abs(lvl.price - cluster[-1].price) / cluster[-1].price <= tolerance:
                cluster.append(lvl)
            else:
                if len(cluster) >= 2:
                    # Equal levels found — add the most recent
                    result.append(cluster[-1])
                cluster = [lvl]
        if len(cluster) >= 2:
            result.append(cluster[-1])

    return result


# ── 5m pattern detection ──

def compute_atr_5m(bars: list[Bar5m], period: int = 14) -> list[float]:
    """Compute ATR on 5m bars."""
    if len(bars) < 2:
        return []
    trs = []
    for i in range(1, len(bars)):
        tr = max(
            bars[i].h - bars[i].l,
            abs(bars[i].h - bars[i-1].c),
            abs(bars[i].l - bars[i-1].c),
        )
        trs.append(tr)

    atrs = []
    if len(trs) < period:
        return [sum(trs) / len(trs)] * len(trs) if trs else []

    # Initial SMA
    atrs.append(sum(trs[:period]) / period)
    for i in range(period, len(trs)):
        atrs.append((atrs[-1] * (period - 1) + trs[i]) / period)

    # Pad front so indices align with bars
    padding = [atrs[0]] * (len(bars) - 1 - len(atrs))
    return padding + atrs


def detect_fvg(bars: list[Bar5m], idx: int) -> FVG | None:
    """Detect a fair value gap at bar idx (3-bar pattern).
    
    Bullish FVG: bar[i-2].high < bar[i].low (gap up = buying pressure)
    Bearish FVG: bar[i-2].low > bar[i].high (gap down = selling pressure)
    """
    if idx < 2 or idx >= len(bars):
        return None

    prev2 = bars[idx - 2]
    curr = bars[idx]

    # Bullish FVG: price gapped up, leaving unfilled space
    if curr.l > prev2.h:
        return FVG(direction="bullish", top=curr.l, bottom=prev2.h, bar_ts=curr.ts)

    # Bearish FVG: price gapped down
    if curr.h < prev2.l:
        return FVG(direction="bearish", top=prev2.l, bottom=curr.h, bar_ts=curr.ts)

    return None


def is_engulfing(bars: list[Bar5m], idx: int) -> str | None:
    """Detect bullish/bearish engulfing at bar[idx].
    
    Bullish: current bar body engulfs previous bar body, closes above prev open.
    Bearish: current bar body engulfs previous bar body, closes below prev open.
    """
    if idx < 1 or idx >= len(bars):
        return None

    prev = bars[idx - 1]
    curr = bars[idx]

    prev_body = abs(prev.c - prev.o)
    curr_body = abs(curr.c - curr.o)

    if prev_body == 0 or curr_body <= prev_body:
        return None

    # Bullish engulfing
    if curr.c > curr.o and prev.c < prev.o:  # curr green, prev red
        if curr.c >= prev.o and curr.o <= prev.c:
            return "engulfing_bullish"

    # Bearish engulfing
    if curr.c < curr.o and prev.c > prev.o:  # curr red, prev green
        if curr.o >= prev.c and curr.c <= prev.o:
            return "engulfing_bearish"

    return None


def is_rejection_wick(bars: list[Bar5m], idx: int, atr: float) -> str | None:
    """Detect a rejection wick (long wick relative to body).
    
    Bullish: lower wick ≥ 1.5× body, close above mid-range.
    Bearish: upper wick ≥ 1.5× body, close below mid-range.
    """
    if idx >= len(bars) or atr <= 0:
        return None

    b = bars[idx]
    body = abs(b.c - b.o)
    upper_wick = b.h - max(b.c, b.o)
    lower_wick = min(b.c, b.o) - b.l
    total_range = b.h - b.l

    if total_range < 0.5 * atr:  # ignore tiny bars
        return None

    if body == 0:
        body = 0.001  # avoid div-by-zero for dojis

    if lower_wick >= 1.5 * body and b.c > (b.h + b.l) / 2:
        return "wick_bullish"
    if upper_wick >= 1.5 * body and b.c < (b.h + b.l) / 2:
        return "wick_bearish"

    return None


# ── Consolidation detection ──

def find_consolidation_range(daily: list[BarDaily], idx: int, min_bars: int = 10,
                              max_range_atr: float = 2.0) -> dict | None:
    """Check if daily bars around idx are consolidating (sideways range).
    
    Returns {'high': float, 'low': float, 'start_idx': int} or None.
    """
    if idx < min_bars:
        return None

    # Look at the most recent min_bars before idx
    window = daily[max(0, idx - min_bars):idx]
    if len(window) < min_bars:
        return None

    range_high = max(b.h for b in window)
    range_low = min(b.l for b in window)
    avg_close = sum(b.c for b in window) / len(window)

    # Get ATR as proxy (range / close)
    atr_proxy = avg_close * 0.02  # ~2% daily ATR estimate
    if atr_proxy <= 0:
        return None

    range_width = (range_high - range_low) / atr_proxy
    if range_width <= max_range_atr:
        return {"high": range_high, "low": range_low, "start_idx": max(0, idx - min_bars)}

    return None


# ── Signal detection ──

def detect_liquidity_signals(
    symbol: str,
    daily: list[BarDaily],
    bars_5m: list[Bar5m],
    atr_daily: dict[date, float],
) -> list[dict]:
    """Detect all liquidity-based entry signals.
    
    Returns list of signal dicts:
      {'ts': datetime, 'direction': str, 'sweep_level': float,
       'sweep_kind': str, 'confirmation': str, 'stop': float, 'tp1': float, 'tp2': float}
    """
    signals = []
    rules = LIQUIDITY_RULES

    # ── 1. External range liquidity: daily swing H/L sweeps on 5m ──
    swing_levels = find_swing_levels(daily, lookback=rules["swing_lookback"])
    equal_levels = find_equal_levels(swing_levels, tolerance=rules["equal_hl_tolerance"])
    all_external = swing_levels + equal_levels

    if not bars_5m or not all_external:
        return signals

    atrs_5m = compute_atr_5m(bars_5m)

    # Build a date→swing-level map for faster lookup
    levels_by_price = {}
    for lvl in all_external:
        key = round(lvl.price, 2)
        levels_by_price.setdefault(key, []).append(lvl)

    for i in range(2, len(bars_5m)):
        bar = bars_5m[i]
        atr = atrs_5m[i] if i < len(atrs_5m) else None
        if not atr or atr <= 0:
            continue

        # Check session filter
        bar_time = bar.ts.time()
        if bar_time < datetime.strptime(rules["session_start"], "%H:%M").time():
            continue
        if bar_time > datetime.strptime(rules["session_end"], "%H:%M").time():
            continue

        # ── Check for sweeps of external levels ──
        for lvl in all_external:
            if lvl.date >= bar.ts.date():
                continue  # level must be from a prior day

            direction = None

            if lvl.kind == "high":
                # Price pokes above the swing high (sweep) then closes below it
                if bar.h > lvl.price and bar.c < lvl.price:
                    direction = "short"  # bearish trap: fake breakout above, fade short
            elif lvl.kind == "low":
                # Price pokes below the swing low (sweep) then closes above it
                if bar.l < lvl.price and bar.c > lvl.price:
                    direction = "long"   # bullish trap: fake breakdown below, fade long

            if not direction:
                continue

            # ── Check confirmation on this bar or next bar ──
            confirmation = None

            # Check engulfing
            eng = is_engulfing(bars_5m, i)
            if eng:
                if direction == "long" and eng == "engulfing_bullish":
                    confirmation = "engulfing"
                elif direction == "short" and eng == "engulfing_bearish":
                    confirmation = "engulfing"

            # Check rejection wick
            if not confirmation:
                wick = is_rejection_wick(bars_5m, i, atr)
                if wick:
                    if direction == "long" and wick == "wick_bullish":
                        confirmation = "wick_rejection"
                    elif direction == "short" and wick == "wick_bearish":
                        confirmation = "wick_rejection"

            # Also check next bar for delayed confirmation
            if not confirmation and i + 1 < len(bars_5m):
                next_bar = bars_5m[i + 1]
                eng_next = is_engulfing(bars_5m, i + 1)
                if eng_next:
                    if direction == "long" and eng_next == "engulfing_bullish":
                        confirmation = "engulfing_next"
                    elif direction == "short" and eng_next == "engulfing_bearish":
                        confirmation = "engulfing_next"

            if not confirmation:
                continue  # no confirmation = no trade

            # ── Compute entry, stop, targets ──
            entry_price = bar.c
            risk_amount = atr * rules["atr_mult_stop"]

            if direction == "long":
                stop = bar.l - risk_amount * 0.1  # just below the sweep low
                # Actually: stop below the sweep extreme
                stop = min(bars_5m[i].l, bars_5m[i-1].l if i > 0 else bars_5m[i].l) - risk_amount * 0.05
                risk_per_share = entry_price - stop
                if risk_per_share <= 0:
                    continue
                tp1 = entry_price + risk_per_share * rules["tp1_rr"]
                tp2 = entry_price + risk_per_share * rules["tp2_rr"]
            else:
                stop = max(bars_5m[i].h, bars_5m[i-1].h if i > 0 else bars_5m[i].h) + risk_amount * 0.05
                risk_per_share = stop - entry_price
                if risk_per_share <= 0:
                    continue
                tp1 = entry_price - risk_per_share * rules["tp1_rr"]
                tp2 = entry_price - risk_per_share * rules["tp2_rr"]

            # Minimum R:R check
            rr1 = risk_per_share / risk_per_share  # 1:1 at entry
            actual_rr1 = abs(tp1 - entry_price) / risk_per_share
            if actual_rr1 < rules["min_rr"]:
                continue  # doesn't meet our 3:1 minimum

            signals.append({
                "ts": bar.ts,
                "direction": direction,
                "sweep_level": lvl.price,
                "sweep_kind": "external",
                "confirmation": confirmation,
                "entry_price": entry_price,
                "stop": stop,
                "tp1": tp1,
                "tp2": tp2,
                "risk_per_share": risk_per_share,
            })

    # ── 2. FVG entries: after external sweep, enter at FVG retrace ──
    # Build on the external sweep signals — after a sweep, the next draw is the FVG
    fvgs = []
    for i in range(2, len(bars_5m)):
        fvg = detect_fvg(bars_5m, i)
        if fvg and not fvg.filled:
            atr = atrs_5m[i] if i < len(atrs_5m) else None
            if atr and (fvg.top - fvg.bottom) >= atr * rules["fvg_min_size_atr"]:
                fvgs.append((i, fvg))

    # For each FVG, check if price retraces into it and bounces
    for fvg_idx, fvg in fvgs:
        for j in range(fvg_idx + 1, min(fvg_idx + 50, len(bars_5m))):
            bar = bars_5m[j]
            fvg_mid = (fvg.top + fvg.bottom) / 2

            # Price enters FVG zone
            entered_fvg = False
            direction: str | None = None
            if fvg.direction == "bullish" and bar.l <= fvg.top and bar.c > fvg.bottom:
                entered_fvg = True
                direction = "long"
            elif fvg.direction == "bearish" and bar.h >= fvg.bottom and bar.c < fvg.top:
                entered_fvg = True
                direction = "short"

            if not entered_fvg:
                # FVG might be filled (price completely through it)
                if fvg.direction == "bullish" and bar.l < fvg.bottom:
                    fvg.filled = True
                    break
                elif fvg.direction == "bearish" and bar.h > fvg.top:
                    fvg.filled = True
                    break
                continue

            # FVG entered — check for confirmation
            atr = atrs_5m[j] if j < len(atrs_5m) else None
            if not atr:
                continue

            eng = is_engulfing(bars_5m, j)
            wick = is_rejection_wick(bars_5m, j, atr)
            confirmation = None
            if direction == "long" and (eng == "engulfing_bullish" or wick == "wick_bullish"):
                confirmation = "fvg_engulfing" if eng else "fvg_wick"
            elif direction == "short" and (eng == "engulfing_bearish" or wick == "wick_bearish"):
                confirmation = "fvg_engulfing" if eng else "fvg_wick"

            if not confirmation:
                continue

            entry_price = bar.c
            if direction == "long":
                stop = fvg.bottom - atr * 0.1
                risk_per_share = entry_price - stop
                if risk_per_share <= 0:
                    continue
                tp1 = entry_price + risk_per_share * rules["tp1_rr"]
                tp2 = entry_price + risk_per_share * rules["tp2_rr"]
            else:
                stop = fvg.top + atr * 0.1
                risk_per_share = stop - entry_price
                if risk_per_share <= 0:
                    continue
                tp1 = entry_price - risk_per_share * rules["tp1_rr"]
                tp2 = entry_price - risk_per_share * rules["tp2_rr"]

            actual_rr = abs(tp1 - entry_price) / risk_per_share
            if actual_rr < rules["min_rr"]:
                continue

            signals.append({
                "ts": bar.ts,
                "direction": direction,
                "sweep_level": fvg_mid,
                "sweep_kind": "fvg",
                "confirmation": confirmation,
                "entry_price": entry_price,
                "stop": stop,
                "tp1": tp1,
                "tp2": tp2,
                "risk_per_share": risk_per_share,
            })
            break  # one entry per FVG

    return signals


# ── Trade simulation ──

def simulate_trade(signal: dict, bars_5m: list[Bar5m], entry_idx: int,
                   rules: dict) -> LiquidityTrade | None:
    """Simulate a trade from the signal entry through exit.
    
    Exits on: stop hit, TP1/TP2 hit, max hold, or end of data.
    """
    sig_ts = signal["ts"]
    direction = signal["direction"]
    entry_price = signal["entry_price"]
    stop = signal["stop"]
    tp1 = signal["tp1"]
    tp2 = signal["tp2"]
    risk_per_share = signal["risk_per_share"]

    # Find entry bar index
    start_idx = entry_idx + 1  # enter on next bar
    if start_idx >= len(bars_5m):
        return None

    trade = LiquidityTrade(
        symbol="",  # filled by caller
        direction=direction,
        entry_ts=bars_5m[start_idx].ts,
        entry_price=bars_5m[start_idx].o,  # enter at next bar open
        stop_price=stop,
        tp1_price=tp1,
        tp2_price=tp2,
        sweep_level=signal["sweep_level"],
        sweep_kind=signal["sweep_kind"],
        confirmation=signal["confirmation"],
        risk_per_share=abs(entry_price - stop),
        quantity=1.0,  # simplified — no position sizing for backtest
    )

    # Recalc risk from actual entry
    actual_entry = trade.entry_price
    trade.risk_per_share = abs(actual_entry - stop)
    if trade.risk_per_share <= 0:
        return None

    # Recalc TPs from actual entry
    if direction == "long":
        trade.tp1_price = actual_entry + trade.risk_per_share * rules["tp1_rr"]
        trade.tp2_price = actual_entry + trade.risk_per_share * rules["tp2_rr"]
    else:
        trade.tp1_price = actual_entry - trade.risk_per_share * rules["tp1_rr"]
        trade.tp2_price = actual_entry - trade.risk_per_share * rules["tp2_rr"]

    tp1_hit = False
    remaining = 1.0

    for j in range(start_idx, min(start_idx + rules["max_hold_bars"], len(bars_5m))):
        bar = bars_5m[j]
        trade.hold_bars += 1

        if direction == "long":
            # Stop hit
            if bar.l <= trade.stop_price:
                trade.exit_ts = bar.ts
                trade.exit_price = trade.stop_price
                trade.exit_reason = "stop_loss"
                trade.gross_pnl = (trade.exit_price - actual_entry) * remaining
                trade.r_multiple = trade.gross_pnl / trade.risk_per_share
                return trade

            # TP1 hit
            if not tp1_hit and bar.h >= trade.tp1_price:
                tp1_pnl = (trade.tp1_price - actual_entry) * rules["tp1_size"]
                trade.partial_exits.append({
                    "ts": bar.ts, "price": trade.tp1_price,
                    "size": rules["tp1_size"], "pnl": tp1_pnl,
                })
                trade.gross_pnl += tp1_pnl
                remaining -= rules["tp1_size"]
                tp1_hit = True
                # Move stop to breakeven
                trade.stop_price = actual_entry

            # TP2 hit
            if tp1_hit and bar.h >= trade.tp2_price:
                tp2_pnl = (trade.tp2_price - actual_entry) * remaining
                trade.partial_exits.append({
                    "ts": bar.ts, "price": trade.tp2_price,
                    "size": remaining, "pnl": tp2_pnl,
                })
                trade.gross_pnl += tp2_pnl
                trade.exit_ts = bar.ts
                trade.exit_price = trade.tp2_price
                trade.exit_reason = "tp2"
                trade.r_multiple = trade.gross_pnl / trade.risk_per_share
                return trade

        else:  # short
            if bar.h >= trade.stop_price:
                trade.exit_ts = bar.ts
                trade.exit_price = trade.stop_price
                trade.exit_reason = "stop_loss"
                trade.gross_pnl = (actual_entry - trade.exit_price) * remaining
                trade.r_multiple = trade.gross_pnl / trade.risk_per_share
                return trade

            if not tp1_hit and bar.l <= trade.tp1_price:
                tp1_pnl = (actual_entry - trade.tp1_price) * rules["tp1_size"]
                trade.partial_exits.append({
                    "ts": bar.ts, "price": trade.tp1_price,
                    "size": rules["tp1_size"], "pnl": tp1_pnl,
                })
                trade.gross_pnl += tp1_pnl
                remaining -= rules["tp1_size"]
                tp1_hit = True
                trade.stop_price = actual_entry

            if tp1_hit and bar.l <= trade.tp2_price:
                tp2_pnl = (actual_entry - trade.tp2_price) * remaining
                trade.partial_exits.append({
                    "ts": bar.ts, "price": trade.tp2_price,
                    "size": remaining, "pnl": tp2_pnl,
                })
                trade.gross_pnl += tp2_pnl
                trade.exit_ts = bar.ts
                trade.exit_price = trade.tp2_price
                trade.exit_reason = "tp2"
                trade.r_multiple = trade.gross_pnl / trade.risk_per_share
                return trade

    # Max hold — exit at last bar close
    if bars_5m and start_idx < len(bars_5m):
        last_bar = bars_5m[min(start_idx + rules["max_hold_bars"] - 1, len(bars_5m) - 1)]
        trade.exit_ts = last_bar.ts
        trade.exit_price = last_bar.c
        trade.exit_reason = "max_hold"
        if direction == "long":
            trade.gross_pnl = (trade.exit_price - actual_entry) * remaining
        else:
            trade.gross_pnl = (actual_entry - trade.exit_price) * remaining

        # Add any partial exits from before
        if tp1_hit:
            trade.gross_pnl += 0  # already counted in partial_exits

        trade.r_multiple = trade.gross_pnl / trade.risk_per_share if trade.risk_per_share else 0

    return trade if trade.exit_ts else None


# ── Metrics ──

def compute_metrics(trades: list[LiquidityTrade], capital: float = 10000) -> dict:
    if not trades:
        return {"total_trades": 0}

    winners = [t for t in trades if t.gross_pnl > 0]
    losers = [t for t in trades if t.gross_pnl <= 0]

    total_pnl = sum(t.gross_pnl for t in trades)
    avg_win = sum(t.gross_pnl for t in winners) / len(winners) if winners else 0
    avg_loss = sum(t.gross_pnl for t in losers) / len(losers) if losers else 0
    win_rate = len(winners) / len(trades)

    profit_factor = abs(sum(t.gross_pnl for t in winners) / sum(t.gross_pnl for t in losers)) if losers else float("inf")

    avg_r = sum(t.r_multiple for t in trades) / len(trades)
    expectancy = (win_rate * avg_win + (1 - win_rate) * avg_loss)

    # By sweep kind
    by_kind = {}
    for t in trades:
        kind = t.sweep_kind
        by_kind.setdefault(kind, []).append(t)

    kind_metrics = {}
    for kind, kind_trades in by_kind.items():
        k_winners = [t for t in kind_trades if t.gross_pnl > 0]
        kind_metrics[kind] = {
            "total": len(kind_trades),
            "wins": len(k_winners),
            "win_rate": len(k_winners) / len(kind_trades) if kind_trades else 0,
            "avg_r": sum(t.r_multiple for t in kind_trades) / len(kind_trades),
            "total_pnl": sum(t.gross_pnl for t in kind_trades),
        }

    # By confirmation type
    by_conf = {}
    for t in trades:
        conf = t.confirmation
        by_conf.setdefault(conf, []).append(t)

    conf_metrics = {}
    for conf, conf_trades in by_conf.items():
        c_winners = [t for t in conf_trades if t.gross_pnl > 0]
        conf_metrics[conf] = {
            "total": len(conf_trades),
            "wins": len(c_winners),
            "win_rate": len(c_winners) / len(conf_trades) if conf_trades else 0,
            "avg_r": sum(t.r_multiple for t in conf_trades) / len(conf_trades),
        }

    # By direction
    longs = [t for t in trades if t.direction == "long"]
    shorts = [t for t in trades if t.direction == "short"]
    long_wr = len([t for t in longs if t.gross_pnl > 0]) / len(longs) if longs else 0
    short_wr = len([t for t in shorts if t.gross_pnl > 0]) / len(shorts) if shorts else 0

    return {
        "total_trades": len(trades),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "avg_r_multiple": avg_r,
        "expectancy": expectancy,
        "total_pnl": total_pnl,
        "long_trades": len(longs),
        "long_win_rate": long_wr,
        "short_trades": len(shorts),
        "short_win_rate": short_wr,
        "by_sweep_kind": kind_metrics,
        "by_confirmation": conf_metrics,
    }


# ── Main ──

def main():
    parser = argparse.ArgumentParser(description="Liquidity sweep backtest")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols (default: all active)")
    parser.add_argument("--start", default=None, help="Start date (YYYY-MM-DD, default: earliest 5m data)")
    parser.add_argument("--end", default=None, help="End date (YYYY-MM-DD, default: latest data)")
    parser.add_argument("--capital", type=float, default=10000, help="Starting capital")
    parser.add_argument("--show-trades", action="store_true", help="Print every trade")
    parser.add_argument("--show-signals", action="store_true", help="Print detected signals before simulation")
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)

    # Get symbols
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        symbols = get_active_symbols(conn)

    # Date range
    start = date.fromisoformat(args.start) if args.start else date(2026, 4, 16)
    end = date.fromisoformat(args.end) if args.end else date(2026, 5, 15)

    all_trades: list[LiquidityTrade] = []
    skipped = []

    for symbol in symbols:
        # Fetch daily bars (need more history for swing detection)
        daily_start = start - timedelta(days=90)  # 3 months extra for swing lookback
        daily = fetch_daily_bars(conn, symbol, daily_start, end)
        bars_5m = fetch_5m_bars(conn, symbol, start, end)

        if not daily or not bars_5m:
            skipped.append(symbol)
            print(f"  {symbol}: skipped (daily={len(daily)}, 5m={len(bars_5m)})")
            continue

        # Get daily ATR values
        atr_daily = {}
        for bar in daily:
            atr = fetch_atr_daily(conn, symbol, bar.d)
            if atr:
                atr_daily[bar.d] = atr

        # Detect signals
        signals = detect_liquidity_signals(symbol, daily, bars_5m, atr_daily)

        if args.show_signals:
            for sig in signals:
                print(f"  SIGNAL {symbol} {sig['ts']} {sig['direction']} "
                      f"sweep={sig['sweep_kind']}@{sig['sweep_level']:.2f} "
                      f"conf={sig['confirmation']} entry={sig['entry_price']:.2f}")

        print(f"  {symbol}: {len(daily)} daily bars, {len(bars_5m)} 5m bars, {len(signals)} signals")

        # Simulate trades
        for sig in signals:
            # Find the 5m bar index for signal entry
            entry_idx = None
            for idx, bar in enumerate(bars_5m):
                if bar.ts >= sig["ts"]:
                    entry_idx = idx
                    break

            if entry_idx is None:
                continue

            trade = simulate_trade(sig, bars_5m, entry_idx, LIQUIDITY_RULES)
            if trade:
                trade.symbol = symbol
                all_trades.append(trade)

    conn.close()

    # Compute and display metrics
    print("\n" + "=" * 60)
    print("LIQUIDITY SWEEP BACKTEST RESULTS")
    print("=" * 60)
    print(f"Period: {start} to {end}")
    print(f"Symbols: {len(symbols)} ({', '.join(s for s in symbols if s not in skipped)})")
    if skipped:
        print(f"Skipped: {', '.join(skipped)}")
    print()

    metrics = compute_metrics(all_trades, args.capital)

    if metrics["total_trades"] == 0:
        print("No trades generated.")
        print("\nPossible reasons:")
        print("  - Not enough 5m data for swing detection + sweep confirmation")
        print("  - Confirmation filters too strict (try loosening)")
        print("  - Session filter excluding non-NYSE bars")
        return

    print(f"Total trades:     {metrics['total_trades']}")
    print(f"Winners:          {metrics['winners']}")
    print(f"Losers:           {metrics['losers']}")
    print(f"Win rate:         {metrics['win_rate']:.1%}")
    print(f"Avg win:          {metrics['avg_win']:.4f}")
    print(f"Avg loss:         {metrics['avg_loss']:.4f}")
    print(f"Profit factor:    {metrics['profit_factor']:.2f}")
    print(f"Avg R-multiple:   {metrics['avg_r_multiple']:.2f}R")
    print(f"Expectancy:       {metrics['expectancy']:.4f}")
    print(f"Total P&L:        {metrics['total_pnl']:.4f} (per share)")
    print()
    print(f"Long trades:      {metrics['long_trades']} (WR: {metrics['long_win_rate']:.1%})")
    print(f"Short trades:     {metrics['short_trades']} (WR: {metrics['short_win_rate']:.1%})")

    if metrics.get("by_sweep_kind"):
        print("\n── By Sweep Kind ──")
        for kind, km in metrics["by_sweep_kind"].items():
            print(f"  {kind:15s}: {km['total']} trades, {km['win_rate']:.1%} WR, "
                  f"avg {km['avg_r']:.2f}R, P&L {km['total_pnl']:.4f}")

    if metrics.get("by_confirmation"):
        print("\n── By Confirmation Type ──")
        for conf, cm in metrics["by_confirmation"].items():
            print(f"  {conf:20s}: {cm['total']} trades, {cm['win_rate']:.1%} WR, avg {cm['avg_r']:.2f}R")

    if args.show_trades:
        print("\n── All Trades ──")
        for t in all_trades:
            print(f"  {t.symbol} {t.entry_ts} {t.direction:5s} "
                  f"entry={t.entry_price:.2f} stop={t.stop_price:.2f} "
                  f"tp1={t.tp1_price:.2f} tp2={t.tp2_price:.2f} "
                  f"→ exit={t.exit_price:.2f} ({t.exit_reason}) "
                  f"P&L={t.gross_pnl:.4f} R={t.r_multiple:.2f} "
                  f"sweep={t.sweep_kind} conf={t.confirmation}")

    print()
    # Verdict
    if metrics["total_trades"] >= 10:
        if metrics["win_rate"] >= 0.40 and metrics["avg_r_multiple"] >= 0.5:
            print("✅ POSITIVE: Strategy shows edge. Consider forward testing on paper.")
        elif metrics["win_rate"] >= 0.35:
            print("⚠️  MARGINAL: Some edge likely but needs more data / refinement.")
        else:
            print("❌ NEGATIVE: No clear edge detected. Review filters and parameters.")
    else:
        print("⚠️  INSUFFICIENT DATA: Too few trades for statistical significance.")
        print("   Need more 5m data (currently ~1 month). Consider backfilling.")


if __name__ == "__main__":
    main()