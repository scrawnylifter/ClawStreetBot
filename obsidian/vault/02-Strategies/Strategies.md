---
created: 2026-05-14
updated: 2026-05-14
tags: [strategy, playbook, mOC]
---

# Strategies — Trade Playbooks

This folder contains the **"how"** — step-by-step playbooks for executing each trade type.

The distinction is important:
- **Fundamentals** (`01-Fundamentals/`) = The rules (Laws of Trading) + when/why to look (Trade Entry Criteria)
- **Strategies** (`02-Strategies/`) = The playbooks — exactly how to enter, size, stop, and exit

Every strategy doc here must comply with the [[Laws of Trading]] and be triggered by [[Trade Entry Criteria]].

## Strategy Index

| Strategy | Status | Description |
|----------|--------|-------------|
| *None yet* | 🔜 Draft | Add playbooks here as we backtest and validate them |

---

## What a Strategy Document Contains

Each playbook follows the same structure:

1. **Trigger** — Which entry criteria signals fired (from Trade Entry Criteria)
2. **Setup** — Exact conditions that must be met before entry
3. **Entry** — Order type, timing, and execution steps
4. **Position Sizing** — Share count / contract count formula (Law 3: ≤20% capital)
5. **Stop-Loss** — Where and why (Law 2: defined before entry)
6. **Take-Profit** — Scale-out plan (Law 4: 1/3 at +30%, 1/3 at +50%, rest rides)
7. **DTE Requirement** — Options must be ≥30 DTE (Law 5)
8. **Research Checklist** — Thesis, data sources, Greeks for options (Law 8)
9. **Risk/Reward** — Expected R:R, probability estimate
10. **Invalidation** — What makes this trade wrong, exit immediately

## When Strategies Get Promoted

A strategy moves from **Draft → Paper → Validated**:

- **Draft** — Idea documented, not tested
- **Paper** — Running on Alpaca Paper Trading for ≥30 days (Law 9)
- **Validated** — Paper results prove positive expected value, ready for live capital