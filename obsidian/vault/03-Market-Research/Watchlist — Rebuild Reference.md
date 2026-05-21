---
created: 2026-05-20
updated: 2026-05-20
tags: [watchlist, rebuild, mOC]
---

# Watchlist — Rebuild Reference

Baseline snapshot for the full rebuild. This page documents **what we have now** and **what we're scanning/searching for**, so we can rebuild piece by piece later.

---

## Current Watchlist (16 symbols)

Source of truth: `config/watchlist.yml` → synced to `market.assets` + Alpaca watchlist via `scripts/setup_watchlist.py`

All 16 are `active=true`, `backfill_status=complete`:

| Symbol | Sector | Industry | Notes |
|--------|--------|----------|-------|
| NVDA | Technology | Semiconductors | AI accelerators — core |
| AMD | Technology | Semiconductors | |
| MU | Technology | Semiconductors | |
| WDC | Technology | Data Storage | |
| STX | Technology | Data Storage | |
| APLD | Technology | Data Centers / HPC | |
| IREN | Technology | Bitcoin Mining / HPC | |
| NBIS | Technology | AI Cloud | |
| CIFR | Technology | Bitcoin Mining | |
| RDDT | Communication Services | Internet Content | |
| SERV | Technology | Robotics | |
| RKLB | Industrials | Aerospace & Defense | |
| ASTS | Communication Services | Space / Satellite | |
| OKLO | Utilities | Nuclear / SMR | |
| NVO | Healthcare | Pharmaceuticals | |
| SPY | ETF | Broad Market | Market regime reference — SPY 50/200 SMA + breadth |

### Sector Concentration

- **AI / Semiconductors:** NVDA, AMD, MU (3)
- **Data Storage:** WDC, STX (2)
- **Data Center / Cloud:** APLD, IREN, NBIS, CIFR (4) — ⚠️ 25% of watchlist
- **Space / Defense:** RKLB, ASTS (2)
- **Nuclear Energy:** OKLO (1)
- **Healthcare:** NVO (1)
- **Robotics:** SERV (1)
- **Social / Tech:** RDDT (1)
- **ETF (regime):** SPY (1)

⚠️ **~40% data center / AI infrastructure** — noted as concentration risk in [[Correlation Risk]]

---

## What We Scan / Search For

### 1. Swing Setup Scanner (`scan_setups.py`)
**Schedule:** Every 15 min during market hours
**8-Gate Filter for BUY signals:**

| Gate | Criterion | Source |
|------|-----------|--------|
| 1 | Trend direction (EMA 9/21) | `market.trend_status` |
| 2 | ADX ≥ 20 (trend strength) | `market.technical_indicators` |
| 3 | RSI not overbought (< 70) | `market.technical_indicators` |
| 4 | IV Rank < 50 (not overpaying) | `market.iv_rank` |
| 5 | IV > RV (premium > realized) | `market.realized_vol` |
| 6 | Option premium reasonable (< 5% of underlying) | `market.greeks` |
| 7 | DTE 30–90 (not too short, not too far) | `market.options` |
| 8 | R:R ≥ 3:1 (ATR × 6 TP1, ATR × 2 stop) | Calculated from `market.ohlcv` + ATR |

**Universe:** All active symbols in `market.assets` WHERE `asset_type='stock'` and `symbol != 'SPY'`

### 2. EMA Crossover — Daily (`detect_ema_crossover.py`)
**Schedule:** Once daily after close
**Triggers:** 9/21 EMA cross on daily bars
**Regime filter:** Bullish signals only in bull regime, bearish signals only in bear regime (currently: bull → bearish suppressed)
**Universe:** All active watchlist stocks (minus SPY)

### 3. EMA Crossover — 15m (`detect_ema_crossover_15m.py`)
**Schedule:** Every 15 min during market hours
**Triggers:** 9/21 EMA cross on 15-min bars + real-time snapshot
**Regime filter:** Same as daily — contra-regime suppressed
**Universe:** All active watchlist stocks (minus SPY)

### 4. ORB — Opening Range Breakout (`detect_orb.py`)
**Schedule:** Every 5 min for first 90 min of session
**Triggers:** 5m bar breaks above/below opening range (first 30 min)
**Regime filter:** None — regime-agnostic
**Universe:** All active watchlist stocks (minus SPY)

### 5. Liquidity Sweep (`detect_liquidity_sweep.py`)
**Schedule:** Every 5 min during market hours
**Triggers:** Price sweeps beyond recent 5m high/low then reverses
**Regime filter:** None — regime-agnostic
**Universe:** All active watchlist stocks (minus SPY)

### 6. Market Regime (`regime` table)
**Source:** SPY 50/200 SMA crossover + VIX + breadth
**Only SPY** — determines bull/bear/transition for the whole watchlist
**Used by:** EMA crossover strategies (signal suppression), composite scoring weights

---

## Data Pipeline Per Symbol

Each watchlist symbol gets ingested through:

1. **OHLCV bars** — daily, 15m, 5m → `market.ohlcv` (Alpaca `StockHistoricalDataClient`)
2. **Options chains** — full chain → `market.options` + `market.greeks` (Alpaca `OptionHistoricalDataClient`)
3. **Technical indicators** — EMA, RSI, MACD, ATR, VWAP, Bollinger → `market.technical_indicators`
4. **IV rank** — percentile per symbol/date → `market.iv_rank`
5. **Realized vol** — 20d/5d RV + IV-RV spread → `market.realized_vol`
6. **GEX/DEX** — gamma exposure per strike/expiry → `market.gex_dex`
7. **Trend status** — multi-timeframe direction + strength → `market.trend_status`
8. **Fundamentals** — quarterly financials → `market.fundamentals` (Polygon)

---

## Key Infrastructure

| Component | Purpose |
|-----------|---------|
| `config/watchlist.yml` | Source of truth — YAML list of symbols + metadata |
| `scripts/setup_watchlist.py` | Syncs YAML → Alpaca watchlist + `market.assets` (add/deactivate/reactivate) |
| `market.assets` | DB registry with lifecycle: `active`, `added_at`, `deactivated_at`, `backfill_status` |
| `market.regime` | SPY-based bull/bear/transition classification |
| `market.signal_alerts` | Strategy alerts with entry/exit plans + approval lifecycle |

---

## Known Issues (as of rebuild start)

- ⚠️ **Concentration risk:** ~40% of watchlist is data center / AI infrastructure
- ⚠️ **Only 1 healthcare, 1 pharma, 0 financials, 0 consumer, 0 energy** — narrow sector coverage
- ⚠️ **Bearish signals suppressed** — EMA crossovers block contra-regime signals (decision pending)
- ⚠️ **SPY-only regime** — single ETF determines entire market regime
- ⚠️ **No automated symbol discovery** — watchlist is manually curated, no scanner to add/remove based on volume/IV criteria

---

## Rebuild Principles

1. **YAML is source of truth** — any symbol change → edit `watchlist.yml` → `setup_watchlist.py`
2. **Soft deactivation** — removing a symbol sets `active=false`, historical data retained
3. **Backfill required** — new symbols must complete ingestion before scanners will see them
4. **Scanners operate on `market.assets WHERE active=true`** — they don't hardcode symbols

---

## See Also

- [[Watchlist]] — Current holdings table
- [[Correlation Risk]] — Sector concentration analysis
- [[Alpaca Data Pipeline]] — Ingestion scripts
- [[Strategies]] — Strategy playbooks and lifecycle
- [[Risk Management]] — R:R, sizing, drawdown halts