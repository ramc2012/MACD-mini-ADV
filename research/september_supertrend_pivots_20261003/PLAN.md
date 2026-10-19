# September MACD ledger-anchored Supertrend and pivot tests

Written before overlay results, 3 October 2026. This replaces the unmatched simplified replay as the evidence path for this request. Source apps, ledgers and settings remain unchanged.

## Baseline and scope

- Read the stopped old MACD app's actual trades, orders and equity history with SQLite mode=ro/query_only. Reconcile the entire retained ledger and the August 31 and September daily endpoints. Preserve all carry-in positions and actual exchange lot quantities.
- Separately read the current app's September 29–30 fills. These are a separate book, not a continuation of the old account; never concatenate capital or sum account returns as a single account.
- Baseline entry and exit fills are the actual recorded fills. Entry-filter experiments retain or reject whole position episodes, preserve the recorded trade sizes and staged/pyramiding fills, and process cash in timestamp order. No hypothetical freed-cash opportunities outside the actual filled-entry schedule are added. An unaffordable entry is rejected; an unaffordable add is skipped and later sells are limited to the actual remaining holding.
- Verify that the no-filter replay reproduces every baseline fill, cash and realised P&L. Carry-in positions are unaffected by entry filters. This is a matched test of the actual filled-entry schedule; it does not replay historical universe selection or ranking of unfilled signals.
- Analyse the full September calendar and report the actual sessions with entries, missing sessions and open positions. Do not describe the old ledger's last mark as September 30 profit. Source data from the old archive and the current copy cover different dates and must be audited and merged with explicit precedence.
- Primary accounting measure is recorded realised September P&L. Also show open cost, available terminal valuation and funded-book outcomes. Never count missing terminal quotes as zero. Expired contracts require observed expiry metadata and a separate, clearly labelled valuation/closure assumption; there are no recorded old-app expiry-day fills after its stop.

## Frozen overlays

Use Wilder Supertrend(10,3) on completed session-anchored 30-minute bars. Prior-day Traditional pivots are P=(H+L+C)/3, R1=2P-L, S1=2P-H, R2=P+H-L, S2=P-H+L. No current-day highs/lows enter pivot levels. CE underlying direction is bullish; PE is bearish; option-premium direction is always bullish.

Entry filters, tested individually rather than an exhaustive parameter grid:

1. Premium Supertrend bullish.
2. Underlying Supertrend aligned with CE/PE.
3. Both Supertrends aligned.
4. Premium above its prior-day P.
5. Underlying on the favourable side of prior-day P.
6. At least 0.5 completed-30-minute ATR of headroom to the next underlying P/R1/R2/S1/S2 in the trade direction.
7. Both Supertrends plus underlying favourable P.
8. Both Supertrends plus underlying pivot headroom.

Unknown indicator readings pass the recorded trade through; disclose coverage and report classifiable-subset diagnostics. Do not discard unmatched or illiquid baseline episodes just to obtain a clean sample.

Exit overlays are separate from entry filters: adverse premium Supertrend, adverse underlying Supertrend, and premium loss of prior-day P. Observe the condition only at a completed 30-minute close and execute at the next observed one-minute open in the same session. Preserve earlier recorded baseline fills and stop processing future fills after a full overlay exit. These added fills are modelled with the app's 5 bp slippage, not falsely labelled actual tick fills. Missing next-minute prices defer the exit and are counted.

## Validation and interpretation

- Freeze a September 15 split: September 1–11 train/context; September 15–30 holdout. No threshold is tuned on holdout. Include no-trade-for-new-entries as a control with carry-in positions preserved.
- Evaluate all fixed comparisons with paired daily realised-cash differences and entry-day clustered episode effects; correct the complete overlay family with Holm. Report actual entry-day count rather than implying 21 independent entry sessions when the old app was inactive.
- Compare CE/PE, preserve the largest winners, and report partial/open positions separately. Compare the current September 29–30 book as a separate later check.
- Run sensitivity with additional 20/50 bp cost per side on matched fills; recorded baseline is shown with its actual zero fees and already-slipped fill prices.
- Validate feature prefix invariance, exact ledger baseline accounting, absence of overselling, nonnegative funded cash, price visibility and strict next-minute exit execution. Preserve immutable input hashes and reproducible scripts/notebook/report.
- The result will establish only the incremental effect on these actual recorded entry opportunities, subject to tick/quote and missing-history limits. It will not prove performance on never-funded opportunities or a new market regime. Applying the indicators in this study does not change the running trading rules.

## Accounting refinements before overlay results

The first exact funding assertion exposed legacy overdrafts: 248 recorded BUY fills could not have been paid from contemporaneous replay cash, and the stored equity history confirms minimum cash -Rs 960,915.09. Preserve a separate exact recorded-ledger reproduction allowing the observed overdraft, and compare every indicator against both that reproduction and a strict funded baseline. Do not silently discard these fills and still call the result the actual book. The strict baseline uses the same actual entry schedule and sizes but rejects unaffordable new episodes/adds; sells remain limited to held whole lots.

The old app's active-window test ends 18 September; its untouched holdout is 15–18 September (four entry days). A separate full-month scenario extends the recorded positions to September 30 but has no new old-app entries after September 18. It uses observed expiry metadata and an intrinsic-value scenario from the last available underlying quote on the expiry date for still-recorded expired positions, never a stale pre-expiry option quote. This is a modelled valuation, not a recorded expiry fill or official settlement. Do not use post-stop overlay benefits as proof of an improvement to a continuously running strategy. The later current-app September 29–30 book is a separate two-session check. No filter or threshold is selected from those results.

Missing same-day unexpired quotes use the last available historical price with a stale-age flag, as carried paper marks; if no prior price exists, valuation remains unknown. Distinguish carried marks, observed same-day prices and expiry-intrinsic scenarios in every book's output. These refinements repair baseline/valuation interpretation rather than optimise an observed overlay result.

The merged extract contains 918,052 OHLC disagreements among 4,706,544 overlapping old/current minute rows (19.5%). The primary replay prefers the old archive before migration; a validation rerun instead prefers the current archive throughout. Save that rerun separately, compare the same frozen rules, and do not choose whichever source produces better results. Costs are not repeated in this source validation because the primary cost scenarios already cover the unchanged recorded fills.
