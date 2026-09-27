# Rust analytics service

`POST /analyze` accepts the original MACD Trader chart response, including its
extra `indicators` and candle OHLCV fields. The service sorts candles by Unix
timestamp and keeps the last close for duplicate timestamps. It calculates
12/26 EMAs and 9-period signal EMA with the same first-value seeding as the
original engine. `trend` is `bullish`, `bearish`, or `neutral` from the MACD
sign; an empty history returns `no_data` and null numeric metrics.

`realized_volatility_pct` is the sample standard deviation of consecutive log
returns, annualized with 252 Indian trading days and 22,500 regular-session
seconds per day (or 252 bars per year for daily candles), multiplied by 100.
It is null until at least three candles are available.

Response shape:

```json
{
  "symbol": "NSE:NIFTY50-INDEX",
  "timeframe_seconds": 60,
  "candle_count": 3,
  "last_timestamp": 1789994400,
  "last_close": 25000.0,
  "ema_fast": 24980.0,
  "ema_slow": 24970.0,
  "macd": 10.0,
  "signal": 8.0,
  "histogram": 2.0,
  "realized_volatility_pct": 15.0,
  "trend": "bullish"
}
```

`GET /health` returns `{"ok":true}`. The service listens on port 8081 by
default (`PORT` overrides it). If `QUESTDB_ADDR` is set to an ILP TCP address
such as `questdb:9009`, completed observations are queued for best-effort
delivery. A QuestDB outage never fails analysis requests.
