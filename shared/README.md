# Shared Modules

Cross-layer utilities imported by scripts in all layers.

## Modules
| Module | Purpose |
|--------|---------|
| `constants.py` | Market hours, watchlist loader, DB config, risk profiles |
| `fetch_alpaca_snapshot.py` | Real-time stock + best option snapshot from Alpaca |

## TODO
- Consolidate `load_env()` from 18 scripts into a shared `db.py` module
- Extract shared `signal_writer.py` helper for signal_alerts INSERT logic