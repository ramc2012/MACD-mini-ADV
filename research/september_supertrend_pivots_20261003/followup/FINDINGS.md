# Trade counts, trade quality, September 18–29 and Smart

3 October 2026. Research continuation of the matched September study. Source books, held positions and trading settings were read only. The running Smart books have no fills or journal decisions; every Smart number below is a separate retrospective reconstruction.

## Counts and quality in the old active book: September 1–18

| Rule | New positions | Closed / open | Closed wins | Closed PF | Mean return / median | New-position marked P&L, ₹L |
|---|---|---|---|---|---|---|
| Baseline | 621 | 544 / 77 | 40.4% | 1.14 | -2.77% / -7.30% | +19.18 |
| Premium Supertrend | 395 | 346 / 49 | 42.8% | 1.34 | -0.98% / -6.65% | +18.55 |
| Underlying Supertrend | 443 | 383 / 60 | 40.5% | 1.19 | -1.78% / -7.25% | +14.14 |
| Pivot headroom | 462 | 402 / 60 | 39.1% | 1.14 | -2.90% / -7.30% | +15.41 |
| Both Supertrends + headroom | 265 | 231 / 34 | 41.6% | 1.43 | +0.24% / -6.59% | +12.61 |

One trade means one flat-to-flat position episode. Pyramid additions and staged exits are fills within that trade. Baseline has 1,127 BUY fills and 1,188 SELL fills for these 621 new positions; premium ST has 715 and 780 for 395 positions. Carry-in is excluded from this quality table. Fully closed trades are measured at the stated period end; still-open positions do not receive a realised win/loss label. PF is total positive closed P&L divided by absolute total negative closed P&L. Percentage returns divide an episode's net cash P&L by all its purchase cost; the reported average gives every episode equal weight. The source fills have zero booked fees, so these are not fee-complete returns.

Premium ST removes 226 trades (36.4%). Win rate rises 2.3 percentage points and PF 1.14 to 1.34, while equal-episode mean return remains negative. Mean winning / losing cash P&L changes from ₹28,825 / −₹17,166 to ₹30,056 / −₹16,755. The combined filter removes 356 trades (57.3%); closed PF is higher but total active-book marked P&L is lower. These comparisons change the traded population and funding; they are descriptive, not a paired causal estimate.

The parent's active portfolio totals include carry-in: baseline +₹26.85 L, premium ST +₹27.65 L. New September positions alone contribute +₹19.18 L versus +₹18.55 L. Carry-in contributes +₹7.66 L versus +₹9.11 L as freed cash changes later additions to existing positions. Thus the +₹0.80 L total improvement does not show that newly selected signals earned more money. Daily marked drawdown falls from ₹10.97 L to ₹4.75 L. Premium ST still underperforms in the separate Sep 29–30 actual-entry book. The parent's funding correction, source-priority sensitivity and holdout conclusions remain applicable.

## Recheck of September 18–29

All six readable old MACD ledgers/backups were inspected. The retained old book has 284 fills on September 18, with its final fill at 15:30 IST. There are no MACD fills on September 19–29 in any of these books. September 28 has three stored stock-underlying signals, not option entries. Old marks continuing into Sep 28/pre-open Sep 29 do not establish continuous trading. The separate migrated book's Sep 29 trades are kept separate.

| Rule | Sep 18–29 new positions | Sep 18–29 marked P&L, ₹L | Sep 19–29 new positions | Sep 19–29 marked P&L, ₹L |
|---|---|---|---|---|
| Baseline | 96 | -13.24 | 0 | -17.32 |
| Premium Supertrend | 61 | -14.85 | 0 | -18.74 |
| Underlying Supertrend | 77 | -18.87 | 0 | -22.89 |
| Pivot headroom | 76 | -12.63 | 0 | -15.10 |
| Both Supertrends + headroom | 46 | -13.28 | 0 | -15.03 |

For the entry-filter rows, Sep 19–29 changes are losses/gains on inventory already carried at Sep 18, with zero new trades. They must not be presented as returns from a running strategy over the missing execution period. Including Sep 18 includes that day's new entries. Indicator-exit scenarios are also retained in `old_gap_inventory.csv`; their exits are explicitly modelled, not source fills.

The market-data gap is different from the execution gap. The old archive has full-session option/underlying observations on Sep 21–25 and Sep 28, and the current archive covers Sep 29–30. Sep 19–20 and Sep 26–27 are weekends. Coverage counts in `minute_coverage_by_day.csv` refer to the matched 702-option/210-underlying universe; Smart preparation expands the requested universe to 3,036 options and 214 underlyings, with 2,993 actual series. The old source is stopped and was not restarted.

## Smart: reconstructed September 18–29

**These are not live-book or out-of-sample profits.** The exported production ranker was fitted on Aug 25–Oct 1, including these September outcomes. Its metadata's walk-forward AUC does not turn this final-model replay into an independent test. No historical per-session models/trainer are available to remove that leakage. Use this only to inspect policy mechanics and exposure to the filters.

₹50 L book:

| Rule | New / fully closed | Closed wins | Closed PF | Mean / median return | Marked P&L / Δ, ₹L | Daily drawdown, ₹L |
|---|---|---|---|---|---|---|
| Baseline | 314 / 295 | 35.9% | 0.75 | -0.79% / -11.93% | -13.34 / +0.00 | 37.20 |
| Premium Supertrend | 208 / 195 | 41.0% | 0.85 | +1.16% / -9.93% | +0.26 / +13.60 | 39.73 |
| Underlying Supertrend | 269 / 255 | 37.3% | 0.77 | -1.56% / -12.16% | -9.57 / +3.77 | 38.07 |
| Pivot headroom | 261 / 244 | 39.8% | 0.93 | +3.88% / -5.56% | +0.42 / +13.76 | 23.84 |
| Both Supertrends + headroom | 172 / 162 | 46.3% | 0.88 | +5.48% / -3.50% | +3.62 / +16.96 | 32.06 |

₹1 L book:

| Rule | New / fully closed | Closed wins | Closed PF | Mean / median return | Marked P&L / Δ, ₹L | Daily drawdown, ₹L |
|---|---|---|---|---|---|---|
| Baseline | 191 / 177 | 44.1% | 1.05 | +2.89% / -5.14% | +0.99 / +0.00 | 0.80 |
| Premium Supertrend | 154 / 144 | 45.1% | 1.05 | +2.91% / -5.32% | +1.15 / +0.16 | 1.22 |
| Underlying Supertrend | 177 / 168 | 41.1% | 0.93 | +2.12% / -9.64% | +0.44 / -0.55 | 1.14 |
| Pivot headroom | 173 / 161 | 42.2% | 1.12 | +3.78% / -5.98% | +1.05 / +0.06 | 1.07 |
| Both Supertrends + headroom | 129 / 122 | 49.2% | 1.31 | +4.54% / -0.84% | +1.51 / +0.52 | 0.92 |

P&L measures equity at Sep 29 close less Sep 17 close, so it includes carried positions. Trade counts and closed quality include only entries made Sep 18–29 and closed by Sep 29. There are eight trading sessions in this window. The comparable Sep 19–29 rows, with seven sessions, are saved in `smart_comparisons.csv`. Neither book is merged with the old app or the other Smart capital.

Excluding September 18 changes the inference. ₹50 L Smart, September 19–29:

| Rule | New / fully closed | Closed wins | Closed PF | Mean / median return | Marked P&L / Δ, ₹L | Daily drawdown, ₹L |
|---|---|---|---|---|---|---|
| Baseline | 286 / 267 | 36.0% | 0.76 | -1.44% / -13.63% | -3.42 / +0.00 | 37.20 |
| Premium Supertrend | 191 / 178 | 40.4% | 0.83 | -0.06% / -11.11% | +2.82 / +6.24 | 39.73 |
| Underlying Supertrend | 242 / 228 | 37.7% | 0.78 | -1.69% / -12.01% | -7.88 / -4.47 | 38.07 |
| Pivot headroom | 240 / 223 | 39.5% | 0.93 | +3.40% / -7.50% | +4.03 / +7.45 | 23.84 |
| Both Supertrends + headroom | 156 / 146 | 45.9% | 0.88 | +5.12% / -3.50% | -1.56 / +1.85 | 32.06 |

The combined filter's relative improvement falls from +₹16.96 L with Sep 18 included to +₹1.85 L after Sep 18; its Sep 19–29 marked P&L is still −₹1.56 L. Premium ST improves that later interval by ₹6.24 L, while pivot headroom improves it by ₹7.45 L. Each is a separate funded path with its own opening inventory. Do not attribute the whole Sep 18–29 difference to superior new entries after the old app stopped.

## Smart: entire September

₹50 L:

| Rule | New / fully closed | Closed wins | Closed PF | Mean / median return | Marked P&L / Δ, ₹L | Daily drawdown, ₹L |
|---|---|---|---|---|---|---|
| Baseline | 601 / 574 | 39.4% | 1.09 | +3.39% / -8.38% | +62.18 / +0.00 | 58.70 |
| Premium Supertrend | 438 / 410 | 42.4% | 1.12 | +4.40% / -7.00% | +39.95 / -22.24 | 48.01 |
| Underlying Supertrend | 506 / 476 | 40.8% | 1.11 | +3.11% / -9.28% | +46.13 / -16.05 | 48.27 |
| Pivot headroom | 511 / 482 | 40.9% | 1.12 | +4.17% / -6.07% | +56.43 / -5.75 | 39.54 |
| Both Supertrends + headroom | 346 / 325 | 46.5% | 1.18 | +7.30% / -2.98% | +47.07 / -15.11 | 36.32 |

₹1 L:

| Rule | New / fully closed | Closed wins | Closed PF | Mean / median return | Marked P&L / Δ, ₹L | Daily drawdown, ₹L |
|---|---|---|---|---|---|---|
| Baseline | 284 / 269 | 48.7% | 1.28 | +9.45% / -1.07% | +5.63 / +0.00 | 0.80 |
| Premium Supertrend | 241 / 228 | 49.1% | 1.31 | +8.61% / -0.64% | +3.47 / -2.17 | 1.22 |
| Underlying Supertrend | 270 / 255 | 46.7% | 1.26 | +8.43% / -2.86% | +4.15 / -1.49 | 1.14 |
| Pivot headroom | 252 / 239 | 46.0% | 1.25 | +7.21% / -2.02% | +2.82 / -2.82 | 1.07 |
| Both Supertrends + headroom | 185 / 175 | 50.3% | 1.50 | +8.74% / +0.19% | +3.19 / -2.45 | 0.92 |

Daily drawdown is measured at session closes and can be much smaller than the sampled intraday drawdown stored in `smart_validation.json`. Full-month closed quality still excludes unfinished positions. Closed-P&L and average winner/loss values, the MACD/Blast source split, all eight fixed entry overlays and every cost/window comparison are retained in the CSVs.

## Costs, implementation and expiry limitations

Extra cost is applied per side on top of the configured five-bp paper slippage; each filter is compared with baseline at the same cost, allowing funding and whole-lot sizes to change. Commissions and spreads are not reconstructed. No overlay threshold was tuned on this follow-up.

| Capital | Period | Rule | Δ at base / +20 / +50 bp, ₹L |
|---|---|---|---|
| ₹1 L | full_sep | Premium Supertrend | -2.17 / +0.15 / -2.01 |
| ₹1 L | full_sep | Pivot headroom | -2.82 / +0.60 / -3.09 |
| ₹1 L | full_sep | Both Supertrends + headroom | -2.45 / +0.08 / -2.04 |
| ₹1 L | period_18_29 | Premium Supertrend | +0.16 / +1.25 / +0.15 |
| ₹1 L | period_18_29 | Pivot headroom | +0.06 / +1.15 / -0.64 |
| ₹1 L | period_18_29 | Both Supertrends + headroom | +0.52 / +1.98 / +0.79 |
| ₹50 L | full_sep | Premium Supertrend | -22.24 / -27.43 / -14.80 |
| ₹50 L | full_sep | Pivot headroom | -5.75 / -4.48 / +14.02 |
| ₹50 L | full_sep | Both Supertrends + headroom | -15.11 / -18.71 / -5.07 |
| ₹50 L | period_18_29 | Premium Supertrend | +13.60 / +15.74 / +25.62 |
| ₹50 L | period_18_29 | Pivot headroom | +13.76 / +16.02 / +34.58 |
| ₹50 L | period_18_29 | Both Supertrends + headroom | +16.96 / +19.91 / +30.42 |

The actual production Smart source and model match the running container's SHA256. The reconstruction uses its rank cutoff, five-minute delay, 15-second ranking wait quantized to the next fresh sampled minute (normally six minutes after a sampled MACD cross), dispersion sizing, 10% equity tickets, cash and exchange-lot limits, no pyramids, staged exits, stops/trails/targets, maximum holding-period logic and Blast intake. It uses an order broker that raises if a real order is attempted. Local book files live in a temporary research directory. The actual ten-second quote-age gate is preserved; zero-volume premium bars cannot become executable quotes.

The contract universe comes from available retrospective catalogs and observed traded metadata, not a point-in-time daily chain. Daily selection uses the first completed opening spot minute, nearest unexpired contract, production 1.5×-volume selector and previous-session option volume; unknown OI remains zero. Blast liquidity also uses this known selection-time volume proxy. Breadth and current-day features use observations available at the decision time. This differs from the exact historical live selector, spread/depth and tick stream. Current candles take priority for full-session Smart coverage; the parent matched-ledger study prioritizes old candles. Their profit totals are not comparable baselines.

Some Smart positions cannot be exited by the execution quote gate. Inventory valuation is separate: valid observed OHLC, including zero-volume bars, can value an open position without creating a quote or an exit. After expiry, residual holdings use intrinsic value against the observed expiry-day underlying close proxy. This is a valuation scenario, not official settlement, exercise/physical delivery, realised P&L or released cash. Quantities and cash remain tied up when there is no fill. This adjustment is made causally during the replay, so later equity-based sizing does not use a frozen pre-expiry premium. No absent quote is silently treated as a zero-price fill.

| Capital | Rule | Open / expired residuals | Expiry-scenario residual value, ₹ |
|---|---|---|---|
| ₹1 L | Baseline | 15 / 7 | 448,114 |
| ₹1 L | Premium Supertrend | 13 / 5 | 234,384 |
| ₹1 L | Underlying Supertrend | 15 / 5 | 280,572 |
| ₹1 L | Pivot headroom | 13 / 6 | 226,957 |
| ₹1 L | Both Supertrends + headroom | 10 / 4 | 150,449 |
| ₹50 L | Baseline | 27 / 8 | 5,861,066 |
| ₹50 L | Premium Supertrend | 28 / 7 | 2,834,114 |
| ₹50 L | Underlying Supertrend | 30 / 8 | 4,098,714 |
| ₹50 L | Pivot headroom | 29 / 8 | 4,855,648 |
| ₹50 L | Both Supertrends + headroom | 21 / 6 | 3,184,218 |

## Decision and validation

Review status: **Share with caveats**. Closed-trade arithmetic and isolated-policy mechanics are checked; Smart out-of-sample edge and official expiry settlement are unavailable.

The trade-count reduction and closed-quality changes are real arithmetic effects within the matched old schedule. They do not establish a stable incremental edge: the old later-book/holdout results differ, carry-in financing contributes, and Smart's final ranker has seen September labels. Keep live/paper entry rules unchanged. Retain premium ST as a shadow risk-control candidate; the hard combined filters sacrifice many trades. Any Smart profit or filter gain here requires a prospective comparison with a frozen model and decision-time chains/quotes before it can support deployment.

Ten focused tests pass: causal feature prefix and opening selection, quote age, entry delay including mature-batch handling, actual staged target accounting, overlay handling, whole-lot funding, real-aware timestamp serialization, and valuation without invented expiry/zero-volume execution. The rank wait applies to the oldest due candidate in a batch, so some MACD entries join at exactly five minutes and some Blast entries join immediately; an isolated MACD candidate normally fills at the sixth sampled minute. Every reconstructed book reconciles episode cash flows to terminal equity, has nonnegative cash, valid exchange-lot fills, no pyramids/oversells, causal entry delays and no unpriced daily holdings. Live books remain unchanged; final checks are in `PRESERVATION.json`.

Reproduce with the bundled Python and `PYTHONPATH=followup/.deps:../supertrend_pivots_20261003/.deps` from the parent research folder: `python followup/test_smart_replay.py`, `python followup/smart_replay.py`, `python followup/summarize.py`, `python followup/deliver.py`. The saved evidence can be reviewed without broker access. `analysis.ipynb` is executed against the saved tables.
