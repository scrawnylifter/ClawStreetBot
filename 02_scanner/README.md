# 02 — Scanner Layer

Pattern detection and signal generation — reads from Data layer, writes to `market.signal_alerts`.

## Scripts
| Script | Purpose | Schedule |
|--------|---------|----------|
| `detect_ema_crossover.py` | Daily EMA 9/21 crossover detection | Daily |
| `detect_ema_crossover_15m.py` | 15m EMA crossover detection | Intraday |
| `detect_orb.py` | Opening Range Breakout (5m bars) | Intraday |
| `detect_liquidity_sweep.py` | Liquidity sweep detection | Intraday |
| `scan_setups.py` | 8-gate composite BUY signal scanner | Intraday |

## n8n Workflows
| Workflow | Schedule |
|----------|----------|
| `ema_crossover_detector.json` | Daily |
| `ema_crossover_15m.json` | Intraday |
| `orb_detector.json` | Intraday |
| `liquidity_sweep.json` | Intraday |
| `setup_scanner.json` | Intraday |