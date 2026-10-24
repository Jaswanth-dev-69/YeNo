#!/usr/bin/env python3
"""YeNo × Builderr volume bot: House Bot v1's entry signal with exits built for one rule.

A position still open at market change ends the official replay, so exits come first:
entries only 100-120 s before close, only when bids cover twice the shares bought and
the spread is inside the stop; exit at once when bids thin below twice the position or
vanish, on a 3-cent adverse price move (fees aside), after 10 s held, and on every call
from 90 s to close; every exit is latched and re-sent until flat. Also: BUY cash headroom for a 2-cent move, SELL on
any error while holding, and a lock around shared state. See CHANGELOG.md and NOTES.md.
Deterministic, stdlib only, no network or disk I/O at decision time.
"""

from __future__ import annotations

import json
import math
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


TARGET_SHARES = 5.0
MINIMUM_REFERENCE_GAP_USD = 50.0
MAXIMUM_ENTRY_PRICE = 0.90
MAXIMUM_IMMEDIATE_LOSS_USD = 0.40
# Stop on the adverse price move (gross, spread included, fees excluded) so it means the same at
# any price level; it fires while bids still exist. Entries need a spread inside it.
STOP_MOVE_PER_SHARE_USD = 0.03
MAXIMUM_CYCLES_PER_MARKET = 2
REENTRY_COOLDOWN_SECONDS = 2.0
ENTRY_WINDOW_SECONDS = 120.0
ENTRY_FLOOR_SECONDS = 100.0  # FORCED_EXIT_SECONDS + MAXIMUM_HOLD_SECONDS
FORCED_EXIT_SECONDS = 90.0   # SELL on every call from here to close
MAXIMUM_HOLD_SECONDS = 10.0
BID_DEPTH_MULTIPLE = 2.0     # exit when bids cover less than this x the position; entry needs it too
MAXIMUM_REFERENCE_AGE_SECONDS = 1.5
BUY_HEADROOM_USD = 0.02  # fund the target shares even if asks move up 2 cents within the 250 ms
MAXIMUM_BUY_CASH_USD = 5.0
# Opt-in, edge untested: size BUYs toward the $5 cap instead of 5 shares (read once at import).
SIZE_TO_CAP = os.environ.get("YENO_SIZE_TO_CAP") == "1"


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


def _sized_budget(asks: Any, bids: Any, best_asks: list[tuple[float, float]], cap: float,
                  minimum: float) -> float | None:
    """Budget for the most shares fundable under cap at asks + 2 cents (YENO_SIZE_TO_CAP=1).

    The immediate round-trip loss limit scales per share (0.40 USD per 5 shares); the stop-loss
    is already per share. Returns None to fall back to the default 5-share budget.
    """
    shifted = [[price + BUY_HEADROOM_USD, size] for price, size in best_asks]
    depth = sum(size for _, size in _levels(shifted, reverse=False))

    def fits(hundredths: int) -> bool:
        cost = _buy_cost(shifted, hundredths / 100)
        return cost is not None and cost[0] + cost[1] <= cap + 1e-9

    low, high = int(round(minimum * 100)), int(depth * 100 + 1e-6)
    if high < low or not fits(low):
        return None
    while low < high:
        middle = (low + high + 1) // 2
        if fits(middle):
            low = middle
        else:
            high = middle - 1
    shares = low / 100
    required, immediate_exit = _buy_cost(asks, shares), _sell_proceeds(bids, shares)
    if required is None or immediate_exit is None:
        return None
    loss_limit = MAXIMUM_IMMEDIATE_LOSS_USD * shares / TARGET_SHARES
    if immediate_exit[0] - immediate_exit[1] - required[0] - required[1] < -loss_limit - 1e-9:
        return None
    gross, fees = _buy_cost(shifted, shares)
    return math.ceil((gross + fees) * 100 - 1e-9) / 100


def _holding(observation: Any) -> bool:
    try:
        return observation["account"]["position"] is not None
    except Exception:  # noqa: BLE001
        return False


class HouseBot:
    def __init__(self) -> None:
        self.market_id: str | None = None
        self.position_was_open = False
        self.completed_cycles = 0
        self.last_flat_at = -math.inf
        self.last_buy_attempt_at = -math.inf
        self.held_since = -math.inf
        self.exiting = False
        self._lock = threading.Lock()

    def decide(self, observation: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            try:
                return self._decide(observation)
            except Exception:  # noqa: BLE001 - a bad field must never strand an open position
                if _holding(observation):
                    return self._sell()
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
        if position is not None and not self.position_was_open:
            self.held_since = now
        self.position_was_open = position is not None

        if position is not None:
            # Once an exit starts, finish it: a split exit only counts when the cycle closes, and
            # the fill uses a book 250 ms later, so SELL even when the observed bids are empty.
            if self.exiting or seconds <= FORCED_EXIT_SECONDS + 1e-9:
                return self._sell()
            if str(position.get("market_id", market_id)) != market_id:
                return self._sell()
            if now - self.held_since >= MAXIMUM_HOLD_SECONDS - 1e-9:
                return self._sell()
            side = str(position["outcome"])
            shares = float(position["shares"])
            bid_rows = books.get(side, {}).get("bids", [])
            # Thin or empty bids: sell into whatever is there now and re-send for the rest,
            # before the book can go fully one-sided.
            if sum(size for _, size in _levels(bid_rows, reverse=True)) < BID_DEPTH_MULTIPLE * shares - 1e-9:
                return self._sell()
            sell_gross, sell_fees = _sell_proceeds(bid_rows, shares)
            bought = float(position["buy_gross_usd"])
            sold = float(position.get("sell_gross_usd", 0))
            paid = bought + float(position["buy_fees_usd"])
            received = sold - float(position.get("sell_fees_usd", 0))
            if received + sell_gross - sell_fees - paid >= -1e-9:
                return self._sell()
            if sold + sell_gross - bought <= -STOP_MOVE_PER_SHARE_USD * shares + 1e-9:
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
        if sell_gross - gross <= -STOP_MOVE_PER_SHARE_USD * TARGET_SHARES + 1e-9:
            return {"action": "HOLD"}  # the stop would close it on the next call

        # A fill below minOrderSize is rejected, so budget the target shares at asks + 2 cents
        # (fee-inclusive, rounded up to the cent) and require it to fit under the cap, rounded down.
        shares = max(TARGET_SHARES, float(books.get(side, {}).get("minOrderSize", 0) or 0))
        padded = _buy_cost([[price + BUY_HEADROOM_USD, size] for price, size in best_asks], shares)
        if padded is None:
            return {"action": "HOLD"}
        budget = math.ceil((padded[0] + padded[1]) * 100 - 1e-9) / 100
        ceiling = min(MAXIMUM_BUY_CASH_USD, float(account["cashUsd"]),
                      float(observation["rules"]["maximumBuyCashUsd"]))
        cap = math.floor(ceiling * 100 + 1e-9) / 100
        if budget > cap + 1e-9:
            return {"action": "HOLD"}
        if SIZE_TO_CAP:
            sized = _sized_budget(asks, books.get(side, {}).get("bids", []), best_asks, cap, shares)
            budget = max(budget, sized or 0.0)
        # No entry the thin-book exit would dump at once: budget / best ask over-counts the shares.
        exit_depth = sum(size for _, size in _levels(books.get(side, {}).get("bids", []), reverse=True))
        if exit_depth < BID_DEPTH_MULTIPLE * budget / best_asks[0][0] - 1e-9:
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


def _bind_address() -> tuple[str, int]:
    return os.environ.get("YENO_BIND_HOST", "127.0.0.1"), int(os.environ.get("YENO_PORT", "8080"))


if __name__ == "__main__":
    ThreadingHTTPServer(_bind_address(), Handler).serve_forever()
