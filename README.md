# MACD Mini Parallel

A separate local Docker deployment of the [MACD Trader](../MACD-mini/README.md) terminal. The full React/TypeScript trading UI and established Python broker, strategy, and paper-book engine remain available. A Go gateway now fronts the engine, while a Rust analytics service computes an independent quantitative view of chart candles and records observations in QuestDB.

## Run

From this directory:

```bash
docker compose up -d --build
docker compose ps
```

Open [the parallel terminal](http://localhost:3200). The Go gateway is at `http://localhost:8201`, and the QuestDB console is at `http://localhost:9002`. All host ports bind to `127.0.0.1`.

The separate US Paper Desk is retired from the default deployment. Its source and `us_paper_data` volume are preserved. To run it again, opt in with `docker compose --profile us up -d --build us-backend us-frontend`; see [its module README](us-app/README.md).

The feed is Fyers market data, with execution fixed to paper-only. The engine reports a broker configuration error until you connect your Fyers account in the terminal's **Settings → Broker connection** panel. The original deployment's ports and `runtime/` directory are untouched. This stack has its own `runtime/` and named QuestDB volume. The earlier simulation databases are preserved in `runtime/simulation-archive-2026-09-23/`, and their QuestDB observations remain in the old `macd-mini-parallel_questdb_data` volume.

To change the watchlist or supply Fyers credentials through Docker, copy `.env.example` to `.env` and edit it before restarting the stack. You can also enter credentials in the terminal's Settings panel. The Compose configuration fixes `MACD_EXECUTION_MODE=paper`, `MACD_ALLOW_LIVE_ORDERS=false`, and `MACD_AUTO_TRADE=false`.

## Services

| Service | Role |
| --- | --- |
| `frontend` | Full React/TypeScript terminal, served by Nginx on port 3200. |
| `gateway` | Go API and WebSocket reverse proxy on port 8201; serves `/parallel/health` and `/parallel/analytics`. |
| `engine` | Original Python/FastAPI application, isolated on the Compose network. It remains the authority for market data, strategies, orders, and paper books. |
| `analytics` | Rust service calculating EMA, MACD, and realized volatility from the engine's chart candles. |
| `questdb` | Time-series store for Rust analytics observations, with its console on port 9002. |

The optional `us` profile contains the retired `us-backend` and `us-frontend` services. They do not start with the default `docker compose up` command.

`GET /parallel/analytics?symbol=NSE:NIFTY50-INDEX` asks Go to fetch the selected chart from the Python engine, then sends those candles to Rust. The React Quant analytics page uses this endpoint. Rust writes the computed observation to QuestDB on a best-effort basis; temporary database unavailability does not stop the trading terminal.

The current architecture deliberately retains Python's existing strategy and execution logic. It does not claim a full Rust rewrite or a trained ML model. The new Rust and QuestDB path provides a measured migration point without changing the original trading decisions.

The Profile / Flow workspace now requests a complete TPO price ladder when its Auction tab is open and renders the rows in a bounded, scrollable viewport. The footprint chart keeps price rows readable when the range widens and labels its bid/ask split as an estimate. These views need captured Fyers prints to show an actual session; a broker connection without prints cannot produce a footprint or Market Profile. See [the architecture and migration gates](ARCHITECTURE.md) for the current limits and the proposed Rust, QuestDB tick, and research-data boundaries.

## Performance measured before broker login

The Go gateway adds a network hop to the existing Python API. In a local read-only test before switching to Fyers, 60 sequential requests per path (after warmup) gave:

| Path | Python direct p50 / p95 | Through Go p50 / p95 |
| --- | ---: | ---: |
| `/health` | 0.208 / 0.241 ms | 0.400 / 0.489 ms |
| `/api/snapshot` | 0.961 / 1.053 ms | 1.150 / 1.237 ms |

These results show about 0.19 ms of added median latency for the gateway and **no measured performance gain** for the original trading API. They do not measure Fyers response, concurrent load, tick-to-screen time, or order throughput. Broker performance can be measured after login under the same market session and symbol set.

## Check and stop

```bash
curl -fsS http://localhost:8201/parallel/health
curl -fsS 'http://localhost:8201/parallel/analytics?symbol=NSE:NIFTY50-INDEX&timeframe_seconds=60'
docker compose logs --tail=50
docker compose down
```

`docker compose down` keeps the paper-book and QuestDB volumes. Use `docker compose down -v` only when you intend to erase the QuestDB data; the bind-mounted `runtime/` directory remains separate.
