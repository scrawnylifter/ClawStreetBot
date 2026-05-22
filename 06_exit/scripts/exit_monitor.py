#!/usr/bin/env python3
"""exit_monitor.py — Phase 5B exit decision + submission.

Walks open positions, fetches the live underlying + option quote, runs the
exit decision tree, and submits closing orders to Alpaca when a condition
fires. Mirror of execute_trade.py for the exit side.

Decision tree (first match wins; full close unless noted):

    1. underlying breached stop   → full close
    2. option premium ≤ 50% entry → full close (option positions only)
    3. trail stop (if active)     → close on breach, else raise/lower trail
                                     monotonically. Trail-active rows
                                     bypass TP1/TP2 (already past those).
    4. underlying reached tp2     → full close (day / long_term modes)
                                     OR trail-activate (swing mode)
   5. underlying reached tp1     → partial close (50%)
                                    submit a SELL for qty//2 and stamp
                                    tp1_sell_order_id + tp1_hit_at;
                                    reconcile_exits reduces positions.quantity
                                    when the partial fills.
   6. stale swing/long_term position → full close (mode=swing: 7 cal days,
                                        mode=long_term: 30 cal days,
                                        underlying within 0.5×ATR of entry)
   7. day-trade time stop        → full close (mode=day, ≥ 12:45 PDT)
   8. option DTE ≤ 1             → full close (Law 5)

Default is dry-run; --confirm submits real SELL orders on Alpaca paper.
paper=True is hard-coded.

Submitting a close stamps positions.sell_order_id + exit_submitted_at +
exit_reason. While sell_order_id IS NOT NULL the row is excluded from
subsequent runs (close in flight). A future reconcile_exits will flip
status='closed' + closed_at + realized_pnl from the fill.

Usage:
    python 06_exit/scripts/exit_monitor.py                # DRY RUN
    python 06_exit/scripts/exit_monitor.py --confirm
    python 06_exit/scripts/exit_monitor.py --id 5 --confirm
    python 06_exit/scripts/exit_monitor.py --verbose
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "shared"))
sys.path.insert(0, str(PROJECT_ROOT / "03_alert" / "scripts"))
from constants import DB_CONFIG, load_env  # noqa: E402

load_env(".env.db")
load_env(".env.alpaca")
load_env(".env.telegram")

# Reuse every helper from the dry-run logger — single source of truth for
# mode mapping, sizing, preflight, and the human-readable plan.
sys.path.insert(0, str(PROJECT_ROOT / "04_approval" / "scripts"))
import process_approved as pa  # noqa: E402

# Import shared alert_telegram for exit notifications
from alert_telegram import send_telegram_message  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("exit_monitor")

# Day-trade time stop: 12:45 PDT/PST (15 min before US equities close).
# 15-min buffer so the SELL doesn't have to fight the worst-liquidity
# window of the session and gets at least two more 5-min cron ticks
# (12:45, 12:50, 12:55) to retry if Alpaca rejects the first attempt.
# zoneinfo handles DST correctly so this works year-round.
TIME_STOP_LOCAL = time(12, 45)
EQUITIES_TZ = ZoneInfo("America/Los_Angeles")

OPTION_PREMIUM_STOP_FRACTION = Decimal("0.50")  # close if mid ≤ 50% of entry
MIN_DTE_HOLDABLE = 1                            # close if DTE ≤ this
STALE_CALENDAR_DAYS = 7                          # swing positions idle this long → close
STALE_CALENDAR_DAYS_LONG = 30                   # long_term positions idle this long → close


# ---------------------------------------------------------------------------
# Alpaca clients (lazy)
# ---------------------------------------------------------------------------

def _alpaca_keys():
    key = os.environ.get("ALPACA_PAPER_API_KEY")
    sec = os.environ.get("ALPACA_PAPER_SECRET_KEY")
    if not key or not sec:
        raise RuntimeError("Missing ALPACA_PAPER_API_KEY / _SECRET_KEY in .env.alpaca")
    return key, sec


def _trading_client():
    from alpaca.trading.client import TradingClient
    k, s = _alpaca_keys()
    return TradingClient(api_key=k, secret_key=s, paper=True)


def _stock_data_client():
    from alpaca.data.historical import StockHistoricalDataClient
    k, s = _alpaca_keys()
    return StockHistoricalDataClient(api_key=k, secret_key=s)


def _option_data_client():
    from alpaca.data.historical.option import OptionHistoricalDataClient
    k, s = _alpaca_keys()
    return OptionHistoricalDataClient(api_key=k, secret_key=s)

def _to_decimal(v) -> Decimal | None:
    if v is None or v == "":
        return None
    if isinstance(v, Decimal):
        return v
    try:
        d = Decimal(str(v))
    except Exception:
        return None
    return d


# ---------------------------------------------------------------------------
# Live quotes
# ---------------------------------------------------------------------------

def fetch_underlying_mid(stock_client, symbol: str) -> Decimal | None:
    """Returns the mid of the latest stock quote, or None on failure."""
    from alpaca.data.requests import StockLatestQuoteRequest
    try:
        req = StockLatestQuoteRequest(symbol_or_symbols=[symbol])
        result = stock_client.get_stock_latest_quote(req)
    except Exception as e:
        log.warning("stock quote fetch failed for %s: %s", symbol, e)
        return None
    q = result.get(symbol) if hasattr(result, "get") else None
    if q is None:
        return None
    bid = _to_decimal(getattr(q, "bid_price", None))
    ask = _to_decimal(getattr(q, "ask_price", None))
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    return ((bid + ask) / 2).quantize(Decimal("0.0001"))

def fetch_option_mid_and_bid(opt_client, occ_symbol: str) -> tuple[Decimal | None, Decimal | None]:
    """Returns (mid, bid). bid is what we'd cross to submit a SELL limit."""
    from alpaca.data.requests import OptionLatestQuoteRequest
    try:
        req = OptionLatestQuoteRequest(symbol_or_symbols=[occ_symbol])
        result = opt_client.get_option_latest_quote(req)
    except Exception as e:
        log.warning("option quote fetch failed for %s: %s", occ_symbol, e)
        return None, None
    q = result.get(occ_symbol) if hasattr(result, "get") else None
    if q is None:
        return None, None
    bid = _to_decimal(getattr(q, "bid_price", None))
    ask = _to_decimal(getattr(q, "ask_price", None))
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None, None
    mid = ((bid + ask) / 2).quantize(Decimal("0.0001"))
    return mid, bid


# ---------------------------------------------------------------------------
# Decision tree
# ---------------------------------------------------------------------------

# Action codes
ACTION_FULL_CLOSE     = "full_close"
ACTION_TP1_PARTIAL    = "tp1_partial"
ACTION_TRAIL_ACTIVATE = "trail_activate"  # TP2 hit in swing — start trailing
ACTION_TRAIL_UPDATE   = "trail_update"    # trail moved forward, no close
ACTION_NO_OP          = "no_op"


def _trail_distance(
    atr_14: Decimal | None,
    entry_price: Decimal | None,
    stop_loss: Decimal | None,
) -> Decimal | None:
    """Distance from underlying to trail stop.

    Primary: 2 × ATR(14) — matches the original swing SL distance prescribed
    in the trading rules.
    Fallback: |entry_price - stop_loss| from the original setup, so a
    signal that didn't store ATR still trails on something sane.
    Returns None if neither is computable; caller should then fall back to
    a regular full close on TP2 rather than trail.
    """
    if atr_14 is not None and atr_14 > 0:
        return atr_14 * Decimal("2")
    if entry_price is not None and stop_loss is not None:
        d = entry_price - stop_loss
        return d if d > 0 else -d
    return None


def _today_time_stop_utc(now_utc: datetime) -> datetime:
    """12:55 in America/Los_Angeles converted to UTC for today's date."""
    local_today = now_utc.astimezone(EQUITIES_TZ).date()
    local_stop = datetime.combine(local_today, TIME_STOP_LOCAL, tzinfo=EQUITIES_TZ)
    return local_stop.astimezone(timezone.utc)


def decide_exit(
    *,
    position: dict, signal: dict, now: datetime,
    underlying_price: Decimal | None,
    option_mid: Decimal | None,
) -> tuple[str, str, Decimal | None]:
    """Returns (action_code, reason_string, extras).

    extras is the new trail_stop_price for ACTION_TRAIL_ACTIVATE /
    ACTION_TRAIL_UPDATE; None for every other action.

    Order of checks:
      1. underlying stop          → full close
      2. option premium stop      → full close (option positions only)
      3. trail stop active        → close on breach; else update if moved.
                                     Skips TP1/TP2 — we're past those.
      4. TP2 reached              → full close (day/long_term) or
                                     trail-activate (swing)
      5. TP1 reached (one-shot)   → partial close
      6. stale swing/long_term   → full close (open > 7/30 days,
                                     underlying within 0.5×ATR of entry)
      7. day-trade time stop      → full close
      8. option DTE ≤ 1           → full close (Law 5)
    """
    direction = signal.get("direction") or "bullish"
    is_option = bool(signal.get("option_symbol"))
    entry_price = _to_decimal(position["entry_price"])

    stop = _to_decimal(signal.get("stop_price"))
    tp1  = _to_decimal(signal.get("tp1_price"))
    tp2  = _to_decimal(signal.get("tp2_price"))
    atr_14 = _to_decimal(signal.get("atr_14"))
    trail_stop = _to_decimal(position.get("trail_stop_price"))

    def crossed_against(level: Decimal | None) -> bool:
        """Did the underlying breach `level` in the position's losing direction?"""
        if level is None or underlying_price is None:
            return False
        return (underlying_price <= level) if direction == "bullish" else (underlying_price >= level)

    def crossed_for(level: Decimal | None) -> bool:
        """Did the underlying reach `level` in the position's winning direction?"""
        if level is None or underlying_price is None:
            return False
        return (underlying_price >= level) if direction == "bullish" else (underlying_price <= level)

    # 1. Invalidation: underlying breached the stop.
    if crossed_against(stop):
        return ACTION_FULL_CLOSE, f"stop: underlying {underlying_price} breached {stop}", None

    # 2. Option premium stop (only when we have an option position + live mid).
    if is_option and option_mid is not None and entry_price is not None:
        prem_floor = (entry_price * OPTION_PREMIUM_STOP_FRACTION).quantize(Decimal("0.01"))
        if option_mid <= prem_floor:
            return (ACTION_FULL_CLOSE,
                    f"premium_stop: option mid {option_mid} ≤ 50% of entry {entry_price}",
                    None)

    # 3. Trail stop (only when activated). Trail-active rows have already
    # passed TP2, so TP1/TP2 branches are bypassed below.
    if trail_stop is not None and underlying_price is not None:
        if crossed_against(trail_stop):
            return (ACTION_FULL_CLOSE,
                    f"trail_stop: underlying {underlying_price} breached {trail_stop}",
                    None)
        trail_dist = _trail_distance(atr_14, entry_price, stop)
        if trail_dist is not None and trail_dist > 0:
            if direction == "bullish":
                candidate = underlying_price - trail_dist
                if candidate > trail_stop:
                    return (ACTION_TRAIL_UPDATE,
                            f"trail_raise: {trail_stop} → {candidate}", candidate)
            else:
                candidate = underlying_price + trail_dist
                if candidate < trail_stop:
                    return (ACTION_TRAIL_UPDATE,
                            f"trail_lower: {trail_stop} → {candidate}", candidate)
        # Trail active but no breach / no update — fall through ONLY to
        # time stop + expiry, never to TP1/TP2 (we're past those).
        skip_tp = True
    else:
        skip_tp = False

    # 4. TP2 — take full profit, or activate trail in swing mode.
    if not skip_tp and crossed_for(tp2):
        mode_for_tp2 = pa.infer_trade_mode(
            signal.get("strategy"),
            signal.get("timeframe"),
            risk_mode=signal.get("risk_mode"),
        )
        if mode_for_tp2 == "swing":
            trail_dist = _trail_distance(atr_14, entry_price, stop)
            if trail_dist is not None and trail_dist > 0 and underlying_price is not None:
                initial_trail = (underlying_price - trail_dist
                                 if direction == "bullish"
                                 else underlying_price + trail_dist)
                return (ACTION_TRAIL_ACTIVATE,
                        f"tp2_trail_activate: underlying {underlying_price} "
                        f"reached {tp2}, trail @ {initial_trail}",
                        initial_trail)
            # No ATR / SL → can't trail. Fall through to full close.
        return (ACTION_FULL_CLOSE,
                f"tp2: underlying {underlying_price} reached {tp2}", None)

    # 5. TP1 — partial. Only fire once; sticky flag protects against re-entry.
    if not skip_tp and crossed_for(tp1) and position.get("tp1_hit_at") is None:
        return (ACTION_TP1_PARTIAL,
                f"tp1: underlying {underlying_price} reached {tp1}", None)

    # Compute trade mode once — used by stale-position and time-stop checks.
    mode = pa.infer_trade_mode(
        signal.get("strategy"),
        signal.get("timeframe"),
        risk_mode=signal.get("risk_mode"),
    )

    # 6. Stale position — full close if the position has been open too
    #    long without the underlying moving meaningfully away from entry.
    #    Applies to swing (7-day threshold) and long_term (30-day threshold).
    #    Day positions are handled by the time stop (step 7). Only checked
    #    when no trail stop is active (trail means we're already managing it).
    if trail_stop is None and mode in ("swing", "long_term") and entry_price is not None and atr_14 is not None:
        stale_days = STALE_CALENDAR_DAYS_LONG if mode == "long_term" else STALE_CALENDAR_DAYS
        opened_at = position.get("opened_at")
        if opened_at is not None:
            if isinstance(opened_at, datetime):
                opened_dt = opened_at
            elif isinstance(opened_at, str):
                opened_dt = datetime.fromisoformat(opened_at)
            else:
                opened_dt = None
            if opened_dt is not None:
                # Make both timezone-aware for comparison
                if opened_dt.tzinfo is None:
                    opened_dt = opened_dt.replace(tzinfo=timezone.utc)
                cal_days = (now - opened_dt).days
                if cal_days >= stale_days:
                    # Underlying has barely moved — within 0.5×ATR of entry
                    if underlying_price is not None:
                        drift = underlying_price - entry_price
                        if drift < 0:
                            drift = -drift
                        half_atr = atr_14 * Decimal("0.5")
                        if drift < half_atr:
                            return (ACTION_FULL_CLOSE,
                                    f"stale: position idle for {cal_days} days, "
                                    f"underlying within 0.5×ATR of entry "
                                    f"(drift={drift:.4f} < 0.5×ATR={half_atr:.4f})",
                                    None)

    # 7. Day-trade time stop. risk_mode='aggressive' promotes a swing setup
    # to day-mode (and 'conservative' demotes a day setup to swing) — matches
    # the inference process_approved.preflight uses for the PDT counter.
    if mode == "day":
        stop_utc = _today_time_stop_utc(now)
        if now >= stop_utc:
            return (ACTION_FULL_CLOSE,
                    f"time_stop: {now.isoformat()} ≥ 12:45 PDT", None)

    # 8. Option expiry imminent.
    if is_option:
        expiry = signal.get("option_expiry")
        if isinstance(expiry, date):
            dte = (expiry - now.astimezone(EQUITIES_TZ).date()).days
            if dte <= MIN_DTE_HOLDABLE:
                return ACTION_FULL_CLOSE, f"expiry: DTE {dte} (Law 5)", None

    return ACTION_NO_OP, "no exit condition met", None


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def get_connection():
    return psycopg2.connect(**DB_CONFIG)


# Columns selected by both fetchers. Kept in one place so the locked single-row
# fetcher and the unlocked batch fetcher (used by tests) stay in sync.
_POSITION_SELECT = """
    SELECT p.id AS position_id, p.asset_id, p.direction AS pos_direction,
           p.entry_price, p.quantity, p.stop_loss, p.take_profit,
           p.status AS pos_status, p.opened_at, p.tp1_hit_at,
           p.sell_order_id, p.exit_submitted_at, p.exit_reason,
           p.tp1_sell_order_id, p.tp1_filled_at,
           p.trail_stop_price,
           s.id AS signal_id, s.symbol, s.strategy, s.direction,
           s.timeframe, s.risk_mode, s.trigger_price, s.stop_price,
           s.tp1_price, s.tp2_price, s.atr_14,
           s.option_symbol, s.option_strike,
           s.option_expiry, s.option_delta, s.option_mid
      FROM trading.positions p
      -- INNER JOIN: a position without an originating signal_alerts row
      -- can't be safely monitored (we'd default direction to 'bullish' and
      -- invert every stop/TP check on a bearish setup). Migration 026's
      -- UNIQUE on signal_alerts.position_id guarantees 1:1, so INNER is
      -- safe — any row that would have been selected by LEFT JOIN with a
      -- NULL signal side is unreachable in the live pipeline. If an
      -- operator ever opens a position by hand without wiring up
      -- signal_alerts, INNER JOIN will skip it (correct fail-safe — they
      -- can monitor it manually).
      INNER JOIN market.signal_alerts s ON s.position_id = p.id
     WHERE p.status = 'open'
       AND p.sell_order_id IS NULL
       AND p.tp1_sell_order_id IS NULL
"""


def fetch_open_position_locked(
    conn, position_id: int | None, exclude_ids: list[int] | None = None,
) -> dict | None:
    """Acquire the next unprocessed open position with a row-level lock.

    Uses SELECT FOR UPDATE OF p SKIP LOCKED so two concurrent monitor runs
    each see different rows — eliminating the window where both could submit
    a closing SELL for the same position. The lock is held by the caller's
    transaction and released by the next commit/rollback.

    exclude_ids: positions already visited by THIS run. A HOLD outcome leaves
    the row matching the WHERE predicates, so the next iteration would
    re-select the same row in an unbounded loop. The caller passes the
    accumulated seen-set so we always advance.

    The caller must commit (or rollback) before requesting the next row,
    otherwise the lock is held longer than needed.
    """
    sql = _POSITION_SELECT
    params: list = []
    if position_id is not None:
        sql += " AND p.id = %s"
        params.append(position_id)
    if exclude_ids:
        sql += " AND p.id <> ALL(%s)"
        params.append(list(exclude_ids))
    sql += " ORDER BY p.opened_at ASC LIMIT 1 FOR UPDATE OF p SKIP LOCKED"
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchone()

def fetch_open_positions(conn, position_id: int | None, limit: int) -> list[dict]:
    """Plain (unlocked) batch fetch — retained for read-only inspection / tests.

    The live main loop uses fetch_open_position_locked instead.
    """
    sql = _POSITION_SELECT
    params: list = []
    if position_id is not None:
        sql += " AND p.id = %s"
        params.append(position_id)
    sql += " ORDER BY p.opened_at ASC LIMIT %s"
    params.append(limit)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())

def stamp_full_close(
    conn, position_id: int, sell_order_id: str, reason: str,
) -> None:
    """Record the close submission. Caller owns the transaction (so the row
    lock acquired by fetch_open_position_locked stays held through submit
    and is released on the caller's next commit)."""
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE trading.positions
                  SET sell_order_id     = %s,
                      exit_submitted_at = %s,
                      exit_reason       = %s
                WHERE id = %s""",
            (sell_order_id, now, reason, position_id),
        )

def stamp_trail_stop(
    conn, position_id: int, new_trail: Decimal,
) -> None:
    """Update trail_stop_price (activation or monotonic move). Caller owns
    the transaction.

    Activation and update use the same UPDATE — the only state that matters
    on the row is the trail price itself. decide_exit guarantees the new
    value is strictly better than the old (raise for bullish, lower for
    bearish), so this is safe to call on every tick that returns a trail
    action.
    """
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE trading.positions
                  SET trail_stop_price = %s
                WHERE id = %s""",
            (new_trail, position_id),
        )


def stamp_tp1_partial(
    conn, position_id: int, sell_order_id: str, reason: str,
) -> None:
    """Record a TP1 50% partial SELL submission.

    Sets tp1_hit_at (sticky — TP1 only fires once) and tp1_sell_order_id
    (the partial close in flight). exit_monitor skips rows with a pending
    partial; reconcile_exits polls Alpaca for the fill and, when filled,
    reduces positions.quantity and clears tp1_sell_order_id.

    Caller owns the transaction.
    """
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE trading.positions
                  SET tp1_hit_at         = %s,
                      tp1_sell_order_id  = %s,
                      exit_submitted_at  = %s,
                      exit_reason        = %s
                WHERE id = %s""",
            (now, sell_order_id, now, reason, position_id),
        )


# ---------------------------------------------------------------------------
# Close-order submission
# ---------------------------------------------------------------------------

def submit_close(client, row: dict, qty: Decimal, reason: str) -> dict:
    """Submit a SELL (or BUY-to-cover) for `qty` of the position. Raises on failure.

    `reason` is the short exit reason (e.g. 'stop', 'tp1_partial', 'tp2',
    'time_stop'). It's baked into a deterministic client_order_id so
    a process crash between Alpaca submit and DB commit doesn't produce
    a duplicate SELL on the next monitor pass — Alpaca rejects duplicate
    client_order_ids while the original is still in-flight.
    """
    from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    is_option = bool(row.get("option_symbol"))
    pos_direction = row["pos_direction"]  # 'long' or 'short' on the position row

    # Options are always 'long' in our books → SELL to close.
    # Stocks long → SELL. Stocks short → BUY to cover.
    if pos_direction == "long":
        side = OrderSide.SELL
    else:
        side = OrderSide.BUY

    # Deterministic id: one Alpaca order per (position, exit reason). After an
    # Alpaca-side terminal status (rejected/canceled/etc) the same id can be
    # reused — Alpaca enforces uniqueness only for currently-open orders.
    client_order_id = f"csb-exit-{row['position_id']}-{reason}"

    if is_option:
        sym = row["option_symbol"]
        # Cross the spread at the bid to get out — slippage is the cost of
        # certainty. Fall back to the mid if no live bid.
        bid = None
        try:
            opt_client = _option_data_client()
            _, bid = fetch_option_mid_and_bid(opt_client, sym)
        except Exception:
            bid = None
        limit_price = bid if (bid is not None and bid > 0) else None
        if limit_price is None:
            # Last resort: use the stored signal option_mid as the limit.
            limit_price = _to_decimal(row.get("option_mid")) or Decimal("0.01")
        req = LimitOrderRequest(
            symbol=sym,
            qty=int(qty),
            side=side,
            time_in_force=TimeInForce.DAY,
            limit_price=float(limit_price),
            client_order_id=client_order_id,
        )
        order = client.submit_order(req)
        return {
            "order_id": str(order.id),
            "submitted_price": Decimal(str(limit_price)),
            "order_type": "limit",
            "symbol": sym,
        }

    # Stock close — market order. Liquidity makes the limit dance unnecessary.
    sym = row["symbol"]

    # H7: stock entries are submitted as BRACKET orders by execute_trade,
    # so Alpaca is holding two child OCO legs (stop_loss + take_profit).
    # If we submit our own SELL while those legs are open, the parent
    # position would be over-sold (or, more likely, Alpaca rejects with
    # 'cannot exceed position qty'). Cancel open orders for this position
    # first so our exit_monitor SELL is the only thing going through.
    # Pass position_id to only cancel orders belonging to THIS position —
    # not an unrelated bracket on the same symbol.
    _cancel_open_orders_for_symbol(client, sym, position_id=row.get("position_id"))

    req = MarketOrderRequest(
        symbol=sym, qty=int(qty), side=side, time_in_force=TimeInForce.DAY,
        client_order_id=client_order_id,
    )
    order = client.submit_order(req)
    return {
        "order_id": str(order.id),
        "submitted_price": None,
        "order_type": "market",
        "symbol": sym,
    }


def _cancel_open_orders_for_symbol(client, symbol: str, position_id: int | None = None) -> int:
    """Cancel open orders for `symbol` that belong to a specific position.

    If position_id is provided, only cancels orders whose client_order_id
    matches that position (csb-entry-{id} or csb-exit-{id}-*). This prevents
    cancelling an unrelated bracket order for a different position on the same
    symbol (e.g., day-trade NVDA exit cancelling swing NVDA's stop).

    If position_id is None, falls back to cancelling ALL open orders for the
    symbol (legacy behavior for stock bracket legs that don't carry position_id).

    Returns the count of orders that were cancelled."""
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        req = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol], limit=50)
        orders = client.get_orders(filter=req)
    except Exception:
        log.exception("_cancel_open_orders_for_symbol: list failed for %s; "
                      "proceeding with close anyway", symbol)
        return 0

    cancelled = 0
    for o in orders:
        oid = getattr(o, "id", None)
        if oid is None:
            continue
        cid = getattr(o, "client_order_id", "") or ""
        # If position_id provided, only cancel orders that belong to this position
        if position_id is not None:
            pid_str = str(position_id)
            if not (cid.startswith(f"csb-entry-{pid_str}") or
                    cid.startswith(f"csb-exit-{pid_str}")):
                log.info("_cancel_open_orders_for_symbol: skipping %s (cid=%s) — "
                          "belongs to different position", oid, cid)
                continue
        try:
            client.cancel_order_by_id(oid)
            cancelled += 1
        except Exception:
            log.warning("_cancel_open_orders_for_symbol: cancel failed for "
                        "order_id=%s symbol=%s (continuing)", oid, symbol)
    if cancelled:
        log.info("Cancelled %d open order(s) for %s before exit_monitor close",
                 cancelled, symbol)
    return cancelled


# ---------------------------------------------------------------------------
# Per-row processing
# ---------------------------------------------------------------------------

def process_one(
    conn, trading_client, stock_client, opt_client,
    row: dict, dry_run: bool, verbose: bool, now: datetime,
) -> str:
    pid = row["position_id"]
    sym = row["symbol"]
    qty_remaining = _to_decimal(row["quantity"])
    if qty_remaining is None or qty_remaining <= 0:
        return f"position #{pid} {sym} SKIP — no quantity"

    # Live quotes (option only when applicable).
    underlying = fetch_underlying_mid(stock_client, sym) if sym else None
    option_mid = None
    if row.get("option_symbol"):
        option_mid, _ = fetch_option_mid_and_bid(opt_client, row["option_symbol"])

    action, reason, extras = decide_exit(
        position=row, signal=row, now=now,
        underlying_price=underlying, option_mid=option_mid,
    )

    quote_str = f"u={underlying} opt={option_mid}"
    if action == ACTION_NO_OP:
        if verbose:
            log.info("#%s %s HOLD — %s (%s)", pid, sym, reason, quote_str)
        return f"position #{pid} {sym} HOLD — {reason} [{quote_str}]"

    if action in (ACTION_TRAIL_ACTIVATE, ACTION_TRAIL_UPDATE):
        # Trail moves are DB-only — no Alpaca submission until the trail is
        # finally breached (which fires ACTION_FULL_CLOSE with reason=trail_stop).
        new_trail = extras  # decide_exit guarantees non-null for trail actions
        if new_trail is None:
            # Defensive: shouldn't happen, but don't double-fault.
            return (f"position #{pid} {sym} HOLD — trail action with no value "
                    f"({reason}) [{quote_str}]")
        if dry_run:
            return (f"position #{pid} {sym} DRY-RUN would {action} → "
                    f"trail={new_trail} [{quote_str}]")
        stamp_trail_stop(conn, pid, new_trail)
        verb = "TRAIL ACTIVATE" if action == ACTION_TRAIL_ACTIVATE else "TRAIL UPDATE"
        log.info("#%s %s %s — trail_stop=%s reason=%s",
                 pid, sym, verb, new_trail, reason)
        return (f"position #{pid} {sym} {verb} — trail_stop={new_trail} "
                f"reason={reason} [{quote_str}]")

    if action == ACTION_TP1_PARTIAL:
        # Close 50% of the remaining quantity. Integer floor — if there's only
        # 1 contract/share left there's nothing meaningful to partial-close, so
        # we just stamp tp1_hit_at and wait for TP2 / stop / time to flatten.
        partial_qty = qty_remaining // Decimal("2") if qty_remaining >= 2 else Decimal("0")
        if partial_qty <= 0:
            msg = (f"position #{pid} {sym} TP1 hit but qty_remaining={qty_remaining}"
                   f" — partial skipped (will full-close at TP2)")
            if dry_run:
                return f"position #{pid} {sym} DRY-RUN {msg}"
            # Stamp tp1_hit_at without a sell order so TP1 won't re-fire and
            # exit_monitor still picks the row up (tp1_sell_order_id stays NULL).
            now_ts = datetime.now(timezone.utc)
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE trading.positions
                          SET tp1_hit_at = %s,
                              exit_reason = COALESCE(exit_reason, 'tp1_skipped_qty1')
                        WHERE id = %s""",
                    (now_ts, pid),
                )
            log.info(msg)
            return msg

        if dry_run:
            return (f"position #{pid} {sym} DRY-RUN would partial-close "
                    f"x{partial_qty} (TP1) — {reason} [{quote_str}]")

        try:
            result = submit_close(trading_client, row, partial_qty, "tp1_partial")
        except Exception as e:
            log.exception("position #%s TP1 partial submission failed", pid)
            return f"position #{pid} {sym} ERROR — TP1 partial: {type(e).__name__}: {e}"

        stamp_tp1_partial(conn, pid, result["order_id"], "tp1_partial")
        price_str = (f" @ ${result['submitted_price']}"
                     if result["submitted_price"] is not None else "")
        log.info("#%s %s TP1 PARTIAL — %s x%s%s order_id=%s",
                 pid, sym, result["order_type"], partial_qty, price_str,
                 result["order_id"])
        return (f"position #{pid} {sym} TP1 PARTIAL — {result['order_type']} "
                f"x{partial_qty}{price_str} order_id={result['order_id']}")

    # Full close.
    short_reason = reason.split(":", 1)[0]  # "stop" / "tp2" / "premium_stop" / ...
    if dry_run:
        return (f"position #{pid} {sym} DRY-RUN would close ({short_reason}) — "
                f"{reason} [{quote_str}]")

    try:
        result = submit_close(trading_client, row, qty_remaining, short_reason)
    except Exception as e:
        log.exception("position #%s close submission failed", pid)
        return f"position #{pid} {sym} ERROR — {type(e).__name__}: {e}"

    stamp_full_close(conn, pid, result["order_id"], short_reason)
    price_str = (f" @ ${result['submitted_price']}"
                 if result["submitted_price"] is not None else "")
    log.info("#%s %s CLOSED — %s x%s%s order_id=%s reason=%s",
             pid, sym, result["order_type"], qty_remaining, price_str,
             result["order_id"], reason)
    return (f"position #{pid} {sym} CLOSING — {result['order_type']} "
            f"x{qty_remaining}{price_str} order_id={result['order_id']} "
            f"reason={short_reason}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true",
                        help="Actually submit closes. Without this: dry-run.")
    parser.add_argument("--id", type=int, default=None,
                        help="Monitor a single position id.")
    parser.add_argument("--limit", type=int, default=50,
                        help="Max positions per run (default 50).")
    parser.add_argument("--verbose", action="store_true",
                        help="Print HOLD rows too (default suppresses them).")
    args = parser.parse_args()

    conn = get_connection()

    try:
        trading_client = _trading_client() if args.confirm else None
        stock_client = _stock_data_client()
        opt_client = _option_data_client()
    except Exception as e:
        log.error("Could not build Alpaca clients: %s", e)
        conn.close()
        return 2

    now = datetime.now(timezone.utc)
    results: list[str] = []
    started_logged = False
    # Positions already visited in THIS run. A HOLD outcome doesn't mutate
    # the row, so without this set the same row would be re-fetched on the
    # next iteration in an unbounded loop (capped only by --limit).
    seen_ids: list[int] = []

    # Pull rows one at a time with SELECT FOR UPDATE SKIP LOCKED so two
    # concurrent monitor runs each see different rows. The lock is held by
    # this transaction through the decision + submit + stamp, and released
    # by our commit at the end of each iteration. That eliminates the
    # double-SELL race that existed when fetch+iterate did a plain SELECT.
    try:
        while len(results) < args.limit:
            try:
                row = fetch_open_position_locked(conn, args.id, seen_ids)
            except Exception:
                log.exception("Lock fetch failed; aborting run")
                conn.rollback()
                break

            if row is None:
                # No more unlocked rows. Could be: nothing to do, or every
                # remaining row is held by another monitor instance.
                conn.commit()
                break

            if not started_logged:
                log.info("Monitoring open positions%s",
                         " (DRY RUN)" if not args.confirm else "")
                started_logged = True

            seen_ids.append(row["position_id"])
            try:
                result = process_one(
                    conn, trading_client, stock_client, opt_client,
                    row, dry_run=not args.confirm, verbose=args.verbose, now=now,
                )
                # Commit releases the row lock (and persists any stamp_*
                # UPDATE that ran inside process_one).
                conn.commit()
                results.append(result)
            except Exception:
                log.exception("Unhandled error monitoring position #%s",
                              row["position_id"])
                results.append(f"position #{row['position_id']} ERROR — internal")
                try:
                    conn.rollback()
                except Exception:
                    pass

        if not results:
            if args.id is not None:
                print(f"No open position with id={args.id} (none unlocked).")
            else:
                print("No open positions to monitor.")
            return 0
    finally:
        conn.close()

    print()
    for line in results:
        if "HOLD" in line and not args.verbose:
            continue
        print(line)
    closing = sum(1 for line in results if "CLOSING" in line)
    tp1     = sum(1 for line in results if "TP1 PARTIAL" in line)
    errored = sum(1 for line in results if "ERROR" in line)
    held    = sum(1 for line in results if "HOLD" in line)
    print(f"\nDONE — closing={closing}, tp1_partial={tp1}, "
          f"error={errored}, hold={held}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
