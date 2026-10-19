# Trend rotation — 17 October 2026

Added a separate **Market breadth → Trend rotation** tab at `#/MACD/MARKET/TREND`. The original RRG remains available.

ATM calls and ATM puts have exactly two valid price states: **Trending up** and **Fading**. Each uses its own exact option premium, so puts rising in a falling market are Trending up. Spot has **Trending up**, **Trending down** and **Fading**. Missing or stale history is unavailable, never a price state.

The plot has tails and state regions, with 30m/1h/daily intervals, a 2–8 bar tail slider, state counts/filters, search, sector/index filtering, and a selectable instrument list. Focus details include the full option contract, strike, expiry, completed-bar close and last stored price time. The route survives refresh.

## Classification

- Trending up: EMA5 above EMA20, with EMA5 rising over the last three completed bars.
- Trending down (spot only): EMA5 below EMA20, with EMA5 falling over three bars.
- Fading: every other valid pattern, including flat or declining premiums and rebounds that have not established the rising trend.

Horizontal position is the EMA5–EMA20 gap. Vertical position is EMA5's three-bar slope. Both are scaled by the mean absolute close change over 14 bars, with a small floor for flat prices. This measures absolute price movement without a benchmark. Rules are available in the UI and in `engine/docs/trend_rotation.md`.

Only completed bars under the app's instrument session calendar are used. At least 21 bars and the latest expected completed bucket are required. Each option tail stays on one contract rather than stitching changing ATM strikes. These are descriptive states, not validated entry signals.

## Verification

- Full engine suite: **1,006 tests passed in 40.22 s**. New tests cover two/three-state behavior, absolute rises/falls, causal prefixes, future/unfinished bar exclusion, no database writes, stale/new contracts, completed daily data and segment/weekend close boundaries.
- Frontend TypeScript and production build passed. UI checks covered CE/PE/spot switching, state filtering, instrument search, sector selection, tail adjustment and exact-contract focus.
- Deployed API: 30m CE had 207 classified / 3 unavailable, PE 197 / 4, and spot 227 / 0. Daily spot, daily CE and hourly PE were also exercised. Daily option contracts with fewer than 21 bars are explicitly unavailable.
- Both running imported engine packages match current source and retain paper execution with live orders disabled. Engine warmed 667/667 with no lane errors. Existing strategy settings are unchanged.
- Consistent backups of all six paper books are in the data volume at `backups/pre-trend-rotation-20261017/`. Trades, orders, closed positions, capital records, Smart metadata and open risk state match the backups, excluding routine save timestamps. All six databases pass integrity checks.
- This tab reads stored data; it adds no order or strategy hooks and makes no new broker calls. Saturday's evidence does not establish Monday's feed freshness or trading performance.

Final rendered check: selecting TCS keeps `NSE:TCS26OCT2400CE` pinned while changing the tail length. Fixed detail-panel height prevents hover from moving rows under a click. Final screenshots: `2026-10-17-trend-calls.png` and `2026-10-17-trend-spot.png`.
