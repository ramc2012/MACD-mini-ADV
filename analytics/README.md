# Rust analytics service

## Live pipeline

With `NATS_ADDR` set, the service subscribes to `md.tick.>` and routes each
symbol to one of `LIVE_WORKERS` shards ([src/pipeline.rs](src/pipeline.rs)).
Each shard owns its symbols' state ([src/live.rs](src/live.rs)): 1-minute
bars on exchange time within the NSE session, MACD with the engine's
periods (read from `/api/settings` every minute; a change resets and
re-seeds), and realized volatility over the last 120 adjacent-bar returns.
A new symbol loads its stored minute bars from `ENGINE_URL`'s
`/api/chart/{symbol}?timeframe_seconds=60`, two at a time, and the shard
applies them in line with its ticks. Prints received more than two minutes
after their exchange time move the price but build no bar and are not stored.

With `QUESTDB_ADDR` and `QUESTDB_HTTP_URL`, the service creates `ticks`
(5-day TTL) and `bars_1m` (90-day TTL, de-duplicated on time and symbol)
before writing, then streams fresh ticks and closed bars over one batched
line-protocol connection. `QUESTDB_STORE_TICKS=off` keeps bars only.
Every queue is bounded and never blocks the bus; drops are counted in
`GET /live/stats`. `GET /live?symbol=` and `GET /live/scan` serve the state.

## On-demand analysis

`POST /analyze` accepts the engine's `/api/chart` response, including its
extra `indicators` and candle OHLCV fields. The service sorts candles by Unix
timestamp and keeps the last close for duplicate timestamps. It calculates
fast/slow EMAs and the signal EMA with the periods in the chart's
`macd_periods` (12/26/9 when absent) and the same first-value seeding as
the engine. `trend` is `bullish`, `bearish`, or `neutral` from the MACD
sign; an empty history returns `no_data` and null numeric metrics.

`realized_volatility_pct` is the sample standard deviation of log returns
between adjacent bars (overnight gaps and missing bars excluded intraday), annualized with 252 Indian trading days and 22,500 regular-session
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
