# Working notes

## Contract addendum from Builderr (authoritative; overrides yeno-evaluator-contract.md)
- No public replay harness or 30-path data exists. The $559 / $943 figures are development diagnostics, are not reproducible, and must never be cited as evidence of edge.
- `account.position` is `null` or `{market_id, outcome, shares, buy_gross_usd, buy_fees_usd, sell_gross_usd, sell_fees_usd}`, all snake_case. Everything else is camelCase.
- `/decide` is called only when both YES/NO books are fresh and no action is pending. Reply within 200 ms.
- Qualification is checked when a completed cycle first reaches $1,000: the bot must then be flat with no pending action. End-of-24h flatness and post-target behaviour don't matter.
- A BUY fill below `minOrderSize` is rejected. A SELL can partially fill; volume counts only when the whole cycle closes.

## Decision log
- **1a SELL logic.** Exit rules are evaluated on whatever bids exist. When bids cover less than the position, the unsellable rest is valued at the same average net price, so the result is identical to House Bot v1 whenever bids cover the position. Once a rule fires, an `exiting` latch returns SELL on every call until flat (reset when flat or on a new market). The hard flatten from 60 s to close is unchanged and also catches positions found inside that window. A position whose `market_id` differs from the current market is sold at once. With no bids at all, outside the window and before any rule fires, the bot HOLDs (nothing to value; the window catches it later). Regression (mechanics only, not edge): volume and cycles identical on baseline/partial/short-window. 6/24 hashes changed, only because the pre-addendum simulator still called `/decide` while a SELL was pending.
- **1b Late entries removed.** Entry requires `70 < secondsToClose <= 90`, with the floor defined as `FORCED_EXIT_SECONDS + 10`. Late BUYs went to 0 and cycles fell, as expected: in this edge-free simulator those entries were pure fee burn (mechanics only, not edge). Whether entries near expiry hold edge with a real exit rule needs real data.
- **1c BUY headroom.**
  - The budget is the fee-inclusive cost (contract formula) of `max(5, minOrderSize)` shares, walking the asks with every level +$0.02.
  - Rounding: the budget is rounded UP to the cent and the cap `min($5, rules cap, cash)` is rounded DOWN to the cent. The bot HOLDs if budget > cap. I read "round down" as applying to the cap: rounding the budget down would undercut +2 cents by up to 1 cent, and an exact +2-cent move would then fill 4.99 shares and be rejected. This way 5 shares are always funded at +2 cents and the cap is never exceeded.
  - Ask levels that reach 1.00 after the shift are dropped, so 5 shares may not be fundable there (HOLD).
  - If the price doesn't move, about 5.1 shares fill. The immediate-loss check still uses 5 shares at the observed book.
  - Result (mechanics only, not edge): sub-5-share BUY fills fell from 57% to 19% in baseline. The rest are moves larger than 2 cents, or thin depth, in the simulated latency window.
