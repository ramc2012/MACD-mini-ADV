# CBE daily trend book

Owner direction: CBE is a medium/long-term daily-timeframe cash-equity paper book. A holding may last months.
Policy: `cbe-daily-trend-v1-20261029`. These parameters are explicit initial research defaults, not optimized or proven profitable.

## Signals and execution

| Rule | Implementation |
| --- | --- |
| Universe | Active F&O stock underlyings, executed as long cash equities only |
| Daily input | Completed cash OHLCV, exact aligned NIFTY closes; no provisional daily bars or price filling |
| History | At least 250 observed aligned daily bars, with all latest 64 exchange sessions complete for the 63-session return. Older seed gaps are disclosed; 80% universe readiness remains required |
| Trend | Close > EMA20 > EMA50 > EMA200; EMA50 above its value ten sessions earlier |
| Momentum | Positive 63-session stock return and positive excess return versus NIFTY |
| Entry event | Daily close above the highest high of the preceding 55 sessions, excluding today's bar |
| Selection | Up to ten eligible names ranked by 63-session excess return / ATR20 as a fraction of price; no obligation to fill all slots |
| Entry fill | Next exchange session in the retained morning window; fresh two-sided quotes, spread/liquidity/cash/concentration checks, adverse slippage and fees. Ask must remain above EMA50 and the modeled fill within one ATR20 of the frozen signal close |
| Holding | No fixed profit target, daily rebalance, expiry, or maximum holding duration. Dropping out of a new top-ten list is not an exit |
| Daily exits | Two consecutive daily closes below their EMA50, EMA50 <= EMA200, or a daily close below the previously established ATR trail |
| Daily trail | Initially max(entry minus 3 ATR20, entry minus 12%); ratchets to max(previous trail, current daily close minus 3 ATR20). Today's updated trail does not retrospectively act on today's intraday low |
| Exit fill | A daily exit is persisted after close, then waits for the next exchange session and a fresh executable quote. No close-price or backdated fill |
| Emergency stop | Independent 12% entry-price protective stop can act intraday, including entry day; gaps may exceed it |
| Size | Retained INR 200,000 ticket, further capped by a 0.5%-of-equity ATR reference-risk budget, cash, ten positions, 100% gross and 30% sector exposure. Reference-risk sizing is not a guaranteed loss bound |
| Settlement | Estimated fees and adverse tick-rounded slippage retained; cash-stock sale proceeds settle at next exchange open. Session-volume simulation does not model market impact or queue position |

MP, intraday OI, Qwen and the earlier context-ranking model do not gate this strategy. Daily decisions are deterministic.
Missing daily exit inputs preserve the last valid trail and emergency stop; they never imply a favorable trend or fabricated exit.
Daily discontinuities above 30% require price-basis/corporate-action review instead of qualifying a breakout.
The system does not automatically adjust paper inventory for splits/dividends; such holdings require reconciliation.

Trend persistence is the general research motivation; the [original time-series momentum paper](https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum)
studies diversified futures/forwards and does **not** validate this cash-stock universe, these thresholds or costs.
No retrospective profitability claim, historical broker download, parameter search or live-order promotion accompanies this implementation.

## Migration and preservation

Activation requires the old CBE book to be flat. It retains account identity, funding, fills, closed trades, settlement obligations and equity history.
Unfilled intraday plans become `SUPERSEDED` with an audit event. Old frozen publications are never edited or used as new daily signals.
The first new daily cohort must be frozen prospectively in a future publication window; old cohorts are not relabeled.
Lifetime account returns include the preceding intraday policy; the UI separately reports closed-trade count under the new policy.
Vanguard's selection, holding rules, book and execution terms are unchanged.

`daily_management` persists each holding's trail, reviewed session and next-session exit instruction. Restarting cannot clear a queued exit or loosen a trail.
For compatibility with the existing non-null SQL schema, new daily orders store a year-9999 sentinel in the legacy `exit_due` column.
It is never used as an exit trigger for daily orders; the API emits `exit_due: null` and the UI displays “Daily trend · no time cap”.

Before activation, online SQLite backups and table hashes were saved under `backups/cbe-daily-20261029/`.
The new source is independently covered by holds lasting months, entry-day protection, restart persistence, stale-quote rejection,
next-session daily exits, trail monotonicity, migration idempotence, old-cohort rejection and Vanguard isolation tests.
Full focused validation: 120 backend tests, 101 frontend tests and production frontend build passed before activation.

Rollback must not blindly restore books or old intraday pending orders. Preserve the latest books and manage any new daily holdings under their recorded terms.
The previous frontend image is retained as `macd-mini-parallel-frontend:before-cbe-daily-20261029`.
The source baseline before this change is local commit `28d99a1`; a rollback involving daily positions requires an explicit migration decision.

## Activated state

Verified at 2026-10-29 18:33:34 IST.

CBE is flat. Twenty closed trades, forty fills, all cash and settlement balances remain unchanged; ten pending intraday orders were superseded.
The strategy activation event occurs once and survives service restart. Vanguard account, positions, fills, orders and settlements match the pre-change backup.
Current data fails the new long-history/64-recent-close gate (0/213 ready); the July session gaps are visible, and no history was downloaded or filled.
No new daily cohort was manufactured from the already-frozen intraday publication. The next publication window will recheck readiness.

Vanguard and other lane decision contexts retain their exact previous daily reader in `shadow_lanes/daily_context.py`, separated from the new CBE selector.
This prevents new CBE horizons, fields and data gates from changing the other lanes’ context ranking.
