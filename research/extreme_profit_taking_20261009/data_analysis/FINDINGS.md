# Extreme-move profit taking: frozen paper-trade diagnostics

Snapshot: **9 October 2026, 21:40:22 IST**. Research only: no production source, settings, trades, candles or books changed. `collect.py` used SQLite `mode=ro`, `query_only=ON`, bounded exact-contract queries and read-only HTTP endpoints. `evidence.json` freezes the inputs; `summary.json`, `coverage.csv` and `comparison.csv` make the calculations inspectable. Deployed and checkout hashes for all eleven captured source files match (`source_hashes.json`).

The data supports testing tighter protection after extreme premium gains, particularly in Blast. It does **not** establish a profitable strategy or production-ready exit. Both declared alternatives still lose money across this restricted cohort. The larger +50% improvement comes with material forfeited upside, and the Smart contribution rests on one trade.

## Retained records and provenance

| Book | Fills | Closing slices | Distinct positions represented | Fully closed positions | Partial slices | Retained-slice realized P&L |
|---|---:|---:|---:|---:|---:|---:|
| MACD | 708 | 352 | 218 | 202 | 150 | -₹108,824 |
| Smart ₹50L | 64 | 18 | 18 | 16 | 2 | -₹229,135 |
| Smart ₹1L | 1 | 0 | 0 | 0 | 0 | ₹0 |
| Blast | 84 | 39 | 39 | 39 | 0 | -₹227,835 |
| MP | 96 | 12 | 12 | 12 | 0 | -₹32,393 |

MACD and Blast history starts 29 September; Smart ₹50L starts 5 October. MP's custom closed table contains only 9 October visible closes, so its twelve records are not lifetime performance for its 96 fills. Main snapshots are independent per-database read transactions, followed by HTTP snapshots, not one atomic snapshot across all books.

There are no missing MFE fields in retained closing records. Availability does not establish accuracy: MFE reflects observed marks, potentially missing outage intervals, and its reference basis can change after additions. Fills contain no historical feed-mode/source tag. Current settings say Fyers, but these records do not independently certify a real-data versus simulation cohort. This is mixed-policy retrospective paper history with no untouched holdout.

## What the recorded extremes show

Seven Blast closes and two MACD closing slices recorded at least +50% favorable premium excursion before finishing at a net loss. For example, Blast `BSE:SENSEX26O0173000CE` entered 29 September at ₹181.4407, recorded ₹295.15 (+62.67%), and exited ₹180.9095 (-0.29%). That is a sampled-path diagnostic; selling at ₹295.15 was never modeled.

At the snapshot, MACD has 53 open positions with reported unrealized +₹477,580; the sum of current-quantity individual peak-to-mark differences is ₹614,952. Smart ₹50L has 30 positions, unrealized +₹44,819 and a corresponding difference of ₹578,026. Blast has six positions, unrealized +₹1,060,308 and a corresponding difference of ₹483,694. Those sums combine individual highs reached at different times, retain only current quantity after actual sales/additions, and **are not portfolio drawdowns, executable missed profit, or simultaneous book high-water values**. MP's engine endpoint returned HTTP 404, so its open-book state is unavailable here.

## Declared alternatives and accounting

The two threshold choices were declared before the first comparison, with no threshold search:

1. Arm after a **completed minute close** reaches +50% premium gain; on a later completed close at or below `entry + 0.75 × (highest completed close − entry)`, sell at the next minute open.
2. Identical rule, armed at +100% premium gain.

Both are whole-position/residual exits. A 25% favorable-profit giveback is different from a 25% fall in premium: entry ₹100 and completed-close peak ₹200 gives a ₹175 signal threshold. The fill remains the following available minute open, including overnight gaps, with 5 bps modeled exit slippage. Original entry fills and initial quantities are identical. No same-bar peak sells or interpolated missing bars are used.

For completed single-BUY/multiple-SELL lifecycles, the comparison retains all actual partial sales completed at or before the alternative fill time, then sells only the remaining whole lots and cancels later actual sales. Actual partial cash is included; it never credits the original quantity after shares have already been sold. Scaled-in/multiple-BUY positions and still-open lifecycles are excluded.

The primary cost scenario charges ₹20 per actual or hypothetical order leg to both choices, including each partial SELL. Existing actual entry/exit fills already contain production slippage, so baseline 5 bps is not charged twice. Hypothetical next-open exits receive 5 bps once. The stress scenario adds 20 bps to all exit proceeds under both choices. CSVs additionally retain actual-recorded-fee baseline results and an alternative charging actual entry/prior-sale fees plus ₹20 for a new exit. Statutory taxes, spread, impact and depth are not comprehensively modeled.

Session clock: 09:15 inclusive to 15:40 exclusive IST, from the frozen deployed `sessions.py`; 2 October excluded using `market_calendar.py`. Official timing references are in `summary.json`.

## Coverage and exclusion

There are 220 eligible completed single-BUY lifecycles before candle quality checks: 182 with one full sale and 38 with actual partial sales. Of these, 148 have every required exact-contract minute: 111 MACD, 30 Blast, seven Smart ₹50L. Sixty-four have missing minutes; eight are too short to contain an eligible full minute. Administrative expiry closes are excluded explicitly. No data was backfilled.

Three positions have a zero-volume hypothetical fill minute under at least one declared arm. The primary comparison removes those positions from **both** alternatives, leaving the same 145 entries and quantities for both arms: 110 MACD, 28 Blast, seven Smart. This is a retrospective data-adequacy exclusion, not a proposed live rule that observes future minute volume. Positive completed-minute volume still cannot prove execution at its opening print or sufficient depth for the position.

The excluded +50% optimistic events are SUNPHARMA (+₹43,046), ADANIPORTS (+₹58,633) and MACD GLENMARK (+₹3,073). The optimistic zero-volume results remain separately saved, never used as the headline. The strict common cohort has 128 single-sale lifecycles and seventeen MACD lifecycles with actual partials.

## Paired primary results

| Rule | Positions changed | Improved / worsened | Baseline net | Alternative net | Difference | Difference with added 20 bps exit stress |
|---|---:|---:|---:|---:|---:|---:|
| +50% arm, 25% favorable-profit giveback | 19 / 145 | 14 / 5 | -₹1,187,550 | -₹859,798 | +₹327,752 | +₹327,097 |
| +100% arm, 25% favorable-profit giveback | 8 / 145 | 8 / 0 | -₹1,187,550 | -₹904,934 | +₹282,616 | +₹282,050 |

| Book | Primary positions | +50% difference | +100% difference |
|---|---:|---:|---:|
| MACD | 110 | +₹34,465 | +₹11,912 |
| Blast | 28 | +₹178,545 | +₹270,704 |
| Smart ₹50L | 7 | +₹114,742 | ₹0 |

For MACD, all effects occur in the seventeen retained single-BUY lifecycles with partial sells. +50% changes seven: five improve and two worsen; +100% changes two and both improve. Smart's entire improvement is one CAMS trade: actual -43.90%, hypothetical +53.31%, using its exact historical minute path rather than a hypothetical peak fill. It is insufficient evidence for changing that lane.

The +50% choice forfeits ₹173,696 compared with eventual actual proceeds across five winners. Blast `NIFTY26O0622700PE` falls from actual +111.67% to modeled +38.81%, costing ₹75,622. MACD `KOTAKBANK26OCT405CE`, including retained partial cash, falls from actual +78.52% to +43.77%, costing ₹31,964. Grouped by actual exit day, the +50% overlay is ₹119,153 worse on 1 October. A higher aggregate is not an assurance of better outcomes on the next session.

The larger recorded diagnostic peak givebacks are not used in these deltas. No portfolio reinvestment, margin/cash interaction, new entries after early closes, pyramiding or full production engine behavior is replayed. In particular, excluding scaled-in positions and incomplete paths creates selection bias. Arms are only exploratory paper/shadow candidates, and this analysis does not choose between them as an optimized winner. ATR confirmation and new partial-harvest schedules remain untested.

## Reproduction

Recompute from the frozen inputs without accessing production:

```sh
/Users/ramachandran/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 /Users/ramachandran/MACD-mini-ADV/research/extreme_profit_taking_20261009/data_analysis/analyze.py
```

`source_hashes.json` records the frozen evidence SHA-256, deployed/local source SHA-256 values and script SHA-256 values. `comparison.csv` is primary paired evidence; `comparison_optimistic.csv` preserves zero-volume sensitivity; `coverage.csv` records exclusions; `closed_slice_diagnostics.csv` lists observed MFE and giveback per closing slice. Refreshing `collect.py` against live databases would produce a new snapshot and must not be described as reproducing this frozen input.
