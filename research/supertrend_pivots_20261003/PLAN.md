# Independent MACD / Supertrend / pivot study

Frozen before computing outcomes, 3 October 2026. This study reads the application's original candle data and current strategy code. It does not import earlier research results or models.

## Decision

Does adding Supertrend or prior-period pivot context to a premium MACD zero-cross improve net return, downside and capital efficiency? Test entry filtering separately from exits. Treat the existing current ranker, dispersion sizing, pyramiding and mixed blast intake as separate allocation effects, outside the first controlled indicator experiment.

## Data and baseline

- Read `historical_candles` through SQLite `mode=ro` and `query_only` in one read transaction. The initial read-only-volume runner cannot open this WAL-mode database without its missing shared-memory files; therefore stream the read-only extraction through the existing engine container. All calculations run separately on the fixed, hashed local five-minute extract. Evaluate exits conservatively on five-minute bars.
- Rebuild session-anchored 30-minute MACD(12,26,9) from completed bars. Standard-rule baseline: zero-cross up, next five-minute open, 30% stop, 25% trail after +30%, prior-bar MFE <10% MACD invalidation. Smart-rule baseline: wait five minutes and recheck MACD, 40% stop, 30% trail after +40%, +100% target. Both use a common four-session research maximum and four initial lots with one-lot scale-outs at +30/+50/+75%, no pyramiding. The standard live lane has no four-session limit. This isolates indicators; it is not an exact tick-level live-book replay.
- Expiry exits at 15:10. Outcomes with unresolved or missing exit data are censored rather than booked as completed profits. New entries stop four observed trading sessions before the sample end. Filter comparisons use exactly the same eligible opportunities, with untraded signals earning zero.
- Premium costs: 5 bp per side (current setting), then 25 and 50 bp sensitivity; costs include a disclosed conservative research charge of 10 bp buy, 20 bp sell and Rs 20 brokerage per fill. These are scenario assumptions, not a tax calculation.

## Prespecified feature tests

- Supertrend: Wilder ATR, default (10,3), five/15/30-minute completed bars, on premium and underlying; 30-minute (7,2), (10,2), (10,4), (14,4) sensitivity. Direction, alignment/contrarian comparison, recent flip and distance from the line in ATR units.
- Daily and weekly traditional pivots from the prior completed regular session/week: P/R1/R2/S1/S2. CPR uses P, BC=(H+L)/2, TC=2P-BC sorted. No developing-day/week high or low enters these levels.
- Premium above P, underlying side aligned with P, supportive/opposed CPR location, nearby obstacle vs at least 0.5 ATR room to the next level, recent pivot break, recent rejection (completed bars only). Match CE with bullish spot and PE with bearish spot.
- Primary conjunction: default 30-minute Supertrend alignment plus underlying pivot headroom. Avoid exhaustive parameter combination search.
- Exit overlays: premium 30-minute Supertrend reversal, underlying adverse 30-minute Supertrend reversal, prior-day pivot loss. Retain baseline hard risk controls.

## Validation

- Signal-level returns and effects separated CE/PE, stock/index, chronological halves and expiry distance. Use entry-day clusters; do not treat thousands of correlated signals as independent observations.
- All fixed strategies are tested in the chronological holdout. Apply Holm correction across the complete baseline/overlay family; report raw and corrected p-values.
- An explicit no-trade benchmark is included after the initial baseline replay showed losses, so reducing exposure cannot be mislabeled as a positive trading edge. Expiry metadata can be recovered from observed sibling strikes only when the exchange, underlying and encoded expiry identifier agree and the recorded date is unique. Save provenance; do not infer calendar dates.
- Walk-forward selector trained only on earlier, fully resolved trades, with a four-session purge. Select from entry filters using day-level net return; compare to an explicit no-filter baseline in subsequent blocks.
- Sequential cash-limited replay with whole lots, per-contract exclusion only for funded entries, fixed Rs 1 lakh tickets and Rs 50 lakh capital. Maintain cash from each partial exit. Mark equity using the extract. Do not claim live-book parity.
- Validate indicator prefix invariance, pivot visibility, cost monotonicity, entry/exit ordering, no data-end winner fabrication, CE/PE mapping and sequential cash conservation. Save scripts, event table, results and a notebook/report alongside this plan.
- Post-scan validation: the prespecified (7,2) sensitivity produced one corrected day-cluster finding. Add ISO-week clusters to all comparisons and a cash-book/cost check for that already-tested rule; classify these as validation on the same sample, not new independent evidence. It fails the corrected weekly check and cash-book significance check.

## Subsequent baseline correction, 3 October

The user supplied a positive ranked/dispersion-sized minute replay and pointed to the app book. `RECONCILIATION.md` documents the resulting audit. A read-only replay of 517 actual MACD fills matches API cash, realised P&L and all 61 holdings; the latest carried-mark book profit is +Rs 1.86 L. The active fill history starts on 29 September, so it is not a full September account ledger. The supplied benchmark's 3,298 signals cover a different period and use materially different entry, allocation and management rules. Exact historical decision/execution parity is not established. Conclusions or candidate preferences for the actual strategy are therefore suspended; the original prespecified simplified experiment remains a diagnostic record. No trading configuration was changed.

## Sources

- App source: `engine/src/macd_trader/{indicators,strategy,execution,smart_lane,candles,sessions}.py`; current non-secret settings.
- Definitions: https://www.tradingview.com/support/solutions/43000634738-supertrend/ and https://www.tradingview.com/support/solutions/43000521824-pivot-points-standard/ . These supply definitions, not evidence of edge.
