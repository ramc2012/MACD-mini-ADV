# Option-price exhaustion: frozen paper experiment

Independent audit passed: **104,244 assertions, 13,869 numeric checks, 1,563 feature bars, 234 prefix checks and 354 cash rows; zero errors**. See the [detector/accounting audit receipt](../exhaustion_review/detector_accounting_audit.json).

This tests the exact held option's premium extreme and subsequent failure, or a confirmed momentum divergence and subsequent failure. It does not identify the future maximum in advance. Definitions were frozen in [PLAN.md](PLAN.md) before evaluating outcomes; no thresholds were searched or changed. Calls and puts both use rising **option premium**, so the signal has the same interpretation for either long option.

All work is offline research. Production code, settings, holdings and ledgers are unchanged. These prototypes sell **all remaining whole lots** after retaining earlier actual partial cash. A proposed future half-harvest plus runner schedule is **untested** here.

## Inputs, context and common cohort

The frozen original single-BUY sample contains 220 completed lifecycles on 169 exact contracts. The new indexed read-only extraction contains 309,609 unique one-minute OHLCV rows, from 24 September through 9 October 2026. Three prior derivative sessions were requested. No extra ten-session queries were needed: every position had at least 100 complete three-minute bars somewhere before entry, but only 159 had 100 consecutive complete bars immediately before entry.

The session grid is 09:15–15:40 IST under the post-3-August-2026 derivatives regime; 2 October is excluded. Three-minute bars align to 09:15 and require all constituent minute rows. The one-minute tail at 15:39 is not fabricated into a three-minute bar. Missing/invalid expected three-minute bars reset indicators and pivot state; legitimate overnight/weekend/holiday gaps do not. The session/calendar sources match the original evidence hashes, and their full text plus regime source are frozen in the new input. No prior-regime or pre-2026 data is blended into this sample.

The 148 original holding paths with complete exact-minute coverage are unchanged: comparison with the earlier frozen input found **zero changed, added or removed overlapping minute rows**. The 100-consecutive-bar warm-up and original holding-minute gates together retain 118 lifecycles; 102 fail at least one gate. Exclusion reasons overlap: 61 lack the required immediate warm-up, 64 have original holding-minute gaps and eight are subminute. Collector and detector warm-up calculations disagree on **0** positions.

An additional 7 positions have a zero-volume hypothetical fill minute under at least one compared policy. Removing each from **every** policy leaves **111 identical entries, quantities and baseline exits**: 84 MACD, 23 Blast and four Smart ₹50L. Twelve retain actual partial-sale lifecycles. This differs from the earlier 145-position study: stricter warm-up and the common three-policy fill filter change the cohort. Compare the new policies with the reference recomputed on **111**, not with the earlier aggregate on 145.

Positive full-minute fill volume is a retrospective data-quality filter, not information available at that minute's opening print. It does not prove opening liquidity, spread or depth. The unfiltered sensitivity is saved separately.

## Exactly what was tested

- **Band extreme → failure:** at a completed post-entry three-minute close, arm above the preceding-20 upper band with RSI14 ≥70. For ten subsequent complete bars in the same session, exit only after a later close returns below the contemporaneous prior-20 upper band and below the preceding bar's low. New extremes refresh arming. Signal volume and estimated residual net liquidation profit must be positive.
- **Confirmed divergence → failure:** a premium swing high requires two strictly lower highs on each side and becomes available only when the second right-hand bar closes. Consecutive confirmed highs must be separated by 3–20 complete bars; the newer must be at least 0.2% higher with RSI ≥60, while RSI falls ≥5 points or MACD histogram falls to ≤80% of an earlier positive histogram. Within ten bars of confirmation in the same session, a close below the intervening neckline can exit, with positive signal volume and residual net liquidation profit. A break already present at confirmation is evaluated at confirmation, never backdated.
- **Original reference:** preserve the earlier completed **one-minute** +50% arm and 25% favorable-profit-giveback rule. It receives no new RSI, divergence, signal-volume or residual-profit gates. This exception preserves the named original rule instead of silently changing the reference.

New entries, changes in entry size, additions and reinvestment are not replayed. The runner preserves actual entry price/quantity, all actual sales at or before the alternative fill, and their proceeds. It sells only the remaining whole-lot quantity and replaces later sales. Both choices pay ₹20 per entry/sale order; only new modeled exits receive 5 bps adverse slippage, once. Existing actual fills already contain their production slippage. The stress scenario adds 20 bps to every exit proceed under both choices. The recorded-fee scenario is retained separately. Statutory taxes, spread, impact and depth are incomplete.

## Same-cohort results

All amounts below use the standardized ₹20/order scenario. The common actual-exit net is **-₹986,961**.

| Alternative | Changed / 111 | Improved / worsened | Net result | Difference from actual | Difference under added 20 bps exit stress |
|---|---:|---:|---:|---:|---:|
| Band extreme → failure | 35 | 22 / 13 | -₹706,832 | +₹280,128 | +₹279,569 |
| Confirmed divergence → failure | 9 | 6 / 3 | -₹927,010 | +₹59,951 | +₹59,832 |
| Original +50% / 25% reference | 15 | 11 / 4 | -₹821,177 | +₹165,784 | +₹165,452 |

Every alternative remains net negative. A higher retrospective difference is not an established strategy edge or a probability of finding a maximum.

| Book | Positions | Band difference | Divergence difference | Same-cohort reference difference |
|---|---:|---:|---:|---:|
| MACD | 84 | +₹356,909 | -₹11,284 | +₹56,234 |
| Blast | 23 | -₹76,781 | +₹71,235 | +₹109,550 |
| Smart ₹50L | 4 | ₹0 | ₹0 | ₹0 |

The book tradeoffs differ. Band protection improves the retained MACD cohort while harming Blast. Divergence improves Blast while slightly harming MACD. These observations do not authorize selecting the best policy separately for each book after seeing outcomes.

The twelve actual-partial lifecycles have baseline net **+₹246,462**. Band changes all twelve with a net difference of **-₹48,028**; divergence changes four with a difference of **-₹61,565**. The old reference changes five with a difference of **+₹56,234**. Early protection therefore also cuts runners that later recover or extend.

Summed negative differences are **-₹303,747** for band, **-₹76,683** for divergence and **-₹141,732** for the reference. Each harmed lifecycle in this run was profitable under actual exits; that statement was checked against baseline net rather than inferred from the field label. Their eventual actual profits were foregone in varying amounts. Future highs are not used by either detector.

The zero-volume-permitted sensitivity has 118 positions and gives differences of +₹439,480, +₹219,213 and +₹402,762, respectively. These larger optimistic figures are not the headline.

## Deterministic examples

These are the two largest favorable and two most harmful primary differences per extreme detector, sorted by difference then position key. They illustrate failure modes; they were not used to adjust definitions. Signal times are IST, at completed-bar close; fills use the next eligible minute open, which may share that close timestamp.

| Detector / example | Exact option | Signal IST | Prior actual sales | Residual quantity | Actual lifecycle net | Alternative lifecycle net | Difference |
|---|---|---|---:|---:|---:|---:|---:|
| Band extreme → failure / most harmful | NSE:NIFTY26O0622700PE | 30 Sep 14:48 | 0 | 780 | +₹115,862 | +₹21,140 | -₹94,723 |
| Band extreme → failure / most harmful | NSE:NIFTY26O0622800PE | 30 Sep 14:48 | 0 | 520 | +₹74,156 | +₹14,022 | -₹60,134 |
| Band extreme → failure / most favorable | NSE:ICICIBANK26OCT1320PE | 01 Oct 13:57 | 0 | 3500 | -₹56,469 | +₹727 | +₹57,197 |
| Band extreme → failure / most favorable | NSE:ADANIPOWER26OCT200CE | 30 Sep 10:24 | 0 | 14200 | -₹33,788 | +₹20,713 | +₹54,501 |
| Confirmed divergence → failure / most harmful | NSE:TATASTEEL26OCT185PE | 01 Oct 10:36 | 1 | 13750 | +₹49,852 | +₹18,668 | -₹31,185 |
| Confirmed divergence → failure / most harmful | NSE:TCS26OCT2100CE | 05 Oct 14:06 | 0 | 1125 | +₹31,977 | +₹6,580 | -₹25,397 |
| Confirmed divergence → failure / most favorable | NSE:TVSMOTOR26OCT4200CE | 30 Sep 10:36 | 0 | 1050 | -₹31,734 | +₹6,572 | +₹38,306 |
| Confirmed divergence → failure / most favorable | NSE:NIFTY26O0622800CE | 29 Sep 12:27 | 0 | 780 | -₹100 | +₹24,496 | +₹24,597 |

For example, band failure protected ICICIBANK from a large later loss after its premium spike failed, but sold NIFTY 22700PE near ₹160.22 before the actual eventual exit produced much more profit. Confirmed divergence protected TVSMOTOR, but exited the TATASTEEL residual after one actual partial sale and forfeited ₹31,185 of the eventual actual lifecycle net. The complete as-of feature values, swing confirmation times and reasons are in `example_events.json` / `trigger_events.json`.

## Sentiment and availability boundary

The tested input schema is exact option OHLCV, asset type, expiry and historical `downloaded_at`. It contains no IV, Greeks, OI, PCR, skew, aggressor side, signed flow or sentiment. All 309,609 rows identify asset type `option`, but expiry metadata is NULL in 283,155 rows; no DTE-normalized rule was claimed or tested. Option volume alone cannot identify opening bullish demand, short covering, hedging or spread activity.

The [timestamped availability audit](../exhaustion_review/AVAILABILITY.md) finds direct chain context for only nine of the 220 original lifecycles; 211 stock/SENSEX lifecycles lack it. All 3,471 bounded composite fields are NULL, with only 0–6 prior sessions against 20 needed. Existing collector timestamps precede fetching and do not establish an `available_at` time. No causal chain/sentiment feature is credited to these results, and missing context is not neutral sentiment. The [research synthesis](../exhaustion_review/RESEARCH.md) and [prospective specification](../exhaustion_review/PROSPECTIVE.md) cover optional future as-of context joins.

Historical `downloaded_at` can postdate the trade/decision. The frozen OHLCV reconstructs market bars, not proof that every feature was present in the live engine then. Original fills lack historical source-mode provenance. The present sample is retrospective, mixed-policy and selected for completed single-BUY paths; open winners, additions, missing data and short warm-up are excluded. There is no untouched holdout, out-of-sample parameter selection or full production portfolio replay. The experiment offers inspectable candidate behavior; it does not validate production promotion or claim to detect an absolute maximum.

## Reproduction and receipts

- `collect_context.py`, `inputs.json.gz`, `collection_summary.json`, `lookback_coverage.csv` and `metadata_quality.json`: frozen source data, exact schemas, query plan, source hashes and missingness.
- `PLAN.md`, `declaration.json`, `detector.py`: declared rules and causal feature implementation.
- `evaluate.py`, `evaluation_summary.json`, `comparison.csv`, `comparison_optimistic.csv`, `trigger_events.json`, `quality_exclusions.csv`, `warmup_checks.csv`: identical-cohort accounting and decisions. Evaluation summary records plan/detector/runner/input hashes.
- `build_findings.py` and `example_events.json`: deterministic report construction and examples.

Recompute only from the frozen input with the bundled Python:

```sh
/Users/ramachandran/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 /Users/ramachandran/MACD-mini-ADV/research/extreme_profit_taking_20261009/exhaustion_analysis/evaluate.py
```

Refreshing the collector is a new snapshot, not reproduction of these frozen inputs.
