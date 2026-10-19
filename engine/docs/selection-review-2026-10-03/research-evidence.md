# Research and selection evidence — 3 October 2026

Read-only evidence was collected from the running `macd-mini-parallel-engine-1`
container's `/app/data` SQLite databases, the ADV checkout, and archived reports
in `/Users/ramachandran/CLAUDE PROJECTS/MACD mini`. The ADV engine's local
`engine/runtime/historical.sqlite3` is an empty file and is **not** the running
research database. The running database is on the `engine_data` named volume.
No backfill, model retraining, report regeneration, ledger edit or deployment
was performed by this research audit.

## Current paper evidence

The following figures reconstruct complete flat-to-flat trades from persisted
fills ordered by timestamp and trade ID. Partial entries/exits remain one trade.
P&L subtracts the fees actually recorded in fills and excludes open positions.
All three books have zero unmatched sell fills in the inspected population.
This is a short descriptive cohort, not evidence for fitting a new filter.

| Paper book | Fill coverage | Complete trades | Wins | Closed P&L | Profit factor | Open symbols | Recorded closed-trade fees |
|---|---|---:|---:|---:|---:|---:|---:|
| MACD | 29 Sep–1 Oct 2026 | 132 | 37 | -₹559,927.35 | 0.548 | 61 | ₹0 |
| Blast | 29 Sep–1 Oct 2026 | 27 | 4 | -₹204,931.76 | 0.584 | 18 | ₹0 |
| MP | 29 Sep–1 Oct 2026 | 36 | 12 | -₹186,591.41 | 0.477 | 0 | ₹1,440 |

Sources: `/app/data/macd_trader.sqlite3`, `blast_trader.sqlite3`, and
`mp_trader.sqlite3`, `trades.payload`. Snapshot collected 3 October 2026; latest
exit timestamps are 1 October 09:51:39Z, 10:08:26Z and 09:50:11Z respectively.
MACD and Blast have no recorded fill fees; their P&L is not a reconstruction of
full brokerage and statutory costs. No independent executable fill or quote
quality validation is implied by these totals.

## Options-agent research

Live `/app/data/historical.sqlite3`, `agent_models`, version 2:

- Champion: `ridge-underlying-l10`, trained 1 October 2026 at 10:51:12Z.
- Training: 47,537 labelled candidate rows, 24 August–28 September.
- Reported walk-forward comparison: 14 test days. CE picked mean return
  -27.019%, excess over the side's universe -7.776% (t=-1.64); PE picked mean
  return +42.980%, excess +15.395% (t=0.96). Mean side excess +3.810%.
- Neither side's excess statistic demonstrates clear independent edge. The
  positive PE absolute return can partly reflect the market's directional move;
  it does not establish contract-selection skill by itself.
- Index champion `index-only-l100`, version 1: 45 opportunities, 36 trades,
  6 no-trades; 28 puts and 8 calls. Mean return +40.828%, mean excess +23.643%,
  excess t=1.22. These are model evaluation labels, not funded net ledger P&L.
- Latest scheduled run: 1 October 2026 at 09:14:43Z, run 17, model 2, 1,008
  candidates. Entire candidate archive: 51,252 rows / 47,537 labelled, 24
  August–1 October, sources `history` and `scheduled`.

Treat repeated model comparison windows as selection/validation data. A fresh,
frozen chronological and prospective period is required for a final claim.

## Archived evidence does not support adding a tuned hard gate

The legacy `runtime/walk_forward_report.json` is run
`wf-20260814-071031-e24673`: 56 closed trades / 7 open positions, 5–13 August,
22 wins, -₹130,840.47 closed P&L. This uses one initial exchange lot and 5 bps
slippage per side. It does not represent the current sizing or a portfolio with
real cash/concurrency constraints. `research.py` labels fixed-parameter replay
as expanding walk-forward while grouping trades by entry date; its equity
accumulation also follows entry order rather than realized exit order.

Legacy `runtime/feature_research.json`, generated 17 August 2026 17:47:57Z:
1,855 events, 1,771 closed, 31.90% wins, -10.240% equal-event average return,
return profit factor 0.483. Its last-two-date baseline has 90 closed events,
23.33% wins and -18.382% average return. The historical shortlist was selected
using the purported holdout (see correction below), and is diagnostic.

`runtime/pattern_profile_findings.md` reports six holdout trades, +12.87% mean
return; one BHARTIARTL trade contributes 132% of net return, and removing it
changes mean return to -4.96%. Its OTM/low-IV cohort follows the earlier feature
discovery on the same dates; those dates cannot now be called untouched.
`pattern_profile_research.py:453` also sorts using holdout positivity. Its
EMA20/EMA50 and anti-chase hypotheses are candidates for prospective logging,
not validated hard entry gates.

The actual two-leg premium-ratio study
`research/premium_ratios_august_2026/REPORT.md` reports no stable incremental
forecasting edge, no eligible three-strike ladder, and only 2–3 actual eligible
test dates. Its 5m CE/PE hypothetical 30m trade means after assumed 1% premium
cost are -6.40% / -3.90%. Keep those ratios descriptive.

The legacy `research/README_of.md` is explicitly based on the pre-27-August
classifier: 21 and 25 August only, 354,834 bars. Its option contemporaneous flow
sign is inverted. Estimated directional edge is 2–8% of the quoted spread.
These baseline/edge figures cannot certify current live flow or a new entry.

## Confirmed research defects and bounded correction

Before this audit, `feature_research.discover()` rejected candidates based on
test coverage and test mean return, then ranked by the minimum train/test mean
return multiplied by the square root of test count. This lets purported
holdout outcomes choose filters and makes their apparent robustness optimistic.

The ADV-only correction in `src/macd_trader/feature_research.py` now:

1. Chooses the last two signal dates before inspecting closed outcomes,
   retaining open holdout events in coverage.
2. Learns feature thresholds, candidate eligibility and shortlist order from
   training rows only. Reports losses and missing holdout coverage afterward.
3. Retains closed-outcome availability at the exit bar's close for newly built
   events. Purges training labels available at or after the test start.
4. Explicitly marks legacy rows without close times as unpurged exploratory
   evidence, with holdout independence unverified and deployment validation
   false. Keeps the old `robust_candidates` output key for compatibility while
   clarifying its provenance.

`tests/test_feature_research_holdout.py`: six regressions pass, covering
holdout-return/feature/coverage perturbation invariance, negative holdout
reporting, horizon-overlap purging, unknown legacy timing, open holdout dates,
and label availability at exit-bar close. These were directly executed with
the bundled Python runtime; the parent task will include them in pytest.
Existing archives and model versions were preserved and are not retroactively
revalidated by this code correction.

Additional research issues observed before the follow-up correction below:

- `pattern_profile_research.py:493` fixes a cohort inherited from already-read
  holdout results; `:453` uses holdout positivity in ranking.
- `strategy_lab.py:699` ranks configurations on out-of-sample net P&L and
  `:725` chooses the best variant for each rule from that same test period.
  This is model selection, not untouched final evaluation.
- `feature_research.trade_outcome()` has no expiry cutoff or fixed maximum
  holding horizon and records the exit-bar high before checking its low stop.
  Hence MFE can include a high occurring after an intrabar stop. Do not use
  its MFE-derived labels as a causal profit-capture guarantee.
- Historic contract universes are incomplete archives/current snapshots,
  not complete point-in-time exchange chains. Broad event studies omit funded
  portfolio cash limits, cross-contract correlation and full observed spread,
  depth and costs.

## Entry and Smart scorer limits

`src/macd_trader/entry_model.py:112` exports a 1,410-row fitted model covering
25 August–29 September, mixing actual trades with replayed trades. Its
embedded walk-forward claim is AUC 0.567, 1,178 evaluated rows, kept P&L
₹3,708,116 and skipped P&L -₹1,834,061. The reproducible trainer, per-fold
parameters, independent final split, quote/fill source and cost reconciliation
were not found in the inspected checkouts. Treat these as artifact-reported
results, not independently verified current profitability.

The documented nightly refit in `entry_scorer.py` is not implemented by
`nightly.py`; entry scoring uses the imported static `entry_model.MODEL`.
`macd_entry_scores` has scored-decision features but no model version or
label/outcome table. Its inspected current counts are: 30 September, 5 skipped /
3 not-filled; 1 October, 20 skipped / 90 not-filled / 61 taken. These rows do not
yet establish the return of skipped signals under a frozen counterfactual.

`smart_model.py` exports 3,298 signals across 27 sessions (25 August–1 October),
labelled position P&L > 0 under MACD rules; claimed AUC 0.587 over 19 sessions.
The referenced scratchpad trainer is absent from the inspected checkouts.
Probability of any positive P&L is not expected net premium return: a higher
win probability can still select small wins with large losses. Model artifact
scores need calibration, payout/cost evaluation, causal feature parity and
prospective evidence before being treated as trading edge.

## Next research protocol

Freeze a baseline and a small prespecified set of shadow variants. Separate
data-quality rejection from predicted-return ranking. Record signal-time
features, model/policy version, exact contract, bid/ask/quote ages, costs,
rejection reasons and forward paths for every scored signal, including skipped
and not-filled rows. Use labels matching the actual intended holding/exit
policy and keep them unavailable until their horizons end.

Use chronological training, model-selection and final evaluation periods with
purged overlapping outcomes and a frozen final test. Add prospective dates and
another expiry; report per-day and per-underlying performance, CE/PE excess,
net expectancy, drawdown, cost stress, no-trade coverage and leave-one-large-
winner-out sensitivity. Validate cash/concurrency behavior in a funded replay.
The existing EMA emerging-trend, spot alignment and anti-chase findings are
reasonable prespecified shadow hypotheses, but these archives do not justify
new optimized production thresholds or a profitability claim.

## Follow-up: pattern and strategy-lab selection correction

The ADV-only follow-up also removes test-dependent selection from
`pattern_profile_research.py` and `strategy_lab.py`; archived reports remain
unchanged. Pattern rules now require training coverage only and rank by
positive training blocks, training profit factor and training average return.
Test losses, positivity and zero coverage remain reported but cannot admit,
remove or reorder rules. The fixed OTM/IV cohort and exit overlay still
originate in previously inspected research, so output provenance explicitly
marks holdout independence unverified and deployment validation false.

Strategy-lab configuration order, its all-negative diagnostic fallback and
per-rule winners now use training net P&L only. Reported test losses remain
attached to the training-selected configuration. Its metadata explicitly
requires fresh independent dates after prior test inspection; this repair
does not rehabilitate earlier claimed out-of-sample performance.

`tests/test_research_rule_selection.py`: four additional regressions pass,
proving holdout profits cannot choose pattern/global/per-rule winners, missing
pattern test coverage cannot remove a rule, and all-negative training fallback
cannot chase a lucky test result. These were directly executed with the
bundled Python runtime. No substantial research job or model training ran.
