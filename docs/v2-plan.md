# ClawStreetBot v2 — Rebuild Plan

## Why v2

v1 execution pipeline is 5,449 lines across 7 scripts (process_approved, execute_trade,
exit_monitor, reconcile_orders, reconcile_exits, alert_telegram, telegram_callback_listener).
Every fix introduced its own bugs (drawdown baseline reset 3x, exit direction mapping wrong,
redundant TradingClient creation, etc.). Patching is no longer viable.

v2 rebuilds the execution pipeline from scratch with a clean webhook-first architecture.
v1 code is archived at git tag `v1-archive`.

## What We Keep (port, not rewrite)

| Component | Reason |
|-----------|--------|
| `constants.py` | Risk thresholds, ATR math, signal TTLs — the actual rules |
| `fetch_alpaca_snapshot.py` | Live option selection, proven working |
| `snapshot_equity.py` | With peak_equity HWM fix |
| `setup_watchlist.py` | Watchlist sync |
| `ingest_alpaca_ohlcv.py` | Market data ingestion |
| `ingest_alpaca_options.py` | Options chain ingestion |
| `compute_*.py` (7 scripts) | Derived analytics (GEX, IV rank, RV, tech ind, trend, greeks filter, IV outliers) |
| `scan_setups.py` | 8-gate scanner logic (keep detection, rewrite dispatch) |
| `detect_orb.py` | ORB detection (keep logic, rewrite dispatch) |
| `detect_ema_crossover.py` / `_15m.py` | EMA cross detection |
| `detect_liquidity_sweep.py` | Sweep detection |
| `.env.*` files | All credentials |
| `docker-compose.yml` | Infrastructure (edit, don't rewrite) |
| DB tables: `market.*` | All market data tables stay |
| DB migrations 001-010 | Core schema (assets, ohlcv, options, greeks, regime, trend, etc.) |

## What We Rebuild From Scratch

| v1 Component | v2 Replacement | Notes |
|---|---|---|
| `process_approved.py` (822 lines) | `pipeline/preflight.py` | Clean gate checks, no heritage bugs |
| `execute_trade.py` (716 lines) | `pipeline/executor.py` | Single submit path, no second-chance duct tape |
| `exit_monitor.py` (834 lines) | `pipeline/exits.py` | Unified with executor |
| `reconcile_orders.py` (512 lines) | `pipeline/reconcile.py` | BUY + SELL fills in one module |
| `reconcile_exits.py` (852 lines) | ↑ merged into reconcile.py | Was separate for no good reason |
| `alert_telegram.py` (1214 lines) | `pipeline/notifier.py` | Clean formatters, HTML-safe by default |
| `telegram_callback_listener.py` (499 lines) | `pipeline/telegram_listener.py` | Keep pattern, simplify |
| `generate_signals.py` | DELETE | 87% noise (trading.signals), never sent 1 Telegram alert |
| `intraday_signal.py` | Merge into scan_setups or DELETE | Redundant with setup scanner |
| n8n cron scheduler workflows | `webhook/server.py` (FastAPI) | TV webhook replaces cron polling |
| DB migrations 015-031 (17 patches) | 1 clean migration: `035_v2_signal_pipeline.sql` | signal_alerts designed right from start |

## v2 Architecture

```
TradingView webhook (bar-close, <1s)
         │
         ▼
┌─────────────────┐
│  webhook/        │  FastAPI receiver
│    server.py     │  POST /api/signal, POST /api/exit
│    auth.py       │  Webhook secret validation
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  pipeline/       │  The core — one unified flow
│    preflight.py  │  Risk gates (drawdown, buying power, PDT, spread, TTL)
│    executor.py   │  Contract selection + Alpaca submit
│    exits.py      │  TP/SL/trailing stop decisions
│    reconcile.py  │  Fill confirmation + slippage tracking
│    notifier.py   │  Telegram alerts (entry, exit, errors)
│    risk.py       │  HWM drawdown, position limits
├─────────────────┤
│  db/             │
│    connection.py │  psycopg2 wrapper
│    schema.py     │  Table definitions, INSERT helpers
│    migration.py  │  v2 migration runner
├─────────────────┤
│  scanners/       │  Detection logic (kept from v1)
│    orb.py        │  detect_orb → write signal_alerts
│    ema_cross.py  │  detect_ema_crossover → write signal_alerts
│    ema_cross_15m│  detect_ema_crossover_15m → write signal_alerts
│    liquidity.py  │  detect_liquidity_sweep → write signal_alerts
│    setups.py     │  scan_setups → write signal_alerts
├─────────────────┤
│  config/         │
│    constants.py  │  From v1 (risk rules, ATR math, TTLs)
│    env.py        │  .env loader (shared pattern)
└─────────────────┘
```

## v2 Signal Flow (The One Protocol)

```
1. INGEST        → signal_alerts row (source='tv_webhook' or 'cron_scanner')
2. RISK GATE     → Pre-Telegram check (drawdown halt? buying power? max positions?)
                    HARD FAIL → status='skipped', one Telegram msg, done
3. NOTIFY        → Telegram alert + approval keyboard
                    Auto-approve: ORB, liquidity_sweep, ALL exits
                    Manual: ema_crossover, setup_scanner
4. CONTRACT      → Live Alpaca option selection (post-approval = fresh chains)
5. FULL PREFLIGHT→ Freshness, R:R, spread, DTE, delta, PDT, notional cap
6. EXECUTE       → Mid-price limit order to Alpaca
7. RECONCILE     → Fill confirm, slippage, position update
8. EXIT          → Same pipeline for exits (exit_long/exit_short, always auto-approved)
```

## v2 DB Schema (signal_alerts, clean design)

```sql
-- Designed for v2 from scratch, not ALTERed from v1
CREATE TABLE market.signal_alerts_v2 (
    id              SERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    source          VARCHAR(16) DEFAULT 'cron_scanner'  -- 'tv_webhook' or 'cron_scanner'
                    CHECK (source IN ('tv_webhook', 'cron_scanner')),
    strategy        VARCHAR(32) NOT NULL,               -- orb, ema_crossover, liquidity_sweep, setup_scanner
    symbol          VARCHAR(16) NOT NULL,
    direction       VARCHAR(16) NOT NULL
                    CHECK (direction IN ('bullish','bearish','exit_long','exit_short')),
    timeframe       VARCHAR(8) DEFAULT '1d',            -- 1d, 15m, 5m

    -- Entry prices (from TV webhook or scanner)
    trigger_price   DECIMAL(12,4),
    stop_price      DECIMAL(12,4),
    tp1_price       DECIMAL(12,4),
    tp2_price       DECIMAL(12,4),
    risk_reward     DECIMAL(5,2),
    atr             DECIMAL(12,4),

    -- Option contract (populated at step 4, post-approval)
    option_symbol   VARCHAR(32),
    option_strike   DECIMAL(12,4),
    option_expiry   DATE,
    option_type     VARCHAR(1) CHECK (option_type IN ('C','P')),
    option_delta    DECIMAL(5,3),
    option_bid      DECIMAL(12,4),
    option_ask      DECIMAL(12,4),
    option_mid      DECIMAL(12,4),
    spread_pct      DECIMAL(8,4),

    -- Execution
    status          VARCHAR(16) DEFAULT 'new'
                    CHECK (status IN ('new','approved','executing','filled','exited',
                                      'skipped','expired','denied','error')),
    risk_mode       VARCHAR(16) DEFAULT 'standard'
                    CHECK (risk_mode IN ('standard','conservative','aggressive')),
    user_action     VARCHAR(16),                        -- 'approved','auto_approved','denied'
    approved_at     TIMESTAMPTZ,
    executed_at     TIMESTAMPTZ,
    position_id     INTEGER REFERENCES trading.positions(id),
    alpaca_order_id VARCHAR(64),
    fill_price      DECIMAL(12,6),
    slippage_pct    DECIMAL(8,4),
    slippage_dollars DECIMAL(10,4),

    -- Exit support
    exiting_position_id INTEGER REFERENCES trading.positions(id),  -- for exit signals

    -- Telegram
    telegram_sent   BOOLEAN DEFAULT FALSE,
    telegram_msg_id BIGINT,
    error_notified_at TIMESTAMPTZ,

    -- Metadata
    composite_score DECIMAL(5,2),
    invalidation   JSONB,
    UNIQUE (symbol, strategy, direction, timeframe, created_at)
);
```

## Key Design Decisions (learned from v1 bugs)

1. **Drawdown always compares against HWM** — `MAX(peak_equity)` from equity_snapshots. Never against latest snapshot.
2. **Buying power checked before Alpaca submit** — no more "insufficient buying power" rejections after signal is already 'executing'
3. **Exits use same pipeline as entries** — exit_long/exit_short are just directions, always auto-approved
4. **Option contract selected post-approval** — fresh chains, not stale scanner-time picks
5. **One reconcile module** — BUY fills + SELL fills, not two scripts with duplicated logic
6. **HTML-safe by default** — notifier escapes all dynamic text, not patch-per-formatter
7. **`record_skip()` on EVERY fail path** — or signals get re-picked forever (v1 had 130+ duplicate alerts from missing this once)
8. **No n8n cron for execution** — FastAPI webhook for signal triggers, single background worker for reconciliation

## Build Order

1. ✅ Archive v1 (git tag `v1-archive`)
2. Create v2 branch, move v1 pipeline scripts to `archive/`
3. Build `pipeline/risk.py` — HWM drawdown, position limits
4. Build `pipeline/preflight.py` — all gates
5. Build `db/schema.py` + migration `035_v2_signal_pipeline.sql`
6. Build `webhook/server.py` — FastAPI signal receiver
7. Build `pipeline/executor.py` — contract selection + Alpaca submit
8. Build `pipeline/notifier.py` — Telegram alerts
9. Build `pipeline/telegram_listener.py` — approval callbacks
10. Build `pipeline/reconcile.py` — fill tracking
11. Build `pipeline/exits.py` — TP/SL/trailing decisions
12. Wire scanners to new pipeline
13. Pine scripts for TradingView
14. End-to-end test
15. Docs sync (CLAUDE.md, Obsidian, README)