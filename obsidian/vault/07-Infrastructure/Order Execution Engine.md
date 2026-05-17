---
created: 2026-05-17
updated: 2026-05-19
tags: [execution, alpaca, orders, implementation-plan, mOC]
---

# Order Execution Engine — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Turn computed signals into actual Alpaca paper trades, with full Laws compliance, greeks filtering, position sizing, and risk management enforced at execution time.

**Architecture:** Python execution engine takes a signal from `trading.signals`, applies greeks filters → position sizing → order construction → submits to Alpaca paper API. All trades logged to `trading.positions` with full audit trail. Human-in-the-loop via Telegram approval for initial deployment.

**Tech Stack:** Python 3.11, `alpaca-py` SDK, `psycopg2`, Telegram Bot API (for approval flow)

---

## Why This Matters

ClawStreetBot currently has all the intelligence — signals, greeks, regime, trend, backtesting — but zero execution capability. The backtest proves the strategies work with your rules; now we need to actually place the trades. This is the bridge from "analysis engine" to "trading bot."

## Design Principles

1. **Paper-first, always** — All execution happens on Alpaca Paper until explicitly promoted to live
2. **Laws enforced in code** — Every Law of Trading is a hard check, not a soft warning
3. **Greeks gate before execution** — No order without passing IV rank, delta, theta budget checks
4. **Human-in-the-loop initially** — Telegram approval flow for first 30 days
5. **Full audit trail** — Every order, approval, rejection, and modification logged to DB
6. **Graceful degradation** — If Alpaca API is down, queue orders for retry; never lose a signal

## Execution Flow

```
Signal (trading.signals)
    │
    ▼
┌──────────────────────┐
│ 1. PRE-FLIGHT CHECKS  │
│                       │
│ • Laws compliance     │
│   - Max position 20%  │
│   - Min DTE 30        │
│   - PDT counter check │
│   - Drawdown halt?    │
│                       │
│ • Greeks filtering    │
│   - IV rank < 75%?    │
│   - Delta 0.50-0.90?  │
│   - Theta < 5%/day?   │
│                       │
│ • Market hours check  │
│   - Trading window ok?│
│   - Earnings blackout?│
└──────┬───────────────┘
       │ PASS
       ▼
┌──────────────────────┐
│ 2. CONTRACT SELECTION │
│                       │
│ • Stock vs options    │
│ • If options:         │
│   - Find contracts    │
│   - Filter by DTE≥30 │
│   - Filter by delta   │
│   - Sort by theta cost│
│   - Pick best match   │
│                       │
│ • If stock:           │
│   - Use signal price  │
│   - Calculate shares  │
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐
│ 3. POSITION SIZING   │
│                       │
│ • Get portfolio equity│
│ • Strategy risk rules: │
│   Day: 5% risk        │
│   Swing: 10% risk    │
│   LT: 5%/tranche     │
│ • ATR-based stop     │
│ • Shares = risk /    │
│   (entry - stop)     │
│ • Cap at 20% equity  │
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐
│ 4. ORDER CONSTRUCTION │
│                       │
│ • Entry order type    │
│   - Limit at signal   │
│   - Stop for breakout │
│ • Bracket: SL + TP   │
│   - SL: ATR × mult   │
│   - TP1: 30% (swing) │
│   - TP2: 50% (swing) │
│   - Trail: rest      │
│ • Time-in-force       │
│   - Day (day trade)   │
│   - GTC (swing/LT)   │
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐
│ 5. APPROVAL FLOW     │
│                       │
│ • Mode: auto/approval│
│ • Auto: submit direct│
│ • Approval:          │
│   - Send to Telegram │
│   - Wait for /approve │
│   - Timeout = cancel │
│                       │
│ FIRST 30 DAYS:       │
│ approval mode only   │
└──────┬───────────────┘
       │ APPROVED
       ▼
┌──────────────────────┐
│ 6. SUBMIT & TRACK    │
│                       │
│ • Submit to Alpaca   │
│ • Log to positions    │
│ • Set monitoring      │
│ • Track fill status   │
│ • Update on exit      │
└──────────────────────┘
```

## Implementation Tasks

### Task 1: Create `scripts/execute_trades.py`
- Main execution engine script
- Subcommands: `--signal <symbol>` (execute one), `--all` (all actionable signals today), `--status` (show pending/approved/filled), `--approve <order_id>` (approve pending order), `--cancel <order_id>`
- Mode flag: `--mode paper` (default, Alpaca paper), `--mode dry-run` (log everything, submit nothing)
- Reads from `trading.signals WHERE composite_score > threshold AND signal_date = TODAY`

### Task 2: Pre-Flight Checks Module
- `check_laws_compliance(signal, portfolio)` → returns (pass/fail, list of violations)
  - Law 3: position not > 20% equity
  - Law 5: if options, DTE >= 30
  - PDT: day trade counter < 3
  - Drawdown halt: check cumulative P&L against 10%/20%/30% thresholds
  - Earnings blackout: within 5 days of earnings → reject
- `check_greeks_filters(signal, greeks)` → returns (pass/fail, reasons)
  - IV rank < 75%
  - Delta 0.50-0.90
  - Theta budget < strategy limit (day 5%, swing 3%, LT 1%)

### Task 3: Contract Selection Module
- `select_contract(symbol, direction, strategy_type)` → returns best OCC symbol
- Query `market.options` + `market.greeks` for:
  - DTE >= 30 (Law 5)
  - Delta in strategy-appropriate range
  - Cheapest theta budget per premium
  - Sort by: theta cost → delta alignment → volume/OI
- For stock trades: no contract needed, use shares directly

### Task 4: Position Sizing Module
- `calculate_position_size(equity, entry, stop, strategy_type)` → shares + risk amount
- Uses YOUR rules (NOT defaults):
  - Day: 5% risk, ATR × 1.5 stop
  - Swing: 10% risk, ATR × 2.0 stop
  - Long-term: 5% per tranche, thesis stop
- Read ATR from `market.technical_indicators`
- Cap shares at 20% equity / entry_price (Law 3)
- Returns: shares, dollar_risk, risk_pct, equity_pct

### Task 5: Order Construction Module
- `construct_order(occ_symbol_or_stock, direction, quantity, strategy, stops)` → Alpaca order request
- Bracket orders with:
  - Entry: limit at current midpoint or stop at breakout level
  - Stop-loss: ATR-based
  - Take-profit: tiered (TP1 sell 1/3, TP2 sell 1/3, trail rest)
- Options: use Alpaca OCC format `ROOTYYMMDD[CP]STRIKE×1000`
- Stocks: simple market/limit + bracket

### Task 6: Telegram Approval Flow
- Format order for Telegram message:
  ```
  📋 ORDER PENDING APPROVAL
  BUY OKLO 45DTE Call $85 Strike
  Delta: 0.62 | Theta: $2.10 (2.1%/day)
  IV Rank: 22% (buy zone)
  Risk: $100 (10% of $1,000)
  Score: 63.5/100 moderate
  
  /approve OKLO_20260517
  /reject OKLO_20260517
  Timeout: 15 min
  ```
- Poll for `/approve` or `/reject` responses
- Default: timeout after 15 min = auto-reject (safety)

### Task 7: Order Submission & Tracking
- Submit approved orders to Alpaca Paper API
- Log every step to `trading.positions`:
  - `status`: pending_approval → approved → submitted → filled → closed
  - `entry_price`: fill price (or estimate if pending)
  - `stop_loss`, `take_profit`: calculated values
  - `opened_at`, `closed_at`: timestamps
- Add columns to positions table: `order_id` (Alpaca), `signal_id` (FK to signals), `strategy_type`, `approval_status`, `rejection_reason`

### Task 8: Exit Management Module
- `check_exits(open_positions, current_data)` → list of positions to close
- Exit triggers (per strategy type):
  - **Day trade:** flatten before market close (time stop)
  - **Swing:** stop-loss hit, TP1/TP2 reached, trend reversal, regime shift
  - **Long-term:** thesis invalidation, not price-based
- Greeks-based exits: theta budget exceeded, IV regime shift, delta erosion
- Partial exit execution: sell 1/3 at TP1, 1/3 at TP2, trail remaining

### Task 9: Create DB Migration `12_execution.sql`
```sql
-- Extend positions table for execution engine
ALTER TABLE trading.positions ADD COLUMN IF NOT EXISTS
    order_id         VARCHAR(50),          -- Alpaca order ID
    signal_id        INTEGER REFERENCES trading.signals(id),
    strategy_type    VARCHAR(20),          -- day, swing, long_term
    approval_status  VARCHAR(20) DEFAULT 'pending',  -- pending, approved, rejected, timed_out
    rejection_reason TEXT,
    asset_type       VARCHAR(10) DEFAULT 'stock',  -- stock, option
    occ_symbol       VARCHAR(30),          -- options contract
    entry_type       VARCHAR(10),          -- market, limit, stop
    exit_reason      VARCHAR(30),          -- stop_loss, take_profit_1, take_profit_2, trail, time_stop, thesis, greeks_exit
    peak_price       NUMERIC,              -- for trailing stop calc
    pdt_counted      BOOLEAN DEFAULT FALSE,  -- counts against PDT?

-- Trade journal for Obsidian sync
CREATE TABLE trading.execution_log (
    id          SERIAL PRIMARY KEY,
    position_id INTEGER REFERENCES trading.positions(id),
    event_type  VARCHAR(30) NOT NULL,  -- pre_flight, greeks_check, sized, constructed, approved, submitted, filled, partial_fill, exited, rejected, error
    event_data  JSONB,                 -- full context snapshot
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_exec_log_position ON trading.execution_log(position_id);
```

### Task 10: PDT Tracker Module
- Track day trade count in rolling 5-business-day window
- Query Alpaca for recent closing transactions
- Store counter in `trading.pdt_status` (new table or Redis key)
- Lock at 3 trades, caution at 2
- Integrate with pre-flight checks and Telegram alerts

### Task 11: Create n8n Workflow `execute_daily.json`
- Triggers after `alerts_daily` (approved orders only)
- Runs `execute_trades.py --all --mode paper`
- Mon-Fri 19:40 ET (after alerts, before market close isn't relevant since paper fills)
- For intraday: `execute_intraday.json` — runs after `intraday_signal_5m` for immediate signals

## Safety Architecture

1. **Paper-only default** — `--mode paper` is default, `--mode live` requires explicit flag + separate API key
2. **Approval mode first 30 days** — every order goes through Telegram approval
3. **Kill switch** — `execute_trades.py --cancel-all` cancels all pending orders immediately
4. **Drawdown circuit breakers** — built into pre-flight, cannot be bypassed
5. **PDT auto-lock** — enforced in code, not optional
6. **Order timeout** — unfilled GTC orders cancelled after 5 days to prevent stale fills
7. **Max daily trades** — configurable hard cap (default: 5 orders/day across all strategies)

## Verification Steps
1. `python scripts/execute_trades.py --dry-run --all` → shows what WOULD be traded without submitting
2. `python scripts/execute_trades.py --signal OKLO --mode paper` → single signal, paper mode
3. Check Alpaca Paper dashboard for submitted orders
4. Confirm position logged in `trading.positions`
5. Test Telegram approval flow: submit, approve via Telegram, confirm fill
6. Test rejection: submit, reject, confirm order NOT placed
7. Test timeout: submit, wait 15 min, confirm auto-reject
8. Test drawdown halt: simulate 10% daily loss, confirm new orders blocked

## See Also
- [[Telegram Alert System]] — approval flow + trade notifications
- [[Monitoring & Dashboards]] — position tracking and P&L visibility
- [[Greeks Strategy]] — greeks filtering logic for trade qualification
- [[Laws of Trading]] — hard constraints enforced at execution time
- [[Position Sizing]] — sizing calculations used by the engine
- [[Loss Limits]] — stop-loss, drawdown halts, time stops
- [[Alpaca API]] — order submission API reference