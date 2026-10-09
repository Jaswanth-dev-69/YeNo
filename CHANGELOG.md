# Changelog

Every simulator number below is **mechanics only, not edge**: synthetic books, no real data.

## Unreleased

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
