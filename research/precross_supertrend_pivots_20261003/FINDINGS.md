# Premium MACD before zero: Supertrend and pivots

3 October 2026. **Decision: indicators help forecast the cross, but the tested broad early-entry rules do not establish a profitable improvement over cross + 5 minutes.** A small high-confidence model group is an exploratory shadow candidate. Source apps, positions, ledgers and settings remain unchanged. This is an independent analysis using September market data; no September-trained Smart scorer or Claude conclusions enter it.

## What happens before a zero cross?

Premium can already be rising and above bullish Supertrend or its daily pivot while the slower MACD remains negative. Among eventual held-out crosses, the median premium 15 minutes beforehand was 6.8% below its price at the cross. Forming premium Supertrend was bullish in 65.7% of the available observations and premium was above P in 80.8%. Five minutes beforehand, those proportions were 67.8% and 84.0%. **These cross-aligned statistics condition on a future successful cross. They explain the path; they cannot estimate an early strategy's success rate.** Available observations differ by offset and are retained in `cross_aligned_summary.csv`.

For production MACD(12,26,9), let F and S be the last committed fast and slow EMAs on session-anchored 30-minute candles. The exact premium required to reach zero in the forming candle is:

`Z = (325*S − 297*F)/28`, and `preview MACD = (28/351)*(premium − Z)`.

Z is fixed within that candle and updates at the next committed candle. This gives a measurable distance to zero, rather than guessing from a visual upward bend. It does not forecast whether premium will reach Z. The signal-line histogram turning positive while MACD is below zero is the first setup condition; it is a different event from a MACD zero cross.

## Predictive study, including failures

Development uses nine September 1–11 sessions. September 12–14 is an embargo; holdout is twelve sessions September 15–30. Base trigger requires MACD below zero, above its preview signal, rising on three consecutive minute steps in the same forming candle, and linear projected time to zero no more than 30 minutes. All four observations must be contiguous and have option volume. Only the earliest trigger per contract/bar/rule is kept, before any cross in that bar, up to 14:45 IST. Each row below is a separately filtered population; do not sum them.

| Trigger after negative MACD starts rising | Trials | Cross ≤30 min | Cross by close | Early fills / timeouts | Median timely signal lead |
|---|---|---|---|---|---|
| MACD turn | 1371 | 24.9% | 61.4% | 1338 / 934 | 13 min |
| Completed premium ST | 494 | 39.9% | 77.3% | 465 / 280 | 11 min |
| Forming premium ST | 785 | 36.6% | 75.4% | 759 / 454 | 12 min |
| Above daily P | 1117 | 28.5% | 68.1% | 1084 / 720 | 13 min |
| Recent P reclaim | 324 | 13.0% | 45.7% | 321 / 252 | 12.5 min |
| Forming ST + above P | 705 | 38.4% | 78.0% | 679 / 396 | 12 min |
| Forming ST + pivot room | 620 | 37.7% | 77.3% | 600 / 353 | 12 min |
| Premium ST + underlying context | 446 | 39.5% | 77.6% | 428 / 244 | 12 min |

The useful combined setup is rising negative premium MACD + bullish forming premium Supertrend + premium above its **own prior-session daily P**. It raises the observed 30-minute cross rate from 24.9% to 38.4%. It also has a 78.0% cross rate by the close, but a much later cross cannot justify a trade with a 30-minute deadline. Of 679 executable early entries, 396 exit for no-cross timeout. Stops and other exits account for some further failures. A sampled positive preview is also weaker than a completed positive 30-minute MACD: only 31.1% have that confirmation within 60 minutes.

Successful timely combined-rule signals lead the cross by a median 12 minutes. Among the 194 cases with both an executable early fill and cross+5 fill, the latter premium is a median 7.7% higher than the early premium. That gain is conditional on obtaining both fills. Across **all 705 trials**, early-minus-wait mean return is −0.13 percentage points per trial, including false attempts and cancellations. Three early positions remain unresolved in this event experiment; marked values are disclosed separately, never classified as realised exits. Equal-ticket closed event profit factor is 1.19; it is not the cash-weighted portfolio PF below.

A separate L2 logistic cross classifier was trained only on September 1–11. MACD geometry/context features alone give held-out AUC 0.850 and Brier 0.138. Adding premium/underlying ST and pivot features gives AUC 0.868 and Brier 0.126. The AUC gain's 2,000-replicate session-block bootstrap interval is −0.005 to +0.036. Mean-day Brier improves on ten of twelve sessions, raw paired p=0.043, but Holm p=0.727 across the predefined family of seventeen comparisons. These results support a useful diagnostic, not a statistically cleared trading edge. Matching on day, call/put side, distance to zero and projected time preserves positive ST/above-P associations in common strata; recent P reclaim is worse. Matched association is not proof of causation.

## Can high-confidence predictions support a narrower entry?

| Model probability band | Trials | Cross ≤30 min | Early fills | Mean early-minus-wait / trial |
|---|---|---|---|---|
| (-0.001, 0.2] | 973 | 10.5% | 967 | -0.87 pp |
| (0.2, 0.5] | 243 | 44.4% | 237 | -1.28 pp |
| (0.5, 0.8] | 129 | 81.4% | 118 | -2.91 pp |
| (0.8, 1.0] | 26 | 100.0% | 16 | +7.27 pp |

The >0.8 model band contains **26 trials across nine held-out sessions; all 26 cross within 30 minutes**. Only 16 permit an early fill; ten cross before the next eligible entry or lack an eligible fill. Median signal-to-cross lead is 2.5 minutes; among the filled subset, median entry-to-cross lead is seven minutes. Those different populations explain the different medians. The band is inspected descriptively after the model evaluation and **was not a selected or tested portfolio policy**.

Its apparent paired return improvement is concentrated: the top three observations contribute 233.4 percentage points, more than the entire group's 189.1-point sum. Removing those three makes the remaining sum negative. The 0.5–0.8 band forecasts crosses well (81.4%) but has negative early-minus-wait return. A cross-probability model therefore cannot be promoted as a profitability model from this sample. Preserve the >0.8 band only as a hypothesis for prospective shadow observation.

## Trade count and trade quality in funded chronological books

These are **unranked intraday entry-isolation experiments**, not live-book profits, Smart-lane results or the full production multi-day/pyramid ladder. Baselines take all eligible crosses, including unexpected ones without an early precursor. A hybrid enters early when its rule fires and otherwise waits for cross+5. One pending/open position per contract; an unfunded/cancelled attempt releases the contract for later signals. Same-timestamp orders use alphabetical symbol order. Cash is applied in chronological order.

All policies share a 30% hard stop, trail armed at +30% with 25% off peak and round-trip breakeven floor, post-cross completed-negative-MACD invalidation when MFE <10%, and fresh-quote intraday closure from 15:25. Early entries without a timely cross exit after 30 minutes. No pyramids or staged exits. Quotes require nonzero volume; absent quotes leave marked unresolved inventory. Ticket is min(₹1 L, 10% equity), with exchange lots, maximum 125 lots and 2% prior-session volume cap. Base assumed paper slippage is five bp per side; this is not fee-complete real execution.

₹50 L, September 15–30:

| Policy | New positions / early | Fully closed wins | Closed PF | Marked P&L, ₹L | Δ vs cross+5, ₹L |
|---|---|---|---|---|---|
| Cross + 5 min | 917 / 0 | 42.1% | 1.40 | +29.56 | +0.00 |
| Cross + 1 min | 981 / 0 | 41.9% | 1.30 | +25.51 | -4.05 |
| MACD turn | 1760 / 1093 | 38.8% | 1.09 | +11.72 | -17.84 |
| Completed premium ST | 1174 / 392 | 40.9% | 1.31 | +27.66 | -1.90 |
| Forming premium ST | 1369 / 651 | 41.4% | 1.27 | +27.85 | -1.71 |
| Above daily P | 1618 / 927 | 39.9% | 1.16 | +18.88 | -10.68 |
| Recent P reclaim | 1187 / 303 | 40.6% | 1.27 | +25.34 | -4.23 |
| Forming ST + above P | 1312 / 590 | 41.5% | 1.30 | +29.64 | +0.08 |
| Forming ST + pivot room | 1287 / 537 | 41.3% | 1.29 | +28.19 | -1.37 |
| Premium ST + underlying context | 1168 / 382 | 41.3% | 1.26 | +23.48 | -6.08 |

Forming ST + P adds 395 positions (43.1%) and 590 early trades, while gaining only ₹7,745 over baseline. Closed PF drops 1.40 to 1.30 and win rate 42.1% to 41.5%. Daily-difference t=0.014 and corrected p=1.0. The development-selected rule was **Recent P reclaim**, which loses ₹4.23 L relative to baseline on holdout. No hybrid clears the multiple-testing correction against either cross+5 or cross+1.

₹1 L, same holdout:

| Policy | New positions / early | Fully closed wins | Closed PF | Marked P&L, ₹L | Δ vs cross+5, ₹L |
|---|---|---|---|---|---|
| Cross + 5 min | 342 / 0 | 42.1% | 1.36 | +2.09 | +0.00 |
| Cross + 1 min | 340 / 0 | 42.9% | 1.25 | +0.75 | -1.33 |
| MACD turn | 631 / 411 | 38.5% | 1.29 | +1.24 | -0.85 |
| Completed premium ST | 460 / 152 | 40.9% | 1.19 | +1.09 | -1.00 |
| Forming premium ST | 519 / 249 | 42.0% | 1.28 | +1.89 | -0.20 |
| Above daily P | 565 / 315 | 40.7% | 1.32 | +1.58 | -0.51 |
| Recent P reclaim | 467 / 140 | 40.7% | 1.32 | +2.02 | -0.07 |
| Forming ST + above P | 503 / 220 | 40.6% | 1.29 | +1.83 | -0.26 |
| Forming ST + pivot room | 474 / 185 | 41.4% | 1.25 | +1.25 | -0.84 |
| Premium ST + underlying context | 473 / 168 | 41.4% | 1.17 | +0.89 | -1.20 |

The combined ST+P rule loses ₹25,876 relative to baseline. Capital constraints change eligible fills, funding and compounding, so a ₹50 L improvement cannot be scaled to a ₹1 L book.

Period marked P&L is close equity minus the previous session's close equity. Books run continuously from September 1; holdout funding inherits each development path. Intraday exit attempts can leave unresolved carried positions, so period P&L can include carry-in marks. Position counts/quality refer to entries within the period and fully closed by its end. PF uses net positive/negative cash P&L on those closed positions. These are simulated funded paths, not independent equal-size randomised bets.

## Costs, timeout and source sensitivity

Same-cost policy comparisons add 20/50 bp per side plus assumed ₹20 per order to both early and baseline books. Fees are reserved for closing held positions; whole-lot funding can change the traded population.

| Capital | Hybrid | Base Δ, ₹L | +20 bp and fees Δ | +50 bp and fees Δ |
|---|---|---|---|---|
| ₹1 L | Recent P reclaim | -0.07 | -1.30 | -1.04 |
| ₹1 L | Forming premium ST | -0.20 | -0.25 | -0.61 |
| ₹1 L | Forming ST + above P | -0.26 | +0.06 | -0.00 |
| ₹1 L | Forming ST + pivot room | -0.84 | -0.02 | -0.99 |
| ₹50 L | Recent P reclaim | -4.23 | -4.10 | -11.91 |
| ₹50 L | Forming premium ST | -1.71 | -4.56 | -10.46 |
| ₹50 L | Forming ST + above P | +0.08 | -1.76 | -7.76 |
| ₹50 L | Forming ST + pivot room | -1.37 | -3.16 | -10.00 |

The ₹50 L ST+P difference changes from +₹0.08 L to −₹1.76 L and −₹7.76 L. This erases the small apparent primary advantage. Costs are sensitivity scenarios, not a reconstruction of spreads, taxes or market impact.

Timeouts were fixed descriptive checks, not tuned on held-out profits:

| Capital | Hybrid | 15-min Δ, ₹L | 30-min Δ | 60-min Δ |
|---|---|---|---|---|
| ₹1 L | Forming premium ST | +0.50 | -0.20 | -1.42 |
| ₹1 L | Forming ST + above P | -0.61 | -0.26 | -0.68 |
| ₹1 L | Forming ST + pivot room | -0.15 | -0.84 | -1.06 |
| ₹50 L | Forming premium ST | +2.13 | -1.71 | -2.94 |
| ₹50 L | Forming ST + above P | +3.49 | +0.08 | -1.14 |
| ₹50 L | Forming ST + pivot room | +2.35 | -1.37 | -0.89 |

A 15-minute deadline improves the ₹50 L ST+P path but worsens its ₹1 L path; a 60-minute deadline worsens the ₹50 L path. Selecting the best timeout after seeing these results would reuse the holdout and requires new data to validate.

Old-priority versus primary current-first candles:

| Same frozen rule | Current-first Δ, ₹L | Old-priority Δ, ₹L |
|---|---|---|
| Recent P reclaim | -4.23 | -3.85 |
| Forming premium ST | -1.71 | -1.31 |
| Forming ST + above P | +0.08 | +0.66 |
| Forming ST + pivot room | -1.37 | -2.01 |

The old-priority ₹50 L cross+5 baseline earns ₹30.64 L, versus ₹29.56 L current-first. The selected development winner also changes with source precedence. The same fixed ST+P rule retains a small advantage under old priority, but neither source produces a dependable margin after costs. Detailed September 18–29 and 19–29 comparisons are retained in the CSV; they are overlapping descriptive windows, not additional independent validation samples.

## Data, validation and practical conclusion

Available catalogs request 3,036 options and 214 underlyings; 2,993 series have observations. The universe is retrospective, not a decision-time archived chain. Daily eligible contracts use the first completed opening underlying minute, nearest unexpired expiry, prior-session option volume and the production 1.5× volume selector. Historical OI, spread/depth and tick quotes are unavailable. Regular-session one-minute archives supply observed prefixes to 30-minute MACD/ST calculations. Prior P/R1/R2/S1/S2 require adequate previous-session observations; missing levels remain unknown. Current-first and old-priority source conflicts are material, hence the repeated analysis.

Supertrend uses ATR(10), multiplier three, with completed and forming states kept separate. Forming bands use only high/low/close observed so far and can revert. Pivots use previous-session OHLC, never the completed current-session range. Definitions were checked against [TradingView Supertrend](https://www.tradingview.com/support/solutions/43000634738-supertrend/) and [Traditional pivot documentation](https://www.tradingview.com/support/solutions/43000521824-pivot-points-standard/).

Ten focused tests passed for zero-price/production preview parity, causal feature prefixes, prior-pivot and ST logic, event censoring, failed-trigger inclusion, delayed-entry cancellation and whole-lot cash handling. Independent fill reconciliation passes on all 64 primary books: terminal cash/quantity/equity, nonnegative cash and no pyramids/oversells. Source SHA/ledger and current positions/cash are unchanged; broker-order calls are zero and live orders remain locked. Raw aggregate evidence, model coefficients/preprocessing, source sensitivity, all predefined rules and seventeen corrected tests are saved here.

**Keep the current entry policy unchanged.** For shadow research, track negative-but-rising MACD's distance to the exact zero premium alongside forming/completed premium ST and prior daily pivots. Log every forecast, cancelled entry and failure, with quote/spread information and a predeclared deadline. A fresh frozen-model prospective comparison must demonstrate incremental net profit and stable quality before early entry can be enabled. This study has temporal holdout for prediction, but one month, retrospective catalog selection and twelve test sessions do not validate deployment or a Smart-lane change.

Reproduce using `analyze.py` (primary), `analyze.py --old-source`, `test_analyze.py`, `preserve.py`, `assess.py`, then `deliver.py`, with the bundled Python runtime and the two research dependency directories on PYTHONPATH. `PLAN.md` records the frozen design. `analysis.ipynb` is an executed companion against the saved study outputs; rerunning it does not contact brokers or modify books.
