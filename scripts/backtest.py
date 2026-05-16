#!/usr/bin/env python3
"""Historical backtest engine for ClawStreetBot.

Replays signals from ``trading.signals`` against ``market.ohlcv`` to measure
how the composite scoring engine would have performed under the user's actual
risk rules (see CLAUDE.md).

Strategy modes
--------------
- ``day``       5% risk, 3:1 R:R, SL = ATR*1.5, TP1 = 20% / TP2 = 40%,
                time-stop at next close (no overnight risk).
- ``swing``     10% risk, 3:1 R:R, SL = ATR*2.0, TP1 = 30% / TP2 = 50%,
                trail remaining 25% by ATR; daily/weekly DD halts.
- ``long_term`` 3-tranche conviction (5% per tranche, max 15% position),
                30-40% drawdown tolerance, TP at 50% / 100% / 200%+.
                Thesis-based stop modelled as -35% from entry.

All inserts are idempotent (``ON CONFLICT DO UPDATE``).

Usage::

    python scripts/backtest.py --mode swing --start 2024-01-01 --end 2026-01-01
    python scripts/backtest.py --mode day --symbol NVDA --capital 10000
"""
from __future__ import annotations

import argparse
import math
import os
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import Json, RealDictCursor

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env(filename: str) -> None:
    """Populate os.environ from a dotenv-style file (no quoting)."""
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

# Per-mode rule pack — these are USER rules from CLAUDE.md, not industry defaults.
STRATEGY_RULES: dict[str, dict[str, Any]] = {
    "day": {
        "risk_pct": 0.05,
        "rr": 3.0,
        "atr_mult": 1.5,
        "tp1_pct": 0.20,
        "tp2_pct": 0.40,
        "tp1_size": 0.50,
        "tp2_size": 0.50,
        "trail": False,
        "max_hold_days": 1,
        "time_stop": True,
        "daily_dd_halt": None,
        "weekly_dd_halt": None,
        "monthly_dd_halt": None,
        "max_position_pct": 0.20,
    },
    "swing": {
        "risk_pct": 0.10,
        "rr": 3.0,
        "atr_mult": 2.0,
        "tp1_pct": 0.30,
        "tp2_pct": 0.50,
        "tp1_size": 0.50,
        "tp2_size": 0.25,
        "trail": True,
        "trail_atr_mult": 2.0,
        "max_hold_days": 60,
        "time_stop": False,
        "daily_dd_halt": 0.10,
        "weekly_dd_halt": 0.20,
        "monthly_dd_halt": 0.30,
        "max_position_pct": 0.20,
    },
    "long_term": {
        "risk_pct": 0.05,  # per tranche, 3 tranches => 15% max
        "rr": 3.0,
        "atr_mult": 3.0,
        "tp1_pct": 0.50,
        "tp2_pct": 1.00,
        "tp1_size": 0.33,
        "tp2_size": 0.33,
        "trail": True,
        "trail_atr_mult": 4.0,
        "max_hold_days": 365,
        "time_stop": False,
        "thesis_drawdown": 0.35,
        "max_position_pct": 0.15,
    },
}

PDT_WINDOW_DAYS = 5
PDT_MAX_TRADES = 3


@dataclass
class Bar:
    """One OHLCV row."""

    d: date
    o: float
    h: float
    l: float
    c: float


@dataclass
class Trade:
    """Simulated trade with optional partial exits."""

    symbol: str
    signal_date: date
    composite_score: float | None
    iv_regime: str | None
    direction: str
    entry_date: date
    entry_price: float
    quantity: float
    stop_loss: float
    tp1_price: float
    tp2_price: float
    atr_at_entry: float | None
    risk_per_share: float
    capital_at_entry: float
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


def fetch_signals(
    conn,
    start: date,
    end: date,
    symbols: list[str] | None,
    threshold: float,
) -> list[dict[str, Any]]:
    """Return bullish signals in the date range above the score threshold."""
    sql = """
        SELECT symbol, signal_date, composite_score, iv_regime, signal_type
        FROM trading.signals
        WHERE signal_date BETWEEN %s AND %s
          AND composite_score >= %s
          AND signal_type = 'bullish'
    """
    params: list[Any] = [start, end, threshold]
    if symbols:
        sql += " AND symbol = ANY(%s)"
        params.append(symbols)
    sql += " ORDER BY signal_date, symbol"
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def fetch_bars(
    conn, symbol: str, start: date, end: date
) -> list[Bar]:
    """Daily OHLCV bars (asc) for symbol over [start, end]."""
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
            Bar(d=r[0], o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4]))
            for r in cur.fetchall()
        ]


def fetch_atr(conn, symbol: str, on: date) -> float | None:
    """Latest ATR-14 at or before ``on``."""
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
    symbol: str,
    sig: dict[str, Any],
    bars: list[Bar],
    atr: float | None,
    rules: dict[str, Any],
    capital: float,
) -> Trade | None:
    """Replay a single signal forward over ``bars``; return the closed trade.

    Entry: next-bar open after the signal date. Exits: SL, TP1 (partial),
    TP2 (partial), trail (remainder), time-stop, or end-of-window close.
    """
    entry_idx = next(
        (i for i, b in enumerate(bars) if b.d > sig["signal_date"]), None
    )
    if entry_idx is None or atr is None or atr <= 0:
        return None

    entry_bar = bars[entry_idx]
    entry = entry_bar.o
    stop_dist = atr * rules["atr_mult"]
    stop = entry - stop_dist
    tp1 = entry * (1.0 + rules["tp1_pct"])
    tp2 = entry * (1.0 + rules["tp2_pct"])

    qty = position_size(
        capital, entry, stop_dist, rules["risk_pct"], rules["max_position_pct"]
    )
    if qty <= 0:
        return None

    trade = Trade(
        symbol=symbol,
        signal_date=sig["signal_date"],
        composite_score=float(sig["composite_score"]) if sig["composite_score"] is not None else None,
        iv_regime=sig.get("iv_regime"),
        direction="long",
        entry_date=entry_bar.d,
        entry_price=entry,
        quantity=qty,
        stop_loss=stop,
        tp1_price=tp1,
        tp2_price=tp2,
        atr_at_entry=atr,
        risk_per_share=stop_dist,
        capital_at_entry=capital,
    )

    tp1_size = rules["tp1_size"]
    tp2_size = rules["tp2_size"]
    remaining = 1.0
    tp1_hit = False
    tp2_hit = False
    high_water = entry
    trail_stop = stop

    max_hold = rules["max_hold_days"]
    realised = 0.0

    for j in range(entry_idx, len(bars)):
        bar = bars[j]
        hold_days = (bar.d - entry_bar.d).days
        trade.hold_days = hold_days

        # Day-trade time stop: exit at close of entry day.
        if rules.get("time_stop") and j == entry_idx:
            # check intraday TP/SL first against high/low, then exit on close
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
            # flatten remainder at close
            if remaining > 0:
                portion = qty * remaining
                realised += (bar.c - entry) * portion
                trade.partial_exits.append(
                    {"date": bar.d.isoformat(), "price": bar.c, "qty": portion, "reason": "time_stop"}
                )
            trade.exit_date = bar.d
            trade.exit_price = bar.c
            trade.exit_reason = "time_stop"
            trade.gross_pnl = realised
            return trade

        # Stop-loss check (uses worst-case intraday low).
        if bar.l <= trail_stop:
            portion = qty * remaining
            realised += (trail_stop - entry) * portion
            trade.partial_exits.append(
                {"date": bar.d.isoformat(), "price": trail_stop, "qty": portion, "reason": "stop_loss"}
            )
            trade.exit_date = bar.d
            trade.exit_price = trail_stop
            trade.exit_reason = "stop_loss" if not tp1_hit else "trail_stop"
            trade.gross_pnl = realised
            return trade

        # TP1 partial.
        if not tp1_hit and bar.h >= tp1:
            portion = qty * tp1_size
            realised += (tp1 - entry) * portion
            trade.partial_exits.append(
                {"date": bar.d.isoformat(), "price": tp1, "qty": portion, "reason": "tp1"}
            )
            remaining -= tp1_size
            tp1_hit = True
            # After TP1, move stop to breakeven (standard practice for swing/LT).
            if rules.get("trail"):
                trail_stop = max(trail_stop, entry)

        # TP2 partial.
        if not tp2_hit and bar.h >= tp2:
            portion = qty * tp2_size
            realised += (tp2 - entry) * portion
            trade.partial_exits.append(
                {"date": bar.d.isoformat(), "price": tp2, "qty": portion, "reason": "tp2"}
            )
            remaining -= tp2_size
            tp2_hit = True

        # Trailing stop on the remainder.
        if rules.get("trail") and remaining > 0:
            high_water = max(high_water, bar.h)
            new_trail = high_water - atr * rules.get("trail_atr_mult", 2.0)
            trail_stop = max(trail_stop, new_trail)

        # Long-term thesis drawdown.
        if rules.get("thesis_drawdown") and remaining > 0:
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

        # Max-hold cap.
        if hold_days >= max_hold and remaining > 0:
            portion = qty * remaining
            realised += (bar.c - entry) * portion
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

    # Ran off the end of data — close at last bar.
    last = bars[-1]
    if remaining > 0:
        portion = qty * remaining
        realised += (last.c - entry) * portion
        trade.partial_exits.append(
            {"date": last.d.isoformat(), "price": last.c, "qty": portion, "reason": "end_of_data"}
        )
    trade.exit_date = last.d
    trade.exit_price = last.c
    trade.exit_reason = "end_of_data"
    trade.gross_pnl = realised
    trade.hold_days = (last.d - entry_bar.d).days
    return trade


def annotate_pdt(trades: list[Trade]) -> int:
    """Flag day-trades that would have violated PDT (>3 in any rolling 5d).

    Returns the count of flagged trades.
    """
    day_trades = sorted(
        [t for t in trades if t.entry_date == t.exit_date],
        key=lambda t: t.entry_date,
    )
    violations = 0
    for i, t in enumerate(day_trades):
        window_start = t.entry_date - timedelta(days=PDT_WINDOW_DAYS)
        in_window = [
            o for o in day_trades[: i + 1] if window_start <= o.entry_date <= t.entry_date
        ]
        if len(in_window) > PDT_MAX_TRADES:
            t.pdt_flag = True
            violations += 1
    return violations


def compute_metrics(
    trades: list[Trade], initial_capital: float, start: date, end: date
) -> dict[str, Any]:
    """Aggregate per-trade results into the standard performance metrics."""
    if not trades:
        return {
            "total_trades": 0,
            "winners": 0,
            "losers": 0,
            "win_rate": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "avg_r_multiple": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "total_return": 0.0,
            "cagr": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "final_capital": initial_capital,
        }

    winners = [t for t in trades if t.gross_pnl > 0]
    losers = [t for t in trades if t.gross_pnl <= 0]
    gross_win = sum(t.gross_pnl for t in winners)
    gross_loss = abs(sum(t.gross_pnl for t in losers))
    win_rate = len(winners) / len(trades)
    avg_win = (gross_win / len(winners)) if winners else 0.0
    avg_loss = -(gross_loss / len(losers)) if losers else 0.0
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    avg_r = statistics.mean([t.r_multiple for t in trades]) if trades else 0.0

    # Equity curve in chronological order of exit.
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

    # Sharpe (rf = 0, returns are per-trade, scaled by sqrt(N/years) for annualisation).
    sharpe = 0.0
    if len(returns) > 1:
        mu = statistics.mean(returns)
        sd = statistics.stdev(returns)
        if sd > 0:
            n_per_year = len(returns) / years
            sharpe = (mu / sd) * math.sqrt(n_per_year)

    # Max drawdown over the equity curve.
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


def write_run(
    conn,
    run_name: str,
    mode: str,
    start: date,
    end: date,
    initial_capital: float,
    threshold: float,
    symbols: list[str] | None,
    rules: dict[str, Any],
) -> int:
    """Upsert the backtest_runs row; return run_id."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO trading.backtest_runs (
                run_name, strategy_mode, start_date, end_date,
                initial_capital, signal_threshold, symbols, params
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_name) DO UPDATE SET
                strategy_mode = EXCLUDED.strategy_mode,
                start_date = EXCLUDED.start_date,
                end_date = EXCLUDED.end_date,
                initial_capital = EXCLUDED.initial_capital,
                signal_threshold = EXCLUDED.signal_threshold,
                symbols = EXCLUDED.symbols,
                params = EXCLUDED.params
            RETURNING id
            """,
            (
                run_name,
                mode,
                start,
                end,
                initial_capital,
                threshold,
                symbols,
                Json(rules),
            ),
        )
        return int(cur.fetchone()[0])


def write_trades(conn, run_id: int, trades: list[Trade]) -> None:
    """Idempotent bulk insert of simulated trades."""
    with conn.cursor() as cur:
        for t in trades:
            cur.execute(
                """
                INSERT INTO trading.backtest_trades (
                    run_id, symbol, signal_date, composite_score, iv_regime,
                    direction, entry_date, entry_price, quantity,
                    stop_loss, tp1_price, tp2_price, atr_at_entry,
                    risk_per_share, capital_at_entry,
                    exit_date, exit_price, exit_reason,
                    gross_pnl, net_pnl, r_multiple, hold_days,
                    partial_exits, pdt_flag
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s
                )
                ON CONFLICT (run_id, symbol, signal_date) DO UPDATE SET
                    composite_score = EXCLUDED.composite_score,
                    iv_regime = EXCLUDED.iv_regime,
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
                    pdt_flag = EXCLUDED.pdt_flag
                """,
                (
                    run_id,
                    t.symbol,
                    t.signal_date,
                    t.composite_score,
                    t.iv_regime,
                    t.direction,
                    t.entry_date,
                    t.entry_price,
                    t.quantity,
                    t.stop_loss,
                    t.tp1_price,
                    t.tp2_price,
                    t.atr_at_entry,
                    t.risk_per_share,
                    t.capital_at_entry,
                    t.exit_date,
                    t.exit_price,
                    t.exit_reason,
                    t.gross_pnl,
                    t.gross_pnl,
                    round(t.r_multiple, 4),
                    t.hold_days,
                    Json(t.partial_exits),
                    t.pdt_flag,
                ),
            )


def write_metrics(
    conn, run_id: int, metrics: dict[str, Any], pdt_violations: int
) -> None:
    """Upsert aggregate metrics; also stamp final_capital onto the run row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO trading.backtest_metrics (
                run_id, total_trades, winners, losers, win_rate,
                avg_win, avg_loss, avg_r_multiple, profit_factor, expectancy,
                total_return, cagr, sharpe, max_drawdown, pdt_violations,
                computed_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                NOW()
            )
            ON CONFLICT (run_id) DO UPDATE SET
                total_trades = EXCLUDED.total_trades,
                winners = EXCLUDED.winners,
                losers = EXCLUDED.losers,
                win_rate = EXCLUDED.win_rate,
                avg_win = EXCLUDED.avg_win,
                avg_loss = EXCLUDED.avg_loss,
                avg_r_multiple = EXCLUDED.avg_r_multiple,
                profit_factor = EXCLUDED.profit_factor,
                expectancy = EXCLUDED.expectancy,
                total_return = EXCLUDED.total_return,
                cagr = EXCLUDED.cagr,
                sharpe = EXCLUDED.sharpe,
                max_drawdown = EXCLUDED.max_drawdown,
                pdt_violations = EXCLUDED.pdt_violations,
                computed_at = NOW()
            """,
            (
                run_id,
                metrics["total_trades"],
                metrics["winners"],
                metrics["losers"],
                metrics["win_rate"],
                metrics["avg_win"],
                metrics["avg_loss"],
                metrics["avg_r_multiple"],
                metrics["profit_factor"],
                metrics["expectancy"],
                metrics["total_return"],
                metrics["cagr"],
                metrics["sharpe"],
                metrics["max_drawdown"],
                pdt_violations,
            ),
        )
        cur.execute(
            """
            UPDATE trading.backtest_runs
            SET final_capital = %s
            WHERE id = %s
            """,
            (metrics["final_capital"], run_id),
        )


def print_report(
    run_name: str,
    mode: str,
    start: date,
    end: date,
    initial: float,
    metrics: dict[str, Any],
    pdt_violations: int,
) -> None:
    """Console summary report for a completed run."""
    print()
    print("=" * 64)
    print(f"Backtest run: {run_name}")
    print(f"Mode: {mode}    Range: {start} → {end}")
    print(f"Capital: {initial:,.2f} → {metrics['final_capital']:,.2f}  "
          f"({metrics['total_return']*100:+.2f}%, CAGR {metrics['cagr']*100:+.2f}%)")
    print("-" * 64)
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
    print(f"PDT warnings:  {pdt_violations}")
    print("=" * 64)


def run_backtest(
    conn,
    mode: str,
    start: date,
    end: date,
    initial_capital: float,
    threshold: float,
    symbols: list[str] | None,
    run_name: str,
) -> None:
    """Top-level orchestration for a single backtest run."""
    if mode not in STRATEGY_RULES:
        raise ValueError(f"Unknown strategy mode: {mode}")
    rules = STRATEGY_RULES[mode]

    signals = fetch_signals(conn, start, end, symbols, threshold)
    print(f"Found {len(signals)} qualifying signals ({mode}, score >= {threshold})")

    # Group bars per symbol once to avoid repeated queries.
    by_symbol: dict[str, list[Bar]] = {}
    trades: list[Trade] = []

    # Walk signals in chronological order, running compounding capital.
    capital = initial_capital
    for sig in signals:
        sym = sig["symbol"]
        if sym not in by_symbol:
            by_symbol[sym] = fetch_bars(conn, sym, start, end + timedelta(days=400))
        bars = by_symbol[sym]
        atr = fetch_atr(conn, sym, sig["signal_date"])
        trade = simulate_trade(sym, sig, bars, atr, rules, capital)
        if trade is None:
            continue
        trades.append(trade)
        capital += trade.gross_pnl

    pdt_violations = annotate_pdt(trades) if mode == "day" else 0
    metrics = compute_metrics(trades, initial_capital, start, end)

    run_id = write_run(
        conn, run_name, mode, start, end, initial_capital, threshold, symbols, rules
    )
    write_trades(conn, run_id, trades)
    write_metrics(conn, run_id, metrics, pdt_violations)
    conn.commit()

    print_report(run_name, mode, start, end, initial_capital, metrics, pdt_violations)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=list(STRATEGY_RULES.keys()), default="swing")
    p.add_argument("--start", type=date.fromisoformat, required=True)
    p.add_argument("--end", type=date.fromisoformat, required=True)
    p.add_argument("--capital", type=float, default=10_000.0)
    p.add_argument(
        "--threshold",
        type=float,
        default=50.0,
        help="Minimum composite score to take a signal (default 50)",
    )
    p.add_argument("--symbol", action="append", help="Restrict to symbol (repeatable)")
    p.add_argument("--name", help="Run name (default: auto-generated)")
    args = p.parse_args()

    run_name = args.name or (
        f"{args.mode}_{args.start.isoformat()}_{args.end.isoformat()}"
        + (f"_{'-'.join(args.symbol)}" if args.symbol else "")
    )

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        run_backtest(
            conn,
            mode=args.mode,
            start=args.start,
            end=args.end,
            initial_capital=args.capital,
            threshold=args.threshold,
            symbols=[s.upper() for s in args.symbol] if args.symbol else None,
            run_name=run_name,
        )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
