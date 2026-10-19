# Status, ledger counts and Fyers connectivity — 19 October 2026

The existing Status-tab source changes were retained and deployed with the completed ledger updates. Portfolio contains positions without Readiness, Portfolio exposure or Book Greeks; those details are in Status.

## Ledger verification

The main paper book has 4,513 saved order attempts: 1,328 FILLED and 3,185 REJECTED. It has 1,328 distinct trade fills, all linked to the filled orders: 572 BUY and 756 SELL. No missing order links were found. Orders badges count attempts, while Trades badges count executed fills, including partial exits. The new reconciliation summary makes this distinction explicit in Orders, Trades and Status. Badges and fill counts stay pending until the complete ledger loads.

Buy and Sell fill counts now sit beside Closed today. They use the same 08:00 IST trading-day book boundary, including weekends and exchange holidays, deduplicate trade IDs, and exclude future fills. Before the Monday roll the retained Friday book showed 40 buys, 73 sells and 53 distinct closed positions; partial exits explain why sell fills exceed closed positions.

Validation: all 88 frontend tests pass; TypeScript/production build passes; rendered Portfolio and Status checks pass. No backend code, order/fill records, capital or strategy settings were changed.

## Fyers failure and repair

The saved token initially expired on 18 October at 06:00 IST. The user's new login failed with network-unreachable during the auth-code exchange, leaving that old token in place. The Mac had IPv6 upstream connectivity and no IPv4 default route; Docker returned IPv6-only Fyers addresses to the IPv4-only engine network.

A dedicated IPv6-enabled broker network was tested in a disposable container, then attached to the running engine. Fyers HTTPS became reachable. Retrying the user's already-pasted login succeeded, and the token was verified and saved with expiry 20 October at 06:00 IST. Neither the credential nor an auth code is reproduced here. The engine process was not restarted. Its original application network and port bindings were preserved.

The machine-local compose override now attaches engine to both default and the external network `macd-mini-parallel_broker_ipv6`, so later container recreations retain the egress path. This network persists in Docker; if Docker networks are reset, recreate it with `docker network create --ipv6 macd-mini-parallel_broker_ipv6` before starting the stack. No global macOS or Docker Desktop settings were changed. Paper execution and live-order locks remain in place.

## Final connection evidence

At 07:40 IST, the engine reported connected, token not expired, feed alive, all **684/684 indicators warmed**, no history errors and no lane errors. Warm-up took 189.5 seconds, including history downloads for the renewed contract set. The rendered header confirms Fyers connected and stream linked. This is pre-open connectivity/warm-up evidence; the regular session has not opened yet.

The deployed frontend uses the verified production image. The same new count helpers were also run directly against the live API: 4,513 attempts / 1,328 booked / 3,185 rejected; 1,328 fills (572 buys, 756 sells); zero unlinked fills, fills linked to unbooked orders, or booked orders without fills. Current book: Buy 40 / Sell 73. Screenshot: `2026-10-19-clean-portfolio.png`.
