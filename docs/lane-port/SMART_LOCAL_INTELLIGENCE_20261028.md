# Smart lanes: local allocation and admission repair

Owner request: inspect missing Smart trades, remove Qwen from Smart lanes, and allocate capital to the best available MACD signals. Work started at 2026-10-28 07:39:30 IST; deployment/verification is pre-open. No live-market success or improved profitability is claimed.

## Why there were no new trades

Persisted Smart journals for the prior session contain 143 decisions in each book:

| Decision | ₹1 lakh initial book | ₹50 lakh initial book |
|---|---:|---:|
| QWEN_UNAVAILABLE | 14 | 62 |
| NO_PRICE | 57 | 55 |
| MACD_FADED | 24 | 25 |
| LOT_ABOVE_TICKET | 48 | 0 |
| TOO_ILLIQUID | 0 | 1 |
| TAKEN | 0 | 0 |

The model service captured 76 Smart requests with shared context unavailable and zero completed Smart reviews that session. These were infrastructure blocks, not model judgments. The price path immediately discarded a signal if its first quote check failed, even though subsequent shared ticks could have supplied a valid price. The larger book retained three earlier positions; it was not an empty/reset account. Small-book lot affordability, failed premium-MACD confirmation and real liquidity constraints remain legitimate blockers.

## Local policy

`smart-local-allocation-v1` has no inference/network dependency. It consumes the existing MACD BUY stream and does not modify the original MACD lane's signal generation, entry rules, settings or ledger. Blast intake remains disabled by default; retained Blast-sourced positions keep their exits.

Admission waits the configured five minutes and the existing ranking window, then prioritizes due candidates using bounded components:

| Component | Weight | Basis |
|---|---:|---|
| Retained research rank | 25% | Existing exported 22-feature ranker percentile; not calibrated expected return |
| Premium momentum | 25% | Current option-premium MACD from the existing indicator preview |
| Side breadth | 25% | Existing CE/PE MACD breadth |
| Spot alignment | 15% | Fresh side-signed underlying open/VWAP and NIFTY return |
| Diversification | 10% | Existing exposure to the same underlying |

Missing contextual observations are neutral, not invented confirmation. The current quote source/times, component values, weights, allocation priority and shared flow context are recorded in the existing decision journal. Historical flow studies did not establish a useful directional edge, so CVD remains diagnostic and does not veto or favor a side. No new flow tracker or broker socket was added. Current context reads do not recompute signals or increment the MACD crowd tally.

The existing research-rank/breadth ticket sizing remains within the configured 10% of current equity, available cash, whole lots and liquidity capacity. A low score sizes down rather than vetoes. Cash is checked again for every actual submission. Budgeting now reserves the larger of adverse LTP slippage and the matching shared ask, avoiding a ticket calculated at LTP that cannot fill at the ask. Existing hard stops, staged exits, trails, targets, expiry exits, hold limits and extreme-booking rules remain in force. Smart execution is explicitly forced to paper even if a global execution setting is changed.

Missing/stale executable prices are retried every five seconds using the existing shared quote stream, without extending the original three-minute deadline after the scheduled entry check. `WAITING_PRICE` is journaled once; expiry becomes `NO_PRICE`. Every retry rechecks original constraints. No stale price is accepted and no missed historical signal is replayed. Duplicate intake for an identical signal/bar is suppressed. New entries require a regular exchange session.

Qwen remains configured for Vanguard/CBE. Smart no longer imports or calls its transport; health explicitly reports `local_model.required=false` and `intelligence.qwen_required=false`. The Smart page shows local allocation status and an input note in each decision. Old Qwen journal rows remain readable history. Aggregate book returns include earlier policies and are labelled accordingly; they are not new-policy results.

## Restart defect and preservation

Post-deployment position checks exposed a pre-existing restoration bug: Smart replayed trade quantities and excursion highs, but ignored persisted `entry_stage`, `exit_stage` and `entry_anchor`. ETERNAL's two completed profit stages reappeared as stage zero. No new fill occurred.

The engine was stopped to isolate the writer. The original consistent pre-deployment backups were retained, a second pre-correction backup was taken, trade-table digests were required to match, and only matching held identities' checkpoint stages/anchor were restored. The restoration code now reads the persisted stages/anchor/fees. A regression test performs two actual partial paper exits, restarts the book, and verifies that the completed stages cannot be sold again. Final runtime stages/anchors for all three held positions match the original backup, including ETERNAL at entry stage four / exit stage two.

Backups and private evidence: `runtime/smart-intelligence-20261028/`. Seven book backups passed `quick_check(1)`. All seven protected execution-table fingerprints and settings hash matched before/after the final deployment too. The final comparison and checkpoint-stage comparison are retained in `books-after-final.json` and `after-final.json`. Books, current holdings and capital were not reset. Original MACD trading behavior was not changed; common engine wiring only supplies additional read-only context to Smart. Verified cash: ₹1,69,418.55 in the small book (zero holdings), ₹57,68,539.52 in the larger book (three holdings). These balances include earlier policies.

## Validation and deployment

- Focused final Smart/routing/execution/exit/restart tests: 88 passed, including Qwen failure/configuration independence, quote retry/deadline, best-first cash allocation, ask-cost sizing, stale/future data handling, duplicate signals, expiry and paper-only enforcement.
- Frontend: 88 tests passed; TypeScript/Vite build passed.
- Broad engine suite: 1,136 passed, one skipped, one existing RRG test failed. Baseline comparison in `baseline-rrg.log` reproduces that failure on the unchanged pre-task checkout; the continuous synthetic-bar fixture no longer yields enough complete session bars. It was not modified to manufacture a green result.
- Existing dependencies/images were reused offline; no packages, data or model were downloaded. The strategy engine and frontend were recreated. The existing Fyers connection reconnected; no second broker socket was opened. The desk and other strategy services were not recreated.
- Final runtime: connected Fyers feed, no lane errors, Smart policy active in both books with no Qwen requirement; installed engine source matches the checkout. No regular-session entry cycle has occurred at this pre-open check.
- Rollback images: `macd-mini-parallel-engine:before-smart-local-20261028` and `macd-mini-parallel-frontend:before-smart-local-20261028`. An image rollback must retain the corrected held-position stages; do not blindly restore an older database or resume the previous stage-loss bug.

Remaining limits: this is a transparent allocation heuristic using an unproven research ranker, not a newly trained or validated profit optimizer. Prospective policy-versioned decisions, fills, capital utilization, drawdown and net outcomes are needed to establish improvement. The pre-open app reports 632/636 required contracts with indicators; four options lack warmup history while historical broker downloads remain disabled. An observed post-open Smart cycle is still outstanding.
