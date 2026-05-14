---
created: 2026-05-14
updated: 2026-05-14
tags: [laws, trading, risk, mOC]
---

# 🦞 Laws of Trading

These are **non-negotiable rules** for ClawStreetBot. Not guidelines. Not suggestions. Laws.

---

## Law 1: Never Trade With Emotion

No FOMO trading. No revenge trading. No YOLO trading. If you get upset about how a trade turned out or about missing out on gains, walk away and don't trade until you cool off. Sometimes that's a day or two. Sometimes it's more than a month.

## Law 2: If It's Bothering You, You're Too Deep

If you're getting upset or bothered by the ups or downs of one of your positions, you're probably in too deep on that trade and need to trade smaller amounts.

## Law 3: Never More Than 20% in a Single Position

No more than 20% of capital in any single position to start. Acknowledged: realistically this should probably be closer to 5%, but 20% is within my risk tolerance.

## Law 4: Realize Gains

"No one ever went broke realizing gains." Don't be afraid to realize gains when your position is up 50%. I've let some positions ride up to 200%, but I realize most gains between 30% to 50%.

## Law 5: No Short-Dated Options

Nothing under 30 DTE — theta decay kills anything shorter. Most of my call options are between 1 to 6 months out. A couple over a year out. This also means **never touch 0DTE**. I also don't play earnings. 90 DTE is fine but the premiums are steep — 30+ DTE balances cost vs. time.

## Law 6: Know the Difference Between Luck and Skill

I know I've gotten lucky a lot and I don't confuse it with being skilled. Skill means you can **consistently repeat** something.

## Law 7: There Will Always Be Another Opportunity

Don't force a bad trade to try to make money. There's always another setup coming.

## Law 8: Do Your Fucking Research

Google is a great tool and even just reading random posts on WSB will give you a random piece of insight. Granted, 98% of WSB is shitposting, so you have a lot of crap to filter through. If you don't know what something is, **research it**. Bare minimum: learn the principles of options (both long and short), learn the Greeks, and learn what Implied Volatility and IV crush are.

---

## Implementation in ClawStreetBot

These laws translate into hard constraints in the trading system:

| Law | System Enforcement |
|-----|-------------------|
| Law 1 | Emotion detection暂停 — if recent losses > threshold, require cooldown before next trade |
| Law 2 | If position P&L variance triggers emotional threshold, auto-suggest size reduction |
| Law 3 | `max_position_size = 0.20 * portfolio_value` — hard cap, no override |
| Law 4 | Auto-generate take-profit orders at 30% and 50% gain levels (1/3 each, let remainder ride) |
| Law 5 | `min_dte = 30` — hard filter on all option scans, reject any entry < 30 DTE |
| Law 6 | Track win rate vs. expected return — flag when performance exceeds statistical expectation |
| Law 7 | Never enter a trade outside defined entry criteria — no "close enough" setups |
| Law 8 | All trade entries must include a research record (thesis, data sources, Greeks analysis for options) |

---

## See Also

- [[Risk Management]] — Position sizing formulas and stop-loss frameworks
- [[Watchlist]] — Current tracked assets
- [[Alpaca API]] — Trading and data endpoints