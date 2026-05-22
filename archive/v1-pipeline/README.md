# v1 Pipeline Archive

**DO NOT import or reference these files.** They are archived here for reference only.

These 9 scripts formed the v1 execution pipeline (5,449 lines). Every fix
introduced its own bugs. Replaced by v2 webhook-first architecture.

Git tag `v1-archive` points to the last commit before v2 rebuild.

Key bugs found in v1 final audit:
- calc_drawdown() compared against latest snapshot, not HWM (baseline reset daily)
- No buying power check before Alpaca submit
- exit_short direction mapped to wrong side/option type
- HTML escaping was patch-per-formatter, not default
- record_skip() missing on some fail paths → 130+ duplicate notifications
- Reconcile split across 2 scripts with duplicated lock/commit patterns

If you need to reference a pattern from v1, READ it here. Never copy it.
