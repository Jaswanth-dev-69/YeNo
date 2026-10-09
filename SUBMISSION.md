# Submission checklist

Nothing has been pushed or submitted. The owner submits.

| Contract item | Status | Evidence |
|---|---|---|
| $5 max per BUY, fees included | ✅ | `maxCashUsd` = fee-inclusive cost (contract formula) of 5 shares at ask + $0.02, rounded up to the cent. It must be ≤ `min($5, rules.maximumBuyCashUsd, cash)` rounded down to the cent, else HOLD. Property test over 3,000 random books (`tests/test_yeno_bot.py::BuyHeadroom`). |
| No leverage or borrowing | ✅ | `maxCashUsd` ≤ `account.cashUsd` (same test). The bot never sells shares it doesn't hold: SELL means "close the owned position". |
| One position or pending BUY at a time | ✅ | BUY only when `account.position` is null. The evaluator calls only when nothing is pending, and the bot also keeps a 2 s cooldown after any BUY attempt. |
| BTC five-minute Up/Down markets only | ✅ | The bot never picks a market; it answers only for the market the evaluator sends, with outcome `YES` or `NO`. |
| No entries in the final 15 s | ✅ | Entries only when `70 < secondsToClose <= 90` (`EntryWindow` test). |
| Stale data | ✅ | The evaluator sends only fresh books. The bot also skips entries when the reference is older than 1.5 s, provisional, or stamped in the future. |
| Flat with nothing pending when a cycle first reaches $1,000 | ✅ | Volume lands only when a cycle fully closes, so the bot is flat at the crossing. Exits latch until flat; hard flatten from 60 s to close; any error while holding returns SELL. |
| Valid decisions only | ✅ | Replies are only `HOLD`, `SELL`, or `BUY` with `outcome` and `maxCashUsd`. 0 invalid replies across the simulator suite; kit smoke test passes, including in the container. |
| Reply within 200 ms | ✅ | Over HTTP, 24 h baseline, ~85k calls: p50 0.76 ms, p99 1.53 ms, max 34 ms, 0 timeouts at 200 ms. Container (1 h): p99 1.9 ms. |
| No network calls at decision time | ✅ | Imports: `json, math, os, threading, http.server, typing` only. `http.server` is the inbound endpoint. No `urllib`/`socket`/`requests`, no file I/O, no logging, no clock, no randomness in `yeno_bot.py`. |
| No secrets in the repo | ✅ | Scan for key/secret/token/password patterns and `.env`/`.pem`/`.key` files found nothing. |
| Deterministic | ✅ | Decisions depend only on the observation sequence. Identical action hashes across repeat runs, in-process vs HTTP, and in-process vs container. |
| No self-trading or coordination | ✅ | One bot, no outbound communication, no shared accounts. |
| Pinned dependencies, clean build | ✅ | Stdlib only. `FROM python:3.12.8-slim-bookworm@sha256:2199a628…a2f0`. Built and run locally as `nobody`. |
| Opt-in `YENO_SIZE_TO_CAP=1` | ⚠️ off by default | Sizes BUYs toward the $5 cap, still within `min($5, cash)` (property test). Its edge is untested; the Dockerfile does not set it. |
| Edge | ⚠️ untested | The simulator measures mechanics only. No real-data evidence exists that this strategy reaches $1,000. |

## Immutable identifiers
- `yeno_bot.py` sha256: `e890b3ed56f6918407232c4dc5189b1333bfa7571df73820e99ceef3cc43b417`
- Final repository commit: the commit containing this file (`git log -1`; a file cannot contain its own SHA). It is also listed in the final report.
