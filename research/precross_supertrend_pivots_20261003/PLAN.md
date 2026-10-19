# Premium MACD before zero: Supertrend and pivots

Frozen 3 October 2026 before inspecting these results. This is a new causal rule study; the September-trained Smart scorer is not used. Source apps, settings, orders and ledgers remain read-only. Results are delivered inline with inspectable evidence and an executed research companion.

## Decision and controlling evidence

Can premium Supertrend or prior-session pivots anticipate a genuine premium MACD zero-cross up early enough to improve a trade? A cross forecast is not a profit forecast. Use the existing read-only 3,036-option/214-underlying archive, selected prospectively per session using opening spot and previous option volume. The retrospective catalog and absent OI/spreads/ticks remain limitations. Primary source precedence is current archive then old; repeat the core analysis with old-before-migration precedence. Only complete regular-session one-minute OHLC observations are used; zero-volume option bars cannot execute orders.

MACD is production 12/26/9 on session-anchored 30-minute candles, previewed from the previous committed EMA state without counting each minute as a new 30-minute observation. A production-style cross requires previous committed MACD <=0 and a positive preview, with one firing per bar. No early trial is allowed after a prior cross in that bar. Derive the exact current zero-cross premium threshold from the EMA state.

## Frozen triggers and behavior tests

Base turn: previous committed MACD <=0; preview <0 but above its preview signal; MACD rises on three consecutive minute steps in the same forming candle, with all four observations nonzero-volume and contiguous; projected time to zero from those three steps <=30 minutes. Earliest eligible observation per contract/bar/rule, up to 14:45 IST. No trigger is selected by its eventual outcome.

Eight early rules: turn alone; turn plus completed premium ST bullish; turn plus forming premium ST bullish; turn plus premium above prior daily P; turn plus P reclaimed in the last three minutes; turn plus forming ST and above P; turn plus forming ST and >=0.5 completed premium ATR headroom to the next Traditional pivot; turn plus forming premium ST, completed underlying ST in CE/PE direction and >=0.5 underlying ATR pivot headroom. ST uses ATR(10), multiplier 3. Forming ST uses only high/low/close observed so far and may revert; it is kept separate from completed ST.

Label all eligible turn events, including failures: first subsequent executable sampled production-style cross within 30/60/120 minutes and by session close; a positive completed 30-minute MACD bar within 60 minutes is a separate confirmation outcome. Windows lacking the full horizon are censored, not counted as failures. Report lead time, distance to zero/ST/P, premium saving versus cross+5 minutes, and price change for failures. Duplicate minute observations are not independent tests.

Fit two transparent L2 logistic models on Sep 1–11 only: MACD geometry/context versus the same inputs plus ST/pivot measurements and missingness. Standardization and missing-value medians are trained only on that period. Models are evaluated once on Sep 15–30; no training/model threshold is selected using that holdout. These are diagnostic predictive models, not trading-policy gates. Compare AUC, Brier/log loss and day-cluster changes; report 30-minute target as primary, 60/120 as descriptive. Match rule/state comparisons by time-to-zero and distance-to-zero strata so an indicator cannot get credit merely for selecting closer MACD values.

## Entry economics

Primary event experiment pairs an early entry with waiting for its actual cross+5 minutes, and includes all false-positive early trials with a 30-minute cross timeout. Early order uses the next nonzero-volume minute within three minutes and must still be below zero with no intervening cross. Waiting order uses the first nonzero-volume observation at/after cross+5 minutes within three minutes, and must still have positive preview. Completed-cross and cancelled delayed orders remain distinct.

After entry: 30% premium hard stop; trail armed at +30%, 25% off peak with round-trip breakeven floor; completed MACD negative invalidates only after an actual cross and if MFE<10%; an early trade that never crosses is flattened after 30 minutes at a fresh quote. Intraday closure from 15:25; no invented last-price/expiry fill. No pyramids or partial exits in this entry-isolation experiment. Mark unresolved quantities with observed OHLC separately and flag them; they are not realised exits. The same post-cross risk rules apply to early and waiting paths.

Operational details fixed before execution: early signals stop at 14:45, normal cross signals at 15:20, and entries at 15:24. A cross at exactly the timeout deadline counts as timely. Simultaneous orders are sorted by symbol rather than any outcome score. Assumed fee scenarios reserve the future closing-order fee for every held position. Forming ST and pivots use only P/R1/R2/S1/S2; no next-level obstacle means headroom capped at five ATR for modelling. The 30-minute predictive-model Brier comparison joins the 16 portfolio comparisons in one Holm family of 17.

Chronological whole-lot books are a secondary feasibility check. Baseline takes ALL selected-universe crosses, including crosses with no early precursor. Compare cross+5 and cross+1 controls with each hybrid (take an early trigger when its rule fires; otherwise wait for a normal cross+5 entry). At most one pending/open position per contract, cash limits in time order, ticket=min(₹1 L,10% current equity), liquidity capped by 2% prior session volume, capitals ₹1 L/₹50 L separate. No Smart ranker or dispersion sizing, no Blast feed, no claim that this reproduces the complete multi-day production ladder.

Development Sep 1–11; embargo Sep 12–14; held-out Sep 15–30, with Sep 18–29 and 19–29 reported separately. Select one hybrid by development ₹50 L profit including no-early control; evaluate selection on holdout without retuning. Primary inference uses paired entry-day/portfolio-day differences and Holm correction for eight hybrids versus each of two controls (16 comparisons); only independent session clusters count. Same-cost sensitivity uses +20/+50 bp per side and assumed ₹20/order for baseline and frozen hybrids; timeout sensitivity 15/60 minutes is descriptive and cannot select the rule.

## Validation and delivery

Test EMA zero price, production preview parity, ST preview versus committed state, prior pivots and prefix invariance, no pre-cross-after-cross trials, horizon censoring, failed-trigger inclusion, confirmation/delay gates, timeout/stop accounting, nonnegative whole-lot cash, and immutable source ledgers. Retain source hashes, all predefined rule results, day-cluster uncertainty, false positives and failed/cancelled fills. Do not deploy an early-entry gate from this limited monthly sample.
