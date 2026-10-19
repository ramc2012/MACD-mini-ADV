# Extreme-move profit-taking study — 9 October 2026

Scope: review the deployed paper app and evaluate exit research. No change to production strategy, settings, orders, books, or historical data is authorized by this study. Research files are additive. Review time is after the NSE/BSE session close.

Decision: determine whether an extreme-move profit safeguard is worth prospective paper testing, and identify correctness issues that must be resolved before any exit comparison is trusted.

## Declared small retrospective comparison

Use retained actual paper entries and baseline exits. Analyze each lane separately. Group partial exits by position_id; never count slices as independent round trips. Restrict exit overlays to fully closed cycles with a constant entry quantity and adequately covered exact-contract minute paths. Report exclusions. Do not synthesize missing candles or prices.

1. Baseline: actual recorded exits.
2. Arm after a completed minute close is at least 50% above the recorded entry basis. Subsequently exit when a completed close falls to or below entry + 75% of the highest completed-close gain known at that time.
3. Repeat with a 100% gain activation threshold.
4. Only if data preparation permits: entry-frozen ATR14, a 3 ATR gain activates a monotonic 1.5 ATR premium trail. This is distinct from the underlying-extension policy below.

Triggers execute at the next eligible minute open before the baseline exit, with adverse slippage. If there is no supported next bar, retain baseline or mark the path unusable as the analysis specifies. Never fill at the bar high or at the theoretical stop through a gap. Run cost stress with a fixed fee per order, account for existing slippage in recorded fills, and show foregone later winners. No threshold selection or parameter grid search. These are isolated exit diagnostics, not a portfolio-equivalent replay: later re-entry, freed cash, concurrent exposure, and new entry selection are unmodeled.

All data through 9 October has now been inspected and is research data. A date split does not make it an untouched holdout. The overlapping Smart books are not independent replication. Retained records do not conclusively establish feed provenance for every fill. Observed price peaks are diagnostic and cannot be treated as executable bids.

## Prospective hypothesis (untested)

Arm protection when the direction-adjusted underlying close is at least 2.5 ATR14 above/below EMA20 and the exact held option has positive liquidation P&L after costs. Use completed strategy bars and ATR from preceding completed bars. The 2.5 threshold is a declared example, not an optimum supported by research.

An extreme alone is not a full-exit signal. For a bullish underlying, a subsequent completed close below the preceding bar's low confirms a retracement; for a bearish underlying, use a close above the preceding bar's high. If at least two lots remain, test selling floor(lots / 2), retaining at least one lot. Test one-lot full exits separately. Keep a monotonic protective trail on the runner and preserve the existing hard-stop, expiry, and holding-limit rules. In a combined policy, use the more protective valid stop; never relax an existing stop.

Independently test the simple 50%/100% premium-gain activation and 25% profit-giveback safeguard. Do not attribute its retrospective results to the untested underlying-confirmed partial policy.

Execution requires fresh exact-contract bid, available size/depth, exchange-session eligibility, and defined handling for absent or stale bids. Persist activation, eligible peak, partial stage, remaining quantity, and original basis independently of valuation marks. A claimed floor is a trigger level, never a guaranteed fill or guaranteed profit.

## Success criteria

Primary outcome: incremental net paper P&L versus unchanged baseline from identical starting cash, inventory, entries, and costs. Also inspect drawdown, downside tails, top-decile winner contribution, profit giveback, turnover, late re-entry/whipsaw, and results by lane, expiry distance, liquidity, and trend regime. Record every tested policy. Require genuinely new prospective sessions across multiple expiries and a consistent cost-stress result before proposing promotion. Improved capture or win rate alone is insufficient.

## Primary research sources

- Fidelity ATR: https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/atr
- Fidelity Bollinger Bands: https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/bollinger-bands
- Options Industry Council Greeks: https://www.optionseducation.org/advancedconcepts/volatility-the-greeks
- Options Industry Council bid/ask execution: https://www.optionseducation.org/news/understanding-the-bid-and-ask-prices-for-options
- Kaminski and Lo stopping-policy research: https://dspace.mit.edu/entities/publication/bb69ca4b-0cdc-487f-831d-63b2e84fafee
- Bailey et al. backtest-overfitting paper: https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf
