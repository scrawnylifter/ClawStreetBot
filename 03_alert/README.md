# 03 — Alert Layer

Telegram alert dispatch — reads from `market.signal_alerts`, sends notifications with approval buttons.

## Scripts
| Script | Purpose | Schedule |
|--------|---------|----------|
| `alert_telegram.py` | Send pending signals to Telegram with approval buttons | Triggered by n8n |

## n8n Workflows
| Workflow | Schedule |
|----------|----------|
| `alert_dispatch.json` | Triggered after scanner workflows |