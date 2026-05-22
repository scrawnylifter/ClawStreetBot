# 01 — Data Layer

The foundation — all raw data ingestion and derived analytics.

## Scripts
| Script | Purpose | Schedule |
|--------|---------|----------|
| `ingest_alpaca_ohlcv.py` | OHLCV bars (1d, 15m, 5m) | Daily + intraday |
| `ingest_alpaca_options.py` | Options chains + greeks | Daily |
| `ingest_alpaca_iv.py` | Daily implied volatility | Daily |
| `ingest_yfinance_fundamentals.py` | Quarterly financials | Weekly |
| `compute_technical_indicators.py` | EMA/RSI/MACD/ATR/VWAP/Bollinger | Daily |
| `compute_iv_rank.py` | IV rank percentiles | Daily |
| `compute_realized_vol.py` | 20d/5d annualized RV | Daily |
| `compute_regime.py` | Market regime classification (bull/bear/transition) | Daily (derived_daily) |
| `compute_gex_dex.py` | GEX/DEX per strike/expiry | Daily |
| `compute_greeks_filter.py` | IV regime + contract filtering | Daily |
| `compute_iv_outliers.py` | 3σ z-score IV outlier flags | Daily |
| `compute_trend.py` | Multi-timeframe trend status | Daily |
| `setup_watchlist.py` | Sync watchlist YAML → Alpaca + DB | On-demand |

## n8n Workflows
| Workflow | Schedule |
|----------|----------|
| `alpaca_ohlcv_daily.json` | Weekdays 15:30 PDT |
| `alpaca_ohlcv_intraday.json` | :05 6:00-13:30 PDT |
| `alpaca_options_daily.json` | Weekdays 21:55 UTC |
| `fundamentals_weekly.json` | Saturdays 8:00 PDT |
| `derived_daily.json` | Weekdays 15:30 PDT (chain) |
| `watchlist_sync.json` | On-demand |
| `backfill_pending.json` | On-demand |
| `trend_daily.json` | Weekdays 15:00 PDT |

## Known Gaps
- `compute_regime.py` doesn't exist — `market.regime` table is 7 days stale