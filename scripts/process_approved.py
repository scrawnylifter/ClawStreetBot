#!/usr/bin/env python3
"""process_approved.py — Phase 5B dry-run order logger.

Scans market.signal_alerts WHERE status='approved' and prints the Alpaca
order it WOULD submit. **Read-only against the DB. No orders are placed.**

This is the safety-first scaffold for execute_trade.py — it lets us
validate sizing, preflight gates, and the lifecycle wiring before any
real submit_order() call is added.

Usage:
    python scripts/process_approved.py              # all 'approved' rows
    python scripts/process_approved.py --id 5        # one specific row
    python scripts/process_approved.py --limit 1     # cap how many to show
    python scripts/process_approved.py --no-alpaca   # skip live equity lookup
    python scripts/process_approved.py --verbose      # show passing checks too
"""
from __future__ import annotations

import argparse
import logging
import math
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("process_approved")

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Trading rules (mirrored from CLAUDE.md — single source of truth lives there)
# ---------------------------------------------------------------------------

RISK_PCT = {
    "day":       Decimal("0.05"),
    "swing":     Decimal("0.10"),
    "long_term": Decimal("0.05"),  # per-tranche
}

# Hard cap from Law 3: never more than 20% in a single position.
MAX_POSITION_FRACTION = Decimal("0.20")

# How much option premium loss we treat as the "stop" for sizing purposes.
# Premium-based stops are conservative: 50% loss = position size halves
# vs sizing off the underlying's stop distance.
OPTION_PREMIUM_STOP_PCT = Decimal("0.50")

# Greeks/DTE gates (CLAUDE.md → Greeks Strategy)
MIN_DTE = 30
DELTA_OK_RANGE_SWING = (Decimal("0.50"), Decimal("0.80"))  # day uses 0.70-0.80 but
DELTA_OK_RANGE_DAY   = (Decimal("0.50"), Decimal("0.80"))  # we keep one band for the dry-run; tightened later.

MIN_RR = Decimal("3.0")

# Default paper-account equity if we can't (or are told not to) query Alpaca.
DEFAULT_EQUITY = Decimal("100000")

# ---------------------------------------------------------------------------
# Env loading (matches the pattern used by other scripts in this repo)
# ---------------------------------------------------------------------------

def _read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip()
    return out


def load_env(name: str) -> dict[str, str]:
    """Find .env.<name> in either /app (docker worker) or repo root."""
    for candidate in (Path(f"/app/.env.{name}"), REPO_ROOT / f".env.{name}"):
        if candidate.exists():
            return _read_env(candidate)
    raise FileNotFoundError(f".env.{name} not found")


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def get_connection():
    # POSTGRES_HOST is set by docker-compose (=postgres) but not in .env.db, so
    # the OS env wins for host/port. The credentials always come from the file.
    cfg = load_env("db")
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST") or cfg.get("POSTGRES_HOST") or "localhost",
        port=int(os.environ.get("POSTGRES_PORT") or cfg.get("POSTGRES_PORT") or 5432),
        user=cfg.get("POSTGRES_USER") or os.environ.get("POSTGRES_USER", "clawstreet"),
        password=cfg.get("POSTGRES_PASSWORD") or os.environ.get("POSTGRES_PASSWORD", ""),
        dbname=cfg.get("POSTGRES_DB") or os.environ.get("POSTGRES_DB", "clawstreet"),
    )


def fetch_approved(conn, signal_id: int | None, limit: int) -> list[dict]:
    """Return approved (but not-yet-executed) signal_alerts rows."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if signal_id is not None:
            cur.execute(
                "SELECT * FROM market.signal_alerts WHERE id = %s",
                (signal_id,),
            )
        else:
            cur.execute(
                """SELECT * FROM market.signal_alerts
                    WHERE status = 'approved'
                      AND executed_at IS NULL
                    ORDER BY approved_at ASC NULLS LAST, id ASC
                    LIMIT %s""",
                (limit,),
            )
        return list(cur.fetchall())


# ---------------------------------------------------------------------------
# Alpaca equity (read-only)
# ---------------------------------------------------------------------------

def get_alpaca_equity() -> Decimal:
    """Query the paper account equity. Falls back to DEFAULT_EQUITY on error."""
    try:
        # Imported lazily so --no-alpaca runs work without the SDK installed.
        from alpaca.trading.client import TradingClient
    except ImportError as e:
        log.warning("alpaca-py not importable (%s); using default equity.", e)
        return DEFAULT_EQUITY

    try:
        cfg = load_env("alpaca")
        key = cfg.get("ALPACA_PAPER_API_KEY") or os.environ.get("ALPACA_PAPER_API_KEY")
        sec = cfg.get("ALPACA_PAPER_SECRET_KEY") or os.environ.get("ALPACA_PAPER_SECRET_KEY")
        if not key or not sec:
            log.warning("Missing ALPACA_PAPER_API_KEY / _SECRET_KEY; using default equity.")
            return DEFAULT_EQUITY
        client = TradingClient(api_key=key, secret_key=sec, paper=True)
        account = client.get_account()
        equity = Decimal(str(account.equity))
        log.info("Alpaca paper equity: $%s", f"{equity:,.2f}")
        return equity
    except Exception as e:
        log.warning("Alpaca equity lookup failed (%s); using default equity.", e)
        return DEFAULT_EQUITY


# ---------------------------------------------------------------------------
# Mode + sizing
# ---------------------------------------------------------------------------

def infer_trade_mode(strategy: str | None, timeframe: str | None) -> str:
    """Map (strategy, timeframe) to one of: day / swing / long_term."""
    s = (strategy or "").lower()
    t = (timeframe or "").lower()

    if s == "ema_crossover_15m" or t in ("5m", "15m"):
        return "day"
    if s == "liquidity_sweep":
        # liquidity_sweep fires on 5m + daily; treat 5m signals as day, else swing.
        return "day" if t in ("5m", "15m") else "swing"
    if s in ("ema_crossover", "setup_scanner"):
        return "swing"
    return "swing"  # safest default — slower stop, larger TP


def _to_decimal(v: Any) -> Decimal | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def size_option_position(
    equity: Decimal, mode: str, option_mid: Decimal,
) -> dict:
    """Compute contract count using premium-based stop + 20% notional cap."""
    risk_pct = RISK_PCT[mode]
    dollar_risk = (equity * risk_pct).quantize(Decimal("0.01"))
    per_contract_risk = (option_mid * OPTION_PREMIUM_STOP_PCT * Decimal("100")).quantize(Decimal("0.01"))
    if per_contract_risk <= 0:
        contracts_by_risk = 0
    else:
        contracts_by_risk = int(math.floor(dollar_risk / per_contract_risk))

    max_notional = (equity * MAX_POSITION_FRACTION).quantize(Decimal("0.01"))
    per_contract_notional = (option_mid * Decimal("100")).quantize(Decimal("0.01"))
    if per_contract_notional <= 0:
        contracts_by_notional = 0
    else:
        contracts_by_notional = int(math.floor(max_notional / per_contract_notional))

    contracts = max(0, min(contracts_by_risk, contracts_by_notional))
    notional = (Decimal(contracts) * per_contract_notional).quantize(Decimal("0.01"))
    return {
        "instrument": "option",
        "risk_pct": risk_pct,
        "dollar_risk": dollar_risk,
        "per_contract_risk": per_contract_risk,
        "per_contract_notional": per_contract_notional,
        "contracts_by_risk": contracts_by_risk,
        "contracts_by_notional": contracts_by_notional,
        "qty": contracts,
        "notional": notional,
        "notional_pct": (notional / equity * 100).quantize(Decimal("0.01")) if equity else Decimal("0"),
        "limiting_factor":
            "risk" if contracts_by_risk <= contracts_by_notional else "20%-cap",
    }


def size_stock_position(
    equity: Decimal, mode: str, entry: Decimal, stop: Decimal, direction: str,
) -> dict:
    """Fallback sizing when no option contract is attached."""
    risk_pct = RISK_PCT[mode]
    dollar_risk = (equity * risk_pct).quantize(Decimal("0.01"))
    per_share_risk = abs(entry - stop)
    if per_share_risk <= 0:
        shares_by_risk = 0
    else:
        shares_by_risk = int(math.floor(dollar_risk / per_share_risk))

    max_notional = (equity * MAX_POSITION_FRACTION).quantize(Decimal("0.01"))
    if entry <= 0:
        shares_by_notional = 0
    else:
        shares_by_notional = int(math.floor(max_notional / entry))

    shares = max(0, min(shares_by_risk, shares_by_notional))
    notional = (Decimal(shares) * entry).quantize(Decimal("0.01"))
    return {
        "instrument": "stock",
        "risk_pct": risk_pct,
        "dollar_risk": dollar_risk,
        "per_share_risk": per_share_risk.quantize(Decimal("0.01")),
        "shares_by_risk": shares_by_risk,
        "shares_by_notional": shares_by_notional,
        "qty": shares,
        "notional": notional,
        "notional_pct": (notional / equity * 100).quantize(Decimal("0.01")) if equity else Decimal("0"),
        "limiting_factor":
            "risk" if shares_by_risk <= shares_by_notional else "20%-cap",
    }


# ---------------------------------------------------------------------------
# Preflight checks (advisory only — they don't block in the dry-run)
# ---------------------------------------------------------------------------

CHECK_PASS = "PASS"
CHECK_WARN = "WARN"
CHECK_FAIL = "FAIL"
CHECK_TODO = "TODO"

GLYPH = {
    CHECK_PASS: "✓",
    CHECK_WARN: "⚠",
    CHECK_FAIL: "✗",
    CHECK_TODO: "ⓘ",
}


def preflight(signal: dict, sizing: dict, mode: str) -> list[tuple[str, str]]:
    """Return [(status, message)] entries describing each gate's verdict."""
    out: list[tuple[str, str]] = []

    # 1) R:R floor
    rr = _to_decimal(signal.get("risk_reward"))
    if rr is None:
        out.append((CHECK_WARN, "risk_reward NULL on signal — no R:R floor enforced"))
    elif rr < MIN_RR:
        out.append((CHECK_WARN, f"R:R {rr}:1 < {MIN_RR}:1 floor (CLAUDE.md)"))
    else:
        out.append((CHECK_PASS, f"R:R {rr}:1 ≥ {MIN_RR}:1"))

    # 2) Position size > 0
    qty = sizing["qty"]
    if qty <= 0:
        out.append((CHECK_FAIL,
                    f"sized {qty} {sizing['instrument']} — would skip (dollar_risk={sizing['dollar_risk']})"))
    else:
        out.append((CHECK_PASS, f"qty {qty} > 0 (limited by {sizing['limiting_factor']})"))

    # 3) 20% notional cap
    if sizing["notional_pct"] > MAX_POSITION_FRACTION * 100:
        out.append((CHECK_FAIL, f"notional {sizing['notional_pct']}% > {int(MAX_POSITION_FRACTION*100)}% cap"))
    else:
        out.append((CHECK_PASS, f"notional {sizing['notional_pct']}% ≤ {int(MAX_POSITION_FRACTION*100)}% cap"))

    # 4) Option-only checks
    if sizing["instrument"] == "option":
        # DTE
        expiry = signal.get("option_expiry")
        if isinstance(expiry, date):
            dte = (expiry - date.today()).days
            if dte < MIN_DTE:
                out.append((CHECK_WARN, f"DTE {dte} < {MIN_DTE} — Law 5 violation risk"))
            else:
                out.append((CHECK_PASS, f"DTE {dte} ≥ {MIN_DTE}"))
        else:
            out.append((CHECK_WARN, "option_expiry NULL — can't verify DTE"))

        # delta band
        delta = _to_decimal(signal.get("option_delta"))
        if delta is None:
            out.append((CHECK_WARN, "option_delta NULL — can't verify delta band"))
        else:
            lo, hi = DELTA_OK_RANGE_DAY if mode == "day" else DELTA_OK_RANGE_SWING
            d = abs(delta)
            if d < lo:
                out.append((CHECK_WARN, f"|Δ| {d} < {lo} — lottery-ticket risk"))
            elif d > hi:
                out.append((CHECK_WARN, f"|Δ| {d} > {hi} — consider shares instead"))
            else:
                out.append((CHECK_PASS, f"|Δ| {d} in [{lo}, {hi}]"))
    else:
        out.append((CHECK_WARN, "no option_symbol on signal — falling back to stock trade"))

    # 5) Things not yet built — flagged so we don't forget them.
    out.append((CHECK_TODO, "PDT 5-business-day counter not yet implemented"))
    out.append((CHECK_TODO, "drawdown halts (10% daily / 20% weekly / 30% monthly) not yet implemented"))

    return out


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _fmt_money(v: Decimal | None) -> str:
    if v is None:
        return "—"
    return f"${v:,.2f}"


def render_plan(
    signal: dict, mode: str, equity: Decimal,
    sizing: dict, checks: list[tuple[str, str]],
    verbose: bool,
) -> str:
    sid = signal["id"]
    sym = signal["symbol"]
    strat = signal["strategy"]
    direction = signal["direction"]
    tf = signal.get("timeframe") or "—"
    side = "buy" if direction == "bullish" else "sell"

    entry = _to_decimal(signal.get("trigger_price"))
    stop  = _to_decimal(signal.get("stop_price"))
    tp1   = _to_decimal(signal.get("tp1_price"))
    tp2   = _to_decimal(signal.get("tp2_price"))

    opt_sym  = signal.get("option_symbol")
    opt_mid  = _to_decimal(signal.get("option_mid"))
    opt_bid  = _to_decimal(signal.get("option_bid"))
    opt_ask  = _to_decimal(signal.get("option_ask"))
    opt_strike = _to_decimal(signal.get("option_strike"))
    opt_delta  = _to_decimal(signal.get("option_delta"))
    opt_expiry = signal.get("option_expiry")

    approved_at = signal.get("approved_at")
    approval_chat = signal.get("approval_chat_id")

    lines: list[str] = []
    bar = "─" * 64
    lines.append(bar)
    lines.append(f"Signal #{sid} — {sym} {strat} {direction} (timeframe {tf} → {mode} mode)")
    lines.append(bar)
    lines.append(
        f"Approved: {approved_at.isoformat() if approved_at else '—'} "
        f"by chat {approval_chat or '—'}"
    )
    lines.append("")
    lines.append("Trade plan (underlying)")
    lines.append(f"  Entry: {_fmt_money(entry)}   Stop: {_fmt_money(stop)}   "
                 f"TP1: {_fmt_money(tp1)}   TP2: {_fmt_money(tp2)}")
    lines.append(f"  R:R: {signal.get('risk_reward') or '—'}:1   "
                 f"Mode: {mode}   Risk %: {sizing['risk_pct']*100}%")
    lines.append(f"  Equity: {_fmt_money(equity)}   "
                 f"Risk $: {_fmt_money(sizing['dollar_risk'])}")
    lines.append("")

    if sizing["instrument"] == "option" and opt_sym:
        lines.append(f"Instrument: OPTION  {opt_sym}")
        lines.append(
            f"  Strike: {_fmt_money(opt_strike)}   Expiry: {opt_expiry or '—'}   "
            f"Δ: {opt_delta if opt_delta is not None else '—'}"
        )
        lines.append(
            f"  Mid: {_fmt_money(opt_mid)}   Bid/Ask: {_fmt_money(opt_bid)} / {_fmt_money(opt_ask)}"
        )
        lines.append("")
        lines.append("Sizing")
        lines.append(f"  Per-contract risk (50% premium stop): {_fmt_money(sizing['per_contract_risk'])}")
        lines.append(f"  By risk:           {sizing['contracts_by_risk']} contracts")
        lines.append(f"  By 20% cap:        {sizing['contracts_by_notional']} contracts")
        lines.append(f"  → Final size:      {sizing['qty']} contracts  "
                     f"(limited by {sizing['limiting_factor']})")
        lines.append(f"  Notional:          {_fmt_money(sizing['notional'])} "
                     f"({sizing['notional_pct']}% of equity)")
    else:
        lines.append("Instrument: STOCK (no option contract attached)")
        lines.append("")
        lines.append("Sizing")
        lines.append(f"  Per-share risk:    {_fmt_money(sizing.get('per_share_risk'))}")
        lines.append(f"  By risk:           {sizing['shares_by_risk']} shares")
        lines.append(f"  By 20% cap:        {sizing['shares_by_notional']} shares")
        lines.append(f"  → Final size:      {sizing['qty']} shares  "
                     f"(limited by {sizing['limiting_factor']})")
        lines.append(f"  Notional:          {_fmt_money(sizing['notional'])} "
                     f"({sizing['notional_pct']}% of equity)")

    lines.append("")
    lines.append("Preflight checks")
    for status, msg in checks:
        if not verbose and status == CHECK_PASS:
            continue
        lines.append(f"  {GLYPH[status]} {msg}")
    if not verbose:
        passed = sum(1 for s, _ in checks if s == CHECK_PASS)
        if passed:
            lines.append(f"  …and {passed} other check(s) passed (use --verbose to show)")

    lines.append("")
    lines.append("WOULD SUBMIT (Alpaca paper — NOT executed):")
    if sizing["qty"] <= 0:
        lines.append("  [SKIP] sized to 0 — nothing to submit.")
    elif sizing["instrument"] == "option" and opt_sym:
        # Crossing the spread at the ask is the realistic fill assumption for a
        # market-able limit on options paper.
        limit = opt_ask if opt_ask is not None else opt_mid
        lines.append(f"  client.submit_order(")
        lines.append(f"      symbol={opt_sym!r},")
        lines.append(f"      qty={sizing['qty']},")
        lines.append(f"      side={side!r},")
        lines.append(f"      type='limit',")
        lines.append(f"      limit_price={limit},")
        lines.append(f"      time_in_force='day',")
        lines.append(f"  )")
    else:
        lines.append(f"  client.submit_order(")
        lines.append(f"      symbol={sym!r},")
        lines.append(f"      qty={sizing['qty']},")
        lines.append(f"      side={side!r},")
        lines.append(f"      type='market',")
        lines.append(f"      time_in_force={'day' if mode == 'day' else 'gtc'!r},")
        lines.append(f"  )")

    lines.append("")
    lines.append("Followup (exit_monitor.py — not yet built):")
    if sizing["instrument"] == "option" and opt_mid:
        prem_stop = (opt_mid * OPTION_PREMIUM_STOP_PCT).quantize(Decimal("0.01"))
        prem_tp1  = (opt_mid * (Decimal("1") + OPTION_PREMIUM_STOP_PCT)).quantize(Decimal("0.01"))
        lines.append(f"  stop:  option premium ≤ {_fmt_money(prem_stop)}  (50% premium loss)")
        lines.append(f"  tp1:   option premium ≥ {_fmt_money(prem_tp1)}   (+50% premium)")
    if tp2:
        lines.append(f"  tp2:   underlying {'≥' if direction == 'bullish' else '≤'} {_fmt_money(tp2)}  → flatten remaining")
    if stop:
        lines.append(f"  invalidation: underlying {'≤' if direction == 'bullish' else '≥'} {_fmt_money(stop)}  → flatten immediately")
    if mode == "day":
        lines.append("  time stop: flatten before 12:55 PDT (day-trade rule)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_one(signal: dict, equity: Decimal, verbose: bool) -> str:
    mode = infer_trade_mode(signal.get("strategy"), signal.get("timeframe"))

    opt_mid = _to_decimal(signal.get("option_mid"))
    if opt_mid and opt_mid > 0:
        sizing = size_option_position(equity, mode, opt_mid)
    else:
        entry = _to_decimal(signal.get("trigger_price"))
        stop  = _to_decimal(signal.get("stop_price"))
        if entry is None or stop is None:
            return (f"Signal #{signal['id']} {signal['symbol']} — SKIP: "
                    f"no trigger_price/stop_price, can't size a stock fallback.")
        sizing = size_stock_position(equity, mode, entry, stop, signal["direction"])

    checks = preflight(signal, sizing, mode)
    return render_plan(signal, mode, equity, sizing, checks, verbose)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", type=int, default=None,
                        help="Process a single signal_alerts row (any status).")
    parser.add_argument("--limit", type=int, default=10,
                        help="Max rows to show (default 10). Ignored with --id.")
    parser.add_argument("--no-alpaca", action="store_true",
                        help="Skip the live Alpaca equity lookup; use the default.")
    parser.add_argument("--verbose", action="store_true",
                        help="Show passing preflight checks too.")
    args = parser.parse_args()

    equity = DEFAULT_EQUITY if args.no_alpaca else get_alpaca_equity()

    conn = get_connection()
    try:
        rows = fetch_approved(conn, args.id, args.limit)
    finally:
        conn.close()

    if not rows:
        if args.id is not None:
            print(f"No signal_alerts row with id={args.id}.")
        else:
            print("No approved signals waiting for execution.")
        return 0

    print(f"Found {len(rows)} signal(s) to plan.  Equity: ${equity:,.2f}\n")
    for r in rows:
        print(process_one(r, equity, args.verbose))
        print()

    print(f"DONE — {len(rows)} dry-run plan(s) printed. No orders submitted, "
          f"no DB rows modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
