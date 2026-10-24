# YeNo volume bot

Paper-trading bot for the Builderr x YeNo volume-bot challenge. Stdlib-only, deterministic, no network calls in the bot.

A `/decide` bot for the YeNo × Builderr volume-bot challenge. It keeps House Bot v1's entry signal and 5-share size. Its exits are built around the official rule that a position still open at market change ends the whole replay (see `CHANGELOG.md`; decisions are logged in `NOTES.md`):

- entries only 100-120 s before close, only with bids for 2x the shares and a spread inside the stop;
- SELL on every call from 90 s to close;
- SELL at once when bids thin below 2x the position or vanish, after 10 s held, or on a 3-cent adverse price move;
- every exit re-sends SELL until flat, selling into whatever bids exist;
- BUY cash headroom for a 2-cent move, inside the fee-inclusive $5 cap;
- SELL on any error while holding, and a lock around shared state.

It uses only the Python standard library and makes no network calls, disk I/O or clock reads when deciding.

**What this repo can and cannot show:** the simulator in `sim/` uses synthetic books. It tests contract mechanics only and says nothing about edge. The strategy's edge on real YeNo data is untested.

## Run
```bash
python3 yeno_bot.py                                     # serves POST http://127.0.0.1:8080/decide
YENO_BIND_HOST=0.0.0.0 YENO_PORT=9000 python3 yeno_bot.py
python3 yeno-smoke-test.py                              # kit contract smoke test (in another terminal)
```

Opt-in, edge untested: `YENO_SIZE_TO_CAP=1` sizes BUYs toward the $5 cap (default off).

## Docker
```bash
docker build -t yeno-bot .
docker run --rm -p 127.0.0.1:8080:8080 yeno-bot         # image sets YENO_BIND_HOST=0.0.0.0
python3 yeno-smoke-test.py --url http://127.0.0.1:8080/decide
```
The base image is pinned by tag and digest. Only `yeno_bot.py` is copied in, and the container runs as `nobody`.

## Test
```bash
python3 -m unittest discover -s tests                   # unit tests (stdlib)
python3 sim/yeno_sim.py --bot yeno_bot.py               # full mechanics suite (~2 min on 16 cores)
python3 sim/yeno_sim.py --bot yeno_bot.py --scenarios baseline,thin-expiry --stress 0,2 --seeds 4
python3 sim/yeno_sim.py --bot x --scenarios short-window --http http://127.0.0.1:8080/decide
python3 sim/fee_cash.py                                 # fee drag and $10 runway (calculations only)
```
Every simulator figure is **mechanics only, not edge**. An open position at market change ends a simulated run, as in the official replay. Saved results: `sim/results/yeno-v3.txt` (with the v2 -> v3 before/after table), `v2-market-change.txt`, `design-v3.txt` and `fee-cash.txt`. `house-v1.txt` and `yeno-v2.txt` are from the earlier simulator, which settled open positions at close.

## Files
- `yeno_bot.py`: the bot.
- `tests/`: unit tests.
- `sim/`: contract simulator and fee/cash calculator.
- `Dockerfile`: container build.
- `NOTES.md`: Builderr's contract addendum and the decision log.
- `FEASIBILITY.md`: public data sources for a real-data recorder.
- `SUBMISSION.md`: compliance checklist.
- Kit files are kept unchanged: `yeno-house-bot-v1.py`, `yeno-evaluator-contract.md`, `yeno-starter-readme.md`, and the rest.
