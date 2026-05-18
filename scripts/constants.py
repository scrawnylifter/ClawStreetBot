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
