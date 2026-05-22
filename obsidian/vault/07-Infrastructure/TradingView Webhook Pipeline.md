---
created: 2026-05-20
updated: 2026-05-20
tags: [tradingview, real-time, pipeline, mOC]
---

# TradingView — Real-Time Data Pipeline

## The Problem

Alpaca's free tier uses **IEX data — 15 minutes delayed**. Every scanner (EMA crossover, ORB, liquidity sweep, setup_scanner) runs on stale prices. Entries and exits are late by definition. No amount of pipeline optimization fixes data that's already 15 minutes old.

## The Fix

Use [TradingView-API](https://github.com/Mathieu2301/TradingView-API) to pull real-time indicator data into Python on a 30-second polling loop. All scanner logic stays in-repo — no Pine scripts, no webhooks from TV, nothing lives on TradingView's servers.

```
Every 30 seconds:
    TradingView-API pull real-time data for watchlist
         │
         ▼
    Scanner logic (Python, in-repo)
    - EMA 9/21 crossover detection
    - ORB breakout detection
    - Liquidity sweep detection
    - ADX / RSI / volume filters
         │
         ▼
    signal_alerts row (source='tv_scanner')
         │
         ▼
    Pipeline (preflight → notify → execute → reconcile)
```

**Latency budget:** Max 30 seconds from bar close to signal detection. Acceptable for options entries/exits.

## Decision: Why Not Webhooks / Pine Scripts

We evaluated two options:

| Approach | Real-time | All code in-repo | Bar-close trigger | Risk |
|----------|-----------|-------------------|-------------------|------|
| **TV-API polling (CHOSEN)** | Yes | Yes | ~30s delay | Lib is unofficial, could break if TV changes site |
| Pine scripts + webhooks | Yes (instant) | No — Pine lives on TV | Yes | Strategy logic split across two platforms, not version controlled |

**Why polling won:** All strategy logic stays in our repo, version controlled, testable. 30-second latency is acceptable for options. No dependency on TradingView's alert infrastructure or Pine script runtime.

**Risk we accept:** The Mathieu2301 lib is unofficial and reverse-engineered. If TV changes their site, the lib breaks until patched. Mitigation: keep cron scanners on Alpaca data as fallback (15 min delayed, but always works).

## What the Alpaca × TV Integration Gives You

The Alpaca integration on your TradingView account means:
- You can see Alpaca positions on TV charts
- You can place manual trades from TV's chart interface
- Useful for **manual oversight** and chart review — not for the bot pipeline

## Division of Labor

```
┌─────────────────────────────────┐
│  TRADINGVIEW (data source)     │  Real-time price + indicators
│  - OHLCV data per bar          │  - Pulled every 30s via API lib
│  - Built-in indicators         │  - No strategy logic here
│  - No Pine scripts needed      │
└──────────────┬──────────────────┘
               │ Python pull (30s interval)
               ▼
┌─────────────────────────────────┐
│  OUR SERVER (all logic)         │
│  - EMA crossover detection     │  In-repo Python
│  - ORB breakout detection      │  In-repo Python
│  - Liquidity sweep detection   │  In-repo Python
│  - ADX / RSI / volume filters  │  In-repo Python
│  - IV rank check (Alpaca)      │  Delayed OK — screening gate
│  - IV vs RV spread (Alpaca)    │  Delayed OK — screening gate
│  - Contract selection (Alpaca) │  Fresh at execution time
│  - Spread / DTE / delta filter │
│  - Drawdown / position limits   │
│  - Risk:R reward calc          │
│  - Buy power check             │
│  - Execute → Alpaca paper      │
└─────────────────────────────────┘
```

**The rule:** TradingView is a data source only. All detection, filtering, and execution logic lives here.

## Data Sources — Who Does What

| Data | Source | Latency | Used For |
|------|--------|---------|----------|
| Real-time OHLCV + indicators | TradingView-API | ~30s (polling) | Signal triggers: EMA cross, ORB, sweeps, ADX/RSI |
| Options chains + greeks | Alpaca | 15 min delayed | Screening gates: IV rank, IV vs RV, DTE, delta, spread |
| Fundamentals | Polygon | Daily batch | Long-term thesis |
| Market regime | Alpaca (SPY) | 15 min delayed | Bull/bear classification — doesn't need real-time |
| Position / account state | Alpaca | Real-time (API) | Drawdown, buying power, execution |

## Rebuild Steps (for this piece)

1. Install TradingView-API lib, test pulling real-time data for one symbol
2. Build `data/tv_client.py` — wrapper for TV-API with watchlist iteration + rate limit handling
3. Modify scanners to accept TV data as input instead of Alpaca delayed data
4. Wire TV polling loop → scanner → signal_alerts (source='tv_scanner')
5. Keep Alpaca cron scanners as fallback
6. Test end-to-end: TV pull → scanner → DB row → Telegram → execute
7. Monitor lib stability, add error handling for TV-API breakage

## Fallback: Cron Scanners on Alpaca Data

If TradingView-API goes down:
- Cron scanners continue on Alpaca's 15-min delayed data
- Same detection logic, just late entries
- `signal_alerts` source switches from 'tv_scanner' to 'cron_scanner'
- Automatic failover — no manual intervention needed

## See Also

- [[v2-plan]] — Full rebuild plan
- [[Watchlist — Rebuild Reference]] — Current watchlist state
- [[Alpaca Data Pipeline]] — Server-side ingestion (delayed data for options)
- [[Telegram Alert System]] — Notification pipeline