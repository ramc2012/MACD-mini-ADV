# Independent MACD / Supertrend / pivot experiment

Study date: 3 October 2026. Original application candles, independently written code; earlier research outputs and models were not used. Engine, configuration, paper positions and trading books were not changed.

## Finding

**Conclusions about improving the actual MACD strategies are suspended: the research baseline was not reconciled to them.** The original negative replay does not contradict the positive returns reported in the supplied Claude study or the positive latest MACD paper-book equity. This experiment changed entry timing, event selection, sizing, execution granularity, holding limits, allocation and costs. Its numerical results are retained below only as a simplified-rule diagnostic. The original recommendation to carry the joint filter forward as the preferred addition is withdrawn pending a faithful baseline comparison.

The subsequent [September study](../september_supertrend_pivots_20261003/FINDINGS.md) confirms the positive old ledger and applies the overlays to its actual recorded entry schedule, including funding, carry-in positions and a separate month-end valuation scenario. Use that study for the September question.

## Reconciliation correction, 3 October 2026

The supplied report describes 3,298 signals from 25 August–1 October, minute-by-minute execution, pyramiding/staged exits and ranked allocation with dispersion sizing. It reports current-rules unlimited-cash P&L of +Rs 53.2 L and a ranked Rs 50 L book P&L of +Rs 63.5 L over 19 out-of-sample sessions. Its first-come book instead lost Rs 10.9 L. These are report-provided benchmarks, not independently reproduced figures or September-only live account profit.

This independent replay also happens to contain 3,298 events, but covers 20 August–25 September, uses a 10–25 September holdout and first-come cash allocation, and excludes 2,188 otherwise eligible events with incomplete compared paths. The equal event counts do not establish matching event sets. Five-minute OHLC execution, four initial lots, no pyramiding, a four-session standard-lane cap, omitted live ranker/dispersion sizing, and extra assumed fees make a direct P&L comparison invalid. The supplied report also shows that allocation alone can change the sign of a book result; that does not quantify which differences caused this replay's losses.

At the read-only audit timestamp 2026-10-03 15:48 IST, the actual MACD lane's API and rendered app show initial capital Rs 50 L, equity Rs 51.86297212 L, realised P&L -Rs 1.547352325 L, unrealised P&L +Rs 3.410324445 L, net marked P&L +Rs 1.86297212 L and 61 open positions. Independently replaying all 517 durable fills reproduces API cash, realised P&L, quantities and average entry prices to less than one paisa in aggregate cash/P&L. These are cached marks during market closure, not newly executable quotes.

The active ledger's first fill is 29 September. Its last retained 30 September equity mark is Rs 47.958482485 L (-Rs 2.041517515 L from capital), while the latest book is positive after 1 October. It does not contain a complete September account history. Both smart books have no fills yet; their positive historical benchmark figures must not be described as those books' live realised profit. See `RECONCILIATION.md`, `paper_book_reconciliation.json` and the hashed read-only source capture for scope and evidence.

**Next required validation:** freeze the real entry/sizing/exit rules; reproduce event-level entries, additions, partial exits, cash and equity under a minute-level replay; identify any gaps against actual fills; then compare only Supertrend/pivot changes against that identical baseline. The supplied Claude benchmark needs its event/fill ledger and walk-forward allocation records for exact independent reproduction. Neither a negative simplified replay nor a profitable MACD book establishes the incremental benefit of these indicators.

## Entry comparisons

Chronological holdout: 2026-09-10 through 2026-09-25, 11 entry days. Returns are net percentages of a four-lot research ticket among executed, retained trades. They are **not** the realised return of the running app. Smart signals that fail the five-minute recheck are not counted as trades.

| Entry rule | Standard mean net return | Smart mean net return |
|---|---:|---:|
| MACD baseline | -3.30% | -2.68% |
| Premium Supertrend (10,3) | -2.82% | -2.16% |
| Underlying Supertrend (10,3) | -3.19% | -2.65% |
| Underlying Supertrend (7,2) | -2.89% | -2.25% |
| Pivot headroom | -2.97% | -2.26% |
| Joint Supertrend + pivot | -2.59% | -1.92% |

The joint rule requires bullish premium Supertrend(10,3), underlying Supertrend(10,3) bullish for CE or bearish for PE, and >=0.5 five-minute ATR of headroom to the next prior-day P/R1/R2/S1/S2 level in the trade direction. Supertrend uses the last completed 30-minute bar. This is a fixed rule, not a parameter combination selected after observing returns.

The CE/PE split matters. Under the smart joint rule, calls average -7.21% and puts +7.53%. The apparent improvement therefore needs a different market regime before it can be trusted.

Underlying Supertrend(7,2) is a useful sensitivity finding rather than grounds to tune the live strategy. Its day-level result reduces losses, but the weekly check has only three holdout clusters; the default (10,3) does not replicate that significance. Cash-book and higher-cost checks for (7,2) are included in the result CSVs. There is insufficient independent time coverage to confirm robustness.

## Cash-constrained comparison

Each book starts independently with Rs 50 L, uses tickets up to Rs 1 L, buys whole exchange lots, and rejects a second position in the same contract only after an entry is funded. Partial exits free cash at their simulated time. Equity includes daily marks and the post-entry runoff period. Current lot sizes are an explicit assumption.

| Lane | Baseline P&L | Joint filter P&L | Joint maximum drawdown | Paired daily t | Corrected p |
|---|---:|---:|---:|---:|---:|
| standard | Rs -24.52 L | Rs -10.32 L | Rs 24.43 L | 1.87 | 1.00 |
| smart | Rs -26.39 L | Rs -0.61 L | Rs 25.07 L | 2.02 | 1.00 |

The joint filter helps allocation as well as signal selection: fewer early entries leave cash for later signals, so retained signal averages and funded book results need not move by the same amount. Even the smart joint book has a large drawdown relative to Rs 50 L. Its uncorrected daily-difference t near 2 does not survive the full test family. The no-trade control has zero P&L and zero drawdown.

See `capital_cost_sensitivity.csv` for the same books with 5/25/50 bp slippage per side. Signal averages are also tested at all three costs and remain negative for the joint rule.

## Supertrend and pivot exits

The exit experiments add an adverse premium Supertrend reading, an adverse underlying reading, or loss of the prior-day premium pivot. Baseline risk controls stay active. A lower average loss under a pivot exit did not reliably improve the cash book: cutting a position changes which subsequent signals can be funded. No exit overlay survives corrected holdout testing. The current Supertrend exit overlay is evaluated at a new completed 30-minute close; it is not an intrabar stop at the Supertrend line.

## Walk-forward check

Entry filters are selected only on earlier fully resolved outcomes, with a four-entry-session purge, and tested in subsequent blocks. There are only two test blocks, totalling seven entry days; this is a pilot, not sufficient prospective validation. The chosen standard-rule filter was premium inside weekly CPR; the smart choices were underlying above/below weekly P. The smart selector underperformed its baseline overall. Selection must also compete with no trade, rather than being forced to choose a losing rule.

## Data and limitations

- Source: `historical_candles`, current MACD-mini-ADV Docker data volume, read with `mode=ro`, `query_only` and a single read transaction. 37,360,308 one-minute rows were processed across all assets; 6,248 option contracts were present. The original inventory counted 33,965,356 option rows. Option coverage is 29 July–1 October; underlying coverage is shorter. This is not a ten-year test.
- Fixed five-minute extract SHA256: `2eb3bb9ba86f7576fcdfe079893c74618971b7b18051fa85057d5347ae77bb97`. The six inspected strategy/indicator/session modules match the deployed source hashes.
- 3,298 resolved eligible cross opportunities on 22 entry days, 2026-08-20–2026-09-25. New entries stop early enough to allow exits before the data endpoint. Candidate exclusions, missing history, gaps and incomplete bars are retained in `quality.json`.
- 2,188 otherwise eligible signals were excluded because at least one compared exit path was incomplete. This complete-path sample can be biased toward more liquid contracts; results must not be generalised to the excluded contracts. 88 sufficiently long series still lacked usable expiry dates. Dates recovered from sibling strikes have explicit provenance; no calendar expiry dates were guessed.
- Baselines use completed 30-minute zero crosses, four starting lots, staged profits, no pyramiding, and a shared four-session cap. The standard live lane has no such cap. The smart replay has no deployed ranker, dispersion sizing or mixed blast intake. Live intrabar signals, historical ATM selection, exchange spread/impact, corporate actions and changing historical lots are not reconstructed. This tests indicator overlays on controlled MACD rules, not exact deployment parity or actual account performance. The separate blast signal-line strategy is outside this zero-cross experiment.
- Five-minute bars cannot identify the order of intrabar high/low. Stops are processed before favourable targets/partial exits, new trails only apply to later bars, and close-driven exits use the next available open. Missing five-minute execution paths are censored, not interpolated or booked as wins.
- Costs use current 5 bp slippage plus scenario charges of 10 bp buy/20 bp sell and Rs 20 per fill. These charges are disclosed research assumptions, not a statutory calculation. Higher slippage scenarios are supplied.
- Effective sample size is entry days, not 3,298 independent observations. Fixed comparisons use entry-day clustered differences and an ISO-week clustering sensitivity for overlapping trades; cash books use paired daily equity differences. The small holdout and correlated trades do not justify promotion. Longer serial dependence and an independent call-favouring sample remain untested.

## Decision

Keep the engine unchanged. Establish baseline parity before choosing or rejecting any Supertrend/pivot addition to the actual strategy. Joint alignment/headroom and Supertrend(7,2) remain observations about the simplified experiment only; neither is a preferred live candidate on this evidence. The baseline's changed allocation and trade management prevent transferring even its relative filter improvements directly to the deployed strategy.

## Reproduction and definitions

`PLAN.md` contains the frozen design and recorded refinements. `study.py`, `capital.py`, `test_study.py`, the fixed candle extract, manifests, event table and result CSVs contain the inspectable computations. `study.ipynb` is the executed results companion. Seven causal/accounting checks pass. Re-run from this folder using the bundled Python and `PYTHONPATH=.deps`: `python study.py`, `python capital.py`, `python test_study.py`, `python deliver.py`. Data extraction requires access to the original container; saved calculations require only the local extract.

Indicator definitions: [TradingView Supertrend](https://www.tradingview.com/support/solutions/43000634738-supertrend/) and [traditional pivots](https://www.tradingview.com/support/solutions/43000521824-pivot-points-standard/). These sources define calculations and do not establish profitability. CPR is calculated explicitly in `study.py`.
