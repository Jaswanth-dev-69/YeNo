# Submission checklist

Tagged `submission-v2` (v3 bot) and pushed; `submission-v1` is unchanged. Nothing has been emailed or submitted. The owner submits.

| Contract item | Status | Evidence |
|---|---|---|
| $5 max per BUY, fees included | ✅ | `maxCashUsd` = fee-inclusive cost (contract formula) of 5 shares at ask + $0.02, rounded up to the cent. It must be ≤ `min($5, rules.maximumBuyCashUsd, cash)` rounded down to the cent, else HOLD. Property test over 3,000 random books (`tests/test_yeno_bot.py::BuyHeadroom`). |
| No leverage or borrowing | ✅ | `maxCashUsd` ≤ `account.cashUsd` (same test). The bot never sells shares it doesn't hold: SELL means "close the owned position". |
| One position or pending BUY at a time | ✅ | BUY only when `account.position` is null. The evaluator calls only when nothing is pending, and the bot also keeps a 2 s cooldown after any BUY attempt. |
| BTC five-minute Up/Down markets only | ✅ | The bot never picks a market; it answers only for the market the evaluator sends, with outcome `YES` or `NO`. |
| No entries in the final 15 s | ✅ | Entries only when `100 < secondsToClose <= 120` (`EntryWindow` test). |
| No position open at market change (ends the replay) | ✅ sim | SELL on every call from 90 s; exits on thin/vanishing bids, 10 s max hold and a 3-cent stop, all latched until flat; entries need 2x bid depth and a spread inside the stop. Simulator stops 1/160 stress runs vs 13/160 for v2, 0 in the full suite. A book that loses all bids within one call of a fill can still strand a position. |
| Stale data | ✅ | The evaluator sends only fresh books. The bot also skips entries when the reference is older than 1.5 s, provisional, or stamped in the future. |
| Flat with nothing pending when a cycle first reaches $1,000 | ✅ | Volume lands only when a cycle fully closes, so the bot is flat at the crossing. Exits latch until flat; hard flatten from 90 s to close; any error while holding returns SELL. |
| Valid decisions only | ✅ | Replies are only `HOLD`, `SELL`, or `BUY` with `outcome` and `maxCashUsd`. 0 invalid replies across the simulator suite; kit smoke test passes, including in the container. |
| Reply within 200 ms | ✅ | Over HTTP, 24 h baseline, 85,066 calls: p50 0.56 ms, p99 0.92 ms, max 16.6 ms, 0 timeouts at 200 ms. Container (1 h): p99 1.07 ms. |
| No network calls at decision time | ✅ | Imports: `json, math, os, threading, http.server, typing` only. `http.server` is the inbound endpoint. No `urllib`/`socket`/`requests`, no file I/O, no logging, no clock, no randomness in `yeno_bot.py`. |
| No secrets in the repo | ✅ | Scan for key/secret/token/password patterns and `.env`/`.pem`/`.key` files found nothing. |
| Deterministic | ✅ | Decisions depend only on the observation sequence. Identical action hashes across repeat runs, in-process vs HTTP, and in-process vs container. |
| No self-trading or coordination | ✅ | One bot, no outbound communication, no shared accounts. |
| Pinned dependencies, clean build | ✅ | Stdlib only. `FROM python:3.12.8-slim-bookworm@sha256:2199a628…a2f0`. Built and run locally as `nobody`. |
| Opt-in `YENO_SIZE_TO_CAP=1` | ⚠️ off by default | Sizes BUYs toward the $5 cap, still within `min($5, cash)` (property test). Its edge is untested; the Dockerfile does not set it. |
| Fees and cash | ⚠️ calculation only | Fee drag ~0.07 x (1 - p) + 1% per $ of volume (4.5% at 0.50, 1.7% at 0.90). At zero edge $10 supports ~23-35 round trips and $110-238 of volume (`sim/results/fee-cash.txt`). Entry price floor and cash-preservation rule evaluated, not added; effect on edge untested. |
| Edge | ⚠️ untested | The simulator measures mechanics only. No real-data evidence exists that this strategy reaches $1,000; at zero edge it cannot (fees alone exceed $10 per $1,000). |

## Immutable identifiers
- `yeno_bot.py` sha256: `5b32cdb92e1f3d4b90ec198c65363b0969e3f193aed7c2f8b71f9f00bac70b9c`
- Previous submission: tag `submission-v1` = `38cf4e6b85b7511ccb1ee1defb08d78e363760cd` (unchanged).
- This submission: tag `submission-v2`, the commit containing this file (`git log -1`; a file cannot contain its own SHA). It is also listed in the final report.
