---
title: Layer 04 — Approval
type: layer
layer: 4
status: planned
tags: [layer, approval, telegram]
up: ["[[System Overview]]"]
down: ["[[Layer 05 — Execution]]"]
related: ["[[Layer 03 — Alert]]", "[[ADR-0002 — Human-in-the-Loop Approval]]"]
created: 2026-06-07
---

# Layer 04 — Approval

**Responsibility:** Human-in-the-loop gate. No order reaches the broker without an
explicit **approve** here. See [[ADR-0002 — Human-in-the-Loop Approval]].

**Status:** 🚧 Planned (`04_approval/`)

## Flow

1. User taps **Approve** / **Reject** on the Telegram alert from [[Layer 03 — Alert]].
2. Approval is recorded (with actor + timestamp) and the signal is marked
   `approved`/`rejected`.
3. Approved signals are released to [[Layer 05 — Execution]].

## Safeguards

- Signals expire via Redis TTL — a stale/unanswered signal is **not** executed.
- Reject is the default if no response before expiry.

## Related

- [[Layer 03 — Alert]]
- [[Layer 05 — Execution]]
