#!/usr/bin/env python3
"""Liquidity sweep backtest v3 — refinement test.

Tests each refinement independently to measure impact:
  A. Volume filter: sweep bar volume > 1.5x 20-bar average
  B. Stop at sweep extreme: stop below the sweep low (not ATR-based)
  C. Minimum penetration: wick must poke > 0.5 ATR past the level
  D. Close-beyond confirmation: next bar closes past the swing level

Then combines winners for a final composite run.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
import statistics

import psycopg2

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

# ── Base rules ──
BASE_RULES = {
    "min_rr": 3.0,
    "max_hold_bars": 78,       # 1 trading day
    "tp1_rr": 3.0,
    "tp2_rr": 5.0,
    "tp1_size": 0.50,
    "tp2_size": 0.50,
    "swing_lookback": 20,
    "equal_hl_tolerance": 0.002,
    "session_start": "09:30",
    "session_end": "16:00",
    "cooldown_bars": 12,
    # Refinement flags (toggled per test)
    "volume_filter": False,        # A: sweep bar vol > 1.5x avg
    "volume_mult": 1.5,
    "stop_at_extreme": False,       # B: stop at sweep extreme, not ATR
    "min_penetration_atr": 0.0,    # C: 0 = off, 0.5 = half ATR
    "close_beyond": False,          # D: next bar closes past level
}


@dataclass
class Bar5m:
    ts: datetime; o: float; h: float; l: float; c: float
    volume: int; trade_count: int; vwap: float


@dataclass
class BarDaily:
    d: date; o: float; h: float; l: float; c: float


@dataclass
class SwingLevel:
    price: float; kind: str; date: date


@dataclass
class Trade:
    symbol: str; direction: str; entry_ts: datetime
    entry_price: float; stop_price: float
    tp1_price: float; tp2_price: float
    sweep_level: float; risk_per_share: float
    quantity: float = 1.0
    exit_ts: datetime | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    gross_pnl: float = 0.0
    r_multiple: float = 0.0
    hold_bars: int = 0
    partial_exits: list[dict] = field(default_factory=list)


# ── Data ──

def fetch_daily_bars(conn, symbol, start, end):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT o.timestamp::date, o.open, o.high, o.low, o.close
               FROM market.ohlcv o JOIN market.assets a ON a.id = o.asset_id
               WHERE a.symbol=%s AND o.timeframe='1d'
                 AND o.timestamp::date BETWEEN %s AND %s ORDER BY o.timestamp""",
            (symbol, start, end))
        return [BarDaily(d=r[0], o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4])) for r in cur.fetchall()]


def fetch_5m_bars(conn, symbol, start, end):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT o.timestamp, o.open, o.high, o.low, o.close,
                      o.volume, o.trade_count, o.vwap
               FROM market.ohlcv o JOIN market.assets a ON a.id = o.asset_id
               WHERE a.symbol=%s AND o.timeframe='5m'
                 AND o.timestamp::date BETWEEN %s AND %s ORDER BY o.timestamp""",
            (symbol, start, end))
        return [Bar5m(ts=r[0], o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4]),
                     volume=int(r[5] or 0), trade_count=int(r[6] or 0), vwap=float(r[7] or 0)) for r in cur.fetchall()]


def get_active_symbols(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT symbol FROM market.assets WHERE active ORDER BY symbol")
        return [r[0] for r in cur.fetchall()]


# ── Swing detection ──

def find_swing_levels(daily, lookback=20):
    levels = []
    n = len(daily)
    if n < lookback:
        return levels
    half = lookback // 2
    for i in range(half, n - half):
        wh = [daily[j].h for j in range(i - half, i + half + 1)]
        wl = [daily[j].l for j in range(i - half, i + half + 1)]
        if daily[i].h == max(wh) and wh.count(daily[i].h) == 1:
            levels.append(SwingLevel(price=daily[i].h, kind="high", date=daily[i].d))
        if daily[i].l == min(wl) and wl.count(daily[i].l) == 1:
            levels.append(SwingLevel(price=daily[i].l, kind="low", date=daily[i].d))
    return levels


def find_equal_levels(levels, tolerance=0.002):
    result = []
    for kind in ["high", "low"]:
        grp = sorted([l for l in levels if l.kind == kind], key=lambda x: x.price)
        if not grp:
            continue
        cluster = [grp[0]]
        for lvl in grp[1:]:
            if abs(lvl.price - cluster[-1].price) / cluster[-1].price <= tolerance:
                cluster.append(lvl)
            else:
                if len(cluster) >= 2:
                    result.append(cluster[-1])
                cluster = [lvl]
        if len(cluster) >= 2:
            result.append(cluster[-1])
    return result


# ── 5m ATR ──

def compute_atr_5m(bars, period=14):
    if len(bars) < 2:
        return []
    trs = []
    for i in range(1, len(bars)):
        tr = max(bars[i].h - bars[i].l, abs(bars[i].h - bars[i-1].c), abs(bars[i].l - bars[i-1].c))
        trs.append(tr)
    if len(trs) < period:
        avg = sum(trs) / len(trs) if trs else 0.001
        return [avg] * (len(bars) - 1)
    atrs = [sum(trs[:period]) / period]
    for i in range(period, len(trs)):
        atrs.append((atrs[-1] * (period - 1) + trs[i]) / period)
    return [atrs[0]] * (len(bars) - 1 - len(atrs)) + atrs


# ── Volume average ──

def compute_vol_avg(bars, period=20):
    """Rolling average volume."""
    if len(bars) < period:
        avg = sum(b.volume for b in bars) / len(bars) if bars else 1
        return [avg] * len(bars)
    avgs = []
    for i in range(len(bars)):
        if i < period:
            avgs.append(sum(b.volume for b in bars[:i+1]) / (i+1))
        else:
            avgs.append(sum(b.volume for b in bars[i-period+1:i+1]) / period)
    return avgs


# ── Trade simulation ──

def simulate_trade(signal, bars_5m, entry_idx, rules):
    direction = signal["direction"]
    stop = signal["stop"]
    tp1 = signal["tp1"]
    tp2 = signal["tp2"]
    entry_price = signal["entry_price"]

    start_idx = entry_idx + 1
    if start_idx >= len(bars_5m):
        return None

    actual_entry = bars_5m[start_idx].o
    risk = abs(actual_entry - stop)
    if risk <= 0:
        return None

    if direction == "long":
        tp1 = actual_entry + risk * rules["tp1_rr"]
        tp2 = actual_entry + risk * rules["tp2_rr"]
    else:
        tp1 = actual_entry - risk * rules["tp1_rr"]
        tp2 = actual_entry - risk * rules["tp2_rr"]

    trade = Trade(symbol="", direction=direction, entry_ts=bars_5m[start_idx].ts,
                 entry_price=actual_entry, stop_price=stop, tp1_price=tp1, tp2_price=tp2,
                 sweep_level=signal["sweep_level"], risk_per_share=risk)

    tp1_hit = False
    remaining = 1.0

    for j in range(start_idx, min(start_idx + rules["max_hold_bars"], len(bars_5m))):
        bar = bars_5m[j]
        trade.hold_bars += 1

        if direction == "long":
            if bar.l <= trade.stop_price:
                trade.exit_ts, trade.exit_price, trade.exit_reason = bar.ts, trade.stop_price, "stop_loss"
                trade.gross_pnl = (trade.exit_price - actual_entry) * remaining
                trade.r_multiple = trade.gross_pnl / risk
                return trade
            if not tp1_hit and bar.h >= trade.tp1_price:
                pnl = (trade.tp1_price - actual_entry) * rules["tp1_size"]
                trade.partial_exits.append({"ts": bar.ts, "price": trade.tp1_price, "pnl": pnl})
                trade.gross_pnl += pnl
                remaining -= rules["tp1_size"]
                tp1_hit = True
                trade.stop_price = actual_entry
            if tp1_hit and bar.h >= trade.tp2_price:
                pnl = (trade.tp2_price - actual_entry) * remaining
                trade.partial_exits.append({"ts": bar.ts, "price": trade.tp2_price, "pnl": pnl})
                trade.gross_pnl += pnl
                trade.exit_ts, trade.exit_price, trade.exit_reason = bar.ts, trade.tp2_price, "tp2"
                trade.r_multiple = trade.gross_pnl / risk
                return trade
        else:
            if bar.h >= trade.stop_price:
                trade.exit_ts, trade.exit_price, trade.exit_reason = bar.ts, trade.stop_price, "stop_loss"
                trade.gross_pnl = (actual_entry - trade.exit_price) * remaining
                trade.r_multiple = trade.gross_pnl / risk
                return trade
            if not tp1_hit and bar.l <= trade.tp1_price:
                pnl = (actual_entry - trade.tp1_price) * rules["tp1_size"]
                trade.partial_exits.append({"ts": bar.ts, "price": trade.tp1_price, "pnl": pnl})
                trade.gross_pnl += pnl
                remaining -= rules["tp1_size"]
                tp1_hit = True
                trade.stop_price = actual_entry
            if tp1_hit and bar.l <= trade.tp2_price:
                pnl = (actual_entry - trade.tp2_price) * remaining
                trade.partial_exits.append({"ts": bar.ts, "price": trade.tp2_price, "pnl": pnl})
                trade.gross_pnl += pnl
                trade.exit_ts, trade.exit_price, trade.exit_reason = bar.ts, trade.tp2_price, "tp2"
                trade.r_multiple = trade.gross_pnl / risk
                return trade

    last = bars_5m[min(start_idx + rules["max_hold_bars"] - 1, len(bars_5m) - 1)]
    trade.exit_ts, trade.exit_price, trade.exit_reason = last.ts, last.c, "max_hold"
    trade.gross_pnl = ((last.c - actual_entry) if direction == "long" else (actual_entry - last.c)) * remaining
    trade.r_multiple = trade.gross_pnl / risk if risk else 0
    return trade


# ── Signal detection (parameterized by rules) ──

def detect_signals(symbol, daily, bars_5m, rules):
    """External range sweep signals with configurable filters."""
    signals = []
    swing_levels = find_swing_levels(daily, rules["swing_lookback"])
    equal_levels = find_equal_levels(swing_levels, rules["equal_hl_tolerance"])
    all_levels = swing_levels + equal_levels
    atrs = compute_atr_5m(bars_5m)
    vol_avgs = compute_vol_avg(bars_5m)

    session_start = datetime.strptime(rules["session_start"], "%H:%M").time()
    session_end = datetime.strptime(rules["session_end"], "%H:%M").time()
    last_trade_bar = -999

    for i in range(2, len(bars_5m) - 1):
        bar = bars_5m[i]
        atr = atrs[i] if i < len(atrs) else None
        vol_avg = vol_avgs[i] if i < len(vol_avgs) else None
        if not atr or atr <= 0:
            continue

        # Session
        bt = bar.ts.time()
        if bt < session_start or bt > session_end:
            continue

        # Cooldown
        if i - last_trade_bar < rules["cooldown_bars"]:
            continue

        for lvl in all_levels:
            if lvl.date >= bar.ts.date():
                continue

            direction = None
            sweep_extreme = None  # the actual bar high/low at the sweep point

            if lvl.kind == "high" and bar.h > lvl.price and bar.c < lvl.price:
                direction = "short"
                sweep_extreme = bar.h
            elif lvl.kind == "low" and bar.l < lvl.price and bar.c > lvl.price:
                direction = "long"
                sweep_extreme = bar.l

            if not direction:
                continue

            # ── Refinement C: Minimum penetration depth ──
            if rules["min_penetration_atr"] > 0:
                if direction == "short" and (bar.h - lvl.price) < atr * rules["min_penetration_atr"]:
                    continue
                if direction == "long" and (lvl.price - bar.l) < atr * rules["min_penetration_atr"]:
                    continue

            # ── Refinement A: Volume filter ──
            if rules["volume_filter"] and vol_avg and vol_avg > 0:
                if bar.volume < vol_avg * rules["volume_mult"]:
                    continue

            # ── Confirmation: engulfing or wick on sweep bar ──
            confirmation = False
            prev, curr = bars_5m[i-1], bars_5m[i]
            prev_body = abs(prev.c - prev.o) or 0.001
            curr_body = abs(curr.c - curr.o) or 0.001
            upper_wick = curr.h - max(curr.c, curr.o)
            lower_wick = min(curr.c, curr.o) - curr.l

            if direction == "long":
                if curr.c > curr.o and prev.c < prev.o and curr_body > prev_body and curr.c >= prev.o:
                    confirmation = True
                if lower_wick >= 1.5 * curr_body and curr.c > (curr.h + curr.l) / 2:
                    confirmation = True
            elif direction == "short":
                if curr.c < curr.o and prev.c > prev.o and curr_body > prev_body and curr.c <= prev.o:
                    confirmation = True
                if upper_wick >= 1.5 * curr_body and curr.c < (curr.h + curr.l) / 2:
                    confirmation = True

            if not confirmation:
                continue

            # ── Refinement D: Close-beyond (next bar closes past level) ──
            if rules["close_beyond"] and i + 1 < len(bars_5m):
                next_bar = bars_5m[i + 1]
                if direction == "long" and next_bar.c <= lvl.price:
                    continue
                if direction == "short" and next_bar.c >= lvl.price:
                    continue

            # ── Entry & stop ──
            entry_price = bar.c

            if rules["stop_at_extreme"]:
                # B: stop at the sweep extreme (the actual high/low of the sweep)
                if direction == "long":
                    stop = (sweep_extreme or bar.l) - atr * 0.05  # tiny buffer
                else:
                    stop = (sweep_extreme or bar.h) + atr * 0.05
            else:
                # Default: stop below previous bar low/high
                if direction == "long":
                    stop = min(bar.l, prev.l) - atr * 0.05
                else:
                    stop = max(bar.h, prev.h) + atr * 0.05

            risk_ps = abs(entry_price - stop)
            if risk_ps <= 0:
                continue

            tp1 = entry_price + risk_ps * rules["tp1_rr"] if direction == "long" else entry_price - risk_ps * rules["tp1_rr"]
            tp2 = entry_price + risk_ps * rules["tp2_rr"] if direction == "long" else entry_price - risk_ps * rules["tp2_rr"]

            if abs(tp1 - entry_price) / risk_ps < rules["min_rr"]:
                continue

            signals.append({
                "ts": bar.ts, "direction": direction,
                "entry_price": entry_price, "stop": stop,
                "tp1": tp1, "tp2": tp2, "sweep_level": lvl.price,
            })
            last_trade_bar = i
            break  # one trade per bar

    return signals


# ── Run backtest with specific rules ──

def run_backtest(conn, symbols, start, end, rules, label):
    all_trades = []
    for symbol in symbols:
        daily = fetch_daily_bars(conn, symbol, start - timedelta(days=90), end)
        bars_5m = fetch_5m_bars(conn, symbol, start, end)
        if not daily or not bars_5m:
            continue
        signals = detect_signals(symbol, daily, bars_5m, rules)
        for sig in signals:
            entry_idx = None
            for idx, bar in enumerate(bars_5m):
                if bar.ts >= sig["ts"]:
                    entry_idx = idx
                    break
            if entry_idx is None:
                continue
            trade = simulate_trade(sig, bars_5m, entry_idx, rules)
            if trade:
                trade.symbol = symbol
                all_trades.append(trade)

    n = len(all_trades)
    if n == 0:
        print(f"  {label:30s}: NO TRADES")
        return {}

    winners = [t for t in all_trades if t.gross_pnl > 0]
    losers = [t for t in all_trades if t.gross_pnl <= 0]
    wr = len(winners) / n
    avg_r = sum(t.r_multiple for t in all_trades) / n
    pf = abs(sum(t.gross_pnl for t in winners) / sum(t.gross_pnl for t in losers)) if losers else float("inf")
    max_r = rules["tp1_size"] * rules["tp1_rr"] + rules["tp2_size"] * rules["tp2_rr"]
    be_wr = 1 / (1 + max_r)
    stop_pct = len([t for t in all_trades if t.exit_reason == "stop_loss"]) / n

    stops = [t.r_multiple for t in all_trades if t.exit_reason == "stop_loss"]
    avg_stop_r = statistics.mean(stops) if stops else 0

    print(f"  {label:30s}: {n:4d} trades  WR={wr:.1%}  PF={pf:.2f}  avgR={avg_r:+.2f}  "
          f"stops={stop_pct:.0%}  avg_stopR={avg_stop_r:.2f}")
    return {"trades": n, "wr": wr, "pf": pf, "avg_r": avg_r, "stop_pct": stop_pct}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-11-18")
    parser.add_argument("--end", default="2026-05-15")
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    symbols = get_active_symbols(conn)
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    print("=" * 80)
    print("LIQUIDITY SWEEP v3 — REFINEMENT TESTS")
    print(f"Period: {start} to {end} | Symbols: {len(symbols)}")
    print("=" * 80)

    # ── BASELINE (v2 result) ──
    print("\n── BASELINE (no refinements) ──")
    run_backtest(conn, symbols, start, end, BASE_RULES, "baseline")

    # ── TEST A: Volume filter ──
    print("\n── A: VOLUME FILTER (sweep bar vol > 1.5x avg) ──")
    for mult in [1.2, 1.5, 2.0]:
        r = {**BASE_RULES, "volume_filter": True, "volume_mult": mult}
        run_backtest(conn, symbols, start, end, r, f"vol > {mult}x avg")

    # ── TEST B: Stop at sweep extreme ──
    print("\n── B: STOP AT SWEEP EXTREME (not ATR-based) ──")
    run_backtest(conn, symbols, start, end, {**BASE_RULES, "stop_at_extreme": True}, "stop@extreme")

    # ── TEST C: Minimum penetration ──
    print("\n── C: MINIMUM PENETRATION DEPTH ──")
    for pen in [0.25, 0.5, 0.75]:
        r = {**BASE_RULES, "min_penetration_atr": pen}
        run_backtest(conn, symbols, start, end, r, f"penetration > {pen} ATR")

    # ── TEST D: Close-beyond confirmation ──
    print("\n── D: CLOSE-BEYOND CONFIRMATION (next bar closes past level) ──")
    run_backtest(conn, symbols, start, end, {**BASE_RULES, "close_beyond": True}, "close_beyond")

    # ── COMBINATIONS ──
    print("\n── COMBINATIONS ──")
    # Best of each
    run_backtest(conn, symbols, start, end,
                 {**BASE_RULES, "volume_filter": True, "volume_mult": 1.5,
                  "stop_at_extreme": True},
                 "A1.5 + B")
    run_backtest(conn, symbols, start, end,
                 {**BASE_RULES, "volume_filter": True, "volume_mult": 1.5,
                  "stop_at_extreme": True, "min_penetration_atr": 0.5},
                 "A1.5 + B + C0.5")
    run_backtest(conn, symbols, start, end,
                 {**BASE_RULES, "volume_filter": True, "volume_mult": 1.5,
                  "stop_at_extreme": True, "close_beyond": True},
                 "A1.5 + B + D")
    run_backtest(conn, symbols, start, end,
                 {**BASE_RULES, "volume_filter": True, "volume_mult": 1.5,
                  "stop_at_extreme": True, "min_penetration_atr": 0.5, "close_beyond": True},
                 "ALL COMBINED")

    # ── Extended hold: 2 days ──
    print("\n── EXTENDED HOLD (2 trading days) ──")
    r2 = {**BASE_RULES, "volume_filter": True, "volume_mult": 1.5,
          "stop_at_extreme": True, "min_penetration_atr": 0.5, "close_beyond": True,
          "max_hold_bars": 156}
    run_backtest(conn, symbols, start, end, r2, "ALL + 2-day hold")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()