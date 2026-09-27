# MACD Mini Parallel

The MACD Trader terminal as one self-contained local deployment: the Python trading engine ([engine/](engine/README.md)), a React/TypeScript terminal, a Go gateway that fans the engine's stream out to browsers and onto a NATS tick bus, and a Rust service that analyses ticks in parallel and records them in QuestDB.

The engine was copied from the standalone `MACD-mini` repository at commit `30b5360` (27 Sep 2026) and is maintained here. That repository is kept as it was but is no longer deployed; its old `macd-trader` Docker images and network have been removed.

## Run

From this directory:

```bash
docker compose up -d --build
docker compose ps
```

Open [the parallel terminal](http://localhost:3200). The Go gateway is at `http://localhost:8201`, and the QuestDB console is at `http://localhost:9002`. All host ports bind to `127.0.0.1`.

The separate US Paper Desk is retired from the default deployment. Its source and `us_paper_data` volume are preserved. To run it again, opt in with `docker compose --profile us up -d --build us-backend us-frontend`; see [its module README](us-app/README.md).

The feed is Fyers market data, with execution fixed to paper-only. The engine reports a broker configuration error until you connect your Fyers account in the terminal's **Settings → Broker connection** panel. This stack keeps all of its state in its own `runtime/` (bind-mounted into the engine) and named QuestDB volume. The earlier simulation databases are preserved in `runtime/simulation-archive-2026-09-23/`, and their QuestDB observations remain in the old `macd-mini-parallel_questdb_data` volume.

To change the watchlist or supply Fyers credentials through Docker, copy `.env.example` to `.env` and edit it before restarting the stack. You can also enter credentials in the terminal's Settings panel. The Compose configuration fixes `MACD_EXECUTION_MODE=paper`, `MACD_ALLOW_LIVE_ORDERS=false`, and `MACD_AUTO_TRADE=false`.

## Services

| Service | Role |
| --- | --- |
| `frontend` | Full React/TypeScript terminal, served by Nginx on port 3200. |
| `gateway` | Go API gateway on port 8201. Holds the one engine stream and fans it out to browsers, publishes every tick to the bus, and serves `/parallel/*`. |
| `engine` | Python/FastAPI engine built from [engine/](engine/README.md): Fyers market data, strategies, orders and paper books. The only process that talks to Fyers, and the authority for orders and risk. |
| `nats` | Tick bus (core NATS, no persistence). The gateway publishes `md.tick.<symbol>`; consumers subscribe. |
| `analytics` | Rust service. Live: worker shards consume the bus and build per-symbol 1-minute bars, MACD and volatility in parallel. On demand: `/analyze` of a chart's candles. |
| `questdb` | Time-series store for raw ticks (5-day TTL), live 1-minute bars (90-day TTL) and on-demand observations; console on port 9002. |

The optional `us` profile contains the retired `us-backend` and `us-frontend` services. They do not start with the default `docker compose up` command.

## Stream fan-out and tick bus

```
Fyers ─► engine (Python, engine/) ─► one websocket ─► Go gateway ─┬─► browsers (per-browser queues)
                                                                   └─► NATS md.tick.<symbol> ─► Rust shards ─► QuestDB
```

**Browsers.** The engine gives each subscriber a 512-event queue and drops the oldest event when it fills; a browser that falls behind then asks for a full 1.7 MB snapshot, which costs the engine's only event loop ~130 ms. The gateway is now the engine's single subscriber and serves every browser from a cached snapshot plus a 50,000-event replay ring. Each browser has its own queue in which the newest tick per symbol, the newest update per bar, and portfolio totals replace older ones, while orders, trades, signals and broker events are always delivered in order. Sequence numbers are rewritten per browser, so coalescing never looks like a gap. The parallel terminal acknowledges what it has processed; the gateway keeps at most 1,000 frames in flight per browser and holds the rest where they keep coalescing, because nginx and Docker's port forwarder buffer far too much for TCP backpressure to keep a slow tab current. A client that never acknowledges (such as the original terminal) is written to as fast as its socket accepts. `STREAM_FANOUT=off` restores the plain tunnel to the engine.

**Bus.** Every engine tick is published as a versioned message carrying the engine's sequence number, exchange time and gateway receipt time (`busTick` in [backend/stream.go](backend/stream.go)). The Rust service routes each symbol to one of `LIVE_WORKERS` shards (default: CPU count, 2–8), so symbols are processed in parallel and each symbol's ticks stay in order. Each symbol loads its stored minute bars from the engine once, two symbols at a time, so its MACD is meaningful from the first live bar. Only fresh session trades build bars and are stored: prints received more than two minutes after their exchange time (Fyers republishes each contract's last trade on connect) move the displayed price only. Analysis-leg quotes reach the bus at the engine's rationed rate, not the raw feed rate.

**What it does not change.** The engine's strategy, orders, risk and books are untouched. Its history download is already store-first: a restart replays stored minute bars and asks Fyers only when a symbol's history is missing or stale, or once for a newly listed contract. Its order-flow analytics (footprint, Market Profile, whale flow, option chain) still run inside the engine; moving them out needs engine changes.

When `MACD_API_TOKEN` is set, `/parallel/live*` and `/parallel/stream/stats` require it (as `X-Macd-Token` or `?token=`), as the engine's API does; `/parallel/health` stays open for container checks. The terminal loads its secondary pages (Quant, Profile, Auction, Blast lane, Equity, RRG, Ratios, Signals) on first visit, which cut the initial script from 612 KB to 419 KB (133 KB gzipped).

| Endpoint | Purpose |
| --- | --- |
| `GET /parallel/stream/stats` | Gateway fan-out: engine connection, gaps, replay ring, per-browser queue, coalescing, flow control, bus publishes. |
| `GET /parallel/live?symbol=…` | One symbol's live bar, MACD, signal, histogram, realized volatility, warm-up and seed state. |
| `GET /parallel/live/scan?sort=abs_histogram\|histogram\|volatility\|ticks&limit=50&warm_only=false` | Symbols ranked, for the Quant page scanner. |
| `GET /parallel/live/stats` | Bus, shards, seeding and QuestDB writer counters. |

### Load test (27 Sep 2026, market closed)

A stand-in engine speaking the real stream protocol sent 5,000 ticks/s across 1,500 symbols for 90 s (450,000 ticks) through the gateway, NATS and eight Rust shards, isolated from the live stack. Browsers were Python clients that apply real backpressure; the slow ones processed one frame per 2 ms.

| Browser | Tick latency p50 / p99 | Orders delivered | Gaps / resyncs |
| --- | ---: | ---: | --- |
| Fast, via gateway | 36 ms / 58 ms | 1,800 / 1,800 | 0 / 0 |
| Slow, via gateway | 2.4 s / 5.3 s | 1,800 / 1,800 | 0 / 0 |
| Slow, direct to the engine protocol | 44 s / 89 s | 166 / 1,800 | 144,200 events dropped for it |

The bus delivered every tick (450,000 published and received, none dropped); shards took 32k–40k ticks each. At that rate the gateway used ~12% of a core and 40 MB, analytics 4–10% and 6 MB, NATS ~3.5%. Latency includes the gateway's 50 ms coalescing window. This measures the new components with a synthetic engine; it does not measure the real engine or Fyers during a session.

## On-demand analytics

`GET /parallel/analytics?symbol=NSE:NIFTY50-INDEX` asks Go to fetch the selected chart from the Python engine, then sends those candles to Rust. The React Quant analytics page uses this endpoint. Rust writes the computed observation to QuestDB on a best-effort basis; temporary database unavailability does not stop the trading terminal.

The current architecture deliberately retains Python's existing strategy and execution logic. It does not claim a full Rust rewrite or a trained ML model. The new Rust and QuestDB path provides a measured migration point without changing the original trading decisions.

The Profile / Flow workspace now requests a complete TPO price ladder when its Auction tab is open and renders the rows in a bounded, scrollable viewport. The footprint chart keeps price rows readable when the range widens and labels its bid/ask split as an estimate. These views need captured Fyers prints to show an actual session; a broker connection without prints cannot produce a footprint or Market Profile. See [the architecture and migration gates](ARCHITECTURE.md) for the current limits and the proposed Rust, QuestDB tick, and research-data boundaries.

## Performance measured before broker login

The Go gateway adds a network hop to the existing Python API. In a local read-only test before switching to Fyers, 60 sequential requests per path (after warmup) gave:

| Path | Python direct p50 / p95 | Through Go p50 / p95 |
| --- | ---: | ---: |
| `/health` | 0.208 / 0.241 ms | 0.400 / 0.489 ms |
| `/api/snapshot` | 0.961 / 1.053 ms | 1.150 / 1.237 ms |

These results show about 0.19 ms of added median latency for the gateway and **no measured performance gain** for the engine's request/response calls. The stream fan-out above is where the gateway earns its hop. They do not measure Fyers response, concurrent load, tick-to-screen time, or order throughput. Broker performance can be measured after login under the same market session and symbol set.

## Check and stop

```bash
curl -fsS http://localhost:8201/parallel/health
curl -fsS http://localhost:8201/parallel/stream/stats
curl -fsS http://localhost:8201/parallel/live/stats
curl -fsS 'http://localhost:8201/parallel/analytics?symbol=NSE:NIFTY50-INDEX&timeframe_seconds=60'
docker compose logs --tail=50
docker compose down
```

`docker compose down` keeps the paper-book and QuestDB volumes. Use `docker compose down -v` only when you intend to erase the QuestDB data; the bind-mounted `runtime/` directory remains separate.
