#!/usr/bin/env python3
"""Clean liquidity sweep backtest — external range only.

Tests the ONE concept all 3 sources agree on:
  "When price sweeps a daily swing H/L and rejects, fade the sweep."

No FVG, no consolidation, no extra stuff. Just the core trade.
Each added concept gets its own test later.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

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

# ── Core rules (user's day-trading parameters) ──
RULES = {
    "risk_pct": 0.05,
    "atr_mult_stop": 1.5,
    "min_rr": 3.0,
    "max_hold_bars": 78,
    "tp1_rr": 3.0,
    "tp2_rr": 5.0,
    "tp1_size": 0.50,
    "tp2_size": 0.50,
    "swing_lookback": 20,
    "equal_hl_tolerance": 0.002,
    "session_start": "09:30",
    "session_end": "16:00",
    "trend_filter": True,       # require daily trend alignment
    "cooldown_bars": 12,        # no re-entry within 1hr after a trade on same symbol
}


@dataclass
class Bar5m:
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
    d: date
    o: float
    h: float
    l: float
    c: float


@dataclass
class SwingLevel:
    price: float
    kind: str  # "high" or "low"
    date: date


@dataclass
class Trade:
    symbol: str
    direction: str
    entry_ts: datetime
    entry_price: float
    stop_price: float
    tp1_price: float
    tp2_price: float
    sweep_level: float
    risk_per_share: float
    quantity: float = 1.0
    exit_ts: datetime | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    gross_pnl: float = 0.0
    r_multiple: float = 0.0
    hold_bars: int = 0
    partial_exits: list[dict] = field(default_factory=list)

    @property
    def is_winner(self) -> bool:
        return self.gross_pnl > 0


# ── Data fetching ──

def fetch_daily_bars(conn, symbol, start, end):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT o.timestamp::date, o.open, o.high, o.low, o.close
               FROM market.ohlcv o
               JOIN market.assets a ON a.id = o.asset_id
               WHERE a.symbol = %s AND o.timeframe = '1d'
                 AND o.timestamp::date BETWEEN %s AND %s
               ORDER BY o.timestamp""",
            (symbol, start, end),
        )
        return [BarDaily(d=r[0], o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4]))
                for r in cur.fetchall()]


def fetch_5m_bars(conn, symbol, start, end):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT o.timestamp, o.open, o.high, o.low, o.close,
                      o.volume, o.trade_count, o.vwap
               FROM market.ohlcv o
               JOIN market.assets a ON a.id = o.asset_id
               WHERE a.symbol = %s AND o.timeframe = '5m'
                 AND o.timestamp::date BETWEEN %s AND %s
               ORDER BY o.timestamp""",
            (symbol, start, end),
        )
        return [Bar5m(ts=r[0], o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4]),
                     volume=int(r[5] or 0), trade_count=int(r[6] or 0), vwap=float(r[7] or 0))
                for r in cur.fetchall()]


def fetch_ema9_21(conn, symbol, on):
    """Get EMA9 and EMA21 for daily trend direction."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT ema_9, ema_21 FROM market.technical_indicators
               WHERE symbol = %s AND date <= %s AND ema_9 IS NOT NULL AND ema_21 IS NOT NULL
               ORDER BY date DESC LIMIT 1""",
            (symbol, on),
        )
        r = cur.fetchone()
        if r and r[0] and r[1]:
            return float(r[0]), float(r[1])
    return None, None


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
        window_h = [daily[j].h for j in range(i - half, i + half + 1)]
        window_l = [daily[j].l for j in range(i - half, i + half + 1)]
        if daily[i].h == max(window_h) and window_h.count(daily[i].h) == 1:
            levels.append(SwingLevel(price=daily[i].h, kind="high", date=daily[i].d))
        if daily[i].l == min(window_l) and window_l.count(daily[i].l) == 1:
            levels.append(SwingLevel(price=daily[i].l, kind="low", date=daily[i].d))
    return levels


def find_equal_levels(levels, tolerance=0.002):
    """Cluster equal H/L — engineered liquidity."""
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
                    result.append(cluster[-1])  # most recent
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
    padding = [atrs[0]] * (len(bars) - 1 - len(atrs))
    return padding + atrs


# ── Confirmation patterns ──

def is_engulfing(bars, idx):
    if idx < 1 or idx >= len(bars):
        return None
    prev, curr = bars[idx - 1], bars[idx]
    prev_body = abs(prev.c - prev.o)
    curr_body = abs(curr.c - curr.o)
    if prev_body == 0 or curr_body <= prev_body:
        return None
    if curr.c > curr.o and prev.c < prev.o and curr.c >= prev.o and curr.o <= prev.c:
        return "engulfing_bullish"
    if curr.c < curr.o and prev.c > prev.o and curr.o >= prev.c and curr.c <= prev.o:
        return "engulfing_bearish"
    return None


def is_rejection_wick(bars, idx, atr):
    if idx >= len(bars) or atr <= 0:
        return None
    b = bars[idx]
    body = abs(b.c - b.o) or 0.001
    total_range = b.h - b.l
    upper_wick = b.h - max(b.c, b.o)
    lower_wick = min(b.c, b.o) - b.l
    if total_range < 0.5 * atr:
        return None
    if lower_wick >= 1.5 * body and b.c > (b.h + b.l) / 2:
        return "wick_bullish"
    if upper_wick >= 1.5 * body and b.c < (b.h + b.l) / 2:
        return "wick_bearish"
    return None


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

    trade = Trade(
        symbol="", direction=direction, entry_ts=bars_5m[start_idx].ts,
        entry_price=actual_entry, stop_price=stop, tp1_price=tp1, tp2_price=tp2,
        sweep_level=signal["sweep_level"], risk_per_share=risk,
    )

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
                trade.partial_exits.append({"ts": bar.ts, "price": trade.tp1_price, "size": rules["tp1_size"], "pnl": pnl})
                trade.gross_pnl += pnl
                remaining -= rules["tp1_size"]
                tp1_hit = True
                trade.stop_price = actual_entry  # breakeven stop
            if tp1_hit and bar.h >= trade.tp2_price:
                pnl = (trade.tp2_price - actual_entry) * remaining
                trade.partial_exits.append({"ts": bar.ts, "price": trade.tp2_price, "size": remaining, "pnl": pnl})
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
                trade.partial_exits.append({"ts": bar.ts, "price": trade.tp1_price, "size": rules["tp1_size"], "pnl": pnl})
                trade.gross_pnl += pnl
                remaining -= rules["tp1_size"]
                tp1_hit = True
                trade.stop_price = actual_entry
            if tp1_hit and bar.l <= trade.tp2_price:
                pnl = (actual_entry - trade.tp2_price) * remaining
                trade.partial_exits.append({"ts": bar.ts, "price": trade.tp2_price, "size": remaining, "pnl": pnl})
                trade.gross_pnl += pnl
                trade.exit_ts, trade.exit_price, trade.exit_reason = bar.ts, trade.tp2_price, "tp2"
                trade.r_multiple = trade.gross_pnl / risk
                return trade

    # Max hold
    last = bars_5m[min(start_idx + rules["max_hold_bars"] - 1, len(bars_5m) - 1)]
    trade.exit_ts, trade.exit_price, trade.exit_reason = last.ts, last.c, "max_hold"
    if direction == "long":
        trade.gross_pnl = (last.c - actual_entry) * remaining
    else:
        trade.gross_pnl = (actual_entry - last.c) * remaining
    trade.r_multiple = trade.gross_pnl / risk if risk else 0
    return trade


# ── Main ──

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--no-trend-filter", action="store_true", help="Disable daily trend alignment")
    parser.add_argument("--show-trades", action="store_true")
    args = parser.parse_args()

    use_trend = not args.no_trend_filter
    RULES["trend_filter"] = use_trend

    conn = psycopg2.connect(**DB_CONFIG)
    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else get_active_symbols(conn)
    start = date.fromisoformat(args.start) if args.start else date(2026, 4, 16)
    end = date.fromisoformat(args.end) if args.end else date(2026, 5, 15)

    all_trades = []
    session_start = datetime.strptime(RULES["session_start"], "%H:%M").time()
    session_end = datetime.strptime(RULES["session_end"], "%H:%M").time()

    for symbol in symbols:
        daily = fetch_daily_bars(conn, symbol, start - timedelta(days=90), end)
        bars_5m = fetch_5m_bars(conn, symbol, start, end)
        if not daily or not bars_5m:
            continue

        swing_levels = find_swing_levels(daily, RULES["swing_lookback"])
        equal_levels = find_equal_levels(swing_levels, RULES["equal_hl_tolerance"])
        all_levels = swing_levels + equal_levels
        atrs_5m = compute_atr_5m(bars_5m)

        # Track last trade bar index per symbol for cooldown
        last_trade_bar = -999

        sig_count = 0
        for i in range(2, len(bars_5m)):
            bar = bars_5m[i]
            atr = atrs_5m[i] if i < len(atrs_5m) else None
            if not atr or atr <= 0:
                continue

            # Session filter
            bt = bar.ts.time()
            if bt < session_start or bt > session_end:
                continue

            # Cooldown
            if i - last_trade_bar < RULES["cooldown_bars"]:
                continue

            # Daily trend filter
            ema9, ema21 = None, None
            if use_trend:
                ema9, ema21 = fetch_ema9_21(conn, symbol, bar.ts.date())

            for lvl in all_levels:
                if lvl.date >= bar.ts.date():
                    continue

                direction = None
                if lvl.kind == "high" and bar.h > lvl.price and bar.c < lvl.price:
                    direction = "short"
                elif lvl.kind == "low" and bar.l < lvl.price and bar.c > lvl.price:
                    direction = "long"

                if not direction:
                    continue

                # Trend filter: only take sweeps in trend direction
                if use_trend and ema9 and ema21:
                    if direction == "long" and ema9 < ema21:
                        continue  # don't go long in downtrend
                    if direction == "short" and ema9 > ema21:
                        continue  # don't go short in uptrend

                # Confirmation
                confirmation = None
                eng = is_engulfing(bars_5m, i)
                if eng:
                    if direction == "long" and eng == "engulfing_bullish":
                        confirmation = "engulfing"
                    elif direction == "short" and eng == "engulfing_bearish":
                        confirmation = "engulfing"

                if not confirmation:
                    wick = is_rejection_wick(bars_5m, i, atr)
                    if wick:
                        if direction == "long" and wick == "wick_bullish":
                            confirmation = "wick"
                        elif direction == "short" and wick == "wick_bearish":
                            confirmation = "wick"

                if not confirmation:
                    continue

                # Entry, stop, targets
                entry_price = bar.c
                if direction == "long":
                    stop = min(bar.l, bars_5m[i-1].l if i > 0 else bar.l) - atr * 0.05
                    risk_ps = entry_price - stop
                else:
                    stop = max(bar.h, bars_5m[i-1].h if i > 0 else bar.h) + atr * 0.05
                    risk_ps = stop - entry_price

                if risk_ps <= 0:
                    continue

                tp1 = entry_price + risk_ps * RULES["tp1_rr"] if direction == "long" else entry_price - risk_ps * RULES["tp1_rr"]
                tp2 = entry_price + risk_ps * RULES["tp2_rr"] if direction == "long" else entry_price - risk_ps * RULES["tp2_rr"]

                # Min R:R gate
                if abs(tp1 - entry_price) / risk_ps < RULES["min_rr"]:
                    continue

                sig_count += 1
                trade = simulate_trade(
                    {"direction": direction, "entry_price": entry_price, "stop": stop,
                     "tp1": tp1, "tp2": tp2, "sweep_level": lvl.price},
                    bars_5m, i, RULES,
                )
                if trade:
                    trade.symbol = symbol
                    all_trades.append(trade)
                    last_trade_bar = i

        print(f"  {symbol}: {len(daily)} daily, {len(bars_5m)} 5m, {len(all_levels)} levels, "
              f"{sig_count} signals")

    conn.close()

    # ── Metrics ──
    n = len(all_trades)
    print(f"\n{'='*60}")
    print(f"LIQUIDITY SWEEP BACKTEST — EXTERNAL RANGE ONLY")
    if use_trend:
        print("Daily trend filter: ON (EMA9 > EMA21 = longs only)")
    else:
        print("Daily trend filter: OFF")
    print(f"{'='*60}")
    print(f"Period: {start} to {end}")
    print(f"Symbols: {len(symbols)}")

    if n == 0:
        print("\nNo trades generated.")
        return

    winners = [t for t in all_trades if t.gross_pnl > 0]
    losers = [t for t in all_trades if t.gross_pnl <= 0]
    wr = len(winners) / n
    avg_r = sum(t.r_multiple for t in all_trades) / n
    pf = abs(sum(t.gross_pnl for t in winners) / sum(t.gross_pnl for t in losers)) if losers else float("inf")
    max_r = RULES["tp1_size"] * RULES["tp1_rr"] + RULES["tp2_size"] * RULES["tp2_rr"]
    be_wr = 1 / (1 + max_r)

    print(f"\nTotal trades:      {n}")
    print(f"Winners:           {len(winners)}")
    print(f"Losers:            {len(losers)}")
    print(f"Win rate:          {wr:.1%} (breakeven: {be_wr:.1%})")
    print(f"Profit factor:     {pf:.2f}")
    print(f"Avg R-multiple:    {avg_r:.2f}R")
    print(f"Total P&L:         {sum(t.gross_pnl for t in all_trades):.2f} (per share)")

    longs = [t for t in all_trades if t.direction == "long"]
    shorts = [t for t in all_trades if t.direction == "short"]
    if longs:
        print(f"Long trades:       {len(longs)} (WR: {len([t for t in longs if t.gross_pnl>0])/len(longs):.1%}, "
              f"avg R: {sum(t.r_multiple for t in longs)/len(longs):.2f}R)")
    if shorts:
        print(f"Short trades:      {len(shorts)} (WR: {len([t for t in shorts if t.gross_pnl>0])/len(shorts):.1%}, "
              f"avg R: {sum(t.r_multiple for t in shorts)/len(shorts):.2f}R)")

    # By confirmation
    by_conf = defaultdict(list)
    for t in all_trades:
        by_conf[t.exit_reason].append(t)
    print(f"\n── By Exit Reason ──")
    for reason, trades in sorted(by_conf.items()):
        r = sum(t.r_multiple for t in trades) / len(trades)
        w = len([t for t in trades if t.gross_pnl > 0]) / len(trades)
        print(f"  {reason:12s}: {len(trades):3d} trades, WR={w:.1%}, avg R={r:.2f}R")

    if args.show_trades:
        print(f"\n── All Trades ──")
        for t in all_trades:
            print(f"  {t.symbol} {t.entry_ts} {t.direction:5s} "
                  f"entry={t.entry_price:.2f} stop={t.stop_price:.2f} "
                  f"tp1={t.tp1_price:.2f} tp2={t.tp2_price:.2f} "
                  f"→ exit={t.exit_price:.2f} ({t.exit_reason}) "
                  f"P&L={t.gross_pnl:.2f} R={t.r_multiple:.2f}")

    # Verdict
    print()
    if n >= 20:
        if wr > be_wr and avg_r > 0:
            print(f"✅ POSITIVE: {wr:.1%} WR > {be_wr:.1%} breakeven, avg {avg_r:.2f}R. Edge confirmed.")
        elif wr >= be_wr * 0.9:
            print(f"⚠️  MARGINAL: Close to breakeven. Need more data or tighter filters.")
        else:
            print(f"❌ NEGATIVE: Below breakeven. Core concept needs revision or discard.")
    else:
        print("⚠️  SMALL SAMPLE: Too few trades for statistical significance. Need more 5m data.")


if __name__ == "__main__":
    main()