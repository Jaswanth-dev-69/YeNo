"""Unit tests for yeno_bot.py (stdlib unittest). Run: python3 -m unittest discover -s tests"""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import yeno_bot  # noqa: E402

SAMPLE = json.loads((ROOT / "yeno-sample-observation.json").read_text())


def obs(seconds: float = 110, t: float = 1_789_880_823.619, position: dict | None = None,
        yes_bids: list | None = None, yes_asks: list | None = None, cash: float = 10.0,
        market: str = "m1") -> dict:
    o = copy.deepcopy(SAMPLE)
    o["timestamp"] = t
    o["reference"]["observedAt"] = t - 0.2
    o["market"] = {"id": market, "secondsToClose": seconds}
    o["account"]["cashUsd"] = cash
    o["account"]["position"] = position
    if yes_bids is not None:
        o["books"]["YES"]["bids"] = yes_bids
    if yes_asks is not None:
        o["books"]["YES"]["asks"] = yes_asks
    return o


def held(shares: float = 5, gross: float = 2.5, fees: float = 0.1125, sold_gross: float = 0.0,
         sold_fees: float = 0.0, market: str = "m1") -> dict:
    return {"market_id": market, "outcome": "YES", "shares": shares, "buy_gross_usd": gross,
            "buy_fees_usd": fees, "sell_gross_usd": sold_gross, "sell_fees_usd": sold_fees}


SELL = {"action": "SELL"}
HOLD = {"action": "HOLD"}


class SellLogic(unittest.TestCase):
    def test_profit_exit_on_partial_depth(self):
        # Only 2 shares bid, but at a price that makes the whole cycle profitable.
        bot = yeno_bot.HouseBot()
        self.assertEqual(bot.decide(obs(position=held(), yes_bids=[[0.60, 2]])), SELL)

    def test_stop_loss_on_partial_depth(self):
        bot = yeno_bot.HouseBot()
        self.assertEqual(bot.decide(obs(position=held(), yes_bids=[[0.30, 1]])), SELL)

    def test_hold_between_rules(self):
        # Bought 5 at 0.50: a 0.48 bid is a 2-cent move (fees aside), inside the 3-cent stop.
        for bid in (0.50, 0.49, 0.48):
            bot = yeno_bot.HouseBot()
            self.assertEqual(bot.decide(obs(position=held(), yes_bids=[[bid, 100]])), HOLD, bid)

    def test_stricter_stop_fires_while_bids_exist(self):
        # A 3-cent adverse move sells with 100 shares still bid, at any price level.
        for price in (0.30, 0.50, 0.87):
            position = held(gross=5 * price, fees=yeno_bot._fees([(price, 5)]))
            bot = yeno_bot.HouseBot()
            self.assertEqual(bot.decide(obs(position=position, yes_bids=[[price - 0.03, 100]])),
                             SELL, price)
            bot = yeno_bot.HouseBot()
            self.assertEqual(bot.decide(obs(position=position, yes_bids=[[price - 0.02, 100]])),
                             HOLD, price)

    def test_stop_counts_shares_already_sold(self):
        # 3 of 5 sold at 0.50 (gross 1.50 of 2.50 paid), 2 left: the whole cycle's move is measured
        # against 3 cents x the 2 shares still held, so a split position (seen after a restart; a
        # live split exit is latched anyway) stops sooner, not later.
        rest = held(shares=2, sold_gross=1.5, sold_fees=0.05)
        self.assertEqual(yeno_bot.HouseBot().decide(obs(position=rest, yes_bids=[[0.475, 100]])), HOLD)
        self.assertEqual(yeno_bot.HouseBot().decide(obs(position=rest, yes_bids=[[0.47, 100]])), SELL)

    def test_exit_latches_until_flat(self):
        bot = yeno_bot.HouseBot()
        t = 1_789_880_823.619
        self.assertEqual(bot.decide(obs(t=t, position=held(), yes_bids=[[0.60, 2]])), SELL)
        # Partial fill left 3 shares; the book now says "hold", but the exit must finish.
        rest = held(shares=3, sold_gross=1.2, sold_fees=0.02)
        self.assertEqual(bot.decide(obs(t=t + 1, position=rest, yes_bids=[[0.50, 100]])), SELL)
        self.assertEqual(bot.decide(obs(t=t + 2, position=rest, yes_bids=[])), SELL)
        bot.decide(obs(t=t + 3, position=None))
        self.assertFalse(bot.exiting)

    def test_hard_flatten_window_even_with_deep_profitless_bids(self):
        for seconds in (90, 75, 60, 30, 14, 1):
            bot = yeno_bot.HouseBot()
            self.assertEqual(bot.decide(obs(seconds=seconds, position=held(), yes_bids=[[0.50, 100]])),
                             SELL, seconds)
        self.assertEqual(yeno_bot.HouseBot().decide(obs(seconds=90.5, position=held(),
                                                        yes_bids=[[0.50, 100]])), HOLD)

    def test_no_bids_sells_and_latches(self):
        bot = yeno_bot.HouseBot()
        t = 1_789_880_823.619
        self.assertEqual(bot.decide(obs(t=t, seconds=200, position=held(), yes_bids=[])), SELL)
        # Bids are back and the book says "hold", but the exit must finish.
        self.assertEqual(bot.decide(obs(t=t + 1, seconds=199, position=held(),
                                        yes_bids=[[0.50, 100]])), SELL)

    def test_thin_bids_sell_into_what_exists(self):
        # 5 shares held: bids must cover 10, or the bot sells into whatever is there now.
        for depth, expected in ((9.99, SELL), (10.0, HOLD)):
            bot = yeno_bot.HouseBot()
            bids = [[0.50, depth / 2], [0.50, depth / 2]]
            self.assertEqual(bot.decide(obs(position=held(), yes_bids=bids)), expected, depth)
        bot = yeno_bot.HouseBot()
        self.assertEqual(bot.decide(obs(position=held(), yes_bids=[[0.50, 4], [0.30, 1]])), SELL)

    def test_maximum_hold_time(self):
        bot = yeno_bot.HouseBot()
        t = 1_789_880_823.619
        for dt, expected in ((0, HOLD), (5, HOLD), (9.9, HOLD), (10, SELL), (11, SELL)):
            self.assertEqual(bot.decide(obs(t=t + dt, seconds=115 - dt, position=held(),
                                            yes_bids=[[0.50, 100]])), expected, dt)
        bot.decide(obs(t=t + 12, seconds=103, position=None))
        # A new position gets a fresh timer.
        self.assertEqual(bot.decide(obs(t=t + 15, seconds=100, position=held(),
                                        yes_bids=[[0.50, 100]])), HOLD)

    def test_position_from_another_market_is_sold(self):
        bot = yeno_bot.HouseBot()
        self.assertEqual(bot.decide(obs(position=held(market="old"), yes_bids=[[0.50, 100]])), SELL)


class EntryWindow(unittest.TestCase):
    def test_only_between_100_and_120_seconds(self):
        for seconds, expected in ((121, "HOLD"), (120, "BUY"), (110, "BUY"), (100.5, "BUY"),
                                  (100, "HOLD"), (90, "HOLD"), (80, "HOLD"), (60, "HOLD"),
                                  (16, "HOLD")):
            bot = yeno_bot.HouseBot()
            self.assertEqual(bot.decide(obs(seconds=seconds))["action"], expected, seconds)

    def test_entry_room_covers_hold_time(self):
        self.assertEqual(yeno_bot.ENTRY_FLOOR_SECONDS,
                         yeno_bot.FORCED_EXIT_SECONDS + yeno_bot.MAXIMUM_HOLD_SECONDS)

    def test_no_entry_without_exit_side_depth(self):
        # Budget 2.72 at a 0.50 ask over-counts 5.44 shares, so bids must cover 10.88.
        self.assertEqual(yeno_bot.HouseBot().decide(obs(yes_bids=[[0.49, 10.87]])), HOLD)
        self.assertEqual(yeno_bot.HouseBot().decide(obs(yes_bids=[[0.49, 10.88]]))["action"], "BUY")

    def test_no_entry_the_stop_would_close_at_once(self):
        # A 3-cent spread is already a full stop move.
        self.assertEqual(yeno_bot.HouseBot().decide(obs(yes_bids=[[0.47, 100]])), HOLD)
        self.assertEqual(yeno_bot.HouseBot().decide(obs(yes_bids=[[0.48, 100]]))["action"], "BUY")

    def test_fresh_entry_is_not_dumped_by_the_depth_exit(self):
        import random
        rng = random.Random(3)
        buys = 0
        for _ in range(2000):
            ask = rng.randint(30, 90) / 100
            asks = [[round(ask + k / 100, 2), round(rng.uniform(0.5, 8), 2)] for k in range(4)]
            bids = [[round(ask - 0.01 - k / 100, 2), round(rng.uniform(0.5, 8), 2)] for k in range(3)]
            bot = yeno_bot.HouseBot()
            d = bot.decide(obs(yes_asks=asks, yes_bids=bids))
            if d["action"] != "BUY":
                continue
            buys += 1
            # Fill the whole budget at the observed asks: the most shares this BUY can get.
            shares, spent = 0.0, 0.0
            while True:
                cost = yeno_bot._buy_cost(asks, round(shares + 0.01, 2))
                if cost is None or sum(cost) > d["maxCashUsd"] + 1e-9:
                    break
                shares, spent = round(shares + 0.01, 2), cost
            depth = sum(q for _, q in bids)
            self.assertGreaterEqual(depth + 1e-9, yeno_bot.BID_DEPTH_MULTIPLE * shares)
        self.assertGreater(buys, 50)


class BuyHeadroom(unittest.TestCase):
    def test_sample_budget_is_five_shares_at_ask_plus_two_cents(self):
        # 5 x 0.52 = 2.60 gross + 0.07*5*0.52*0.48 = 0.08736 + 1% = 0.026 -> 2.71336 -> 2.72
        self.assertEqual(yeno_bot.HouseBot().decide(obs()), {"action": "BUY", "outcome": "YES",
                                                             "maxCashUsd": 2.72})

    def test_hold_when_cash_cannot_fund_headroom(self):
        self.assertEqual(yeno_bot.HouseBot().decide(obs(cash=2.71)), HOLD)
        self.assertEqual(yeno_bot.HouseBot().decide(obs(cash=2.72))["action"], "BUY")

    def test_never_exceeds_cap_or_cash_and_always_funds_headroom(self):
        import random
        rng = random.Random(7)
        buys = 0
        for _ in range(3000):
            ask = rng.randint(5, 90) / 100
            asks = [[round(ask + k / 100, 2), round(rng.uniform(0.5, 8), 2)] for k in range(4)]
            bids = [[round(ask - 0.01 - k / 100, 2), 50] for k in range(3)]
            cash = round(rng.uniform(0.5, 12), 6)
            d = yeno_bot.HouseBot().decide(obs(cash=cash, yes_asks=asks, yes_bids=bids))
            if d["action"] != "BUY":
                continue
            buys += 1
            cap = min(5.0, cash)
            self.assertLessEqual(d["maxCashUsd"], cap + 1e-9)
            self.assertEqual(d["maxCashUsd"], round(d["maxCashUsd"], 2))
            gross, fees = yeno_bot._buy_cost([[p + 0.02, q] for p, q in asks], 5.0)
            self.assertGreaterEqual(d["maxCashUsd"] + 1e-9, gross + fees)
        self.assertGreater(buys, 500)


class SafetyNet(unittest.TestCase):
    def test_malformed_position_sells(self):
        for position in ({}, {"outcome": "YES"}, {"outcome": "YES", "shares": "x"}, [], "junk", 5,
                         {"outcome": "YES", "shares": 5, "buy_gross_usd": None}):
            bot = yeno_bot.HouseBot()
            self.assertEqual(bot.decide(obs(position=position)), SELL, position)

    def test_error_while_holding_latches_exit(self):
        bot = yeno_bot.HouseBot()
        t = 1_789_880_823.619
        self.assertEqual(bot.decide(obs(t=t, position={"outcome": "YES"})), SELL)
        self.assertEqual(bot.decide(obs(t=t + 1, position=held(), yes_bids=[[0.49, 100]])), SELL)

    def test_errors_while_flat_hold(self):
        broken = obs()
        broken["books"] = None
        for o in (None, {}, {"account": {}}, broken, {"account": {"position": None}}):
            self.assertEqual(yeno_bot.HouseBot().decide(o), HOLD)


class ServerAndLock(unittest.TestCase):
    def test_decide_waits_for_the_lock(self):
        import threading
        bot = yeno_bot.HouseBot()
        results = []
        with bot._lock:
            worker = threading.Thread(target=lambda: results.append(bot.decide(obs())))
            worker.start()
            worker.join(0.2)
            self.assertTrue(worker.is_alive())
        worker.join(2)
        self.assertEqual(results[0]["action"], "BUY")

    def test_concurrent_calls_stay_valid(self):
        from concurrent.futures import ThreadPoolExecutor
        bot = yeno_bot.HouseBot()
        t = 1_789_880_823.619
        cases = [obs(t=t + k, seconds=95 - k % 40, position=held() if k % 3 else None,
                     market=f"m{k % 4}") for k in range(400)]
        with ThreadPoolExecutor(16) as pool:
            out = list(pool.map(bot.decide, cases))
        self.assertTrue(all(d["action"] in ("HOLD", "BUY", "SELL") for d in out))

    def test_bind_address_from_env(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(yeno_bot._bind_address(), ("127.0.0.1", 8080))
        with mock.patch.dict(os.environ, {"YENO_BIND_HOST": "0.0.0.0", "YENO_PORT": "9001"}):
            self.assertEqual(yeno_bot._bind_address(), ("0.0.0.0", 9001))

    def test_http_round_trip(self):
        import threading
        import urllib.request
        server = yeno_bot.ThreadingHTTPServer(("127.0.0.1", 0), yeno_bot.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/decide"
            request = urllib.request.Request(url, data=json.dumps(obs(seconds=30)).encode(),
                                             method="POST")
            with urllib.request.urlopen(request, timeout=2) as response:
                self.assertEqual(json.loads(response.read()), HOLD)
        finally:
            server.shutdown()
            server.server_close()


class SizeToCap(unittest.TestCase):
    def test_off_by_default(self):
        self.assertFalse(yeno_bot.SIZE_TO_CAP)

    def test_sizes_toward_cap_and_never_exceeds_it(self):
        from unittest import mock
        import random
        with mock.patch.object(yeno_bot, "SIZE_TO_CAP", True):
            d = yeno_bot.HouseBot().decide(obs())
            self.assertEqual(d["action"], "BUY")
            self.assertTrue(4.9 <= d["maxCashUsd"] <= 5.0, d)
            self.assertLessEqual(yeno_bot.HouseBot().decide(obs(cash=3.0))["maxCashUsd"], 3.0)
            rng = random.Random(11)
            for _ in range(1000):
                ask = rng.randint(5, 90) / 100
                asks = [[round(ask + k / 100, 2), round(rng.uniform(0.5, 30), 2)] for k in range(4)]
                bids = [[round(ask - 0.01 - k / 100, 2), 80] for k in range(3)]
                cash = round(rng.uniform(0.5, 12), 6)
                d = yeno_bot.HouseBot().decide(obs(cash=cash, yes_asks=asks, yes_bids=bids))
                if d["action"] == "BUY":
                    self.assertLessEqual(d["maxCashUsd"], min(5.0, cash) + 1e-9)
                    gross, fees = yeno_bot._buy_cost([[p + 0.02, q] for p, q in asks], 5.0)
                    self.assertGreaterEqual(d["maxCashUsd"] + 1e-9, gross + fees)


if __name__ == "__main__":
    unittest.main()
