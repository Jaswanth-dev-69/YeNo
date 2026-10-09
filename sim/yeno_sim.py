#!/usr/bin/env python3
"""Local mechanics simulator for the YeNo x Builderr volume-bot contract.

Mechanics only, not edge. BTC paths and order books are synthetic, and the
books are priced off the same BTC path the bot sees, so no strategy has a real
edge here (the "lagged" scenario adds an artificial one only to keep bots
trading all day). Use this to check that a bot obeys the contract under
partial fills, stale data, one-sided books, schema variants and timeouts.
Volume, cash and drawdown printed here say nothing about the official score.

Execution follows yeno-evaluator-contract.md as literally as possible. Where
the contract is silent, the choice is a Scenario knob, so a bot can be tested
under each reading:
  * Builderr's contract addendum (NOTES.md) is applied: /decide is called only
    when both books updated within 2 s and nothing is pending; the position is
    snake_case with market_id; BUY fills below minOrderSize are rejected; a run
    qualifies when a completed cycle first reaches the target (flat by then).
  * A decision executes against the first update of that outcome's book at or
    after decision + 250 ms. If none arrives within 2 s, it is rejected as
    stale. A pending action blocks any further BUY or SELL.
  * BUY spends at most maxCashUsd, fees included, walking the asks in
    0.01-share steps. SELL sells the whole position into whatever bids exist;
    the rest stays open (split exit). Fees are rounded per order.
  * A position still open at market close settles at 1 or 0 and its cycle
    adds zero volume. Terminal flat = no position and no pending action when
    the window ends.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import runpy
import socket
import statistics
import sys
import time
import urllib.error
import urllib.request
from bisect import bisect_left, bisect_right
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

LABEL = "mechanics only, not edge"
MARKET_SECONDS = 300.0
NOISE_BUCKET_SECONDS = 5.0             # quote noise and spread persist for this long
LATENCY_SECONDS = 0.25
STALE_BOOK_SECONDS = 2.0
NO_ENTRY_SECONDS = 15.0
BTC_GRID_SECONDS = 0.25
EPS = 1e-9


@dataclass(frozen=True)
class Scenario:
    name: str
    hours: float = 24.0
    target_usd: float = 1000.0
    start_cash: float = 10.0
    max_buy_usd: float = 5.0
    min_order_size: float = 5.0
    obs_interval: float = 1.0
    obs_jitter: float = 0.2
    book_update_mean: float = 0.4      # mean seconds between updates, per outcome
    book_lag_seconds: float = 0.0      # >0 gives the book an artificial lag (synthetic edge)
    btc_sigma_per_sec: float = 6.0     # USD; about $100 per five minutes
    depth_mean: float = 80.0           # shares per level
    thin_prob: float = 0.0             # chance a snapshot's top two levels hold 0.5-6 shares
    stale_book_prob: float = 0.0       # per market and outcome: chance of a 3-8 s update gap
    stale_ref_prob: float = 0.0        # per market: chance of a 2-6 s frozen reference
    missing_ref_prob: float = 0.0      # per observation: chance the reference is absent
    target_delay_max: float = 6.0      # target turns final 1..max seconds after open
    onesided: str = "near_expiry"      # "none" | "near_expiry" | "aggressive"
    string_numbers: bool = False       # book levels sent as strings
    unsorted_levels: bool = False
    int_seconds: bool = False          # secondsToClose sent as a floored int
    thin_expiry: bool = False          # persistently thin or one-sided books in the last 90 s
    buy_below_min: str = "reject"      # "allow" | "reject" BUY fills under minOrderSize
    sell_below_min: str = "allow"      # "allow" | "reject" SELL fills under minOrderSize
    late_exec_prob: float = 0.0        # ambiguous write: executes 0.5-4 s late
    drop_exec_prob: float = 0.0        # ambiguous write: never executes
    obs_gap_prob: float = 0.0          # per observation: chance of a 2-10 s evaluator stall
    adverse_cents: float = 0.0         # stress: every fill this many cents worse


SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in (
        Scenario("baseline"),
        Scenario("partial", thin_prob=0.5, depth_mean=8.0),
        Scenario("stale", stale_book_prob=0.3, stale_ref_prob=0.5, missing_ref_prob=0.02,
                 target_delay_max=20.0),
        Scenario("onesided", onesided="aggressive"),
        Scenario("thin-expiry", thin_expiry=True),
        Scenario("timeouts", late_exec_prob=0.15, drop_exec_prob=0.05, obs_gap_prob=0.01),
        Scenario("minsize", buy_below_min="reject", sell_below_min="reject", thin_prob=0.3),
        Scenario("lagged", book_lag_seconds=5.0),
        Scenario("short-window", hours=1.0, target_usd=40.0, book_lag_seconds=5.0),
        Scenario("everything", thin_prob=0.3, depth_mean=20.0, stale_book_prob=0.3,
                 stale_ref_prob=0.5, missing_ref_prob=0.02, target_delay_max=20.0,
                 onesided="aggressive", string_numbers=True,
                 unsorted_levels=True, int_seconds=True, buy_below_min="reject",
                 sell_below_min="reject", late_exec_prob=0.15, drop_exec_prob=0.05,
                 obs_gap_prob=0.01),
    )
}

# (scenario, adverse cents, seed multiplier) run by default.
DEFAULT_SUITE: list[tuple[str, float, int]] = [
    ("baseline", 0, 1), ("baseline", 1, 1), ("baseline", 2, 1), ("baseline", 3, 1),
    ("partial", 0, 1), ("stale", 0, 1), ("onesided", 0, 1), ("thin-expiry", 0, 1),
    ("timeouts", 0, 1), ("minsize", 0, 1), ("lagged", 0, 1), ("everything", 0, 1),
    ("short-window", 0, 8),
]


def fees(fills: list[tuple[float, float]]) -> float:
    gross = sum(p * q for p, q in fills)
    dynamic = sum(0.07 * q * p * (1 - p) for p, q in fills)
    return round(dynamic + 1e-12, 5) + round(gross * 0.01 + 1e-12, 5)


def take(levels: list[tuple[float, float]], shares: float) -> list[tuple[float, float]]:
    out, left = [], shares
    for p, s in levels:
        if left <= EPS:
            break
        q = min(left, s)
        out.append((p, q))
        left -= q
    return out


class Market:
    __slots__ = ("idx", "id", "open", "close", "target", "target_final_at", "provisional_target",
                 "times", "quote", "stale_ref", "expiry_mode")


class World:
    """Synthetic BTC path plus per-market YES/NO book update streams."""

    def __init__(self, sc: Scenario, seed: int) -> None:
        self.sc, self.seed = sc, seed
        rng = random.Random(f"world:{sc.name}:{seed}")
        # The window starts mid-market, so it ends mid-market too.
        self.t0 = 1_790_000_000.0 + rng.uniform(0, MARKET_SECONDS)
        self.base = math.floor(self.t0 / MARKET_SECONDS) * MARKET_SECONDS
        self.t_end = self.t0 + sc.hours * 3600
        n_markets = int(math.ceil((self.t_end - self.base) / MARKET_SECONDS)) + 1
        n = int(n_markets * MARKET_SECONDS / BTC_GRID_SECONDS) + 8
        step = sc.btc_sigma_per_sec * math.sqrt(BTC_GRID_SECONDS)
        price, path = 80_000.0, [0.0] * n
        for i in range(n):
            path[i] = price
            price += rng.gauss(0, step)
        self.path = path
        self.markets: list[Market] = []
        for k in range(n_markets):
            m = Market()
            m.idx, m.id = k, f"sim-btc-5m-{seed}-{k:04d}"
            m.open = self.base + k * MARKET_SECONDS
            m.close = m.open + MARKET_SECONDS
            m.target = self.btc(m.open)
            m.target_final_at = m.open + rng.uniform(1.0, sc.target_delay_max)
            m.provisional_target = m.target + rng.gauss(0, 3.0)
            m.times, m.quote = {}, {}
            for outcome in ("YES", "NO"):
                # Persistent quote noise (AR(1) on 5 s buckets) and spread in ticks, so quotes do
                # not flicker on every update; price changes come mostly from BTC moves.
                noise, quote = 0.0, []
                for _ in range(int(MARKET_SECONDS / NOISE_BUCKET_SECONDS) + 1):
                    noise = 0.7 * noise + rng.gauss(0, 0.005)
                    quote.append((noise, rng.choice((1, 1, 1, 2, 2, 3))))
                m.quote[outcome] = quote
                ts, t = [], m.open + rng.expovariate(1 / sc.book_update_mean)
                gap = None
                if rng.random() < sc.stale_book_prob:
                    g0 = rng.uniform(m.open, m.close - 3)
                    gap = (g0, g0 + rng.uniform(3, 8))
                while t < m.close:
                    if gap is None or not gap[0] <= t < gap[1]:
                        ts.append(t)
                    t += rng.expovariate(1 / sc.book_update_mean)
                m.times[outcome] = ts
            m.expiry_mode = {"YES": "normal", "NO": "normal"}
            if sc.thin_expiry:
                for outcome in ("YES", "NO"):
                    m.expiry_mode[outcome] = rng.choices(
                        ("thin", "patchy", "onesided", "normal"), weights=(4, 3, 1, 2))[0]
            m.stale_ref = None
            if rng.random() < sc.stale_ref_prob:
                s0 = rng.uniform(m.open, m.close - 2)
                m.stale_ref = (s0, s0 + rng.uniform(2, 6))
            self.markets.append(m)
        self._cache: dict[tuple[int, str, int], dict[str, list[tuple[float, float]]]] = {}

    def btc(self, t: float) -> float:
        i = int((t - self.base) / BTC_GRID_SECONDS)
        return self.path[min(max(i, 0), len(self.path) - 1)]

    def market_at(self, t: float) -> int:
        return int((t - self.base) // MARKET_SECONDS)

    def fair_yes(self, m: Market, t: float) -> float:
        remaining = max(m.close - t, BTC_GRID_SECONDS)
        gap = self.btc(t - self.sc.book_lag_seconds) - m.target
        z = gap / (self.sc.btc_sigma_per_sec * math.sqrt(remaining))
        return 0.5 * (1 + math.erf(z / math.sqrt(2)))

    def snapshot(self, m: Market, outcome: str, i: int) -> dict[str, list[tuple[float, float]]]:
        key = (m.idx, outcome, i)
        book = self._cache.get(key)
        if book is not None:
            return book
        if len(self._cache) > 4096:
            self._cache.clear()
        sc = self.sc
        t = m.times[outcome][i]
        r = random.Random(f"book:{self.seed}:{key}")
        fair = self.fair_yes(m, t)
        fair = fair if outcome == "YES" else 1 - fair
        noise, spread = m.quote[outcome][int((t - m.open) / NOISE_BUCKET_SECONDS)]
        mid = fair + noise
        bid_c = int(math.floor(mid * 100 - spread / 2))
        bid_c = min(max(bid_c, 1), 99 - spread)
        ask_c = bid_c + spread

        def size(level: int) -> float:
            if level < 2 and thin:
                return round(r.uniform(0.5, 6.0), 2)
            return round(max(sc.depth_mean * r.lognormvariate(0, 0.6), 0.5), 2)

        thin = r.random() < sc.thin_prob
        bids = [(c / 100, size(j)) for j, c in enumerate(range(bid_c, max(bid_c - 5, 0), -1))]
        asks = [(c / 100, size(j)) for j, c in enumerate(range(ask_c, min(ask_c + 5, 100)))]
        remaining = m.close - t
        mode = m.expiry_mode[outcome]
        if remaining < 90 and mode == "thin":
            bids = [(px, round(r.uniform(0.5, 3.0), 2)) for px, _ in bids]
            asks = [(px, round(r.uniform(0.5, 3.0), 2)) for px, _ in asks]
        elif remaining < 90 and (mode == "onesided" or (mode == "patchy" and r.random() < 0.6)):
            bids = []
        if sc.onesided == "near_expiry":
            if remaining < 30 and fair > 0.95 and r.random() < 0.8:
                asks = []
            if remaining < 30 and fair < 0.05 and r.random() < 0.8:
                bids = []
        elif sc.onesided == "aggressive":
            if remaining < 60 and fair > 0.90:
                asks = []
            if remaining < 60 and fair < 0.10:
                bids = []
            if r.random() < 0.05:
                if r.random() < 0.5:
                    bids = []
                else:
                    asks = []
        book = {"bids": bids, "asks": asks}
        self._cache[key] = book
        return book

    def latest(self, m: Market, outcome: str, t: float) -> dict[str, list[tuple[float, float]]] | None:
        i = bisect_right(m.times[outcome], t) - 1
        return self.snapshot(m, outcome, i) if i >= 0 else None


def run_one(sc: Scenario, seed: int, decide: Callable[[dict], Any]) -> dict[str, Any]:
    w = World(sc, seed)
    obs_rng = random.Random(f"obs:{sc.name}:{seed}")      # timeline is independent of the bot
    exec_rng = random.Random(f"exec:{sc.name}:{seed}")
    cash, volume, cycles = sc.start_cash, 0.0, 0
    pos: dict[str, Any] | None = None
    pend: dict[str, Any] | None = None
    st: Counter[str] = Counter()
    rejects: Counter[str] = Counter()
    latencies: list[float] = []
    digest = hashlib.sha256()
    peak_eq = peak_cash = cash
    max_dd_eq = max_dd_cash = 0.0
    cross_seconds = cross_cash = None
    mi = w.market_at(w.t0)
    t_obs = w.t0

    def note_cash_dd() -> None:
        nonlocal peak_cash, max_dd_cash
        peak_cash = max(peak_cash, cash)
        max_dd_cash = max(max_dd_cash, peak_cash - cash)

    def schedule(kind: str, outcome: str, max_cash: float, t: float) -> dict[str, Any]:
        te = t + LATENCY_SECONDS
        roll = exec_rng.random()
        if roll < sc.drop_exec_prob:
            st["injected_dropped_writes"] += 1
            return {"at": te + STALE_BOOK_SECONDS, "kind": kind, "reject": "write_dropped"}
        if roll < sc.drop_exec_prob + sc.late_exec_prob:
            st["injected_late_writes"] += 1
            te += exec_rng.uniform(0.5, 4.0)
        times = w.markets[mi].times[outcome]
        i = bisect_left(times, te)
        if i == len(times):
            return {"at": te, "kind": kind, "reject": "market_closed"}
        if times[i] - te > STALE_BOOK_SECONDS:
            return {"at": te + STALE_BOOK_SECONDS, "kind": kind, "reject": "stale_book"}
        return {"at": times[i], "kind": kind, "outcome": outcome, "max_cash": max_cash, "snap": i}

    def resolve(p: dict[str, Any]) -> None:
        nonlocal cash, volume, cycles, pos, cross_seconds, cross_cash
        if "reject" in p:
            rejects[f"{p['kind']}:{p['reject']}"] += 1
            return
        m = w.markets[mi]
        book = w.snapshot(m, p["outcome"], p["snap"])
        shift = sc.adverse_cents / 100
        if p["kind"] == "BUY":
            asks = [(min(px + shift, 0.999), s) for px, s in book["asks"]]
            if not asks:
                rejects["BUY:no_asks"] += 1
                return
            depth_h = sum(int(s * 100 + 1e-6) for _, s in asks)
            lo, hi = 0, depth_h
            while lo < hi:
                mid = (lo + hi + 1) // 2
                f = take(asks, mid / 100)
                if sum(px * q for px, q in f) + fees(f) <= p["max_cash"] + EPS:
                    lo = mid
                else:
                    hi = mid - 1
            if lo == 0:
                rejects["BUY:zero_fill"] += 1
                return
            shares = lo / 100
            if shares < sc.min_order_size - EPS:
                if sc.buy_below_min == "reject":
                    rejects["BUY:below_min_size"] += 1
                    return
                st["buys_below_min_size"] += 1
            if lo == depth_h:
                st["buys_depth_limited"] += 1
            f = take(asks, shares)
            gross, fee = sum(px * q for px, q in f), fees(f)
            cash -= gross + fee
            pos = {"market_id": m.id, "outcome": p["outcome"], "shares": shares, "buy_gross": gross, "buy_fees": fee,
                   "sell_gross": 0.0, "sell_fees": 0.0}
            st["buy_fills"] += 1
            return
        # SELL: close as much of the position as the bids allow.
        assert pos is not None
        bids = [(max(px - shift, 0.001), s) for px, s in book["bids"]]
        depth = sum(s for _, s in bids)
        q = math.floor(min(pos["shares"], depth) * 100 + 1e-6) / 100
        if q <= 0:
            rejects["SELL:no_bids"] += 1
            return
        if q < sc.min_order_size - EPS and sc.sell_below_min == "reject":
            rejects["SELL:below_min_size"] += 1
            return
        f = take(bids, q)
        gross, fee = sum(px * qq for px, qq in f), fees(f)
        cash += gross - fee
        if q < pos["shares"] - EPS:
            st["partial_sells"] += 1
        pos["shares"] = round(pos["shares"] - q, 2)
        pos["sell_gross"] += gross
        pos["sell_fees"] += fee
        st["sell_fills"] += 1
        if pos["shares"] <= EPS:
            volume += pos["buy_gross"] + pos["sell_gross"]
            cycles += 1
            pos = None
            note_cash_dd()
            if cross_seconds is None and volume >= sc.target_usd - EPS:
                cross_seconds, cross_cash = p["at"] - w.t0, cash

    def position_view() -> dict[str, Any] | None:
        if pos is None:
            return None
        return {"market_id": pos["market_id"], "outcome": pos["outcome"], "shares": pos["shares"],
                "buy_gross_usd": round(pos["buy_gross"], 6), "buy_fees_usd": round(pos["buy_fees"], 6),
                "sell_gross_usd": round(pos["sell_gross"], 6),
                "sell_fees_usd": round(pos["sell_fees"], 6)}

    def book_view(book: dict[str, list[tuple[float, float]]] | None, t: float) -> dict[str, Any]:
        out: dict[str, Any] = {"bids": [], "asks": [], "minOrderSize": sc.min_order_size}
        if book is None:
            return out
        for side in ("bids", "asks"):
            rows = [[px, s] for px, s in book[side]]
            if sc.unsorted_levels:
                random.Random(f"shuffle:{seed}:{t}:{side}").shuffle(rows)
            if sc.string_numbers:
                rows = [[f"{px:.2f}", f"{s:.2f}"] for px, s in rows]
            out[side] = rows
        return out

    def reference_view(m: Market, t: float) -> dict[str, Any] | None:
        if obs_rng.random() < sc.missing_ref_prob:
            return None
        seen = t - obs_rng.uniform(0.05, 0.6)
        if m.stale_ref is not None and m.stale_ref[0] <= t < m.stale_ref[1]:
            seen = m.stale_ref[0]
        final = t >= m.target_final_at
        return {
            "btcMidUsd": round(w.btc(seen), 3),
            "openingTargetUsd": round(m.target if final else m.provisional_target, 3),
            "observedAt": round(seen, 3),
            "targetObservedAt": round(m.target_final_at if final else min(m.open + 0.5, t), 3),
            "targetProvisional": not final,
        }

    def advance(t: float) -> float:
        nxt = t + sc.obs_interval + obs_rng.uniform(-sc.obs_jitter, sc.obs_jitter)
        if obs_rng.random() < sc.obs_gap_prob:
            nxt += obs_rng.uniform(2, 10)
            st["injected_obs_gaps"] += 1
        return nxt

    while True:
        m = w.markets[mi]
        events = [(t_obs, 3, "obs"), (m.close, 1, "close"), (w.t_end, 2, "end")]
        if pend is not None:
            events.append((pend["at"], 0, "pend"))
        t, _, kind = min(events)
        if kind == "end":
            break
        if kind == "pend":
            assert pend is not None
            resolve(pend)
            pend = None
            continue
        if kind == "close":
            if pend is not None:
                rejects[f"{pend['kind']}:market_closed"] += 1
                pend = None
            if pos is not None:
                yes_won = w.btc(m.close) >= m.target
                won = (pos["outcome"] == "YES") == yes_won
                cash += pos["shares"] * (1.0 if won else 0.0)
                st["settled_positions"] += 1
                st["settled_buy_usd_lost_from_volume"] += round(pos["buy_gross"], 4)
                pos = None
                note_cash_dd()
            mi += 1
            continue

        # Observation. Per the addendum, /decide is called only when both books updated within
        # 2 s and no action is pending; skipped ticks still advance the (bot-independent) clock.
        seconds = m.close - t
        books, stale = {}, False
        for o in ("YES", "NO"):
            i = bisect_right(m.times[o], t) - 1
            books[o] = w.snapshot(m, o, i) if i >= 0 else None
            stale = stale or i < 0 or t - m.times[o][i] > STALE_BOOK_SECONDS
        ref = reference_view(m, t)

        mark = 0.0
        if pos is not None and books[pos["outcome"]] and books[pos["outcome"]]["bids"]:
            mark = pos["shares"] * books[pos["outcome"]]["bids"][0][0]
        peak_eq = max(peak_eq, cash + mark)
        max_dd_eq = max(max_dd_eq, peak_eq - cash - mark)

        if pend is not None or stale:
            st["calls_skipped_pending" if pend is not None else "calls_skipped_stale_book"] += 1
            t_obs = advance(t)
            continue
        obs = {
            "schemaVersion": 1,
            "timestamp": round(t, 3),
            "market": {"id": m.id,
                       "secondsToClose": math.floor(seconds) if sc.int_seconds else round(seconds, 3)},
            "account": {"cashUsd": round(cash, 6), "eligibleVolumeUsd": round(volume, 6),
                        "completedCycles": cycles, "position": position_view()},
            "rules": {"maximumBuyCashUsd": sc.max_buy_usd, "targetVolumeUsd": sc.target_usd,
                      "evaluationWindowHours": sc.hours, "onePositionAtATime": True,
                      "buyFeesIncludedInMaximum": True},
            "books": {o: book_view(b, t) for o, b in books.items()},
        }
        if ref is not None:
            obs["reference"] = ref

        started = time.perf_counter()
        try:
            resp = decide(json.loads(json.dumps(obs)))
            resp = json.loads(json.dumps(resp))
        except TimeoutError:
            st["decide_timeouts"] += 1
            resp = {"action": "HOLD"}
        except Exception as exc:  # noqa: BLE001 - any bot failure is a HOLD, as the evaluator would see it
            st[f"decide_error:{type(exc).__name__}"] += 1
            resp = {"action": "HOLD"}
        latencies.append(time.perf_counter() - started)
        st["decisions"] += 1
        digest.update(f"{t:.3f}|{json.dumps(resp, sort_keys=True)}\n".encode())

        action = resp.get("action") if isinstance(resp, dict) else None
        if action == "BUY":
            st["buys_sent"] += 1
            if seconds <= 60:
                st["buys_sent_<=60s_to_close"] += 1
            if volume >= sc.target_usd - EPS:
                st["buys_sent_after_target"] += 1
            max_cash, outcome = 0.0, None
            try:
                max_cash = float(resp["maxCashUsd"])
                outcome = resp["outcome"]
                valid = outcome in ("YES", "NO") and math.isfinite(max_cash) and max_cash > 0
            except (KeyError, TypeError, ValueError):
                valid = False
            if not valid:
                rejects["BUY:invalid"] += 1
            elif pend is not None:
                rejects["BUY:pending_action"] += 1
            elif pos is not None:
                rejects["BUY:position_open"] += 1
            elif seconds <= NO_ENTRY_SECONDS:
                rejects["BUY:final_15s"] += 1
            elif max_cash > sc.max_buy_usd + EPS:
                rejects["BUY:over_5_usd_cap"] += 1
            elif max_cash > cash + EPS:
                rejects["BUY:insufficient_cash"] += 1
            else:
                pend = schedule("BUY", outcome, max_cash, t)
        elif action == "SELL":
            st["sells_sent"] += 1
            if pend is not None:
                rejects["SELL:pending_action"] += 1
            elif pos is None:
                rejects["SELL:no_position"] += 1
            else:
                pend = schedule("SELL", pos["outcome"], 0.0, t)
        elif action != "HOLD":
            st["invalid_responses"] += 1

        t_obs = advance(t)

    flat = pos is None and pend is None
    latencies.sort()
    return {
        "label": LABEL,
        "scenario": sc.name, "seed": seed, "adverse_cents": sc.adverse_cents,
        "volume": round(volume, 4), "cycles": cycles, "cash_end": round(cash, 4),
        "flat": flat,
        "not_flat_reason": None if flat else ("position" if pos is not None else "pending_action"),
        # The crossing happens when a cycle fully closes, so the bot is flat with nothing pending.
        "qualified": cross_seconds is not None,
        "cross_seconds": cross_seconds and round(cross_seconds, 1),
        "cross_cash": cross_cash and round(cross_cash, 4),
        "max_dd_equity": round(max_dd_eq, 4), "max_dd_cash": round(max_dd_cash, 4),
        "latency_ms_p50": round(latencies[len(latencies) // 2] * 1000, 3) if latencies else None,
        "latency_ms_p99": round(latencies[int(0.99 * (len(latencies) - 1))] * 1000, 3) if latencies else None,
        "latency_ms_max": round(latencies[-1] * 1000, 3) if latencies else None,
        "stats": dict(sorted(st.items())),
        "rejects": dict(sorted(rejects.items())),
        "action_hash": digest.hexdigest()[:16],
    }


def load_inprocess(bot_path: str) -> Callable[[dict], Any]:
    ns = runpy.run_path(bot_path, run_name="yeno_sim_bot")  # fresh module state per run
    bot = ns.get("BOT")
    if bot is not None and callable(getattr(bot, "decide", None)):
        return bot.decide
    if callable(ns.get("decide")):
        return ns["decide"]
    raise SystemExit(f"{bot_path}: expected a BOT object with decide() or a decide() function")


def http_decider(url: str, timeout: float) -> Callable[[dict], Any]:
    def decide(obs: dict) -> Any:
        request = urllib.request.Request(url, data=json.dumps(obs).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read())
        except (socket.timeout, TimeoutError) as exc:
            raise TimeoutError from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise TimeoutError from exc
            raise
    return decide


def _job(args: tuple[str, str, float, int]) -> dict[str, Any]:
    bot_path, name, cents, seed = args
    sc = replace(SCENARIOS[name], adverse_cents=cents)
    return run_one(sc, seed, load_inprocess(bot_path))


def summarize(rows: list[dict[str, Any]]) -> str:
    groups: dict[tuple[str, float], list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault((r["scenario"], r["adverse_cents"]), []).append(r)
    head = (f"{'scenario':<13}{'adv':>4}{'runs':>5}{'med vol':>9}{'best vol':>9}{'qual%':>6}"
            f"{'med cash':>9}{'med DDeq':>9}{'med cyc':>8}{'settled':>8}{'<5sh rej':>9}"
            f"{'late buy':>9}{'errors':>7}")
    lines = [f"=== {LABEL} === synthetic books; volumes and cash are not evidence of edge", head]
    for (name, cents), rs in groups.items():
        n = len(rs)
        errors = sum(v for r in rs for k, v in r["stats"].items()
                     if k.startswith("decide_error") or k in ("invalid_responses", "decide_timeouts"))
        lines.append(
            f"{name:<13}{cents:>4.0f}{n:>5}"
            f"{statistics.median(r['volume'] for r in rs):>9.1f}{max(r['volume'] for r in rs):>9.1f}"
            f"{100 * sum(r['qualified'] for r in rs) / n:>6.0f}"
            f"{statistics.median(r['cash_end'] for r in rs):>9.2f}"
            f"{statistics.median(r['max_dd_equity'] for r in rs):>9.2f}"
            f"{statistics.median(r['cycles'] for r in rs):>8.0f}"
            f"{sum(r['stats'].get('settled_positions', 0) for r in rs):>8}"
            f"{sum(r['rejects'].get('BUY:below_min_size', 0) for r in rs):>9}"
            f"{sum(r['stats'].get('buys_sent_<=60s_to_close', 0) for r in rs):>9}{errors:>7}")
    lines.append("")
    lines.append(f"rejections, partial sells and skipped calls, summed over runs ({LABEL}):")
    for (name, cents), rs in groups.items():
        rej: Counter[str] = Counter()
        for r in rs:
            rej.update(r["rejects"])
        partial = sum(r["stats"].get("partial_sells", 0) for r in rs)
        skipped = sum(r["stats"].get("calls_skipped_stale_book", 0) for r in rs)
        lines.append(f"  {name}@{cents:.0f}c: partial_sells={partial} skipped_stale_calls={skipped} "
                     f"rejects={dict(rej.most_common(5))}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=f"YeNo contract simulator ({LABEL}).")
    ap.add_argument("--bot", required=True, help="bot file exposing BOT.decide or decide()")
    ap.add_argument("--scenarios", default="suite",
                    help="'suite' (default set) or comma list of: " + ",".join(SCENARIOS))
    ap.add_argument("--stress", default="0", help="adverse cents for --scenarios lists, e.g. 0,1,2,3")
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--jobs", type=int, default=16)
    ap.add_argument("--http", help="drive a running server at this URL instead (one seed, sequential)")
    ap.add_argument("--timeout", type=float, default=1.0, help="HTTP response timeout in seconds")
    ap.add_argument("--out", type=Path, help="write per-run JSON lines here")
    args = ap.parse_args()

    if args.http:
        name = "baseline" if args.scenarios == "suite" else args.scenarios.split(",")[0]
        started = time.time()
        row = run_one(SCENARIOS[name], 0, http_decider(args.http, args.timeout))
        row["wall_seconds"] = round(time.time() - started, 1)
        print(json.dumps(row, indent=2))
        rows = [row]
    else:
        if args.scenarios == "suite":
            plan = DEFAULT_SUITE
        else:
            plan = [(n, float(c), 1) for n in args.scenarios.split(",") for c in args.stress.split(",")]
        jobs = [(args.bot, n, float(c), seed) for n, c, mult in plan for seed in range(args.seeds * mult)]
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            rows = list(pool.map(_job, jobs))
        print(summarize(rows))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text("".join(json.dumps(r) + "\n" for r in rows))


if __name__ == "__main__":
    sys.exit(main())
