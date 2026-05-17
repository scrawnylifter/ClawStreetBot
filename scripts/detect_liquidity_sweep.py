#!/usr/bin/env python3
"""Liquidity Sweep Scanner — live detection with close-beyond confirmation.

Runs on 5m + daily data, detects external range sweeps of swing H/L levels,
requires close-beyond confirmation (next bar closes past swept level),
then writes to market.signal_alerts and sends Telegram alerts.

Backtest-proven: PF 1.56, WR 32.4%, avg +0.16R over 259 trades (6-month data).
Best on mid-cap momentum (IREN, ASTS, MU). Fails low-float erratic (RKLB, RDDT, OKLO).

Usage:
    python detect_liquidity_sweep.py               # Live: scan + alert
    python detect_liquidity_sweep.py --dry-run      # Print only, no DB/Telegram
    python detect_liquidity_sweep.py --symbols NVDA,IREN  # Override watchlist
    python detect_liquidity_sweep.py --no-option    # Skip option lookup
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg2

log = logging.getLogger("liquidity_sweep")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── Env loading ──

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
# Sibling imports (for option lookup)
sys.path.insert(0, str(Path(__file__).resolve().parent))

# ── Strategy rules (backtest-validated) ──

RULES = {
    "swing_lookback": 20,
    "equal_hl_tolerance": 0.002,
    "cooldown_bars": 12,
    "close_beyond": True,       # D refinement — THE key filter (PF 1.24 → 1.56)
    "tp1_rr": 3.0,
    "tp2_rr": 5.0,
    "tp1_size": 0.50,
    "tp2_size": 0.50,
    "session_start": "09:30",
    "session_end": "16:00",
    "min_rr": 3.0,
    # Symbols where the strategy works (skip known failures)
    "skip_symbols": {"RKLB", "RDDT", "OKLO"},
}


# ── Data classes ──

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


# ── Data fetching ──

def get_connection():
    env_path = Path("/app/.env.db")
    if not env_path.exists():
        env_path = PROJECT_ROOT / ".env.db"
    conn_params = {}
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                conn_params[k.strip()] = v.strip()
    return psycopg2.connect(
        host=conn_params.get("POSTGRES_HOST", "localhost"),
        port=int(conn_params.get("POSTGRES_PORT", 5432)),
        user=conn_params.get("POSTGRES_USER", "clawstreet"),
        password=conn_params.get("POSTGRES_PASSWORD", ""),
        dbname=conn_params.get("POSTGRES_DB", "clawstreet"),
    )


def fetch_daily_bars(conn, symbol, lookback_days=120):
    end = date.today()
    start = end - timedelta(days=lookback_days)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT o.timestamp::date, o.open, o.high, o.low, o.close
            FROM market.ohlcv o JOIN market.assets a ON a.id = o.asset_id
            WHERE a.symbol=%s AND o.timeframe='1d'
              AND o.timestamp::date BETWEEN %s AND %s
            ORDER BY o.timestamp
        """, (symbol, start, end))
        return [BarDaily(d=r[0], o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4]))
                for r in cur.fetchall()]


def fetch_5m_bars(conn, symbol, days=5):
    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=days)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT o.timestamp, o.open, o.high, o.low, o.close,
                   o.volume, o.trade_count, o.vwap
            FROM market.ohlcv o JOIN market.assets a ON a.id = o.asset_id
            WHERE a.symbol=%s AND o.timeframe='5m'
              AND o.timestamp::date BETWEEN %s AND %s
            ORDER BY o.timestamp
        """, (symbol, start, end))
        return [Bar5m(ts=r[0], o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4]),
                      volume=int(r[5] or 0), trade_count=int(r[6] or 0), vwap=float(r[7] or 0))
                for r in cur.fetchall()]


def get_active_symbols(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT symbol FROM market.assets WHERE active ORDER BY symbol")
        return [r[0] for r in cur.fetchall()]


# ── Swing detection (from backtest v3) ──

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


# ── ATR computation ──

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


# ── Live signal detection ──

def detect_sweep_signals(symbol, daily, bars_5m, rules):
    """Detect liquidity sweep signals with close-beyond confirmation.
    
    Returns list of signal dicts ready for DB insert.
    Only returns signals from the LAST bar (or last 2 bars for close-beyond).
    This is the LIVE scanner — it only fires on current data, not historical replay.
    """
    signals = []
    swing_levels = find_swing_levels(daily, rules["swing_lookback"])
    equal_levels = find_equal_levels(swing_levels, rules["equal_hl_tolerance"])
    all_levels = swing_levels + equal_levels
    atrs = compute_atr_5m(bars_5m)

    if not bars_5m or not all_levels:
        return signals

    session_start = datetime.strptime(rules["session_start"], "%H:%M").time()
    session_end = datetime.strptime(rules["session_end"], "%H:%M").time()

    # In live mode, only check the last 2 bars (current bar for sweep, next bar for close-beyond)
    # We scan from i = len(bars_5m) - 2 back a few bars to handle timing edge cases
    scan_start = max(2, len(bars_5m) - 3)  # Check last ~3 bars in case of slight timing
    
    for i in range(scan_start, len(bars_5m) - 1):
        bar = bars_5m[i]
        atr = atrs[i] if i < len(atrs) else None
        if not atr or atr <= 0:
            continue

        # Session filter
        bt = bar.ts.time()
        if bt < session_start or bt > session_end:
            continue

        for lvl in all_levels:
            if lvl.date >= bar.ts.date():
                continue

            direction = None
            sweep_extreme = None

            if lvl.kind == "high" and bar.h > lvl.price and bar.c < lvl.price:
                direction = "short"
                sweep_extreme = bar.h
            elif lvl.kind == "low" and bar.l < lvl.price and bar.c > lvl.price:
                direction = "long"
                sweep_extreme = bar.l

            if not direction:
                continue

            # ── Confirmation: engulfing or wick on sweep bar ──
            prev = bars_5m[i - 1]
            prev_body = abs(prev.c - prev.o) or 0.001
            curr_body = abs(bar.c - bar.o) or 0.001
            upper_wick = bar.h - max(bar.c, bar.o)
            lower_wick = min(bar.c, bar.o) - bar.l

            confirmation = False
            if direction == "long":
                if bar.c > bar.o and prev.c < prev.o and curr_body > prev_body and bar.c >= prev.o:
                    confirmation = True
                if lower_wick >= 1.5 * curr_body and bar.c > (bar.h + bar.l) / 2:
                    confirmation = True
            elif direction == "short":
                if bar.c < bar.o and prev.c > prev.o and curr_body > prev_body and bar.c <= prev.o:
                    confirmation = True
                if upper_wick >= 1.5 * curr_body and bar.c < (bar.h + bar.l) / 2:
                    confirmation = True

            if not confirmation:
                continue

            # ── Close-beyond confirmation (refinement D) ──
            if rules["close_beyond"] and i + 1 < len(bars_5m):
                next_bar = bars_5m[i + 1]
                if direction == "long" and next_bar.c <= lvl.price:
                    continue
                if direction == "short" and next_bar.c >= lvl.price:
                    continue

            # ── Entry & stop ──
            entry_price = bar.c
            stop = min(bar.l, prev.l) - atr * 0.05 if direction == "long" else max(bar.h, prev.h) + atr * 0.05
            risk_ps = abs(entry_price - stop)
            if risk_ps <= 0:
                continue

            tp1 = entry_price + risk_ps * rules["tp1_rr"] if direction == "long" else entry_price - risk_ps * rules["tp1_rr"]
            tp2 = entry_price + risk_ps * rules["tp2_rr"] if direction == "long" else entry_price - risk_ps * rules["tp2_rr"]

            if abs(tp1 - entry_price) / risk_ps < rules["min_rr"]:
                continue

            risk_dollars = risk_ps * 100  # 1 contract = 100 shares
            tp1_dollars = (tp1 - entry_price) * 100 if direction == "long" else (entry_price - tp1) * 100
            tp2_dollars = (tp2 - entry_price) * 100 if direction == "long" else (entry_price - tp2) * 100

            invalidation = [
                f"Stop ${stop:.2f}" if direction == "long" else f"Stop ${stop:.2f}",
                f"Sweep level ${lvl.price:.2f} reclaimed",
                f"Max hold 1 trading day (78 bars)",
            ]

            sig = {
                "symbol": symbol,
                "strategy": "liquidity_sweep",
                "direction": "bullish" if direction == "long" else "bearish",
                "status": "new",
                "timeframe": "5m",
                "trigger_price": round(entry_price, 2),
                "atr_14": round(atr, 4),
                "stop_price": round(stop, 2),
                "tp1_price": round(tp1, 2),
                "tp2_price": round(tp2, 2),
                "risk_reward": round(abs(tp1 - entry_price) / risk_ps, 2),
                "invalidation": json.dumps(invalidation),
                # Extra fields for alert formatting (not all go to DB)
                "_direction_raw": direction,
                "_sweep_level": lvl.price,
                "_sweep_kind": lvl.kind,
                "_entry_price": entry_price,
                "_risk_ps": risk_ps,
                "_risk_dollars": risk_dollars,
                "_tp1_dollars": tp1_dollars,
                "_tp2_dollars": tp2_dollars,
                "_atr": atr,
                "_bar_ts": bar.ts,
            }
            signals.append(sig)
            break  # one signal per bar max

    return signals


# ── Option lookup (via Alpaca live API, same as scan_setups.py) ──

def fetch_nearest_option(symbol: str, direction: str, budget: float = 2000.0) -> dict | None:
    """Find the best OTM option via Alpaca API (same as setup scanner)."""
    try:
        from fetch_alpaca_snapshot import select_best_option
    except ImportError:
        log.warning("fetch_alpaca_snapshot not available — skipping option lookup")
        return None

    want_type = "C" if direction == "bullish" else "P"
    # min_dte=30 per ClawStreetBot rules, max_dte=90
    opt = select_best_option(symbol, want_type, min_dte=30, max_dte=90)
    if opt and opt.get("mid", 0) <= budget:
        return opt
    return None


# ── DB persistence ──

def save_signal(conn, sig: dict) -> int | None:
    """Insert into market.signal_alerts. Returns row id or None on duplicate.

    Skips insert if an alert for the same (symbol, strategy, direction, timeframe)
    fired within the last 4 hours — the table's UNIQUE constraint on
    created_at never collides because the column defaults to NOW().
    """
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT 1 FROM market.signal_alerts
            WHERE symbol = %s AND strategy = %s
              AND direction = %s AND timeframe = %s
              AND created_at > NOW() - INTERVAL '4 hours'
            LIMIT 1
        """, (sig["symbol"], sig["strategy"], sig["direction"], sig["timeframe"]))
        if cur.fetchone():
            log.info("%s: cooldown active (%s %s %s alerted within 4h), skipping",
                     sig["symbol"], sig["strategy"], sig["direction"], sig["timeframe"])
            return None

        cur.execute("""
            INSERT INTO market.signal_alerts (
                symbol, strategy, direction, status, timeframe,
                trigger_price, atr_14,
                stop_price, tp1_price, tp2_price, risk_reward,
                invalidation,
                option_symbol, option_strike, option_expiry,
                option_delta, option_theta,
                option_bid, option_ask, option_mid
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s,
                %s, %s, %s, %s,
                %s,
                %s, %s, %s,
                %s, %s,
                %s, %s, %s
            )
            ON CONFLICT (symbol, strategy, direction, timeframe, created_at)
            DO NOTHING
            RETURNING id
        """, (
            sig["symbol"], sig["strategy"], sig["direction"], sig["status"],
            sig["timeframe"],
            sig["trigger_price"], sig["atr_14"],
            sig["stop_price"], sig["tp1_price"], sig["tp2_price"],
            sig["risk_reward"],
            sig["invalidation"],
            sig.get("option_symbol"), sig.get("option_strike"),
            sig.get("option_expiry"),
            sig.get("option_delta"), sig.get("option_theta"),
            sig.get("option_bid"), sig.get("option_ask"), sig.get("option_mid"),
        ))
        row = cur.fetchone()
        conn.commit()
        return row[0] if row else None
    except Exception as e:
        log.error("save_signal error for %s: %s", sig["symbol"], e)
        conn.rollback()
        return None
    finally:
        cur.close()


# ── Main ──

def main() -> int:
    parser = argparse.ArgumentParser(description="Liquidity Sweep Scanner")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print alerts to stdout; no DB writes or Telegram sends")
    parser.add_argument("--symbols", type=str, default="",
                        help="Comma-separated symbols (overrides watchlist)")
    parser.add_argument("--no-option", action="store_true",
                        help="Skip option contract lookup")
    parser.add_argument("--option-budget", type=float, default=2000.0,
                        help="Max option premium budget (default $2000)")
    args = parser.parse_args()

    conn = get_connection()

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        symbols = get_active_symbols(conn)

    # Filter out known failure symbols
    skip = RULES["skip_symbols"]
    symbols = [s for s in symbols if s not in skip]
    log.info("Scanning %d symbols: %s", len(symbols), ", ".join(symbols))

    all_signals = []
    for symbol in symbols:
        daily = fetch_daily_bars(conn, symbol)
        bars_5m = fetch_5m_bars(conn, symbol)
        if not daily or not bars_5m:
            log.debug("%s: no data, skipping", symbol)
            continue

        signals = detect_sweep_signals(symbol, daily, bars_5m, RULES)
        if signals:
            log.info("%s: %d sweep signal(s) detected", symbol, len(signals))
            all_signals.extend(signals)

    if not all_signals:
        log.info("No liquidity sweep signals detected — staying silent.")
        conn.close()
        return 0

    log.info("Total: %d signal(s) across all symbols", len(all_signals))

    # Enrich with option data
    for sig in all_signals:
        if not args.no_option:
            opt = fetch_nearest_option(sig["symbol"], sig["direction"],
                                       args.option_budget)
            if opt:
                sig["option_symbol"] = opt["occ_symbol"]
                sig["option_strike"] = opt["strike"]
                sig["option_expiry"] = opt["expiry"]
                sig["option_delta"] = opt["delta"]
                sig["option_theta"] = opt["theta"]
                sig["option_bid"] = opt["bid"]
                sig["option_ask"] = opt["ask"]
                sig["option_mid"] = opt["mid"]
                sig["option_dte"] = opt["dte"]
            else:
                log.info("%s: no suitable option found within budget", sig["symbol"])

    # Dry-run: print a one-line summary per signal and exit.
    if args.dry_run:
        for sig in all_signals:
            opt_str = ""
            if sig.get("option_symbol"):
                opt_str = (f" | {sig['option_symbol']} @ ${sig['option_mid']:.2f}"
                           f" Δ{sig['option_delta']:.2f}")
            print(f"{sig['symbol']:6s} {sig['direction']:8s} "
                  f"@ ${sig['trigger_price']:.2f}  "
                  f"stop ${sig['stop_price']:.2f}  "
                  f"TP1 ${sig['tp1_price']:.2f}  TP2 ${sig['tp2_price']:.2f}  "
                  f"R:R {sig['risk_reward']}:1{opt_str}")
        log.info("Dry run — no DB writes.")
        conn.close()
        return 0

    # Real run: write to DB only. alert_telegram.py is the sole dispatcher
    # — it reads telegram_sent=FALSE rows and sends with the 4-button keyboard.
    for sig in all_signals:
        sig_id = save_signal(conn, sig)
        if sig_id is None:
            log.info("%s: duplicate / cooldown active, skipping", sig["symbol"])
            continue
        log.info("Saved %s sweep for %s (id=%s) — awaiting alert_telegram dispatch",
                 sig["direction"], sig["symbol"], sig_id)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())