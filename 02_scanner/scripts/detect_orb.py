#!/usr/bin/env python3
"""ORB (Opening Range Breakout) Scanner — live detection on 5m bars.

Strategy summary:
  1. Use the FIRST 15-min candle after SESSION_OPEN ET as the Opening Range.
     ORB high = that bar's high; ORB low = that bar's low (wicks count).
  2. On 5-min bars AFTER SCANNER_ORB_START ET, detect:
       • a CLOSE above ORB high  → bullish breakout
       • a CLOSE below ORB low   → bearish breakout
     A wick through is NOT a breakout — must be a full body close.
  3. External level filter: skip if current price is within 0.5% of the
     previous trading day's high OR low. Those are liquidity magnets and
     ORB setups around them have low edge (audit per the strategy video).
  4. Entry sizing uses 5-minute ATR(14): stop = ATR×1.5, TP1 = ATR×4.5
     (3:1 R), TP2 = ATR×7.5 (5:1 R) — same as our intraday default.

Pattern matches detect_liquidity_sweep.py: write to market.signal_alerts
with strategy='orb', timeframe='5m', 4-hour cooldown dedup, no direct
Telegram send (alert_telegram.py is the sole dispatcher with the
4-button approval keyboard).

Usage:
    python detect_orb.py                  # Live: scan + DB write
    python detect_orb.py --dry-run         # Print only, no DB writes
    python detect_orb.py --symbols NVDA,AMD --dry-run
    python detect_orb.py --no-option       # Skip option lookup
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg2

log = logging.getLogger("orb")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ET = ZoneInfo("America/New_York")

# Shared imports (constants + option lookup) — load shared/ regardless of CWD.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from constants import SESSION_OPEN, SCANNER_ORB_START, is_market_day  # noqa: E402


# ── Env loading ──

def load_env(filename: str) -> None:
    path = PROJECT_ROOT / filename
    if not path.exists():
        # Inside the worker container the .env files live under /app
        alt = Path("/app") / filename
        if alt.exists():
            path = alt
        else:
            return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


load_env(".env.db")


# ── Strategy rules ──

RULES = {
    "atr_period": 14,
    "stop_mult":  1.5,
    "tp1_mult":   4.5,
    "tp2_mult":   7.5,
    "ext_level_pct":     0.005,  # skip if within 0.5% of prev day H/L
    # Maximum ORB signals to alert on per run. The watchlist can be large
    # and opening-range breakouts tend to cluster — sending 7+ alerts at
    # once is noise that drowns out the best setups. Rank by R:R and keep
    # only the top N.
    "max_signals": 3,
    # ORB is an opening-range strategy. The range forms at SCANNER_ORB_START ET and
    # breakouts that happen in the first 30-60 minutes are the ones worth
    # trading — they have momentum and volume. A "breakout" at 11 AM or
    # 2 PM is just price drifting below the morning low — not the same
    # pattern at all. Hard-stop scanning after this time.
    "cutoff_time_et": "10:30",
    # Scan ALL of today's post-SCANNER_ORB_START ET 5m bars (not just the last N).
    # alpaca_ohlcv_intraday ingests bars HOURLY at :05 PDT; the ORB scanner
    # cron runs every 5min. With a small scan window (N=3), a breakout
    # that landed in the middle of an ingestion window (say 10:30 ET)
    # would be missed: by the time the next ingestion adds it to the DB
    # (11:05 PDT), the next scanner tick (11:10 PDT) only looks at the
    # 3 most recent bars (10:55-11:05 PDT = 10:55-11:05 ET) and skips
    # right past it. Iterating the full post-SCANNER_ORB_START ET window keeps every
    # breakout in scope; the 4-hour cooldown SELECT in save_signal
    # blocks duplicate alerts so we don't re-fire on every cron tick.
    "scan_full_session": True,
    # Strategy needs liquidity. Same skips that hurt liquidity_sweep are
    # likely to hurt ORB; we keep this conservative and adjust after live
    # data.
    "skip_symbols": {"RKLB", "RDDT", "OKLO"},
}


# ── Data classes ──

@dataclass
class Bar5m:
    ts: datetime
    o: float
    h: float
    l: float
    c: float


# ── DB ──

def get_connection():
    """Connect to Postgres.

    Resolution order for each setting: .env.db file (if present) → process
    env (docker-compose populates POSTGRES_HOST=postgres etc. via env_file)
    → safe default. The file-first order matters because the worker
    container inherits POSTGRES_HOST=postgres from compose but the .env.db
    file inside the repo doesn't carry POSTGRES_HOST — so a file-only
    lookup with localhost-default fails to reach the postgres service
    container.
    """
    env_path = Path("/app/.env.db")
    if not env_path.exists():
        env_path = PROJECT_ROOT / ".env.db"
    cfg: dict[str, str] = {}
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    cfg[k.strip()] = v.strip()

    def _get(key: str, default: str | None = None) -> str | None:
        return cfg.get(key) or os.environ.get(key) or default

    return psycopg2.connect(
        host=_get("POSTGRES_HOST", "postgres"),
        port=int(_get("POSTGRES_PORT", "5432")),
        user=_get("POSTGRES_USER", "clawstreet"),
        password=_get("POSTGRES_PASSWORD", ""),
        dbname=_get("POSTGRES_DB", "clawstreet"),
    )


def get_active_symbols(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT symbol FROM market.assets WHERE active ORDER BY symbol")
        return [r[0] for r in cur.fetchall()]


def fetch_first_15m_bar_today(conn, symbol: str) -> tuple[float, float] | None:
    """Return (orb_high, orb_low) from today's SESSION_OPEN-to-ORB_END 15-min bar, or
    None if the bar isn't in market.ohlcv yet (pre-ORB or ingestion
    hasn't caught up).

    Alpaca's 15M bars are aligned to :00/:15/:30/:45 — the SESSION_OPEN ET bar
    covers SESSION_OPEN to SCANNER_ORB_START ET, so timestamp = SESSION_OPEN ET in UTC.
    """
    today_et = datetime.now(ET).date()
    bar_start_et = datetime.combine(today_et, SESSION_OPEN, tzinfo=ET)
    bar_start_utc = bar_start_et.astimezone(timezone.utc)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT o.high, o.low
              FROM market.ohlcv o
              JOIN market.assets a ON a.id = o.asset_id
             WHERE a.symbol = %s
               AND o.timeframe = '15m'
               AND o.timestamp = %s
        """, (symbol, bar_start_utc))
        row = cur.fetchone()
    if not row:
        return None
    return (float(row[0]), float(row[1]))


def fetch_5m_bars_today(conn, symbol: str, lookback_days: int = 3) -> list[Bar5m]:
    """Today's 5m bars plus a small history tail so ATR(14) has enough data.

    Returns bars sorted ascending by timestamp. ATR needs ≥14 prior bars,
    and the first 14 bars of a session don't give us that — so we pull
    3 days back, compute ATR over the rolling history, and use today's
    bars for the actual breakout detection.
    """
    today_et = datetime.now(ET).date()
    start_et = datetime.combine(today_et - timedelta(days=lookback_days), time(0, 0), tzinfo=ET)
    start_utc = start_et.astimezone(timezone.utc)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT o.timestamp, o.open, o.high, o.low, o.close
              FROM market.ohlcv o
              JOIN market.assets a ON a.id = o.asset_id
             WHERE a.symbol = %s
               AND o.timeframe = '5m'
               AND o.timestamp >= %s
             ORDER BY o.timestamp
        """, (symbol, start_utc))
        return [
            Bar5m(ts=r[0], o=float(r[1]), h=float(r[2]),
                  l=float(r[3]), c=float(r[4]))
            for r in cur.fetchall()
        ]


def fetch_prev_day_hl(conn, symbol: str) -> tuple[float | None, float | None]:
    """Previous trading day's high/low from market.ohlcv (1d). Skips
    weekends by simply taking the most recent 1d bar with timestamp <
    today's date (calendar-naive; markets are closed Sat/Sun so the most-
    recent bar IS the previous trading day)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT o.high, o.low
              FROM market.ohlcv o
              JOIN market.assets a ON a.id = o.asset_id
             WHERE a.symbol = %s
               AND o.timeframe = '1d'
               AND o.timestamp::date < (NOW() AT TIME ZONE 'America/New_York')::date
             ORDER BY o.timestamp DESC
             LIMIT 1
        """, (symbol,))
        row = cur.fetchone()
    if not row:
        return (None, None)
    return (float(row[0]), float(row[1]))


# ── ATR ──

def compute_atr_5m(bars: list[Bar5m], period: int = 14) -> list[float]:
    """Wilder ATR on 5-min bars. Returns one ATR per bar (length = len(bars)).
    Index 0 is None-equivalent (we just repeat ATR[0] backwards for the
    pre-warmup tail so callers can index by bar position). Identical
    formula to detect_liquidity_sweep.compute_atr_5m."""
    if len(bars) < 2:
        return [0.0] * len(bars)
    trs = []
    for i in range(1, len(bars)):
        tr = max(
            bars[i].h - bars[i].l,
            abs(bars[i].h - bars[i - 1].c),
            abs(bars[i].l - bars[i - 1].c),
        )
        trs.append(tr)
    if len(trs) < period:
        avg = sum(trs) / len(trs) if trs else 0.001
        return [avg] * len(bars)
    seed = sum(trs[:period]) / period
    atrs = [seed]
    for i in range(period, len(trs)):
        atrs.append((atrs[-1] * (period - 1) + trs[i]) / period)
    # Pad so result aligns with bars (one ATR per bar).
    pad = len(bars) - len(atrs)
    return [atrs[0]] * pad + atrs


# ── Detection ──

def detect_orb_signals(
    symbol: str,
    orb_high: float,
    orb_low: float,
    bars_5m: list[Bar5m],
    atrs: list[float],
    prev_day_high: float | None,
    prev_day_low: float | None,
) -> list[dict]:
    """ORB breakout detection on the last N 5m bars.

    A "breakout" is a 5m candle whose CLOSE crosses outside the ORB range.
    Wick-only crosses are ignored (the spec is explicit on this).

    External level filter: if the underlying's current price is within
    RULES['ext_level_pct'] of yesterday's high OR low, we refuse to signal
    — those levels are liquidity sweep targets and ORB setups there have
    no edge.
    """
    signals: list[dict] = []
    if not bars_5m or orb_high is None or orb_low is None:
        return signals

    # Skip if near previous day H/L (external level proximity).
    current_price = bars_5m[-1].c
    ext_pct = RULES["ext_level_pct"]
    near_pdh = (prev_day_high is not None and prev_day_high > 0
                and abs(current_price - prev_day_high) / current_price < ext_pct)
    near_pdl = (prev_day_low is not None and prev_day_low > 0
                and abs(current_price - prev_day_low) / current_price < ext_pct)
    if near_pdh or near_pdl:
        which = "high" if near_pdh else "low"
        ref = prev_day_high if near_pdh else prev_day_low
        log.info(
            "%s: current $%.2f within %.1f%% of prev day %s ($%.2f) — "
            "external level, skipping ORB",
            symbol, current_price, ext_pct * 100, which, ref,
        )
        return signals

    # Only consider bars AFTER the ORB closes (i.e. SCANNER_ORB_START ET on). Iterate
    # forward through the FULL session — record the FIRST close-beyond
    # candle in each direction. A symbol can produce up to 2 signals per
    # day (1 bullish + 1 bearish) when price whipsaws through both ORB
    # boundaries. 4-hour cooldown in save_signal handles dedup across
    # cron ticks.
    orb_end_et = SCANNER_ORB_START
    today_et = datetime.now(ET).date()
    seen_directions: set[str] = set()

    for i in range(len(bars_5m)):
        bar = bars_5m[i]
        # Bar timestamp is UTC; convert to ET for the session-time check.
        bar_et = bar.ts.astimezone(ET)
        if bar_et.date() != today_et:
            # Skip the 3-day history tail we fetched for ATR warmup.
            continue
        if bar_et.time() < orb_end_et:
            continue

        atr = atrs[i] if i < len(atrs) else 0.0
        if atr <= 0:
            continue

        direction: str | None = None
        if bar.c > orb_high and "bullish" not in seen_directions:
            direction = "bullish"
            entry = bar.c
            stop = entry - atr * RULES["stop_mult"]
            tp1  = entry + atr * RULES["tp1_mult"]
            tp2  = entry + atr * RULES["tp2_mult"]
        elif bar.c < orb_low and "bearish" not in seen_directions:
            direction = "bearish"
            entry = bar.c
            stop = entry + atr * RULES["stop_mult"]
            tp1  = entry - atr * RULES["tp1_mult"]
            tp2  = entry - atr * RULES["tp2_mult"]
        else:
            continue

        # R:R is fixed by construction (tp1_mult / stop_mult = 3.0).
        rr = round(RULES["tp1_mult"] / RULES["stop_mult"], 2)

        invalidation = {
            "orb_range_high": round(orb_high, 4),
            "orb_range_low":  round(orb_low,  4),
            "orb_range_size": round(orb_high - orb_low, 4),
            "breakout_type":  "direct",  # retest-entry is a future enhancement
            "near_prev_day_high": bool(near_pdh),
            "near_prev_day_low":  bool(near_pdl),
            "reasons": [
                f"close back through ORB {'high' if direction == 'bearish' else 'low'} = invalidation",
                "ADX < 20 = momentum failing → exit",
                "Time stop: flatten by 12:45 PDT (day mode)",
            ],
        }

        signals.append({
            "symbol": symbol,
            "strategy": "orb",
            "direction": direction,
            "status": "new",
            "timeframe": "5m",
            "trigger_price": round(entry, 4),
            "atr_14": round(atr, 4),
            "stop_price": round(stop, 4),
            "tp1_price": round(tp1, 4),
            "tp2_price": round(tp2, 4),
            "risk_reward": rr,
            "invalidation": json.dumps(invalidation),
        })

        log.info(
            "%s: %s ORB breakout — close $%.2f vs ORB(%.2f/%.2f) ATR $%.2f",
            symbol, direction.upper(), bar.c, orb_high, orb_low, atr,
        )
        seen_directions.add(direction)
        # Stop once we've recorded both directions for this symbol.
        if len(seen_directions) == 2:
            break

    return signals


# ── Option enrichment ──

def fetch_nearest_option(symbol: str, direction: str,
                        budget: float = 2000.0) -> dict | None:
    """Live Alpaca option lookup. Same pattern as detect_liquidity_sweep
    and scan_setups — uses fetch_alpaca_snapshot.select_best_option with
    the standard delta band; risk-mode re-selection happens later in
    execute_trade if the user picks Conservative / Aggressive."""
    try:
        from fetch_alpaca_snapshot import select_best_option
    except ImportError:
        log.warning("fetch_alpaca_snapshot not importable — skipping option lookup")
        return None

    want_type = "C" if direction == "bullish" else "P"
    try:
        opt = select_best_option(symbol, want_type, min_dte=30, max_dte=90)
    except Exception as e:
        log.warning("%s: option lookup failed (%s) — proceeding stock-only",
                    symbol, type(e).__name__)
        return None
    if opt and opt.get("mid", 0) <= budget:
        return opt
    return None


# ── DB persistence ──

def save_signal(conn, sig: dict) -> int | None:
    """Insert into market.signal_alerts. Returns row id or None on duplicate.

    Mirrors detect_liquidity_sweep.save_signal — explicit 4-hour cooldown
    SELECT before INSERT because the table's UNIQUE on (symbol, strategy,
    direction, timeframe, created_at) never collides (created_at defaults
    to NOW())."""
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
                option_bid, option_ask, option_mid,
                spread_pct
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s,
                %s, %s, %s, %s,
                %s,
                %s, %s, %s,
                %s, %s,
                %s, %s, %s,
                %s
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
            sig.get("spread_pct"),
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
    parser = argparse.ArgumentParser(description="ORB (Opening Range Breakout) Scanner")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print alerts to stdout; no DB writes or Telegram sends")
    parser.add_argument("--symbols", type=str, default="",
                        help="Comma-separated symbols (overrides watchlist)")
    parser.add_argument("--no-option", action="store_true",
                        help="Skip option contract lookup")
    parser.add_argument("--option-budget", type=float, default=2000.0,
                        help="Max option premium budget (default $2000)")
    args = parser.parse_args()

    # Hard-fail-early when we're outside the session window: the ORB range
    # doesn't exist until SCANNER_ORB_START ET, and breakouts after the cutoff are stale.
    # The cron may fire at 6:00 PDT (= 9:00 ET) before any ORB candle exists.
    now_et = datetime.now(ET)
    if not is_market_day(now_et.date()):
        log.info("Non-market day (%s) — market closed, exiting silent.",
                 now_et.strftime("%A"))
        return 0
    if now_et.time() < SCANNER_ORB_START:
        log.info("Pre-ORB-close (%s ET) — ORB range not yet formed, exiting silent.",
                 now_et.strftime("%H:%M"))
        return 0
    cutoff = RULES["cutoff_time_et"]
    cutoff_h, cutoff_m = int(cutoff.split(":")[0]), int(cutoff.split(":")[1])
    if now_et.time() > time(cutoff_h, cutoff_m):
        log.info("Post-ORB-cutoff (%s ET, cutoff %s) — midday breakouts are noise, exiting silent.",
                 now_et.strftime("%H:%M"), cutoff)
        return 0

    conn = get_connection()
    try:
        if args.symbols:
            symbols = [s.strip().upper() for s in args.symbols.split(",")]
        else:
            symbols = get_active_symbols(conn)

        skip = RULES["skip_symbols"]
        symbols = [s for s in symbols if s not in skip]
        log.info("Scanning %d symbols for ORB breakouts: %s",
                 len(symbols), ", ".join(symbols))

        all_signals: list[dict] = []
        for symbol in symbols:
            orb = fetch_first_15m_bar_today(conn, symbol)
            if orb is None:
                log.debug("%s: ORB 15m bar not in DB yet, skipping", symbol)
                continue
            orb_high, orb_low = orb

            bars_5m = fetch_5m_bars_today(conn, symbol)
            if not bars_5m:
                log.debug("%s: no 5m bars in DB, skipping", symbol)
                continue

            atrs = compute_atr_5m(bars_5m, period=RULES["atr_period"])
            prev_high, prev_low = fetch_prev_day_hl(conn, symbol)

            sigs = detect_orb_signals(
                symbol, orb_high, orb_low, bars_5m, atrs,
                prev_high, prev_low,
            )
            if sigs:
                log.info("%s: %d ORB signal(s) detected", symbol, len(sigs))
                all_signals.extend(sigs)

        if not all_signals:
            log.info("No ORB signals detected — staying silent.")
            return 0

        # Rank by R:R and cap at max_signals. Opening-range breakouts cluster
        # heavily — 7+ alerts at once is noise. Keep only the best setups.
        max_signals = RULES["max_signals"]
        all_signals.sort(key=lambda s: s.get("risk_reward", 0), reverse=True)
        if len(all_signals) > max_signals:
            log.info(
                "Capping ORB signals: %d detected, keeping top %d by R:R",
                len(all_signals), max_signals,
            )
            dropped = [f"{s['symbol']} ({s['direction']}, R:R {s.get('risk_reward', '?')}:1)"
                       for s in all_signals[max_signals:]]
            log.info("Dropped: %s", ", ".join(dropped))
            all_signals = all_signals[:max_signals]

        # Option enrichment.
        if not args.no_option:
            for sig in all_signals:
                opt = fetch_nearest_option(
                    sig["symbol"], sig["direction"], args.option_budget,
                )
                if opt:
                    sig["option_symbol"] = opt["occ_symbol"]
                    sig["option_strike"] = opt["strike"]
                    sig["option_expiry"] = opt["expiry"]
                    sig["option_delta"]  = opt["delta"]
                    sig["option_theta"]  = opt["theta"]
                    sig["option_bid"]    = opt["bid"]
                    sig["option_ask"]    = opt["ask"]
                    sig["option_mid"]    = opt["mid"]
                    sig["spread_pct"]    = opt.get("spread_pct")
                else:
                    log.info("%s: no suitable option within budget", sig["symbol"])

        if args.dry_run:
            for sig in all_signals:
                opt_str = ""
                if sig.get("option_symbol"):
                    opt_str = (f" | {sig['option_symbol']} @ ${sig['option_mid']:.2f}"
                               f" Δ{sig['option_delta']:.2f}")
                inv = json.loads(sig["invalidation"])
                print(
                    f"{sig['symbol']:6s} {sig['direction']:8s} "
                    f"@ ${sig['trigger_price']:.2f}  "
                    f"ORB({inv['orb_range_low']:.2f}/{inv['orb_range_high']:.2f})  "
                    f"stop ${sig['stop_price']:.2f}  "
                    f"TP1 ${sig['tp1_price']:.2f}  TP2 ${sig['tp2_price']:.2f}  "
                    f"R:R {sig['risk_reward']}:1{opt_str}"
                )
            log.info("Dry run — no DB writes.")
            return 0

        for sig in all_signals:
            sig_id = save_signal(conn, sig)
            if sig_id is None:
                continue
            log.info("Saved %s ORB for %s (id=%s) — awaiting alert_telegram dispatch",
                     sig["direction"], sig["symbol"], sig_id)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
