# US Paper Desk

This separate US equities app is retired from the default deployment. Its source and SQLite volume are preserved. It can still be started on [http://localhost:3300](http://localhost:3300), with its Go API on port 8301. It does not share orders, capital, or market data with the FYERS app on port 3200.

## Run

```sh
cd parallel-app
docker compose --profile us up -d --build us-backend us-frontend
```

Enter the Finnhub and Alpha Vantage keys with **Data keys**. The saved keys stay in the Docker volume with `0600` file permissions. Finnhub supplies market quotes. Selecting a stock automatically loads up to 100 Alpha Vantage daily candles; the backend caches each symbol for six hours. The chart's **1m** tab shows locally stored, closed bars assembled from actual Finnhub WebSocket trades. SPY, QQQ, DIA, and IWM are labeled ETF gauges, not stock trading symbols.

## Automatic spot paper strategy

The app has one persistent $1,000,000 paper stock account. The 100-stock universe remains in the watchlist. Finnhub's subscription limit constrains the active one-minute scanner to the first 32 stocks; the remaining stocks still receive paced quote updates but are not automatically traded. The exact active count appears in the strategy panel and may decrease if Finnhub rejects subscriptions. Automatic paper trading starts enabled as requested; **Pause automation** and **Resume automation** persist across restarts. Manual order submissions are disabled.

The saved Finnhub key returns HTTP 403 for historical one-minute stock candles. The scanner therefore builds actual closed one-minute OHLCV bars from fresh Finnhub WebSocket trades and persists them locally. It never inserts simulated bars or fills. Each stock needs at least 120 prior bars. A signal requires a MACD(12,26,9) signal-line cross upward, at least 20 fresh tracked stocks with at least 50% positive MACD, and the stock price at least 25% below its previous 750-bar high. Each buy is capped to the lesser of $100,000, 10% of equity, available cash, and 2% of the symbol's last-minute observed trade volume. The account allows at most 10 positions, long-only, with no pyramiding. A managed position exits at a 50% loss or, after a 30% gain, a 40% trailing stop floored at breakeven.

Paper entry and exit prices come from fresh observed Finnhub trades during regular NYSE hours. Orders use the latest observed trade price and do not model spread, slippage, or market impact. No broker order is sent. The feed and minute-bar coverage must be healthy before an entry can occur. Pausing automation stops new entries; protective exits on existing managed positions continue.

**This is a spot adaptation, not the validated options Blast Lane.** The original uses option premium relative to spot and same-side option breadth; stock trades cannot provide either. Its historical results do not establish an edge for this variant.
