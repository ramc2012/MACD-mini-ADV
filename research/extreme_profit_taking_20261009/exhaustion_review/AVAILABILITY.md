# Available evidence for option-price exhaustion research

Read-only snapshot taken 9 October 2026 after the session, from the running
MACD-mini-parallel engine's existing SQLite stores and the current application
source. No broker request, backfill, production import, setting change, or book
mutation was performed. The previously checked checkout has no applicable
AGENTS.md in its directory or parent directories.

The most widely available inputs are exact-contract minute OHLCV and technical
features reconstructed from completed bars. Option-chain positioning and modeled
IV/delta histories also exist, but cover NIFTY/BANKNIFTY only. They are not a
stock-option sentiment history. News/text sentiment and a persisted IV-skew
series are absent from the inspected source and active schemas.

## Capability matrix

| Feature | Historical data actually retained | Current/display capability | Research limitations |
|---|---|---|---|
| Exact option OHLCV | All 169 symbols in the original 220 single-BUY completed lifecycles have minute history. Across their entire retained histories: 1,047,585 rows; individual counts 2,007 to 14,041, median 5,733; earliest observed symbol row 3 Aug, latest 9 Oct 15:39 IST. | Existing chart/candle engine and broker-fed minute capture. | 688,324 rows, 65.71%, have zero volume. Availability is contract-specific; global span is not proof of continuous lookback for every contract. The separate exhaustion collection validates the exact preceding-bar grid. |
| RSI, MACD/histogram, ATR, Bollinger bands, extension/velocity, OBV, volume climax | Can be reconstructed causally from the exact minute OHLCV. `historical_indicators` stores MACD/signal/histogram, Bollinger/KAMA-derived fields at stored timeframes. | Strategy indicators and live displays exist. | Use preceding completed bars only, reset sessions consistently, freeze initialization/aggregation conventions, and distinguish real traded volume from unchanged zero-volume bars. OBV and price/volume divergences are derived proxies, not bid-side liquidation evidence. |
| OI history for exact stock options | No dedicated stock-chain history found. Raw ticks have an OI column, but the sampled latest 50,000 ticks contain zero non-null OI values. | Contract selection cache stores current/selected OI; live Greek display can fall back to it. | Selection cache is overwritten rather than a dated chain history. OI cannot be reconstructed from OHLCV or guessed from volume. Missing stock OI changes are unknown. |
| Index-chain OI, bid/ask, spot, volume, VIX | `chain_snapshots` stores these fields for NIFTY/BANKNIFTY. `chain_days` records 18 collected days per root from 3 Sep to 9 Oct. In the bounded original-lifecycle interval: NIFTY 87,700 leg rows / 1,754 snapshots; BANKNIFTY 87,650 / 1,753. | Collector requests chains roughly once per loop minute during the derivative session. | The broker request uses `strikecount=12` and front expiry; these are sampled limited-strike chains, not the entire market or every held expiry. No SENSEX or stock-chain collector. Snapshot timestamps are collector-start times, with no provider quote time or fetched/available time. |
| IV and Greeks | `whale_strike_windows` retains modeled IV and delta for collected index legs, with `delta_source`; no broker-supplied historical Greek series was found. | `option_greeks()` solves IV and gamma/GEX from option and underlying LTP, checks freshness, and caches briefly. | Historical IV is fractional model sigma; displayed watchlist IV is percent. Intrinsic-fallback delta rows have no IV and must not masquerade as modeled IV. Ordinary watchlist IV/gamma/GEX is current-only. Model outputs depend on synchronized prices, expiry assumptions, rate, and stale/illiquid LTP. |
| Historical IV skew / term structure | No dedicated persisted skew or term-structure series or computation found. | Individual model IVs exist. | Index skew could be researched from same-snapshot valid IVs with matched expiry/moneyness/delta and quote checks. That would be a new derived feature; stock skew and multi-expiry structure are unsupported by current chain coverage. |
| PCR / chain positioning | `whale_windows` retains OI PCR, previous-window PCR, jump, volume PCR, net delta exposure, and window timestamp. All 3,471 bounded rows have OI PCR. | Auction/whale UI displays PCR, OI walls and delta windows. | PCR refers to the captured strikes/front expiry. It is positioning context, not a measured bullish/bearish probability, news sentiment, or proof of the maximum option move. Delta/OI direction mostly uses a premium-change proxy. |
| Index option-versus-futures divergence | `whale_windows` stores divergence flag/ratio; `whale_flow_minutes` stores CVD/OFI and source labels. Bounded flags: NIFTY 786/1,736; BANKNIFTY 511/1,735. | Existing whale/auction flow logic. | This compares modeled option positioning and inferred futures flow, not the held option's RSI/MACD price divergence. Futures OI is null in all inspected retained flow-source aggregates and bounded windows. A false flag can mean unavailable/flat futures context; inspect status and supporting fields, not just boolean. |
| Calibrated whale/sentiment composite | The schema persists composites and z-scores, but every one of the 3,471 bounded window composites is null. | Composite calculation and alert logic are implemented. | `history_days` is only 0 to 6 versus the required 20 prior sessions at the slot. The app correctly withholds the composite. No numeric calibrated sentiment score is usable for these lifecycles. |
| CE/PE breadth | `dispersion_history` has 59 `live` 30-minute rows from 28 Sep 15:15 to 9 Oct 15:15; 311 `reconstructed` rows from 25 Aug 09:15 to 28 Sep 14:45. Counts and eligibility denominators are stored. | Live ATM-cohort MACD breadth. | Reconstruction infers an ATM cohort because the selection file is overwritten. Preserve the source label; do not use reconstructed rows as verified contemporaneous breadth. Cadence is 30 minutes, not 3 minutes. |
| Raw tick bid/ask / size / order flow | Active raw tick range is 5 Oct 10:51:15 to 9 Oct 15:59:59 IST. Recent 50,000-row sample: 43,086 valid bid/ask pairs, 46,390 positive bid-size rows, 50,000 pending buy/sell quantity pairs. Condensed minute/ladder history exists for earlier captured days. | Live depth/flow, absorption, OFI and footprint diagnostics. | Raw retention is short and not backfillable. The tail includes off-session ticks and is not a representative intraday liquidity sample. Aggressor side is inferred, not exchange-labelled; condensed rows include quality/confidence counters. Historical availability varies, and raw OI was absent in the sample. |
| News / social / text sentiment | No news/text sentiment table or ingestion/feature code found in active inspected schemas or engine/frontend source searches. | None found in this app. | Cannot add genuine news sentiment to this retrospective test without a separate point-in-time source, publication and arrival times, entity mapping, and validation. Missing news context is unknown, not neutral. |

## Direct coverage for the original 220 lifecycles

The exact original frozen cohort comprises 204 stock-option lifecycles, seven
SENSEX, five BANKNIFTY, and four NIFTY. Only nine lifecycles have a directly
supported chain root. They use five exact symbols; the bounded receipt freezes
5,623 matching strike-window rows and 5,685 exact-leg raw chain rows, plus 3,471
root window rows over the cohort time span. This is a scoped extract, not a
database copy.

For the nine directly supported lifecycles, a backward join to the latest
`whale_windows.ts <= decision`, on the same IST day with sample age at most 300
seconds, matches all nine entry samples and 3,088 of 3,106 observed full-minute
endpoints. The other 18 endpoints have missing/stale sampled context. The
remaining 211 lifecycles have no direct chain root.

Across all 98,336 observed holding-minute endpoints, each index has 60,600
sample-time matches at that tolerance. Such joins can describe general market
context for stock holdings; they must remain explicitly labelled index proxies.
They do not become a stock's own chain sentiment, and unmatched context must
remain missing rather than zero or neutral. These denominators are observed
frozen minute endpoints, not proof of complete expected coverage.

## Point-in-time qualification

The timestamp join above is **sample-time proximity, not certified availability**.
`snapshot_stamp()` is called before sequential chain REST requests, SQLite stores,
futures quote requests and window evaluation. `chain_snapshots` and
`whale_strike_windows` have no provider quote timestamp, request-completion time,
or `available_at` column; `whale_windows.ts` is the latest sampled chain time.
Consequently, a row stamped before a proposed decision may have been delivered
or computed afterward. `source='live'` identifies its producer rather than
certifying that the field was available at the decision.

Price-only indicators can be audited from completed historical bars. The frozen
chain/flow context can support retrospective descriptions and missingness
analysis. It should not be credited with causal exit improvements unless actual
availability is established or a clearly justified conservative lag is used and
its limitations remain explicit. Never join to the nearest row on either side
of a decision, replace missing context with future context, or use same-day EOD
fields in earlier decisions.

For OI/IV quality, the bounded source rows show most Greeks use the model, but
intrinsic fallbacks occur; most option flow signs use `flow_source='premium'`,
with a small share `prints`. A model dealer-exposure sign convention or OI build
does not identify the true buyer/seller or dealer inventory. No feature here can
identify the future absolute maximum with certainty; these support falsifiable
exhaustion-risk hypotheses.

## Evidence and source pointers

- `collect_availability.py` contains the exact bounded read-only queries and
  as-of matching method. `availability_evidence.json` contains schemas, query
  parameters/results, row hash, frozen original input hash, per-symbol candle
  spans, source/quality counts, and all 220 per-lifecycle coverage receipts.
- Source schema: `engine/src/macd_trader/whale.py:139` through 201;
  `tick_store.py:51` through 110; `dispersion.py:1` through 39.
- Collector coverage and timestamps: `desk.py:380` through 408, `:445` through
  469; `whale.py:313` through 314, `:1443` through 1516.
- Broker chain sampling: `brokers.py:565` through 625.
- Current Greek provenance/freshness: `greeks.py:1` through 17, `:154` through
  178; `engine.py:1991` through 2040.
- Premium/inferred flow provenance: `whale.py:845` through 867; composite warmup:
  `whale.py:105`, `:1234` through 1269.
- Historical selection overwrite: `contracts.py:511` through 513; reconstructed
  breadth warning: `dispersion.py:11` through 19.

An old file explicitly named `ticks.corrupt-20261007.sqlite3` is malformed and
was excluded. The active `ticks.sqlite3` queried successfully. No repair,
cleanup, integrity mutation, or substitution from old snapshots was performed.
