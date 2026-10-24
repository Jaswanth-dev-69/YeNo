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
- **3 Verification (mechanics only, not edge).**
  - 18 unit tests pass (20 after item 6). Regression hashes: unchanged 24/24 for 1d and 1e. Fixes 1a/1b/1c intentionally change actions.
  - Full suite run once per bot on the addendum simulator: `sim/results/house-v1.txt` (regenerated) and `sim/results/yeno-v2.txt`, with the before/after table.
  - **Settlements: yeno-v2 had 2 vs house-v1's 1, both in `thin-expiry`.** Traces show each was a fully one-sided market: the entry saw a book stamped just before the simulated 90 s cutoff, then that outcome had no bids until close. The bot sent SELL on every call from 60 s (54 and 66 `SELL:no_bids` rejections). Nothing could be sold, so the difference is entry-timing luck, not a defect.
  - Partial sells are rare in this simulator (1 per suite). Even "thin" books usually hold 5 shares across 5 levels, so the partial-depth paths are covered mainly by unit tests.
  - HTTP, 24 h baseline, ~85k calls per bot: p50 0.76 ms, p99 1.53 ms, max 34 ms, 0 timeouts at 200 ms. Action hashes match the in-process runs.
- **4 Recorder feasibility** (FEASIBILITY.md): feasible without an account via Polymarket Gamma + CLOB `/book` + exchange tickers. Gaps: resolution uses the Chainlink 60 s TWAP (credentialed), and YeNo's books are not confirmed to equal Polymarket's. Important for later strategy work: Up/Down resolves on a 60 s TWAP, not spot. `priceToBeat` appears in Gamma with a delay. No recorder was built or left running.
- **5 Packaging.**
  - Dockerfile: `python:3.12.8-slim-bookworm`, pinned by tag and digest. Copies only `yeno_bot.py` (the `.dockerignore` excludes everything else). Runs as `nobody` with `YENO_BIND_HOST=0.0.0.0`.
  - Built and run locally: the smoke test passes, and the container's action hash equals the in-process one.
  - The image was not pushed. README.md and SUBMISSION.md added.
- **6 Opt-in `YENO_SIZE_TO_CAP=1`** (default OFF, read once at import; the Dockerfile does not set it).
  - When on, the bot budgets the most shares fundable under the cent-floored `min($5, rules cap, cash)` at asks + $0.02.
  - The immediate-loss limit scales to 0.08 USD per share; the stop-loss was already per share. If depth or the loss check fails, it falls back to the default 5-share budget.
  - Default-mode action hashes are unchanged. Mechanics check (mechanics only, not edge): 0 sub-5 rejections. Cycles fell (median 19 vs 24) because in an edge-free world bigger positions lose cash faster.
  - **Its edge is untested.** Turn it on only after real-data evidence.
- **Pre-push audit (Part 1).** All commits use only the GitHub no-reply address. No secrets, personal paths or other emails anywhere in history. Largest file 30 KB. Added the one-line summary to the top of the README.
- Correction: the unit-test count was misreported as 19 (task 3) and 21 (final report); the actual counts are 18 at task 3 and 20 now.

## v3: no position open at market change (submission-v2)
- **Official-snapshot facts (from the owner):** an open position at market change ENDS the whole replay. This build (v2) stopped after 50 cycles with a position open. Top builds: fees 1.9-2.8% of volume and $7.8-9.7 volume per cycle; this build 4.5% and $3.29, out of cash after about 60 markets.
- **7 Simulator.** A position open at market close now ends the run (`open_at_change="end_run"`, default; `"settle"` keeps the old reading and reproduces the old action hashes 24/24). New per-run fields: `stopped`, `stop`, `markets_survived`, `markets_total`, `cycle_fees`, `hold_seconds`; the summary shows stop%, markets survived (median/min), volume per cycle and fee%.
  - New `cliff` scenario: per market and outcome, 50% chance that at a time 10-150 s before close (skewed toward close) bids turn thin, patchy, or fade out over 2-20 s and then vanish. Added because the existing stops all came from thin-expiry's fixed 90 s switch, which rewards any window that happens to avoid it.
  - v2 on the requested scenarios (8 seeds): thin-expiry stopped 2/8 runs (at markets 25 and 34 of 288), onesided 0/8, stale 0/8. 32 seeds: thin-expiry 6/32, cliff 7/32, onesided/stale/everything 0. `sim/results/v2-market-change.txt`.
- **8 Exit design, picked by simulator** (`sim/results/design-v3.txt`; 5 grid stages, up to 256 stress runs per variant).
  - Selling while bids still exist (on no bids, or bids < 2x position) cut cliff stops 7 -> 2 of 32. No exit rule fixed thin-expiry stops: they are BUYs decided just inside 90 s that fill after the switch into a book with no bids until close. Only an earlier window removed them.
  - Chosen: entries 100-120 s before close; forced exit (SELL every call) from 90 s; maximum hold 10 s; SELL at once when bids cover < 2x the position or vanish; stop on a 3-cent adverse gross price move; all exits latched and re-sent until flat.
  - Entry needs bids for 2x `budget / best ask` (over-counts the shares bought) so the depth exit never dumps a fresh entry, and a spread inside the stop so the stop never closes it on the next call.
  - The stop is a gross price move, not fee-inclusive: a fee-inclusive $0.05/share stop (best in the sim at ~0.87 asks) is already crossed by fees alone at a 0.50 ask, which is where the official snapshot's 4.5% fee rate puts this build. 2c and 3c gave equal stops (10); 3c still allows 2-tick spreads.
  - Result (mechanics only, not edge): stress stops 13/160 -> 1/160; full suite 2 -> 0. Costs: fee drag 2.05% -> 2.20% and volume per cycle $8.68 -> $8.45 (earlier entries are at lower prices); baseline median volume 209 -> 169; in `lagged` (artificial edge) median volume 952 -> 529 and qualification 50% -> 0%, because short holds also cut winners. `sim/results/yeno-v3.txt`.
  - The remaining stop (cliff seed 15) filled 110 s before close and its bids faded out before the next call; nothing reacting to the book can catch that.
- **9 Fees and cash, calculations only** (`sim/fee_cash.py`, `sim/results/fee-cash.txt`). Fee drag per $ of volume is about 0.07 x (1 - p) + 1%: 4.5% at a 0.50 ask, 1.7% at 0.90. At zero edge $10 supports about 23-35 round trips (26 at 0.50, 26 at 0.90 with a 1c spread) and $110-238 of volume; reaching $1,000 burns $23-76, so qualifying needs real edge of at least 1.8-6.8% of volume. Snapshot check: 4.5% x $3.29 = $0.148/cycle -> ~49 cycles, matching this build's ~50.
  - Entry price floor (higher-priced side only): cuts worst-case fee drag from 5.9% (0.30) to 2.4% (0.80) and raises zero-edge volume from $110 to $195. Not added to the bot: it removes every cheaper entry, and whether those had edge is untested.
  - Cash-preservation rule (no BUY unless cash - one stop-loss cycle >= the next minimum BUY): at zero edge it only stops 2-3 cycles earlier and keeps one minimum BUY funded. Not added to the bot; its effect on edge is untested.
- **10 Verification.** 35 unit tests pass (28 bot, 7 sim/fee). HTTP 24 h baseline, 85,066 calls: p50 0.56 ms, p99 0.92 ms, max 16.6 ms, 0 timeouts at 200 ms; hash equals in-process. Docker image from the unchanged pinned Dockerfile: smoke test passes, hash equals in-process. `sim/results/house-v1.txt` and `yeno-v2.txt` are from the earlier simulator (positions settled at close).
