# App review and extreme-move profit taking

9 October 2026, after market close. This is a paper-only review and exploratory research. Production source, saved settings, books, holdings, orders, and historical data were preserved.

## Recommendation

Fix the three reproduced persistence defects before relying on an exit-policy experiment. Then compare an extreme-gain profit protector in shadow paper trading, separately by lane. The strongest initial rationale is in the Blast lane, whose 40%-of-price trail can return much of an extreme open gain. A blanket earlier exit for the main MACD lane is not established by this review.

The proposed principle is: **an extreme move arms protection; a retracement triggers harvesting; whole lots permitting, retain a runner.** The thresholds below are declared research hypotheses, not validated optimal settings. The follow-up requested by the user examines the exact option premium's extremes and momentum divergence, plus the actual availability of sentiment/flow inputs; see the linked follow-up below. Its technical rules have different lane effects from the simple gain-giveback rule, so these results must not be combined into a universal exit recommendation.

## What is deployed

At 21:32 IST, the maintained `macd-mini-parallel` engine reported paper mode, `live_orders_unlocked=false`, an authenticated Fyers connection, 678/678 warmed indicators, zero history errors, and a closed session. Stored `auto_trade=true` permits paper activity despite the environment default being false. This post-close observation does not establish next-session feed freshness or readiness.

The MACD book reported 53 open positions, equity ₹53,68,756, realized inception P&L −₹1,08,824 and unrealized P&L ₹4,77,580. These are marked paper accounting values; they do not establish an independently validated strategy edge. The displayed portfolio and summary were checked in the running terminal.

All eleven backend files captured by the final research collector matched the engine container; five shared desk module hashes also matched. The local frontend build differs from deployed Nginx assets: 12 of 15 local build files are missing or different. This establishes build divergence, not which frontend source is preferable. Rendered UI findings refer to the actual served app. No rebuild was performed.

Evidence: `runtime_receipt.json`, `runtime_review.py`, `frontend_parity.json`.

## Implementation findings

1. **P1 — Smart restart repeats a profit stage.** `SmartLane._restore()` loads excursion values but omits persisted `exit_stage`, `entry_stage`, and `entry_anchor`. In a disposable reproduction, +30% stage 1 sold a lot, a restart reset the stage to zero, and an unchanged quote sold stage 1 again. Restore the saved stage state consistently with the main MACD engine. See [smart_lane.py](/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/smart_lane.py:311).
2. **P1 — Blast can lose an armed trail on an unclean restart.** Periodic maintenance flushes journal watchers but omits the open-position checkpoint; explicit checkpointing occurs at fills and clean shutdown. Entry 10 → eligible tick 20 armed a 12 trail; unclean restart restored peak 10 with no trail. Add bounded periodic persistence of execution state. See [engine.py](/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/engine.py:1736).
3. **P2 — Restart converts valuation highs into executable peaks.** MACD, Smart, and Blast rebuild their execution peak from `max_price`, which includes valuation-only marks. Entry 100 → execution tick 140 set trail 105; valuation-only mark 200 left it unchanged, but restart raised it to 150. Persist the eligible execution peak separately and restore that field. See [engine.py](/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/engine.py:664).

Four disposable-book reproductions use the deployed matching source and a broker stub that raises if any broker order is attempted. Three establish the defects above; the fourth establishes the policy interaction below. See `code_review/REVIEW.md`, `code_review/reproduce_exit_review.py`, and `code_review/reproduction_results.jsonl`.

## Why current profit protection can give back gains

The main MACD lane already sells **one lot at +30%, +50%, and +75% above current average cost**, retaining the last lot. It also has a −30% hard stop and a trailing stop that activates at +30%, sits 25% below peak premium, and is clamped to modeled breakeven. MACD invalidation is currently enabled and applies only while observed lifetime MFE is below 10%.

The verified Blast settings are −50% hard stop, +30% trailing activation, 40% trail below peak, and no partial ladder. Smart MACD uses a different rule family, including a +100% full target and a four-session limit; do not transfer results among lanes without testing.

Three design consequences matter:

- **Price giveback differs from profit giveback.** With fixed entry 100 and peak 150, the main 25% price trail triggers near 112.50, leaving only 12.50 of the 50 gain before costs. A hypothetical 25% profit-giveback rule triggers at 137.50, preserving 37.50. At peak 200, the current Blast 40% price trail triggers at 120, returning 80% of the open gain. A stop is a trigger level, never a guaranteed fill.
- **Pyramiding precedes harvesting.** A qualifying add returns from the tick handler before partial profit taking. The add threshold uses original entry, while sales use rising average cost. In a zero-cost deterministic example, one 100-unit lot entered at 100 and three ticks at 200 produce four lots averaging 175 with no partial sale; a subsequent 150 exit realizes a ₹10,000 loss. This is a strategy interaction, not a measured historical frequency or a proven benefit from changing the order. See [execution.py](/Users/ramachandran/MACD-mini-ADV/engine/src/macd_trader/execution.py:447).
- **Fixed one-lot sales shrink as a proportion of large tickets.** A 12-lot ticket sells only three lots over all three stages; a two-lot ticket can take one partial and retain its last lot. Fractional quantities must not be used to simulate a partial-exit policy.

The main portfolio view shows max/min return but does not expose active trailing stop, profit-giveback threshold, protection activation, or completed partial stage. These would make profit protection reviewable in the UI. Persisted maximum marks remain hindsight diagnostics because valuation and execution highs currently differ.

## Research interpretation

ATR can normalize an extension to recent volatility; it does not supply a reversal probability. Prices can remain at an extreme during a strong trend, so an immediate full exit can sacrifice the large winners on which a trend strategy depends. This supports testing arming plus confirmation or a runner, without proving the chosen thresholds. [Fidelity ATR](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/atr), [Bollinger Bands](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/bollinger-bands).

Option protection must also observe the held premium: IV and elapsed time can alter its value while underlying direction remains favorable. Market sales are exposed to bid/ask spread and available size; LTP and candle peaks are not assured liquidation prices. These general options mechanisms apply to the research design; the OIC material describes US options and does not establish NSE/BSE-specific execution costs or rules. [OIC Greeks](https://www.optionseducation.org/advancedconcepts/volatility-the-greeks), [OIC bid/ask execution](https://www.optionseducation.org/news/understanding-the-bid-and-ask-prices-for-options).

Stopping rules can add or subtract value depending on the process and sampling frequency; daily index-futures research is not proof for this intraday-options app. Testing many exit configurations also increases the chance of fitting noise. This study declares only two premium thresholds and requires genuinely new prospective evidence. [Kaminski and Lo](https://dspace.mit.edu/entities/publication/bb69ca4b-0cdc-487f-831d-63b2e84fafee), [Bailey et al.](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).

## What the historical comparison found

Two alternatives were declared before comparison: arm at a completed minute-close premium gain of +50% or +100%, then sell the remaining whole position after a later completed close gives back 25% of the highest favorable close-to-entry gain. A trigger fills at the next eligible minute open with 5 bps adverse slippage, never at the observed high or an assumed stop price. Actual prior partial-sale proceeds are retained and only the residual whole lots are sold. No threshold grid was searched.

The same **145 fully closed, single-BUY positions** are compared under both alternatives: 110 MACD, 28 Blast and seven Smart ₹50L, including seventeen MACD lifecycles with actual partial exits. The cost scenario charges ₹20 per entry or sale order to both alternatives. Actual fills already include production slippage and are not charged 5 bps again. Results are rupee P&L on this selected cohort, not portfolio returns.

| Exit alternative | Actual-exit net P&L | Alternative net P&L | Difference | Improved / worsened |
|---|---:|---:|---:|---:|
| +50% activation, 25% profit giveback | −₹11,87,550 | −₹8,59,798 | +₹3,27,752 | 14 / 5 |
| +100% activation, 25% profit giveback | −₹11,87,550 | −₹9,04,934 | +₹2,82,616 | 8 / 0 |

| Lane | Positions | +50% difference | +100% difference |
|---|---:|---:|---:|
| Main MACD | 110 | +₹34,465 | +₹11,912 |
| Blast | 28 | +₹1,78,545 | +₹2,70,704 |
| Smart ₹50L | 7 | +₹1,14,742 | ₹0 |

Both alternative totals remain negative. Main MACD changes involve only seven positions at +50% and two at +100%; the pooled result primarily reflects Blast and one Smart CAMS trade. The +50% rule sacrifices ₹1,73,696 across five eventual winners, including ₹75,622 on one Blast NIFTY put. The top three favorable differences explain 68.81% and 65.70% of the respective net improvements. These are concentrated outcomes, not independent replications across lanes. Adding a further 20 bps to every exit under both alternatives leaves differences of +₹3,27,097 and +₹2,82,050, but does not model complete statutory charges or market impact.

Coverage is materially selective. Of 220 completed single-BUY candidates, 148 have complete exact-contract minute paths; 64 have missing minutes and eight contain no eligible full minute. Three positions with a zero-volume hypothetical fill minute under either rule are excluded from both primary alternatives. Administrative expiry valuations, additions/pyramids and open positions are excluded. The complete-path and full-minute-volume filters use retrospective information: they are data-adequacy filters, not executable live selection rules. Positive minute volume does not prove a bid or enough depth existed at the opening print. Large still-open Blast winners are outside this completed-position comparison.

The minute schedule follows the app's 09:15–15:40 derivative session, with its holiday calendar; NSE's current derivative closing time was separately verified against [official market timings](https://www.nseindia.com/static/market-data/market-timings). BSE timing was not independently certified as part of this audit. Historical fills lack feed-mode provenance, so current Fyers settings do not independently establish the source of every historical observation. Policies changed during the retained history, starting 29 September for MACD/Blast and 5 October for Smart. No untouched holdout remains.

An independent reviewer checked 220 lifecycle paths, 301 slice-to-fill matches, both alternatives including prior partial cash, and 3,377 arithmetic/timestamp equalities with zero discrepancies. That validates the diagnostic calculations, not executable performance. No full portfolio replay, capital reuse, later re-entry, or production-engine equivalence is established.

Inspect the [data findings](/Users/ramachandran/MACD-mini-ADV/research/extreme_profit_taking_20261009/data_analysis/FINDINGS.md), [paired rows](/Users/ramachandran/MACD-mini-ADV/research/extreme_profit_taking_20261009/data_analysis/comparison.csv), and [independent audit](/Users/ramachandran/MACD-mini-ADV/research/extreme_profit_taking_20261009/code_review/DATA_AUDIT.md). Frozen inputs, reproducible scripts, source hashes and zero-volume sensitivity are saved beside these files.

## Follow-up: exact-option price extremes, divergences and sentiment

The user requested option-price extremes and divergences as possible signs of the maximum move. A research-only causal detector and accounting runner now evaluate two frozen three-minute-bar rules on the **exact held contract**. Both long calls and long puts benefit from rising premium. The rules identify weakening extremes; neither identifies the absolute future maximum or a calibrated probability of a top.

- **Premium extreme then rejection:** a completed close reaches at least two standard deviations above its preceding 20-close mean with RSI14 ≥70. Within ten subsequent bars of the most recent arm, in the same session, a later close back below the upper band and below the preceding bar's low triggers. Positive completed signal-bar volume and estimated residual net liquidation profit are required.
- **Confirmed momentum divergence then breakdown:** consecutive confirmed higher OHLC highs with weaker closing RSI or MACD histogram create a setup; a subsequent close below the intervening trough triggers. Two right-hand bars must close before the later pivot is available. The newer high starts after entry, and the setup expires after ten bars or the confirmation session ends. Exact amplitude, separation and momentum thresholds are frozen in the plan.

These alternatives close the full remaining whole-lot quantity to isolate signal usefulness. A half-position harvest plus retained runner remains a separate untested proposal. Volume ratio, wick fraction and premium ATR extension are logged as context rather than added to an optimized rule after the outcomes are seen.

Pre-entry warm-up requires 100 consecutive complete three-minute bars, with missing expected bars resetting indicator state. Original holding-minute paths must be complete and unchanged since the earlier freeze. Of 220 candidates, 118 pass these combined gates; seven positions with a zero-volume modeled fill under any compared policy are excluded from every alternative, leaving **111 shared completed lifecycles**. This is a different cohort from the earlier 145-position comparison.

| Exit rule on the same 111 positions | Net P&L | Difference versus actual exits | Improved / worsened |
|---|---:|---:|---:|
| Actual exits | −₹9,86,961 | — | — |
| Premium extreme and rejection | −₹7,06,832 | +₹2,80,128 | 22 / 13 |
| Confirmed divergence and breakdown | −₹9,27,010 | +₹59,951 | 6 / 3 |
| Earlier +50% / 25% giveback reference, unchanged one-minute definition | −₹8,21,177 | +₹1,65,784 | 11 / 4 |

The premium-rejection rule improves the retained MACD subset by ₹3,56,909 but worsens Blast by ₹76,781. Divergence improves Blast by ₹71,235 but worsens MACD by ₹11,284. Neither changes the four retained Smart lifecycles. The twelve already-partial MACD lifecycles collectively worsen by ₹48,028 under rejection and ₹61,565 under divergence, while the named giveback reference improves them by ₹56,234. All pooled alternatives remain net negative, and the rejection rule sacrifices ₹3,03,747 across thirteen actual winners. These observed subgroup effects do not justify selecting a lane-specific winner after examining this sample.

Sentiment was audited rather than assumed. Existing chain history directly covers only **nine of the original 220** lifecycles (NIFTY/BANKNIFTY); 211 stock/SENSEX lifecycles lack their own chain history. All 3,471 bounded whale composite scores are null because slot history is insufficient. Available PCR/OI/model IV and inferred flow rows use collector-start timestamps and lack reliable arrival/computation-availability times. They are sampled retrospective context, not certified causal inputs. News sentiment and a persisted skew series were not found. Missing context is not neutral context, and index sentiment is not stock-specific sentiment.

Inspect the [follow-up findings](/Users/ramachandran/MACD-mini-ADV/research/extreme_profit_taking_20261009/exhaustion_analysis/FINDINGS.md), [frozen rules](/Users/ramachandran/MACD-mini-ADV/research/extreme_profit_taking_20261009/exhaustion_analysis/PLAN.md), [detector](/Users/ramachandran/MACD-mini-ADV/research/extreme_profit_taking_20261009/exhaustion_analysis/detector.py), [feature-availability audit](/Users/ramachandran/MACD-mini-ADV/research/extreme_profit_taking_20261009/exhaustion_review/AVAILABILITY.md), and [prospective logging design](/Users/ramachandran/MACD-mini-ADV/research/extreme_profit_taking_20261009/exhaustion_review/PROSPECTIVE.md).

Independent review passed 104,244 assertions with zero errors, including 1,563 independently reconciled feature bars, 234 prefix-invariance cases and 354 accounting rows. All earlier execution, selection, missing-data, excluded-open-winner, cost and no-holdout caveats apply. The implementation remains an offline research prototype; no production detector or monitor was deployed.

## Proposed prospective policy

The follow-up priority is the held option's own premium. Keep the two technical detectors frozen for prospective paper comparison against the unchanged baseline and simple profit-giveback reference. Collect contemporaneous bids, size and actual feature-availability timestamps before crediting executable protection or sentiment. The lane effects above are exploratory; they do not establish a reason to deploy the retrospectively best rule per lane.

The simple safeguard remains a reference: after a completed minute close reaches +50% or +100% above entry basis, allow at most 25% giveback of the favorable premium gain. Update the eligible high using information available at the time. Place the modeled exit at the next eligible fresh quote, keeping the more protective existing stop. This policy family does not include a new partial fraction.

Separately freeze an untested extension policy: direction-adjusted underlying distance from EMA20 reaches 2.5 prior ATR14 on completed strategy bars, and the held option has positive liquidation P&L after costs. Arm protection. Confirm retracement with a subsequent bullish close below the preceding bar's low, or bearish close above its preceding high. Test selling half the remaining whole lots, rounded down and retaining at least one; test one-lot positions separately. Retain a monotonic trail and all existing hard-stop, expiry, and holding-limit exits. The exact 2.5 ATR and 1.5 ATR runner examples are hypotheses, not established settings.

The combined policy must define handling for prior staged sales, additions after arming, fee allocation, and remaining quantity. Persist original basis, eligible execution peak, activation, stages, and the protective level independently of display marks. Use actual fresh bids and size for prospective shadow evaluations. Compare total net P&L and drawdown with identical initial cash/inventory, not profit capture alone. Include lost large winners, order costs, expiry distance, liquidity, and trend regime.

Detailed declaration: `POLICY.md`. No new strategy or live/shadow monitor was deployed by this review.
