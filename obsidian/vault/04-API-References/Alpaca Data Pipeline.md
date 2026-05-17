---
created: 2026-05-17
updated: 2026-05-17
tags: [api, alpaca, data-pipeline, mOC]
---

# Alpaca Data Pipeline

## Overview

The Alpaca Data Pipeline is the **primary data ingestion system** for ClawStreetBot, replacing the former Polygon.io-based OHLCV and options ingestion. It uses Alpaca's free-tier data APIs (IEX feed, 15-minute delayed) to fetch:

- **OHLCV bars** — 1d, 15m, and 5m timeframes
- **Options chains + greeks** — full chain snapshots with bid/ask
- **Real-time snapshots** — stock price + best filtered option at signal time

See also: [[Alpaca API]] for the full API reference, [[Polygon.io API]] for the secondary data source (fundamentals + backfill).

## Why the Migration?

| Aspect | Alpaca (Free) | Polygon ($108/mo) |
|---------|--------------|-------------------|
| OHLCV bars | ✅ Free (1d/15m/5m) | ✅ Paid |
| Options greeks | ✅ Free (snapshots) | ✅ Paid |
| Bid/ask on options | ✅ Free | ❌ Not available |
| Real-time snapshots | ✅ Free (15-min delayed) | Paid only |
| trade_count + VWAP | ✅ Included in bars | ❌ Not in bars |
| Fundamentals | ❌ | ✅ Still active |
| Flat-file backfill | ❌ | ✅ Still active |
| **Cost** | **$0/mo** | **$108/mo** |

The migration saves $108/mo on data that's free through Alpaca, while adding new capabilities (bid/ask, trade_count, VWAP, real-time snapshots) that weren't available from Polygon.

## Scripts

### `ingest_alpaca_ohlcv.py` — OHLCV Bars

Replaces `ingest_polygon_ohlcv.py`. Fetches bars for all active watchlist symbols and upserts into `market.ohlcv`.

```bash
# Full backfill (all timeframes, all symbols)
python scripts/ingest_alpaca_ohlcv.py --all-timeframes

# Incremental daily bars
python scripts/ingest_alpaca_ohlcv.py --timeframe 1d

# Single symbol
python scripts/ingest_alpaca_ohlcv.py --timeframe 15m --symbol NVDA
```

**Key features:**
- Timeframes: `1d`, `15m`, `5m`
- Includes `trade_count` and `vwap` columns (added by `017_ohlcv_alpaca_columns.sql`)
- Incremental ingestion via `market.ingest_state` (tracks last timestamp per symbol/timeframe)
- Upsert on `(asset_id, timeframe, timestamp)` to avoid duplicates

### `ingest_alpaca_options.py` — Options Chains + Greeks + Bid/Ask

Replaces `ingest_polygon_options.py`. Fetches the full option chain for each watchlist symbol and upserts contract metadata + greeks + bid/ask into `market.options` and `market.greeks`.

```bash
# All active symbols
python scripts/ingest_alpaca_options.py --all

# Single symbol
python scripts/ingest_alpaca_options.py --symbol NVDA

# With DTE range
python scripts/ingest_alpaca_options.py --symbol NVDA --min-dte 30 --max-dte 120
```

**Key features:**
- Uses Alpaca's `OptionHistoricalDataClient.get_option_chain()`
- Writes bid/ask to `market.greeks` (columns added by `018_alpaca_options_columns.sql`)
- Re-running on the same trading day overwrites the snapshot row
- Filters by DTE range (default: all available)

### `fetch_alpaca_snapshot.py` — Real-Time Snapshot

New script (no Polygon equivalent). Fetches the current stock price for a symbol along with its live option chain (greeks + bid/ask), filters to the strategy's preferred contracts (≥30 DTE, |delta| in [0.50, 0.70]), picks the best single contract, and outputs a compact JSON document.

```bash
python scripts/fetch_alpaca_snapshot.py --symbol NVDA
python scripts/fetch_alpaca_snapshot.py --symbol NVDA --type C
python scripts/fetch_alpaca_snapshot.py --symbol NVDA --max-dte 60
```

**Output example:**
```json
{
  "symbol": "NVDA",
  "price": 123.45,
  "as_of": "2026-05-16T20:31:02+00:00",
  "best_option": {
    "occ_symbol": "NVDA260619C00125000",
    "contract_type": "C",
    "strike": 125.0,
    "expiry": "2026-06-19",
    "dte": 34,
    "delta": 0.5523,
    "theta": -0.0421,
    "bid": 4.10,
    "ask": 4.25,
    "mid": 4.175,
    "iv": 0.4912
  }
}
```

**Used by:**
- `detect_ema_crossover_15m.py` — enriches 15m EMA crossover alerts with live option data
- `alert_telegram.py` — includes bid/ask/mid in the option line of Telegram alerts

### `detect_ema_crossover_15m.py` — 15m EMA Crossover Detector

Uses `fetch_alpaca_snapshot.py` to enrich 15m crossover alerts with real-time stock price and best option contract data. Runs every 15 minutes during market hours.

```bash
python scripts/detect_ema_crossover_15m.py --lookback 1
```

## Database Migrations

### `017_ohlcv_alpaca_columns.sql`

Adds Alpaca-specific bar fields to `market.ohlcv`:
- `trade_count BIGINT` — number of trades in the bar (nullable, historical Polygon rows have NULL)
- `vwap NUMERIC(18,6)` — volume-weighted average price (nullable)

### `018_alpaca_options_columns.sql`

Adds Alpaca options snapshot fields to `market.greeks`:
- `bid NUMERIC(10,4)` — option bid price (nullable)
- `ask NUMERIC(10,4)` — option ask price (nullable)

Both migrations use `ADD COLUMN IF NOT EXISTS` so they're safe to re-run.

## n8n Workflows

Three new workflows replace the old Polygon ingestion schedules:

| Old Workflow | New Workflow | Status |
|-------------|-------------|--------|
| `ohlcv_daily` | `alpaca_ohlcv_daily` | ⛔ Deactivated → ✅ Active |
| `ohlcv_intraday` | `alpaca_ohlcv_intraday` | ⛔ Deactivated → ✅ Active |
| `options_daily` | `alpaca_options_daily` | ⛔ Deactivated → ✅ Active |

The new workflows use the same cron schedules (PDT timezone) but call the Alpaca ingestion scripts instead of the Polygon ones. The `alpaca_ohlcv_intraday` workflow now runs both 15m and 5m timeframes in sequence.

See [[n8n Scheduler]] for the full workflow table and pipeline order.

## Data Source Architecture (Phase 5+)

```
┌─────────────────────────────────────────────────────────────┐
│                    Data Ingestion Pipeline                    │
├───────────────────────┬─────────────────────────────────────┤
│   Alpaca (Primary)    │    Polygon (Secondary/Legacy)       │
│                       │                                     │
│ • OHLCV (1d/15m/5m)  │ • Fundamentals (quarterly)          │
│ • Options + greeks    │ • Flat Files (S3 backfill)          │
│ • Bid/ask on greeks   │                                     │
│ • Real-time snapshots │   ⛔ OHLCV ingestion (decommissioned)│
│ • trade_count + VWAP  │   ⛔ Options ingestion (decommissioned)│
└───────────┬───────────┴──────────────┬──────────────────────┘
            │                           │
            ▼                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    PostgreSQL market.*                        │
│  ohlcv  ·  options  ·  greeks  ·  iv_rank  ·  realized_vol │
│  gex_dex  ·  technical_indicators  ·  signal_alerts          │
└─────────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────┐
│              Derived Compute Pipeline (n8n)                   │
│  derived_daily: RV → IV-rank → GEX → tech → greeks → IV    │
│  trend_daily · signals_daily · intraday_signal_5m            │
│  ema_crossover_detector · ema_crossover_15m                  │
└─────────────────────────────────────────────────────────────┘
```

## See Also

- [[Alpaca API]] — Full API reference
- [[Polygon.io API]] — Secondary data source (fundamentals, backfill)
- [[Database Architecture]] — Postgres schema details
- [[n8n Scheduler]] — All 17 workflows
- [[EMA Crossover]] — Strategy doc for the crossover detector