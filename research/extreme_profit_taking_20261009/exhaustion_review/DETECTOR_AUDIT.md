# Independent frozen exhaustion-detector audit

Audit completed 2026-10-09 18:06:51 UTC. No blocking defect was found in the frozen research detector, collection or cash-accounting runner. This is a bounded implementation audit; it does not establish prospective trading edge or production-engine equivalence.

## Reproducible evidence

- `audit_exhaustion.py` contains the independent checks; run with the bundled Python runtime from the repository root.
- `detector_accounting_audit.json` records the exact input/code/output hashes and results: **104,244 assertions, 13,869 numeric reconciliations, zero errors**.
- All 220 lifecycle inputs and warm-up decisions were checked. The original holding candles were unchanged, the immediately preceding 100 complete three-minute grid bars were used for the warm-up gate, and missing grid bars reset continuity. A sufficient total count elsewhere in history did not qualify a lifecycle with a broken trailing window.
- Standard indicators were independently reconciled on 1,563 feature bars from 13 deterministically sampled lifecycles. The audit used closed-form weighted sums for EMA/Wilder smoothing, independently calculated prior-20 population bands, RSI, ATR, prior ATR, MACD and signal/histogram. It did not merely call the detector's indicator helpers as its expected values.
- 234 A/B prefix cases verified that removing future candles preserves the published signal and its fill time, or yields no signal when the full-history decision had not yet been published. Handcrafted checks verified strict two-left/two-right pivot confirmation, tied-high rejection, and missing-bar resets. This finite sample is evidence of causality, not an exhaustive formal proof.
- All 354 optimistic comparison rows, including all 73 triggered rows, were independently reconciled for actual prior partial-sale proceeds, remaining whole lots, the first eligible next-minute open, the actual-exit horizon, one application of five-basis-point slippage on the new exit, and fixed fees of INR 20 per order on both paths. The equal additional exit-cost stress reconciled as well.
- The 111-position common primary cohort (333 policy rows) and all three primary aggregates reconciled. Each position appears in all three policies; any policy's zero-volume hypothetical fill excludes that position from all primary arms.

## Plan conformance reviewed

The frozen definitions use complete session-aligned three-minute candles, carry indicator state across legitimate overnight/session gaps, and reset it after a missing or invalid expected bar. Bands exclude the current close. The band rule requires a qualifying extreme followed by a later failure within the frozen same-session arming window. The divergence rule publishes a pivot only after its second right-hand bar closes; its new pivot is post-entry, the neckline is formed strictly between highs, and confirmation-bar breakdowns are actionable only at confirmation. Both A/B rules require positive signal volume and positive modeled remaining-position liquidation profit before triggering. The unchanged one-minute reference intentionally does not inherit the additional A/B signal gates.

The runner retains recorded sales at or before the alternate fill, replaces only later sales, and does not charge baseline recorded fills a second five-basis-point slippage haircut. Later extrema are hindsight diagnostics and do not enter the signal calculation.

## Interpretation limits

Primary eligibility uses retrospective complete-path and future fill-minute-volume quality filters. These improve paired research comparability but cannot be described as information available when entering a position. The experiment ends at each actual exit and does not simulate a new portfolio, capital reuse, intervening entry decisions, depth, spread or impact. The small selected historical cohort, adverse baseline, lane concentration and prior exploration remain reasons to keep the rules in prospective paper/shadow evaluation.

Chain and sentiment availability has a separate audit in `AVAILABILITY.md`. Missing context is unknown, not neutral. The persisted chain timestamp is a producer sample timestamp without a provider quote time or fetch-completion/available-at field; a backward five-minute timestamp join alone is insufficient to certify a contemporaneously available causal sentiment feature. No sentiment-performance claim is supported by this detector audit.

No production source, settings, broker orders, positions or books were changed during this audit.
