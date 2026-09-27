# August premium-ratio research: frozen protocol

Written before evaluating forward returns on 31 August 2026. Research only;
no broker calls, strategy changes, paper orders, or production database writes.
Input: SQLite online backup of historical.sqlite3, quick_check result `ok`.

## Question

Do same-expiry ITM/ATM, ATM/OTM and ITM/OTM premium-close ratios help forecast
the NEXT underlying move, beyond information already in spot/ATM momentum?
Also distinguish contemporaneous description from prediction, and test the
cost sensitivity of buying the directionally corresponding ATM option.

## Data and causal construction

- August 2026 expiries only; August regular-session observations only.
- Parse monthly/weekly contracts. Resolve missing monthly expiry metadata from
  the dated monthly expiry on other contracts of the same exchange. Reject
  observations after expiry. Report inconsistent metadata and missing coverage.
- For each underlying/expiry/day, select a separate CE and PE ladder at 09:45
  IST, using the spot close and positive-volume option minute closes at 09:44.
  Choose the nearest available ATM strike, then adjacent available strikes on
  each side. Require genuine ITM and OTM relative to that spot, ATM distance
  <=2% of spot, and each wing gap <=2% of spot. Do not use future availability
  or end-of-day volume in selection. This is the available historical archive,
  NOT proof that a complete exchange option chain was retained.
- Freeze those contracts for the session (as in the app's daily ladder).
  ITM/ATM/OTM names refer to selection-time roles; do not splice changing strikes.
- Aggregate 5/15/30 minute bars anchored to 09:15 IST. Primary observations
  require every expected minute and the exact closing minute on all three legs;
  all signal premiums >=5 and closing minute volume >0. No forward-filling.
- Ratio changes require consecutive bars on the SAME contracts and day.
- Compute all three ratios, but only the first two enter models: the third is
  exactly their product, so these are not three independent confirmations.
- Underlying labels: NEXT minute open to the close 30/60 minutes later, within
  the same session. Option returns use the SAME ATM contract, next-minute open,
  clock-aligned exit and complete minute path. Missing option outcomes do not
  remove observations from the separate underlying-direction analysis.

## Evaluation fixed before seeing outcomes

- Train: 3–13 August. Validation: 14–18 August. Test: 19–27 August, bounded by
  each contract's expiry. No refitting, feature scaling, cutoffs or sign choices
  from test data. These dates were used in earlier unrelated research, so this
  is a chronological feature-study holdout, not a pristine prospective trial.
- Primary: paired CE+PE, 5m bars, next 30m underlying return. CE-only, PE-only,
  15m/30m bars and 60m outcomes are prespecified secondary comparisons, not
  independent replications or a license to select the best test result.
- Fixed direction hypothesis: CE ratio compression is bullish, PE compression
  is bearish. Score CE = -change(log(ITM/OTM)); PE = +change(log(ITM/OTM));
  paired = average of CE and PE scores. Test contemporaneous and forward ranks.
- Fixed ridge regression (alpha=10): price/control baseline; two ratio levels
  and changes only; baseline plus those ratios. Scaling and 1%/99% feature
  clipping fit on training data only. Baseline includes current spot return,
  30/60m spot momentum, 30m range, session return/time, ATM premium momentum,
  strike distance/gaps and calendar DTE. Also compare 30m spot momentum sign.
- Do not train a panel with <100 train observations or <4 train sessions.
- Confidence intervals resample WHOLE held-out dates (2,000 draws, fixed seed),
  not independent overlapping bars; compare augmented vs baseline on the exact
  same rows. Report dates, symbols, pooled and equal-date metrics.
- Hypothetical long-option entries: fixed clock grid at 09:45 plus integer
  multiples of the forward horizon, no overlapping same-underlying/side bets
  within a lane. Direction-aligned scores must exceed the TRAIN 75th percentile
  of absolute score. No threshold sweep. Report gross and assumed 0.5%, 1%, 2%
  premium round-trip costs; these are sensitivities, not observed bid/ask fees.
- Report CE/PE, date, underlying concentration, expiry-day exclusion, premium
  denominator sensitivity (>=10), and 100% vs >=80% source-minute coverage.
  The 80% panel is a separately labeled sensitivity, never silently substituted.
- One month, one monthly expiry, missing quotes and historically selected
  contracts cannot establish deployability. No production rule follows this run.
