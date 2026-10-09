#!/usr/bin/env python3
"""Development baseline for the YeNo × Builderr Volume Bot Challenge.

Evidence class: development diagnostic on resampled recorded regimes.
This bot did not qualify: its best terminal-flat run was $942.59 and no
validation path crossed the $1,000 target. Treat it as a starting point.
"""

from __future__ import annotations

import json
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


TARGET_SHARES = 5.0
MINIMUM_REFERENCE_GAP_USD = 50.0
MAXIMUM_ENTRY_PRICE = 0.90
MAXIMUM_IMMEDIATE_LOSS_USD = 0.40
STOP_LOSS_PER_SHARE_USD = 0.10
MAXIMUM_CYCLES_PER_MARKET = 2
REENTRY_COOLDOWN_SECONDS = 2.0
ENTRY_WINDOW_SECONDS = 90.0
ENTRY_FLOOR_SECONDS = 70.0  # FORCED_EXIT_SECONDS + 10 s, so no entry is dumped on the next call
FORCED_EXIT_SECONDS = 60.0
MAXIMUM_REFERENCE_AGE_SECONDS = 1.5
BUY_HEADROOM_USD = 0.02  # fund the target shares even if asks move up 2 cents within the 250 ms
MAXIMUM_BUY_CASH_USD = 5.0


def _levels(rows: Any, *, reverse: bool) -> list[tuple[float, float]]:
    output: list[tuple[float, float]] = []
    for row in rows if isinstance(rows, list) else []:
        try:
            price, size = float(row[0]), float(row[1])
        except (IndexError, TypeError, ValueError):
            continue
        if 0 < price < 1 and size > 0:
            output.append((price, size))
    return sorted(output, reverse=reverse)


def _fees(fills: list[tuple[float, float]]) -> float:
    gross = sum(price * shares for price, shares in fills)
    protocol = sum(0.07 * shares * price * (1 - price) for price, shares in fills)
    return round(protocol + 1e-12, 5) + round(gross * 0.01 + 1e-12, 5)


def _buy_cost(asks: Any, shares: float) -> tuple[float, float] | None:
    remaining = shares
    fills: list[tuple[float, float]] = []
    for price, available in _levels(asks, reverse=False):
        quantity = min(remaining, available)
        fills.append((price, quantity))
        remaining -= quantity
        if remaining <= 1e-9:
            break
    if remaining > 1e-9:
        return None
    return sum(price * quantity for price, quantity in fills), _fees(fills)


def _sell_proceeds(bids: Any, shares: float) -> tuple[float, float] | None:
    remaining = shares
    fills: list[tuple[float, float]] = []
    for price, available in _levels(bids, reverse=True):
        quantity = min(remaining, available)
        fills.append((price, quantity))
        remaining -= quantity
        if remaining <= 1e-9:
            break
    if remaining > 1e-9:
        return None
    return sum(price * quantity for price, quantity in fills), _fees(fills)


class HouseBot:
    def __init__(self) -> None:
        self.market_id: str | None = None
        self.position_was_open = False
        self.completed_cycles = 0
        self.last_flat_at = -math.inf
        self.last_buy_attempt_at = -math.inf
        self.exiting = False

    def decide(self, observation: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._decide(observation)
        except (KeyError, TypeError, ValueError, OverflowError):
            return {"action": "HOLD"}

    def _decide(self, observation: dict[str, Any]) -> dict[str, Any]:
        market = observation["market"]
        account = observation["account"]
        books = observation["books"]
        now = float(observation["timestamp"])
        seconds = float(market["secondsToClose"])
        market_id = str(market["id"])
        position = account.get("position")

        if market_id != self.market_id:
            self.market_id = market_id
            self.position_was_open = False
            self.completed_cycles = 0
            self.last_flat_at = -math.inf
            self.last_buy_attempt_at = -math.inf
            self.exiting = False

        if position is None:
            self.exiting = False
        if position is None and self.position_was_open:
            self.completed_cycles += 1
            self.last_flat_at = now
        self.position_was_open = position is not None

        if position is not None:
            # Once an exit starts, finish it: a split exit only counts when the cycle closes, and
            # the fill uses a book 250 ms later, so SELL even when the observed bids are empty.
            if self.exiting or seconds <= FORCED_EXIT_SECONDS + 1e-9:
                return self._sell()
            if str(position.get("market_id", market_id)) != market_id:
                return self._sell()
            side = str(position["outcome"])
            shares = float(position["shares"])
            bid_rows = books.get(side, {}).get("bids", [])
            sellable = min(shares, sum(size for _, size in _levels(bid_rows, reverse=True)))
            if sellable <= 1e-9:
                return {"action": "HOLD"}
            executable = _sell_proceeds(bid_rows, sellable)
            if executable is None:
                return {"action": "HOLD"}
            sell_gross, sell_fees = executable
            net_now = sell_gross - sell_fees
            if sellable < shares:
                # Thin book: value the unsellable rest at the same average net price.
                net_now *= shares / sellable
            paid = float(position["buy_gross_usd"]) + float(position["buy_fees_usd"])
            received = float(position.get("sell_gross_usd", 0)) - float(position.get("sell_fees_usd", 0))
            full_cycle_pnl = received + net_now - paid
            if full_cycle_pnl >= -1e-9:
                return self._sell()
            if full_cycle_pnl <= -STOP_LOSS_PER_SHARE_USD * shares + 1e-9:
                return self._sell()
            return {"action": "HOLD"}

        if self.completed_cycles >= MAXIMUM_CYCLES_PER_MARKET:
            return {"action": "HOLD"}
        if now - self.last_flat_at < REENTRY_COOLDOWN_SECONDS - 1e-9:
            return {"action": "HOLD"}
        if now - self.last_buy_attempt_at < REENTRY_COOLDOWN_SECONDS - 1e-9:
            return {"action": "HOLD"}
        if not (ENTRY_FLOOR_SECONDS < seconds <= ENTRY_WINDOW_SECONDS):
            return {"action": "HOLD"}

        reference = observation.get("reference")
        if not isinstance(reference, dict) or bool(reference.get("targetProvisional", True)):
            return {"action": "HOLD"}
        if now - float(reference.get("observedAt", -math.inf)) > MAXIMUM_REFERENCE_AGE_SECONDS:
            return {"action": "HOLD"}
        if float(reference.get("targetObservedAt", math.inf)) > now + 1e-9:
            return {"action": "HOLD"}
        gap = float(reference["btcMidUsd"]) - float(reference["openingTargetUsd"])
        if abs(gap) < MINIMUM_REFERENCE_GAP_USD - 1e-9:
            return {"action": "HOLD"}

        side = "YES" if gap >= 0 else "NO"
        asks = books.get(side, {}).get("asks", [])
        best_asks = _levels(asks, reverse=False)
        if not best_asks or best_asks[0][0] > MAXIMUM_ENTRY_PRICE + 1e-9:
            return {"action": "HOLD"}
        required = _buy_cost(asks, TARGET_SHARES)
        immediate_exit = _sell_proceeds(books.get(side, {}).get("bids", []), TARGET_SHARES)
        if required is None or immediate_exit is None:
            return {"action": "HOLD"}
        gross, fees = required
        sell_gross, sell_fees = immediate_exit
        if sell_gross - sell_fees - gross - fees < -MAXIMUM_IMMEDIATE_LOSS_USD - 1e-9:
            return {"action": "HOLD"}

        # A fill below minOrderSize is rejected, so budget the target shares at asks + 2 cents
        # (fee-inclusive, rounded up to the cent) and require it to fit under the cap, rounded down.
        shares = max(TARGET_SHARES, float(books.get(side, {}).get("minOrderSize", 0) or 0))
        padded = _buy_cost([[price + BUY_HEADROOM_USD, size] for price, size in best_asks], shares)
        if padded is None:
            return {"action": "HOLD"}
        budget = math.ceil((padded[0] + padded[1]) * 100 - 1e-9) / 100
        ceiling = min(MAXIMUM_BUY_CASH_USD, float(account["cashUsd"]),
                      float(observation["rules"]["maximumBuyCashUsd"]))
        if budget > math.floor(ceiling * 100 + 1e-9) / 100 + 1e-9:
            return {"action": "HOLD"}
        self.last_buy_attempt_at = now
        return {"action": "BUY", "outcome": side, "maxCashUsd": round(budget, 2)}

    def _sell(self) -> dict[str, Any]:
        self.exiting = True
        return {"action": "SELL"}


BOT = HouseBot()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/decide":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            response = BOT.decide(json.loads(self.rfile.read(length)))
            body = json.dumps(response, separators=(",", ":")).encode()
        except Exception:
            self.send_error(400)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8080), Handler).serve_forever()
