# Option-premium exhaustion research: frozen definitions

Declared 9 October 2026 before evaluating detector outcomes. This extends the earlier exit review using the exact held option's own OHLCV. Both long calls and long puts seek rising premium. The objective is to identify a weakening extreme in time to protect net profit; the absolute future maximum is an evaluation label, never an available input.

Production code, saved settings, holdings and books remain outside this experiment. No training, parameter grid, threshold optimization, or claim of a calibrated maximum-move probability.

## Inputs and common quality gate

- Retain original frozen single-BUY completed lifecycles and actual earlier partial proceeds. Exclude administrative expiry, additions and open lifecycles as before. Missingness is reported.
- Collect exact-contract one-minute OHLCV from the third prior derivative session through original exit. Compare overlapping rows to the original snapshot and report revisions.
- Build session-aligned complete three-minute bars from 09:15 IST; require all three minute rows and valid positive OHLC, with nonnegative volume. Exclude the partial 15:39–15:40 bar under the app's 15:40 derivative schedule. No forward filling or inventing candles.
- A missing/invalid expected three-minute bar resets indicator and pivot state. Legitimate overnight/weekend/holiday gaps do not create missing bars. Three prior sessions provide context, not assurance of completeness.
- Require 100 consecutive complete three-minute bars immediately before entry. Holding-window exact-minute completeness is required as in the original study. Report the eligible cohort and excluded positions. Require positive completed signal-bar volume. Context lacks broker/source history and can contain stale prints; completeness is not executability.
- EMA uses an SMA seed and standard alpha; RSI14 and ATR14 use Wilder smoothing. MACD12/26/9 uses SMA-seeded EMAs. Upper band is the mean of the preceding 20 closes plus two population standard deviations; the current close is excluded from its reference window.

## Declared detector A: option-price extreme followed by failure

1. At a completed post-entry three-minute close, arm when premium is at least two standard deviations above the preceding 20-close mean and RSI14 is at least 70. Signal bar must have positive volume and estimated residual liquidation P&L must be positive.
2. Arming lasts ten subsequent complete bars, within the same session. Repeated extremes refresh arming. Exit only on a later close back below the contemporaneous prior-20 upper band AND below the preceding completed bar's low, with positive residual liquidation P&L and positive signal-bar volume.
3. Fill at the next eligible minute open, no later than the original actual exit. No exit at the extreme or same-bar high.

## Declared detector B: confirmed momentum divergence followed by failure

1. A swing high requires two bars on each side with strictly lower highs. Its earliest availability is the close of the second right-hand bar, never the plotted pivot timestamp.
2. Compare consecutive confirmed highs separated by 3–20 complete bars. The newer high must be at least 0.2% higher, RSI14 at that high at least 60, and either RSI falls by at least five points or MACD histogram falls to at most 80% of an earlier positive histogram. The newer pivot starts after entry. Reference features use only information available at their own bar close.
3. The neckline is the minimum low strictly between the two highs. After confirmation, within ten complete bars and the same session, exit on a close below that neckline, with positive residual liquidation P&L and positive signal-bar volume. A neckline break already present at confirmation is actionable at confirmation, not backdated.
4. Fill at the next eligible minute open, no later than the original exit.

These are two fixed alternatives, not a search. The earlier +50%-arm/25%-profit-giveback rule is retained only as a named reference, recomputed on the same eligible cohort.

## Volume, volatility, flow and sentiment context

Record prior-20 volume ratio, upper-wick fraction, premium ATR extension, RSI, MACD histogram, and exact trigger reasons; none is relabeled as a probability. Volume does not identify whether a trade opens a bullish position, closes a short, hedges, or forms a spread.

Audit actual timestamped availability before using IV, Greeks, OI, PCR, skew, futures/order-flow divergence, breadth or news sentiment. Use only as-of observations available by the decision time, with source freshness and scope stated. Never apply today's chain/news values to past trades, infer signed order flow from OHLCV, or turn absent sentiment into neutral sentiment. Existing NIFTY/BANKNIFTY chain fields may be recorded as context if independently validated. No third sentiment-gated policy is selected after outcomes are seen.

## Accounting and evaluation

Keep actual entry prices/quantities and actual partial sales through the alternative fill time. Close only the positive remaining whole-lot quantity and replace later actual sales. Standard scenario: INR20 per entry/sale order on both choices, 5 bps adverse slippage on new modeled exits; original fills already include production slippage. Residual profit gate at decision close uses modeled liquidation `close*(1-0.0005)*remaining - entry*remaining - 20 - 20*remaining/initial_quantity > 0`. Costs/depth remain incomplete.

Compare identical eligible lifecycles under actual exits, A, B and the named gain-giveback reference. Primary paired cohort excludes positions whose triggered fill minute has zero volume under any compared policy; retain the unfiltered sensitivity separately. This is retrospective quality selection, not future-volume information for a live rule.

Report counts, net P&L, incremental difference, improved/worsened positions, lane concentration, foregone large winners, added 20 bps exit stress, feature coverage and timing. Optionally describe distance to later observed highs only as hindsight diagnostics; never use it in decisions. Excluded open winners and additions, missing paths, short history and no untouched holdout prevent production promotion. Require independent prefix-invariance checks, indicator reconciliation, partial-cash accounting and next-minute timing checks before sharing results.
