# Changelog

Every simulator number below is **mechanics only, not edge**: synthetic books, no real data.

## v3 (submission-v2)
Official rule from the owner: a position open at market change ends the whole replay.

### yeno_bot.py
- Entries only 100-120 s before close (was 70-90 s); SELL on every call from 90 s (was 60 s).
- Maximum hold 10 s; SELL at once when the held side's bids cover < 2x the position or vanish (was HOLD with no bids).
- Stop on a 3-cent adverse gross price move (was $0.10/share fee-inclusive), so it fires while bids still exist at any price level.
- No entry unless bids cover 2x the shares a BUY can buy and the spread is inside the stop.
- Unchanged: entry signal, 5-share size, fee-inclusive $5 per-BUY cap with +2-cent headroom, opt-in YENO_SIZE_TO_CAP=1, lock, error-while-holding SELL, Dockerfile.
- Simulator (mechanics only, not edge): stress stops 13/160 -> 1/160, full suite 2 -> 0; fee drag 2.05% -> 2.20%; baseline median volume 209 -> 169.

### sim
- An open position at market change ends the run (old settle reading kept as `open_at_change="settle"`); reports markets survived, stops, fee% and volume per cycle.
- New `cliff` scenario (fading liquidity near close).
- `sim/fee_cash.py`: fee drag and $10 runway calculations, entry price floor and cash-preservation rule (calculations only; effect on edge untested).
- Results: `sim/results/v2-market-change.txt`, `design-v3.txt`, `yeno-v3.txt`, `fee-cash.txt`.

## Unreleased (v2, submission-v1)

### Added
- `sim/yeno_sim.py`: a local simulator built from `yeno-evaluator-contract.md`. It covers the 250 ms latency against a later book update, the 2 s stale-book rejection, partial fills and split exits, one-sided books, the final-15 s entry block, settlement at market close, terminal-flat checks, and fault injection: schema variants, stale or missing reference data, late or dropped writes, and evaluator stalls. It runs bots in-process or over HTTP; both give the same action hash.
- `sim/results/house-v1.txt`: mechanics baseline for the unmodified House Bot v1.

### House Bot v1 mechanics findings (simulator)
- With camelCase position keys it never sells: 0 volume and every position settles (`schema`, `everything`).
- It never stops after the target: 407 BUYs after a $40 target across 64 one-hour runs (`short-window`).
- It still enters with 60 s or less to close, and those positions are force-sold on the next tick: 46 BUYs in 8 baseline days.
- About half its BUYs fill below the 5-share minimum, because `maxCashUsd` is the exact cost of 5 shares at the observed ask. Under `minsize`, 233 BUYs are rejected.
- It does not reproduce the published 12/30 non-flat finishes. Flat was 100% in every scenario, so the real cause must be something the simulator doesn't model (for example, persistently thin books in real data).

## yeno_bot.py
- 1a: SELL on whatever bid depth exists. An exit, once triggered, re-sends SELL until flat. Hard flatten from 60 s to close, including positions found inside that window.
- 1b: entries only when 70 < secondsToClose <= 90 (late BUYs that were dumped on the next call are gone).
- 1c: maxCashUsd = fee-inclusive cost of 5 shares at ask + $0.02 (rounded up to the cent), must fit under min($5, cash) rounded down, else HOLD.
- 1d: any exception while holding returns SELL (and latches the exit); while flat it returns HOLD.
- 1e: threading.Lock around decide; YENO_BIND_HOST (default 127.0.0.1) and YENO_PORT (default 8080).
- sim: addendum semantics (fresh-book/no-pending call gating, snake_case position with market_id, sub-minimum BUYs rejected, qualification at first crossing); removed camelCase scenario; added thin-expiry; p50 latency.
- verification: full suite for house-v1 and yeno-v2 on the addendum simulator; before/after table in sim/results/yeno-v2.txt.
- packaging: Dockerfile (digest-pinned, stdlib only, runs as nobody), README.md, SUBMISSION.md.
- 6: opt-in YENO_SIZE_TO_CAP=1 sizes BUYs toward the $5 cap (default off; edge untested).
