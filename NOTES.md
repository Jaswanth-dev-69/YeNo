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
- **1d Safety net.** `decide` now catches every `Exception`, not just the house bot's four types. If `account.position` is non-null, it returns SELL and sets the `exiting` latch, so a position the bot couldn't value is closed even if later calls parse cleanly. Otherwise it returns HOLD. No camelCase parsing. Regression: 24/24 action hashes unchanged.
- **1e Lock and bind.** `HouseBot.decide` runs under a `threading.Lock` (the in-process simulator exercises it too). Host comes from `YENO_BIND_HOST` (default 127.0.0.1) and port from `YENO_PORT` (default 8080). Both are read once at startup; the hot path does no I/O or logging. Regression: 24/24 action hashes unchanged.
- **2 Simulator follows the addendum.**
  - Removed the camelCase `schema` scenario and the `position_keys` knob; `everything` keeps the string, unsorted and int-seconds variants.
  - The position now carries `market_id`.
  - `/decide` is skipped whenever a book's last update is more than 2 s old, or an action is pending. The `stale` scenario therefore skips calls instead of sending stale books.
  - BUY fills under 5 shares are rejected by default. `qualified` = a completed cycle reached the target.
  - New `thin-expiry` scenario: per market and outcome, the last 90 s are thin (0.5–3 shares per level) 40% of the time, patchy (bids missing on 60% of updates) 30%, fully one-sided 10%, normal 20%.
  - `minsize` now differs from the default only in also rejecting sub-minimum SELLs.
  - The saved `house-v1.txt` came from the pre-addendum simulator; the final suite regenerates it on this version.
