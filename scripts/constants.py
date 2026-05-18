"""Shared trading-pipeline constants.

Single source of truth for thresholds that must stay in lockstep across the
scanner → snapshot → preflight → executor chain. If the constant lives in
more than one place, a stale copy will eventually drift past a real one and
silently invert a gate.
"""
from decimal import Decimal

# Maximum acceptable option bid-ask spread as a fraction of mid price.
# (ask - bid) / mid, so symmetric around the midpoint.
#
# Enforced at three layers:
#   1. fetch_alpaca_snapshot.select_best_option — rejects wide-spread
#      contracts at signal time before they ever hit signal_alerts.
#   2. Scanner-time hardening (e.g. detect_ema_crossover) — re-queries
#      Alpaca for live bid/ask on the DB-picked contract and clears
#      option_symbol if the live spread is too wide.
#   3. process_approved preflight — catches stale spreads that widened
#      between scan and approval.
MAX_SPREAD_PCT = 0.15
MAX_SPREAD_PCT_DECIMAL = Decimal("0.15")

# ---------------------------------------------------------------------------
# Signal freshness / expiry windows
# ---------------------------------------------------------------------------
# How long a signal remains actionable after creation. After this window
# elapses, alert_telegram.py expires the row to 'expired' status and
# telegram_callback_listener.py rejects approval.
#
# The key insight: ORB signals are only valid for ~60 min after market open
# (they're based on a fixed opening range — by 11 AM the range is stale
# and the probability of continuation has decayed). Setup scanner signals
# are broader but still involve live option quotes that go stale.
#
# Strategy-specific windows (minutes). Default is 120 min for strategies
# not listed here. Used by:
#   - alert_telegram.py: expire_stale_new() per-strategy TTL
#   - telegram_callback_listener.py: reject stale approvals
#   - detect_orb.py: skip generation outside the ORB session window
SIGNAL_TTL_MINUTES: dict[str, int] = {
    "orb": 60,                  # ORB is opening-range only; stale after ~1h
    "ema_crossover_15m": 60,   # 15m timing signal; stale within an hour
    "ema_crossover": 240,      # Daily EMA cross has more staying power
    "setup_scanner": 120,      # 8-gate composite; 2h is reasonable
    "liquidity_sweep": 90,     # 5m sweep setup; decay faster than daily
    "intraday_signal": 30,     # 5-min re-score; very time-sensitive
}

DEFAULT_SIGNAL_TTL_MINUTES = 120  # fallback for strategies not in the dict