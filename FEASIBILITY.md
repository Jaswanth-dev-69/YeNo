# Recorder feasibility (findings only)

Checked on 2026-10-09 from this machine with about 14 short requests: curl with a 10 s timeout and a descriptive User-Agent, public read-only endpoints only, no account, no long-running process.

## Verdict
**Feasible, with two gaps.** No account is needed to read Polymarket's public APIs: market discovery, timestamped YES/NO L2 books, timing and the opening target. Public exchange tickers give a BTC midpoint. The gaps:
1. The official resolution series is a Chainlink TWAP that is not publicly readable.
2. It is unconfirmed that YeNo's own books equal Polymarket's.

## What was reachable
| Endpoint | Result |
|---|---|
| `gamma-api.polymarket.com/events?slug=btc-updown-5m-<start epoch>` | 200. One event per 5-minute window. The slug uses the window start (a multiple of 300 s). |
| `clob.polymarket.com/book?token_id=<id>` | 200. Full L2 book with a millisecond `timestamp` (0.11 s old when fetched), `min_order_size` "5", `tick_size` "0.01". |
| `api.exchange.coinbase.com/products/BTC-USD/ticker`, `api.binance.com/api/v3/ping` | 200. Public BTC bid/ask available. |
| `yeno.market`, `/robots.txt`, `/llms.txt` | 200. Describes YeNo as a Solana prediction market. The homepage links `gamma-api.polymarket.com` and Polymarket image hosting. `/llms.txt` points to `docs.yeno.trade` for a public API, but that page is rendered by JavaScript and showed no API details in static HTML. |
| `polymarket.com` (website) | TLS handshake failure (curl error 35) from this network, so the website's internal endpoints are not usable here. The API subdomains work. |

## Mapping to the evaluator's observation fields
| Field | Source | Status |
|---|---|---|
| `market.id` | Gamma event `slug` or market `conditionId` | Available |
| `market.secondsToClose` | Gamma `endDate` minus receive time (`eventStartTime` = open) | Available |
| `books.YES` / `books.NO` | CLOB `/book` for the two `clobTokenIds`. Outcomes are `["Up","Down"]`, so YES = Up. | Available. Prices and sizes are strings. Bids come ascending and asks descending, so the best level is the last element. |
| `minOrderSize` | `book.min_order_size` / `market.orderMinSize` | Available (5) |
| book freshness | `book.timestamp` (ms) | Available |
| `reference.btcMidUsd` | Coinbase or Binance bid/ask midpoint | Available, but only a proxy (see gap 1) |
| `reference.openingTargetUsd` | Gamma `eventMetadata.priceToBeat` | Available, with a delay. Present on a closed event; absent on the live event about 4 min after its start. A recorder would log first-seen time as `targetObservedAt`. |
| fees | Market `feeSchedule`: `{"rate": 0.07, "exponent": 1, "takerOnly": true}`, `feeType` `crypto_fees_v2` | Matches the contract's `0.07 × shares × p × (1 − p)`. YeNo's 1% overlay is not observable here. |

## Gaps and facts that matter
1. **Resolution uses a TWAP, not spot.**
   - Market description and `cryptoMarketConfig` (`twapEnabled: true`, `twapLookbackSeconds: 60`): "Up" wins if the Chainlink BTC/USD TWAP is ≥ the price at the start of the range.
   - The source is Chainlink Data Streams (`btc-usd-twap-60s-streams`), which needs credentials, so exchange spot is only an approximation.
   - It's not known which source feeds the evaluator's `btcMidUsd`.
   - Strategy implication, not tested: the result is a 60 s average, so the final minute is partly locked in, and spot-vs-target gaps overstate late moves.
2. **YeNo vs Polymarket.** YeNo's site references Polymarket's API and the contract cites Polymarket's fee docs, which suggests YeNo mirrors these markets. Whether YeNo's books (depth, spread, one-sidedness) equal Polymarket's is not confirmed.
3. **One-sided books near expiry are real.** About 53 s before close, the Up book had 0 bids and asks starting at 0.01.
4. **Tick size changes.** A closed 5-minute market showed `orderPriceMinTickSize` 0.001 (live: 0.01), so ticks can shrink near the extremes.
5. **Update stream.** REST polling gives snapshots only. Polymarket documents a public market WebSocket for L2 updates; it was not tested here (no long-running processes). Rate limits for continuous polling were not checked.
6. **Terms.** Only public read endpoints were used. Before any continuous recording, review the API terms and rate limits of Polymarket, Coinbase/Binance and YeNo. The Polymarket series is flagged `restricted: true` (trading is region-restricted). Reading public data worked from here.
