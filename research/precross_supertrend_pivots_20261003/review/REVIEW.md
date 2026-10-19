# Review of premium MACD, Supertrend and pivot research

3 October 2026. **Share with caveats. Coverage: Partial**, because source-row reconstruction was sampled and historical decision-time chains, quotes/spreads and production-policy transfer cannot be established from these inputs. No P0/P1 computational error was found in the current study. No study, strategy or book was changed; the items below are proposed P2 refinements. This review covers the latest pre-cross study deeply. Earlier September and Smart reports were inspected for context and compatibility, not re-audited end to end.

## Prioritized problems and proposed fixes

1. **P2 — The test-period cash comparison inherits different development results.** In FINDINGS.md, the ₹50 L and ₹1 L headings identify September 1 initial funding, not equal capital on September 15. For ₹50 L, cross+5 enters the test with ₹38,71,934.64 equity; ST+P with ₹38,84,842.58. Both carry the same IREDA position. The ₹7,745 additional ST+P profit is arithmetically correct, but percentage returns are 76.3486% for cross+5 and 76.2943% for ST+P. The ₹1 L paths likewise start the test with different equity (₹1,26,894.48 versus ₹1,24,831.98). The original report discloses continuity later, so this is a presentation/comparability weakness, not hidden cash creation. **Fix (Proposed):** put opening equity, cash, inventory and period return beside rupee P&L; use identical starting states for a clean new-policy experiment. Normalising returns is informative but does not recreate an equal-funded path because sizing and eligibility depend on cash.

2. **P2 — “Round-trip breakeven floor” excludes fixed order fees.** FINDINGS.md describes the trailing floor this way, but analyze.py:143 computes entry/(1−slippage). It covers the price/slippage component; the ₹20 entry and ₹20 exit fees in stress scenarios are still deducted. There are 64 negative-net trailing exits across the saved fee-scenario books whose gross pre-fee return was nonnegative; these are repeated scenario observations, not 64 independent trading opportunities, and their exit prices may also reflect gaps. Base-cost headline comparisons have no fixed fee and are unaffected by this wording issue. **Fix (Proposed):** call it a slippage-adjusted price floor excluding fixed fees, or define a net-cost threshold as (total entry debit + closing fee)/(quantity × (1−slippage)) and rerun affected scenarios. A threshold still cannot guarantee a breakeven fill after price gaps.

3. **Research limitation — Temporal exclusion is not an untouched research holdout.** The new classifier's fitting/preprocessing uses September 1–11 only, which the audit reproduces. However, earlier reports in this same research series already inspected September 15–30. Selection of this follow-up question therefore occurred with knowledge of that month's behaviour. The seventeen-test Holm family covers the frozen current comparisons, not every earlier analysis, timeout inspection or probability-band exploration. **Fix (Proposed):** describe these as exploratory time-split results and freeze rules/model before observing new sessions. This is not evidence that held-out labels were used in the classifier fit.

## What was verified

- Independent row-based arithmetic reproduces 341/1,371 base crosses, 197/494 completed-ST crosses, 271/705 ST+P crosses and 42/324 pivot-reclaim crosses within thirty minutes; 679 early fills and 396 ST+P timeouts also match.
- Independent prediction calculations reproduce AUC 0.8501039 → 0.8678444 and Brier 0.1377696 → 0.1260413. The twelve-day Brier test has raw p=0.0427521 and Holm p=0.7267851. The claimed corrected significance failure is correct.
- The >0.8 band has 26/26 crosses across nine days, 16 early fills and fifteen paired fills. The top three paired differences exceed the whole-group sum, so the remaining observations contribute −44.36 percentage points in total. This remains exploratory; it is not an evaluated portfolio gate.
- Independent cash-book episode/mark calculations reproduce 917 versus 1,312 positions, PF 1.4023 versus 1.3034 and win rates 42.09% versus 41.46% for the main ₹50 L comparison. Saved fill reconciliation evidence remains tied to the current hashes.
- From original old/current minute exports, an independent candle-prefix calculation verifies entry-observation timing, premium price, MACD and exact zero premium for 74 events across 65 contracts, including every >0.8 prediction plus a random sample. Maximum zero-price discrepancy is below 1e−12. This is a sample, not exhaustive proof of every feature. Historical indicator candles follow the app's instrument-specific session calendar; the experiment's execution/label cutoff at 15:30 is a separate policy choice.
- All three figures were visually inspected. Cross-conditioned descriptions are labelled as hindsight, cross-rate bars include failures and start at zero, and portfolio difference signs/counts match tables. Existing receipt payloads preserve the main qualifications. Prior rendered receipt inspection is reused for the unchanged shared interaction path.

## Decision

The caution in the original conclusion is justified. ST/pivot states are associated with more subsequent crosses in this sample, but there is no validated incremental net-profit edge. “A useful exploratory cross forecast” is a better description than “an established early-entry advantage.” The 26/26 group is too small, concentrated and selectively executable to justify deployment.

Keep cross+5 as the comparison baseline. The next decisive test would use new data, a frozen model/rule, identical starting cash/inventory, complete costs and the actual strategy/Smart execution policy. No current study result establishes the effect of early entry on the production Smart lane.

## Scope and evidence

The saved report, frozen plan, replay/feature code, model outputs, event records, book marks/episodes, source metadata, prior study conclusions and all three figures were inspected. Exact price/EMA source reconstruction was sampled; source-priority and timeout tables were checked against saved outputs rather than rerunning all raw-data replays. Historical universe completeness, tick-level execution, official exchange-session assumptions and a fresh prospective test remain unverified. The ten existing focused tests were rerun successfully but are supporting checks, not independent proof of edge.

Evidence: `statistical_audit.json`, `independent_prefix_checks.csv`, `independent_source_checks.json`, `fee_floor_examples.csv`, `capital_comparison.json`, `coverage.json`. Checks can be reproduced with `review_probe.py` and the saved inputs; raw evidence and study results were preserved. Scorecard counts below describe scoped components, not percentages of correctness, and categories overlap.

## Scoped scorecard

| Category | Observed defects | Assessment |
|---|---|---|
| Usefulness and completeness | 0 / 11 | The cross-forecast and simplified entry questions are answered. Full production/Smart transfer is explicitly untested. |
| Analytical clarity | 3 / 11 | Actual test-period opening equity needs prominence; the fee-scenario floor is not net of fixed fees. |
| Visual and interaction consistency | 0 / 3 | Three saved figures inspected; labels, signs and visible marks agree. Receipt uses the previously checked renderer. |
| Source authority and confidence | 0 / 11 | Archive/catalog provenance retained. Historical point-in-time chain and tick/spread evidence is unavailable. Partial review: 2 scoped items have incomplete or stale evidence (Selection and execution definitions; Production and Smart applicability). |
| Value accuracy | 1 / 10 | Headline rates, prediction metrics, seventeen corrected tests and main book quality recomputed. Fee-floor definition has a minor mismatch. Partial review: 3 scoped items have incomplete or stale evidence (Cost scenarios; Timeout scenarios; Source precedence sensitivity). |
| Within-chart agreement | 0 / 3 | All three figure comparisons match their saved tables; hindsight conditioning is visible. |
| Complete source details | 0 / 4 | Four existing source-card payloads retain denominators and limitations. Shared interaction path was previously checked. |
| Cross-artifact consistency | 0 / 10 | Report, notebook outputs, figures and aggregate tables agree on the scoped comparisons. |
| Data-quality controls | 0 / 9 | No future-price use found in 74 sampled MACD/zero-price prefixes. Full source-row verification and historical universe completeness are unavailable. Partial review: 2 scoped items have incomplete or stale evidence (Selection and execution definitions; Production and Smart applicability). |
| Conclusion support | 0 / 11 | Retaining cross+5 is supported. No cleared incremental trading edge or valid Smart promotion is claimed. |
