---
title: System Overview
type: architecture
tags: [architecture, overview]
up: ["[[Home]]"]
down: ["[[Layer 01 — Data]]", "[[Infrastructure]]"]
created: 2026-06-07
---

# System Overview

ClawStreetBot is a staged, human-in-the-loop **paper-trading** pipeline. Data
flows strictly downstream through seven numbered layers; each layer owns one
responsibility and hands off via Postgres tables and Redis signals. Orchestration
(scheduling, retries) is handled by **n8n**; alerting and approvals run through
**Telegram**; brokerage is **Alpaca** (paper API).

## Data Flow

```
Alpaca/Market ──▶ 01 Data ──▶ 02 Scanner ──▶ 03 Alert ──▶ Telegram
                                                              │
                                                          (approve?)
                                                              ▼
07 Reconcile ◀── 06 Exit ◀── 05 Execution ◀── 04 Approval ◀──┘
        │                          │
        └──────────▶ Postgres ◀────┘   (Redis carries live signals/TTLs)
```

See [[99 Diagrams/System Architecture.excalidraw|the Excalidraw diagram]] for the
visual version.

## Design Principles

- **Single source of truth** — all config flows through `config/settings.py`; no
  module reads `os.environ` directly. See [[ADR-0001 — Single Source of Truth Config]].
- **Fail loudly** — structured JSON logs, no silent excepts.
- **Idempotent layers** — every job can re-run safely (upserts, `IF NOT EXISTS`).
- **Human in the loop** — no order is placed without explicit Telegram approval.
  See [[ADR-0002 — Human-in-the-Loop Approval]].
- **Paper first** — `ALPACA_BASE_URL` defaults to the paper endpoint.

## Layers

The pipeline in order: [[Layer 01 — Data]] → [[Layer 02 — Scanner]] →
[[Layer 03 — Alert]] → [[Layer 04 — Approval]] → [[Layer 05 — Execution]] →
[[Layer 06 — Exit]] → [[Layer 07 — Reconcile]].

## Related

- [[Infrastructure]]
- [[Runbook — Bring the Stack Up]]
