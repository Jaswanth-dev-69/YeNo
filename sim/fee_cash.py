#!/usr/bin/env python3
"""Fee drag and cash runway for the YeNo contract: calculations only, not edge.

Every cycle here is a zero-edge round trip: buy at the ask, sell the same shares at
ask - spread, with no price move. Fees use the contract formula per order
(0.07 x q x p x (1 - p) and 1% of gross, each rounded to 5 dp). The bot's own BUY
rule is applied: maxCashUsd funds 5 shares at ask + $0.02 (rounded up to the cent),
and there is no BUY while cash is below that. So "cycles $10 supports" is how many
round trips the fees and spread allow before cash can no longer fund a minimum BUY.
The effect of an entry price floor or a cash-preservation rule on EDGE is untested:
these numbers say how fast cash drains, never whether a strategy wins.
"""

from __future__ import annotations

import math

START_CASH, CAP, MIN_SHARES, HEADROOM, TARGET = 10.0, 5.0, 5.0, 0.02, 1000.0
STOP_LOSS_PER_SHARE = 0.10   # yeno_bot full-cycle stop, fees included


def fees(price: float, shares: float) -> float:
    return round(0.07 * shares * price * (1 - price) + 1e-12, 5) + round(0.01 * price * shares + 1e-12, 5)


def cost(price: float, shares: float) -> float:
    return price * shares + fees(price, shares)


def budget(ask: float) -> float:
    """yeno_bot's maxCashUsd: 5 shares at ask + 2 cents, fee-inclusive, rounded up to the cent."""
    return math.ceil(cost(ask + HEADROOM, MIN_SHARES) * 100 - 1e-9) / 100


def filled(ask: float, cash: float) -> float:
    """Shares a maxCashUsd of `cash` buys at an unmoved ask, in 0.01-share steps (as the sim)."""
    q = int(cash / ask * 100)
    while q > 0 and cost(ask, q / 100) > cash + 1e-9:
        q -= 1
    return q / 100


def cycle(ask: float, spread: float, spend: float) -> tuple[float, float, float]:
    """(volume, fees, net loss) of one zero-edge round trip spending `spend`."""
    q = filled(ask, spend)
    bid = ask - spread
    buy, sell = ask * q, bid * q
    f = fees(ask, q) + fees(bid, q)
    return buy + sell, f, buy - sell + f


def runway(ask: float, spread: float, *, size_to_cap: bool = False, reserve: float = 0.0) -> dict:
    """Cycles until cash cannot fund the minimum BUY (plus `reserve`, the cash-preservation rule)."""
    cash, n, vol, fee = START_CASH, 0, 0.0, 0.0
    need = budget(ask)
    while cash + 1e-9 >= need + reserve and n < 100_000:
        spend = math.floor(min(CAP, cash) * 100 + 1e-9) / 100 if size_to_cap else need
        v, f, loss = cycle(ask, spread, spend)
        cash, n, vol, fee = cash - loss, n + 1, vol + v, fee + f
    return {"cycles": n, "volume": vol, "fees": fee, "cash_end": cash, "min_buy": need}


def main() -> None:
    print("=== calculation only, not edge === zero-edge round trips; effect on edge is UNTESTED")
    print()
    print("1. Fee drag per $ of volume and cycles $10 supports (5-share BUYs, yeno_bot budget rule)")
    print(f"{'ask':>5}{'sprd':>6}{'fee/$vol':>9}{'sprd/$vol':>10}{'vol/cyc':>8}{'loss/cyc':>9}"
          f"{'min BUY':>8}{'cycles':>7}{'volume':>8}{'$ for 1k':>9}{'edge/$ for 1k':>14}")
    for spread in (0.01, 0.02):
        for ask in (0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.85, 0.90):
            v, f, loss = cycle(ask, spread, budget(ask))
            r = runway(ask, spread)
            # Zero-edge cash burned to reach $1,000, and the gross edge per $ of volume it would take
            # to get there from $10 while keeping one minimum BUY funded.
            burn = TARGET * loss / v
            edge = (burn - (START_CASH - r["min_buy"])) / TARGET
            print(f"{ask:>5.2f}{spread * 100:>5.0f}c{100 * f / v:>8.2f}%{100 * (loss - f) / v:>9.2f}%"
                  f"{v:>8.2f}{loss:>9.3f}{r['min_buy']:>8.2f}{r['cycles']:>7}{r['volume']:>8.0f}"
                  f"{burn:>9.2f}{100 * edge:>13.2f}%")
    print("  fee/$vol ~ 0.07 x (1 - p) + 1%: it falls as the bought side gets more expensive.")
    print("  '$ for 1k' = cash a zero-edge bot burns to reach $1,000; it exceeds $10 everywhere,")
    print("  so qualifying needs real edge of at least 'edge/$ for 1k' per $ of volume.")
    print()

    print("2. Official-snapshot figures, fees only (spread unknown, so these are upper bounds on runway)")
    for name, fee_rate, per_cycle, min_buy in (("this build", 0.045, 3.29, 2.72),
                                                ("top builds, low", 0.019, 9.70, 4.66),
                                                ("top builds, high", 0.028, 7.80, 4.66)):
        loss = fee_rate * per_cycle
        n = math.floor((START_CASH - min_buy) / loss)
        print(f"  {name:<17} fee {100 * fee_rate:.1f}% x ${per_cycle:.2f}/cycle = ${loss:.3f}/cycle -> "
              f"~{n} cycles, ~${n * per_cycle:.0f} volume before cash < ${min_buy:.2f} min BUY; "
              f"$1,000 burns ${TARGET * fee_rate:.0f} in fees")
    print("  (min BUY assumed at ask 0.50 for this build and 0.90 for top builds; per-cycle volume and")
    print("  fee rate are from the snapshots. ~49 cycles matches this build's 50 cycles / ~60 markets.)")
    print()

    print("3. Entry price floor (buy only the higher-priced side, ask >= floor, existing cap 0.90), 1c spread")
    print(f"{'floor':>6}{'fee/$vol at floor':>18}{'cycles at floor':>16}{'volume at floor':>16}")
    for floor in (0.00, 0.50, 0.60, 0.70, 0.80, 0.90):
        p = max(floor, 0.30)
        v, f, _ = cycle(p, 0.01, budget(p))
        r = runway(p, 0.01)
        label = "none" if floor == 0 else f"{floor:.2f}"
        print(f"{label:>6}{100 * f / v:>17.2f}%{r['cycles']:>16}{r['volume']:>16.0f}")
    print("  (floor 'none' is shown at ask 0.30.) A floor cuts worst-case fee drag and raises volume per")
    print("  cycle, but it also skips every cheaper entry: whether those entries had edge is UNTESTED.")
    print()

    print("4. Cash-preservation rule: no BUY unless cash - worst cycle loss >= next minimum BUY, 1c spread")
    print(f"{'ask':>5}{'worst loss':>11}{'cycles no rule':>15}{'cycles rule':>12}"
          f"{'cash end no rule':>17}{'cash end rule':>14}")
    for ask in (0.50, 0.70, 0.90):
        q = filled(ask, budget(ask))
        worst = STOP_LOSS_PER_SHARE * q  # the stop's full-cycle loss, fees included; gaps can exceed it
        a, b = runway(ask, 0.01), runway(ask, 0.01, reserve=worst)
        print(f"{ask:>5.2f}{worst:>11.2f}{a['cycles']:>15}{b['cycles']:>12}"
              f"{a['cash_end']:>17.2f}{b['cash_end']:>14.2f}")
    print("  At zero edge the rule only stops earlier: it gives up a few cycles to keep one minimum BUY")
    print("  funded after a stop-loss cycle. It adds volume only if the bot has edge, which is UNTESTED.")
    print()

    print("5. Size to the $5 cap (YENO_SIZE_TO_CAP=1, default off), 1c spread")
    for ask in (0.50, 0.70, 0.90):
        a, b = runway(ask, 0.01), runway(ask, 0.01, size_to_cap=True)
        print(f"  ask {ask:.2f}: 5 shares -> {a['cycles']} cycles, ${a['volume']:.0f} volume; "
              f"to cap -> {b['cycles']} cycles, ${b['volume']:.0f} volume")
    print("  Same fee drag per $ of volume, so the zero-edge volume barely moves; larger size only")
    print("  reaches the target in fewer cycles if there is edge (UNTESTED).")


if __name__ == "__main__":
    main()
