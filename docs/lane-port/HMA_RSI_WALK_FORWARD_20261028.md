# Research handoff: HMA / option-premium RSI walk-forward

Completed 2026-10-28 22:56:11 IST. Research only; production strategy source/settings/books were not changed. No broker requests, downloads, model calls, service restarts or orders were made. Existing data volume was mounted read-only in network-disabled, CPU/memory-bounded research containers.

Reproducible scripts, frozen plan, source/data hashes, full trade records, audit, chart and detailed findings:
`/Users/ramachandran/MACD-mini-ADV/research/hma_rsi_wf_20261028/FINDINGS.md`.
Research remains uncommitted under the owner's convention; this handoff is committed locally.

Fixed opening ATM pairs: 192 underlyings, 47 sessions, 2,314 selected contracts and 14.2 million exact-contract minute rows. Five walk-forward folds trained on 20 preceding sessions and tested on the next five: 25 OOS sessions, 2026-09-18 through 2026-10-26. The owner's confirmed RSI(14) rule runs independently on CE/PE premiums, enters a cross above 40 and exits above 80 or a later cross below 60. HMA lengths 9/16/25/49, slope/price-cross rules and 1/3/5/15/30-minute bars were frozen before outcome calculation.

Conservative full-sample modeled means: HMA -3.03%/trade on 257 trades, PF 0.71; RSI -4.40% on 1,156 trades, PF 0.62. Missing exits (7/34) are charged as full-premium loss stress, not claims of observed loss. Even the optimistically selected priced-exit subset remained negative: HMA -0.31%, RSI -1.50%. The train-only RSI selector chose 30 minutes in every fold; about 88% of its trades reached scheduled EOD liquidation rather than an indicator exit. Neither combined intraday policy is established as profitable.

Exploratory HMA index subgroup: 24 trades, +20.06% modeled mean, versus -5.40% in 233 stock trades. Only three index trades have live-observation eligibility, and historical spreads are sparse. This is not a separately validated index-only rule. RSI PE had marginal positive mean but became negative under doubled execution friction.

Stored histories are backfill-biased, many minutes have zero volume, actual historical L1 execution/depth and lot metadata are incomplete, and fills/costs remain scenarios. Strict complete-bar continuity yielded no warmed 30-minute RSI / HMA(49)-15-minute signals in the two reserved latest sessions. Do not claim these settings were ready to trade those sessions or that the app itself has no indicator values. No multi-session test or automatic promotion was performed.

Eight regression checks plus independent time/nonoverlap audit of 1,100,303 hypothetical trades passed. Research RSI matches 500 values from the app's pure IncrementalRSI. Independent audit reselected every fold from training only and reconciled totals. Corrected simulator issues were preserved with prior attempts; parameters/windows were not retuned after OOS inspection. Next research would require new prospective evidence, particularly for index-only HMA; do not reuse inspected subgroups as a sealed holdout.
