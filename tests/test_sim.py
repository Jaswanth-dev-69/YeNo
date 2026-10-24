"""Unit tests for the simulator's market-change rule and the fee/cash calculator (mechanics only)."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sim"))
import fee_cash  # noqa: E402
import yeno_sim  # noqa: E402

SHORT = replace(yeno_sim.SCENARIOS["baseline"], hours=0.5)


def buy_and_hold(o: dict) -> dict:
    if o["account"]["position"] is None and o["market"]["secondsToClose"] > 20:
        return {"action": "BUY", "outcome": "YES", "maxCashUsd": 3}
    return {"action": "HOLD"}


class MarketChange(unittest.TestCase):
    def test_open_position_at_market_change_ends_the_run(self):
        r = yeno_sim.run_one(SHORT, 0, buy_and_hold)
        self.assertTrue(r["stopped"])
        self.assertEqual(r["not_flat_reason"], "open_at_market_change")
        self.assertEqual(r["markets_survived"], 0)
        self.assertEqual(r["stop"]["market"], 1)
        self.assertGreater(r["markets_total"], 1)

    def test_settle_mode_keeps_the_old_reading(self):
        r = yeno_sim.run_one(replace(SHORT, open_at_change="settle"), 0, buy_and_hold)
        self.assertFalse(r["stopped"])
        self.assertEqual(r["markets_survived"], r["markets_total"])
        self.assertGreater(r["stats"]["settled_positions"], 1)

    def test_flat_bot_survives_every_market(self):
        r = yeno_sim.run_one(SHORT, 0, lambda o: {"action": "HOLD"})
        self.assertFalse(r["stopped"])
        self.assertEqual(r["markets_survived"], r["markets_total"])

    def test_yeno_bot_survives_a_short_baseline(self):
        r = yeno_sim.run_one(SHORT, 0, yeno_sim.load_inprocess(str(ROOT / "yeno_bot.py")))
        self.assertFalse(r["stopped"])
        self.assertTrue(r["flat"])


class FeeCash(unittest.TestCase):
    def test_fee_drag_matches_the_contract_formula(self):
        volume, fees, _ = fee_cash.cycle(0.50, 0.01, fee_cash.budget(0.50))
        self.assertAlmostEqual(fees / volume, 0.07 * 0.5 + 0.01, delta=0.001)
        volume, fees, _ = fee_cash.cycle(0.90, 0.01, fee_cash.budget(0.90))
        self.assertAlmostEqual(fees / volume, 0.07 * 0.1 + 0.01, delta=0.001)

    def test_budget_is_the_bots(self):
        self.assertEqual(fee_cash.budget(0.50), 2.72)  # same as tests/test_yeno_bot.py

    def test_runway_stops_below_the_minimum_buy(self):
        r = fee_cash.runway(0.70, 0.01)
        self.assertLess(r["cash_end"], r["min_buy"])
        self.assertGreater(r["cycles"], 0)
        guarded = fee_cash.runway(0.70, 0.01, reserve=0.5)
        self.assertLessEqual(guarded["cycles"], r["cycles"])
        self.assertGreaterEqual(guarded["cash_end"], guarded["min_buy"])


if __name__ == "__main__":
    unittest.main()
