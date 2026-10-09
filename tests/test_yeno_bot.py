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


def obs(seconds: float = 80, t: float = 1_789_880_823.619, position: dict | None = None,
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
        bot = yeno_bot.HouseBot()
        self.assertEqual(bot.decide(obs(position=held(), yes_bids=[[0.49, 100]])), HOLD)

    def test_exit_latches_until_flat(self):
        bot = yeno_bot.HouseBot()
        t = 1_789_880_823.619
        self.assertEqual(bot.decide(obs(t=t, position=held(), yes_bids=[[0.60, 2]])), SELL)
        # Partial fill left 3 shares; the book now says "hold", but the exit must finish.
        rest = held(shares=3, sold_gross=1.2, sold_fees=0.02)
        self.assertEqual(bot.decide(obs(t=t + 1, position=rest, yes_bids=[[0.49, 100]])), SELL)
        self.assertEqual(bot.decide(obs(t=t + 2, position=rest, yes_bids=[])), SELL)
        bot.decide(obs(t=t + 3, position=None))
        self.assertFalse(bot.exiting)

    def test_hard_flatten_window_even_without_bids(self):
        for seconds in (60, 30, 14, 1):
            bot = yeno_bot.HouseBot()
            self.assertEqual(bot.decide(obs(seconds=seconds, position=held(), yes_bids=[])), SELL)

    def test_no_bids_outside_window_holds(self):
        bot = yeno_bot.HouseBot()
        self.assertEqual(bot.decide(obs(seconds=200, position=held(), yes_bids=[])), HOLD)

    def test_position_from_another_market_is_sold(self):
        bot = yeno_bot.HouseBot()
        self.assertEqual(bot.decide(obs(position=held(market="old"), yes_bids=[[0.49, 100]])), SELL)


if __name__ == "__main__":
    unittest.main()
