---
title: ADR-0002 — Human-in-the-Loop Approval
type: adr
status: accepted
date: 2026-06-07
tags: [adr, risk, approval]
up: ["[[Decisions]]"]
related: ["[[Layer 04 — Approval]]"]
---

# ADR-0002 — Human-in-the-Loop Approval

**Status:** Accepted

## Context

A fully automated signal→order path can place trades from a bad signal, bug, or
bad data with no human checkpoint. Even on paper, we want the discipline of a gate
before the broker.

## Decision

Insert [[Layer 04 — Approval]] between scanning/alerting and execution. Every order
requires an explicit **Approve** in Telegram. Signals carry a Redis TTL; if not
approved before expiry they are dropped (reject-by-default).

## Consequences

- ✅ No order without a human yes; natural kill-switch (just don't approve).
- ✅ Audit trail (actor + timestamp) per decision.
- ⚠️ Throughput is bounded by human responsiveness — fine for this strategy.

## Related

- [[Layer 04 — Approval]]
- [[Layer 03 — Alert]]
- [[Layer 05 — Execution]]
