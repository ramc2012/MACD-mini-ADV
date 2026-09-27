# Trading system architecture and migration gates

## What runs locally today

| Layer | Implementation | Role |
| --- | --- | --- |
| Browser | React/TypeScript, Lightweight Charts for candles/CVD, Canvas 2D footprint, SVG profile | Trading terminal and chart interactions |
| API | Go reverse proxy | Routes browser API/WebSocket requests to the engine and exposes Rust analytics |
| Live engine | Python/FastAPI and Fyers SDK | Market data, inferred order flow, strategy, risk and **paper-only** execution |
| Durable data | SQLite | Raw ticks, condensed flow, research history and paper books |
| Analytics | Rust service and QuestDB | EMA/MACD/volatility observations from chart candles; QuestDB does **not** yet store raw ticks |

The Go hop has not improved the measured local API latency. The Rust service
currently has no authority over orders or risk. Polars, Parquet, DuckDB and a
WebGL footprint renderer are not part of this deployment.

## Target boundaries

1. **Market-data contract.** Normalize broker updates into versioned events with
   exchange timestamp, receipt timestamp, symbol, sequence, cumulative volume,
   last quantity, best bid/ask and depth quality. Track gaps, stale data and
   dropped updates. Persist the raw stream before calculating derived views.
2. **Deterministic projections.** Derive bars, TPO, volume at price, footprint
   and inferred aggressor side from that same event contract in both replay and
   live mode. Keep the original update and classification method alongside each
   estimate. The broker does not provide an exchange aggressor flag in the
   current SymbolUpdate stream; bid/ask split and delta remain estimates.
   Evaluate FYERS' separate TBT depth socket for a small focused symbol set;
   it does not by itself turn inferred trade side into an exchange label.
3. **Risk and orders.** Move order state and a fail-closed pre-trade gate into a
   separately tested Rust core only after replay parity is established. Strategy
   code, whether Python or Rust, submits an intent through that gate. A Rust
   implementation alone does not make skipped checks impossible; the broker
   adapter must have no other order path, and retries need idempotent IDs.
4. **Storage and research.** Add QuestDB raw-tick ingestion with observed loss
   and lag metrics, then export immutable session partitions to Parquet. Use
   DuckDB to query those files and Polars in Python research. Keep replay inputs
   immutable and record strategy/configuration versions with each run.
5. **Rendering.** Keep Lightweight Charts for time series. The footprint and
   Market Profile need readable price-row navigation, complete on-demand
   ladders, visible POC/VA/IB references, provenance/coverage labels and honest
   empty states. Choose WebGL only after profiling Canvas 2D with realistic
   sessions; it does not repair missing or sampled market data.

## Gates before claiming an improvement

- **Fidelity:** replay and live consume the same normalized events; volume at
  price reconciles with captured quantities, including unclassified volume.
- **Safety:** tests prove every order intent passes the risk gate and failures
  reject the intent; paper mode never calls the Fyers order API.
- **Durability:** restart/replay produces the same bars and profile; stream gaps,
  buffer drops, writer lag and unavailable historical ticks are visible.
- **Performance:** measure p50/p95/p99 broker-update-to-chart latency, dropped
  updates, ingest throughput, UI frame time and memory under the same symbol
  set. Compare against the Python system during a real market session. Broker
  API latency is measured separately from local processing.

The chart improvements in this directory are the first rendering step. They
do not imply that the live engine has been migrated to Rust or that broker
data provides exchange-certified aggressor volume.
