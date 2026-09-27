# Trading engine

The Python/FastAPI engine of this deployment: Fyers market data, the MACD
strategy, the auction desk and blast lane, and the paper books. It is the
authority for orders and risk; the Go gateway and Rust analytics only read
from it.

It was copied from the standalone MACD Trader repository
(`ramc2012/MACD-mini`) at commit `30b5360` on 27 Sep 2026 and is maintained
here from now on. That repository is left as it was and is no longer
deployed; changes made in one are not carried to the other.

Operating notes for the strategy, desks and blast lane are in [docs/](docs/).

## Process roles

`MACD_ENGINE_ROLE` selects what one process runs:

| Role | Runs | Entry |
| --- | --- | --- |
| `all` (default) | Everything in one process, as the engine always did | `macd_trader.app` |
| `strategy` | Fyers feed, contract selection, MACD and blast lanes; publishes ticks to `MACD_NATS_URL`; serves the desk its context, broker proxy and settings saves | `macd_trader.app` |
| `desk` | Market Profile / order-flow desk, tick capture, whale tracker, chain collector, nightly memory, from the bus | `macd_trader.desk_app` |

The desk's logic lives in `desk.py` (`DeskMixin`), shared by `all` and `desk`, and its routes in `desk_routes.py`. `python -m macd_trader` starts the app for the role. A `desk` process stays idle unless the engine it polls reports the `strategy` role.

Paths, secrets and the role are environment-only (`config.ENVIRONMENT_ONLY`); values for them in `settings.json` are ignored.

## Develop and test

```bash
cd engine
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q
```

Run the tests from this directory. The engine keeps its books under
`runtime/` relative to the working directory, so running them from the
deployment root would write test fixtures into the live `../runtime/`.

The Compose file builds this directory as the `engine` service and mounts
`../runtime` at `/app/runtime`. Paper execution is fixed by Compose
(`MACD_EXECUTION_MODE=paper`, `MACD_ALLOW_LIVE_ORDERS=false`).
