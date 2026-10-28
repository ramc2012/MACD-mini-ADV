# HMA(20) / KAMA crossover research

Completed 2026-10-28 23:10:25 IST. Research only; no lane/settings/ledger changes, broker requests, downloads, app restarts or orders.

Full reproducible record: `/Users/ramachandran/MACD-mini-ADV/research/hma20_kama_wf_20261028/FINDINGS.md`. Frozen plan, source hashes, pair signals, tests, full hypothetical trades and independent fold/timing audit are retained. Research remains uncommitted under the owner's convention.

The preceding ATM label was approximate: nearest available common opening CE/PE strike, frozen for the session. Median strike-to-spot gap 0.69%, 95th percentile 3.00%; no systematic ITM/OTM band comparison. The follow-up reuses the exact prior source/execution snapshots.

Tested HMA20 crossing above KAMA to buy the option premium, below to exit; baseline KAMA(10, fast 2, slow 30). Efficiency periods 5/10/20 and candle lengths 1/3/5/15/30 were frozen before pair results. Same five 20-train/5-test folds, 25 historical OOS sessions. Fixed KAMA10 selected 30-minute candles in every fold.

Fixed baseline: 776 hypothetical trades; 749 priced exits averaged +2.87% net (+1.70% under doubled crossing/slippage), positive in each priced-exit fold. There were 27 unresolved exits; full-premium-loss stress on each produces -0.72% full-sample mean / PF 0.93. Priced cases are a coverage-selected subset, not complete policy performance. Missing exits would need mean net -79.54% to erase the priced-exit sum; their true returns are unknown. A train-selected KAMA period/timeframe did not improve the sample: 715 trades, -1.08% full-loss-stress mean, +2.51% on 690 priced exits.

Index subgroup: 44 trades, +16.13% modeled mean, only five with matching live-observation eligibility; exploratory rather than independently validated. Latest two reserved sessions yielded no qualified slow-pair trades under strict warmup/gap rules. Intraday only; no funded whole-lot portfolio or multi-session result is claimed. Previously inspected dates were reused for a new hypothesis, so this is not fresh independent confirmation. Do not auto-promote the pair.

Four new tests matched 1,500 KAMA values to the app's pure indicator, checked HMA20 warmup/prefix causality, cross direction/equal touches, and gap resets. The execution core matches the previous eight-check audited snapshot. An independent audit checked 301,983 hypothetical trades for timing/duplicates/nonoverlap and reselected each fold using training only. Source/data files were mounted read-only in network-disabled, one-CPU research containers.
