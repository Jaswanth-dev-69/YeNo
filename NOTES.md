# Working notes

## Contract addendum from Builderr (authoritative; overrides yeno-evaluator-contract.md)
- No public replay harness or 30-path data exists. The $559 / $943 figures are development diagnostics, are not reproducible, and must never be cited as evidence of edge.
- `account.position` is `null` or `{market_id, outcome, shares, buy_gross_usd, buy_fees_usd, sell_gross_usd, sell_fees_usd}`, all snake_case. Everything else is camelCase.
- `/decide` is called only when both YES/NO books are fresh and no action is pending. Reply within 200 ms.
- Qualification is checked when a completed cycle first reaches $1,000: the bot must then be flat with no pending action. End-of-24h flatness and post-target behaviour don't matter.
- A BUY fill below `minOrderSize` is rejected. A SELL can partially fill; volume counts only when the whole cycle closes.

## Decision log
