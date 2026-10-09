# YeNo volume bot

Paper-trading bot for the Builderr x YeNo volume-bot challenge. Stdlib-only, deterministic, no network calls in the bot.

A `/decide` bot for the YeNo × Builderr volume-bot challenge. It is House Bot v1's strategy with mechanical fixes only (see `CHANGELOG.md`; decisions are logged in `NOTES.md`):

- exits on partial bid depth, re-sending SELL until flat;
- hard flatten from 60 s to close;
- no entries with 70 s or less to close;
- BUY cash headroom for a 2-cent move;
- SELL on any error while holding;
- a lock around shared state.

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
```
Every simulator figure is **mechanics only, not edge**. Saved results: `sim/results/house-v1.txt` and `sim/results/yeno-v2.txt` (the latter includes the before/after table).

## Files
- `yeno_bot.py`: the bot.
- `tests/`: unit tests.
- `sim/`: contract simulator.
- `Dockerfile`: container build.
- `NOTES.md`: Builderr's contract addendum and the decision log.
- `FEASIBILITY.md`: public data sources for a real-data recorder.
- `SUBMISSION.md`: compliance checklist.
- Kit files are kept unchanged: `yeno-house-bot-v1.py`, `yeno-evaluator-contract.md`, `yeno-starter-readme.md`, and the rest.
