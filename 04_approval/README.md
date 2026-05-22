# 04 — Approval Layer

Status: **MISSING** — needs recreation from v1 archive

## Purpose
Pre-flight checks and approval pipeline: PDT compliance, drawdown gates, composite signal scoring.

## Missing Scripts
- `process_approved.py` — approve/deny signals based on PDT, drawdown, risk rules
- `generate_signals.py` — composite scoring + regime weighting → `trading.signals`
- `snapshot_equity.py` — Alpaca equity snapshot for drawdown denominator

## Archive Reference
- `archive/v1-pipeline/process_approved.py`
- `archive/v1-pipeline/generate_signals.py`
- `archive/v1-dependents/snapshot_equity.py`

## n8n Workflows Needed
- `equity_snapshot_daily.json` — daily equity snapshot (archived in `archive/v1-dependents/`)