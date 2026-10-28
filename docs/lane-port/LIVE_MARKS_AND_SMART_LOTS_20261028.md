# Live marks and Smart minimum-lot correction

Review started 2026-10-28 09:41:10 IST, during the regular paper session.

## Diagnosis

The small Smart book was receiving current MACD signals and running local allocation. At the initial check it had 19 current-day lot-size rejections and six faded confirmations. Five rejected one-lot costs fit the normal 10% equity/cash limit but exceeded the breadth-scaled minimum-lot allowance. Other one-lot costs genuinely exceeded the normal cap. The small book subsequently opened VOLTAS and JIOFIN positions under the previous rules, before deploying the rounding correction; those fills are not evidence caused by this patch.

Minimum-lot rounding now uses the unscaled normal ticket cap. Rank and breadth still scale multi-lot tickets; available cash, current MACD confirmation, executable quote, liquidity and other entry gates remain mandatory. The cap is not raised. Missed/rejected historical signals are not replayed. Original MACD behavior is unchanged.

Backend held marks were updating. Smart summary cards polled every 15 seconds while position rows already used live ticks. The frontend now consumes namespaced Smart portfolio events (cash, fills, remaining quantities and realized P/L), overlays the shared tick marks, and values both cards and rows from the same book. A newer REST snapshot takes precedence over a retained older stream portfolio after reconnect. Retained book returns still include earlier policies and are not evidence of the new policy's performance.

## Vanguard/CBE display

- Preserve previous close and same-session depth metadata across broker quote updates. Reject older ticks and discard prior-session metadata when the session changes.
- Market tables show signed rupee and percentage change from broker previous close, falling back to the completed-session close already in the local dataset. Historical session views do not overlay live prices.
- Watchlists show movement from actual fill entry when traded, otherwise movement from the frozen source mark. Net paper P/L is shown only for actual positions/closed trades; untraded rows say `Not traded`.
- Open paper positions and equity use the shared current bid when it is newer than the recorded position mark, with the book's adverse tick-rounded slippage and estimated exit fee. Entry fees are retained in open P/L. Frozen ranks, entry prices and books are not rewritten.
- Fresh Vanguard/CBE headers use the global latest market-quote indicator. Repeated visible row timestamps/source columns are removed from current market tables; individual mark ages, feature source metadata and stale coloring remain available through tooltips. Completed daily indicator basis is still identified globally.

## Validation

Focused Smart and local-model tests: 39 passed. Frontend tests: 95 passed. TypeScript/Vite production build passed. CBE display liquidation math on all ten captured positions reconciled to the backend to the paisa (stored net open P/L ₹390.54 at 2026-10-28 09:50:16 IST).

Private runtime evidence and seven consistent online SQLite backups are in `runtime/live-ui-20261028/`. Each backup passed `quick_check(1)`. Live-session table hashes can change because legitimate entries, marks and exits continue; they are not proof of byte equality. No ledger was reset, no historical broker download was performed, and no real orders were enabled.

Deployment and final runtime checks are appended below.

## Deployed verification

Both Smart pending queues reached zero at 2026-10-28 09:55:06 IST; the immediate pre-restart snapshot confirmed zero in both books. Only the strategy engine and frontend were recreated, using cached offline image layers. Engine initialization/feed reconnection was observed complete by 2026-10-28 09:57:13 IST. No stale historical signals were replayed.

At 2026-10-28 09:57:48 IST, every pre-restart Smart position retained its symbol, quantity, average entry, position identity, entry anchor, completed entry/exit stages and entry fees. The small book held VOLTAS (375 units) and JIOFIN (2,350 units); the larger book held 20 positions. Vanguard/CBE account identities were unchanged; CBE retained ten positions and ten fills, while Vanguard had no position yet. The settings hash matched. Protected execution-table counts did not decrease; legitimate live MACD/Smart fills occurred during the wider evidence window, so not all hashes match.

Two post-restart observations, at 2026-10-28 09:57:13 IST and 2026-10-28 09:57:48 IST, showed moving Smart and CBE P/L. Small-book unrealized P/L moved from -₹991.75 to -₹1,320.50; the larger book from -₹2,03,505.75 to -₹1,95,124.50; CBE from -₹519.91 to -₹541.58. These observations prove changing marks, not profitability or improved selection. Fyers reported connected, a live feed with zero current tick age, and no lane errors or dropped stale signals in the restarted process. Environment locks remain `paper` / live orders disabled.

Installed Smart source SHA and served frontend index SHA match the checkout/build. Browser verification confirmed current quotes, day changes, one global market quote timestamp, compact rows and actual entry/P&L in the CBE watchlist. Screenshots are retained alongside the private evidence.

Rollback image tags: `macd-mini-parallel-engine:before-live-ui-20261028`, `macd-mini-parallel-frontend:before-live-ui-20261028`. Do not restore a database backup over later legitimate paper fills. No post-patch new small-book entry has been observed yet; the two observed fills preceded the rounding correction. Full-lot costs above the normal ticket/cash limit still correctly block entry.
