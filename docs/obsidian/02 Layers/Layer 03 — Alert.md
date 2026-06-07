---
title: Layer 03 — Alert
type: layer
layer: 3
status: planned
tags: [layer, alert, telegram]
up: ["[[System Overview]]"]
down: ["[[Layer 04 — Approval]]"]
related: ["[[Layer 02 — Scanner]]"]
created: 2026-06-07
---

# Layer 03 — Alert

**Responsibility:** Turn scanner signals into human-readable **Telegram alerts**,
including the inline controls used by [[Layer 04 — Approval]].

**Status:** 🚧 Planned (`03_alert/`)

## Notes

- `alert_telegram` already provides `format_exit_notification` +
  `send_exit_notification` (see commit `c7e8a2f`) — exit alerts piggyback here.
- Alerts carry the signal id so approvals can be correlated back.

## Hand-off

Downstream → [[Layer 04 — Approval]] waits for the user's approve/reject.

## Related

- [[Layer 02 — Scanner]]
- [[Layer 06 — Exit]]
