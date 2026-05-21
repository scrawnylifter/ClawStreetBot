#!/usr/bin/env python3
"""Diagnose why the liquidity backtest win rate is so low."""
import os
import sys
from collections import Counter
from datetime import datetime, date, timedelta
import statistics

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from backtest_liquidity import (
    load_env, DB_CONFIG, LIQUIDITY_RULES,
    fetch_daily_bars, fetch_5m_bars, fetch_atr_daily,
    get_active_symbols, detect_liquidity_signals, simulate_trade,
)

load_env(".env.db")
import psycopg2
conn = psycopg2.connect(**DB_CONFIG)

symbols = get_active_symbols(conn)
start = date(2026, 4, 16)
end = date(2026, 5, 15)

all_trades = []
for symbol in symbols:
    daily_start = start - timedelta(days=90)
    daily = fetch_daily_bars(conn, symbol, daily_start, end)
    bars_5m = fetch_5m_bars(conn, symbol, start, end)
    if not daily or not bars_5m:
        continue
    atr_daily = {}
    for bar in daily:
        atr = fetch_atr_daily(conn, symbol, bar.d)
        if atr:
            atr_daily[bar.d] = atr
    signals = detect_liquidity_signals(symbol, daily, bars_5m, atr_daily)
    for sig in signals:
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

n = len(all_trades)
print(f"Total trades: {n}")

# ── Exit reason distribution ──
exit_reasons = Counter(t.exit_reason for t in all_trades)
print("\n=== EXIT REASON DISTRIBUTION ===")
for reason, count in exit_reasons.most_common():
    pct = count / n * 100
    print(f"  {reason:15s}: {count:5d} ({pct:5.1f}%)")

# ── R-multiple stats ──
r_vals = [t.r_multiple for t in all_trades]
print(f"\n=== R-MULTIPLE STATS ===")
print(f"  Mean:   {statistics.mean(r_vals):.3f}R")
print(f"  Median: {statistics.median(r_vals):.3f}R")
print(f"  Stdev:  {statistics.stdev(r_vals):.3f}R")

# ── R-multiple by exit reason ──
print(f"\n=== R-MULTIPLE BY EXIT REASON ===")
for reason in ["stop_loss", "tp2", "max_hold"]:
    r_vals_r = [t.r_multiple for t in all_trades if t.exit_reason == reason]
    if r_vals_r:
        avg = statistics.mean(r_vals_r)
        print(f"  {reason:15s}: {len(r_vals_r):5d} trades, avg R = {avg:.3f}")

# ── FVG vs External breakdown ──
print(f"\n=== FVG vs EXTERNAL EXIT BREAKDOWN ===")
for label, kind in [("FVG", "fvg"), ("EXTERNAL", "external")]:
    trades = [t for t in all_trades if t.sweep_kind == kind]
    if not trades:
        continue
    print(f"  {label}: {len(trades)} trades")
    for reason in ["stop_loss", "tp2", "max_hold"]:
        sub = [t for t in trades if t.exit_reason == reason]
        if sub:
            avg_r = statistics.mean([t.r_multiple for t in sub])
            print(f"    {reason:15s}: {len(sub):5d} ({len(sub)/len(trades)*100:5.1f}%), avg R={avg_r:.2f}")

# ── TP2 hit rate ──
tp2_trades = [t for t in all_trades if t.exit_reason == "tp2"]
print(f"\n=== TARGET HIT RATES ===")
print(f"  TP2 hit (5:1):   {len(tp2_trades):5d} trades ({len(tp2_trades)/n*100:.1f}%)")
max_hold = [t for t in all_trades if t.exit_reason == "max_hold"]
print(f"  Max hold exited: {len(max_hold):5d} trades ({len(max_hold)/n*100:.1f}%)")
stop = [t for t in all_trades if t.exit_reason == "stop_loss"]
print(f"  Stop loss:       {len(stop):5d} trades ({len(stop)/n*100:.1f}%)")

# ── Session analysis ──
session_start = datetime.strptime("09:30", "%H:%M").time()
session_end = datetime.strptime("16:00", "%H:%M").time()
pre = [t for t in all_trades if t.entry_ts.time() < session_start]
during = [t for t in all_trades if session_start <= t.entry_ts.time() <= session_end]
post = [t for t in all_trades if t.entry_ts.time() > session_end]
print(f"\n=== SESSION ANALYSIS ===")
print(f"  Pre-market (< 09:30):    {len(pre):5d} ({len(pre)/n*100:.1f}%)")
print(f"  In-session (09:30-16:00): {len(during):5d} ({len(during)/n*100:.1f}%)")
print(f"  Post-market (> 16:00):   {len(post):5d} ({len(post)/n*100:.1f}%)")
for label, trades in [("Pre-market", pre), ("In-session", during), ("Post-market", post)]:
    if not trades:
        continue
    wr = len([t for t in trades if t.gross_pnl > 0]) / len(trades)
    avg_r = statistics.mean([t.r_multiple for t in trades])
    print(f"  {label:25s}: WR={wr:.1%}, avg R={avg_r:.2f}R")

# ── The math check: with 3:1 targets and 2 partial exits, what WR breaks even? ──
print(f"\n=== BREAKEVEN MATH ===")
tp1_size = LIQUIDITY_RULES["tp1_size"]
tp2_size = LIQUIDITY_RULES["tp2_size"]
tp1_rr = LIQUIDITY_RULES["tp1_rr"]
tp2_rr = LIQUIDITY_RULES["tp2_rr"]
print(f"  TP1: close {tp1_size:.0%} at {tp1_rr:.0f}R = {tp1_size * tp1_rr:.2f}R contribution")
print(f"  TP2: close {tp2_size:.0%} at {tp2_rr:.0f}R = {tp2_size * tp2_rr:.2f}R contribution")
print(f"  Max winner R: {tp1_size * tp1_rr + tp2_size * tp2_rr:.2f}R")
print(f"  Max loser R: -1.00R (stop hit)")
# Breakeven: p * maxR + (1-p) * (-1) = 0 => p = 1 / (1 + maxR)
max_r = tp1_size * tp1_rr + tp2_size * tp2_rr
breakeven_wr = 1 / (1 + max_r)
print(f"  Breakeven win rate: {breakeven_wr:.1%}")
print(f"  Actual win rate:    {len([t for t in all_trades if t.gross_pnl > 0])/n:.1%}")

# ── Max hold analysis: how much R is left on the table? ──
if max_hold:
    mh_avg = statistics.mean([t.r_multiple for t in max_hold])
    mh_positive = len([t for t in max_hold if t.gross_pnl > 0])
    mh_wr = mh_positive / len(max_hold)
    print(f"\n=== MAX HOLD TRADES (time stop) ===")
    print(f"  Count: {len(max_hold)}")
    print(f"  WR: {mh_wr:.1%}")
    print(f"  Avg R: {mh_avg:.2f}R")
    print(f"  These trades never hit stop or TP — they timed out")
    print(f"  If avg R is near 0, many are going nowhere (chop city)")

# ── How many trades fire in the same 5m bar? (over-trading check) ──
from collections import defaultdict
ts_counts = defaultdict(int)
for t in all_trades:
    ts_counts[t.entry_ts] += 1
multi_entry = {k: v for k, v in ts_counts.items() if v > 1}
print(f"\n=== OVER-TRADING CHECK ===")
print(f"  Unique entry timestamps: {len(ts_counts)}")
print(f"  Bars with >1 trade: {len(multi_entry)}")
if multi_entry:
    total_multi = sum(multi_entry.values())
    avg_per_bar = statistics.mean(multi_entry.values())
    print(f"  Trades in multi-entry bars: {total_multi} ({total_multi/n*100:.1f}% of all trades)")
    print(f"  Avg trades per multi-entry bar: {avg_per_bar:.1f}")
    # What if we took only 1 trade per bar?
    single_trades = [t for t in all_trades if ts_counts[t.entry_ts] == 1]
    if single_trades:
        wr = len([t for t in single_trades if t.gross_pnl > 0]) / len(single_trades)
        avg_r = statistics.mean([t.r_multiple for t in single_trades])
        print(f"  Single-entry trades: {len(single_trades)} (WR={wr:.1%}, avg R={avg_r:.2f}R)")