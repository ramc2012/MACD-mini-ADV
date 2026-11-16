# MCX lane

A paper book that runs the MACD lane's rules on MCX commodity options:
GOLD, SILVER, CRUDEOIL, COPPER and NATURALGAS to start with. It shares the
engine's Fyers websocket and nothing else: its own contracts, clock, bars,
book and settings.

## What it shares with the MACD lane

| | MACD lane | MCX lane |
|---|---|---|
| Contract | Side-rounded ATM CE and PE, 1.5× liquidity tie-break over the two strikes either side, nearest series ≥ `min_days_to_expiry` out | The same rule (`contracts.select_liquid_contract`), on each root's nearest option series ≥ `mcx_min_days_to_expiry` out, priced off that series' underlying futures |
| Entry | `MACDStrategyManager`: premium MACD crosses up through zero, intrabar or on the close, one signal per bar, optional KAMA/RSI/ROC confirmations | The same object, with the MACD lane's periods and confirmation switches |
| Stale signals | Dropped when the bar is older than one period plus 120 s | The same |
| Exits | `ExecutionManager`: hard stop, trail armed at +activation and floored at breakeven, scale-in +7.5/15/22.5%, scale-out +30/50/75%, optional MACD invalidation | The same object and the MACD lane's risk settings |
| Sizing | Whole lots nearest `target_position_notional` | Whole lots nearest `mcx_target_notional`, capped by `mcx_max_entry_lots` |
| Entry gate | NSE entry scorer ranks and skips the weakest 30% | None: the scorer's features describe NSE options, not commodities |

## What is MCX-specific

**Session.** 09:00 to 23:30 IST while New York is on daylight time, and to
23:55 while it is on standard time (early November to mid March). Bars are anchored at 09:00 (`mcx_bucket`), so 30-minute bars are
09:00, 09:30 … 17:00 … 23:00. The 2026 holiday list is built in: full
closures, and days on which only the evening session (17:00 onward) trades.
Add later years with `mcx_holidays_csv` and `mcx_evening_only_csv`; health
reports `calendar_covers_year: false` when the year has no entries.

**Lot units.** The book values a position as premium × units, and MCX's
instrument files count quantity in lots. The units per lot are therefore the
contract specification, not the master's lot column:

| Root | Lot | Premium quoted per | Units per lot |
|---|---|---|---|
| GOLD | 1 kg | 10 g | 100 |
| SILVER | 30 kg | 1 kg | 30 |
| CRUDEOIL | 100 barrels | 1 barrel | 100 |
| COPPER | 2,500 kg | 1 kg | 2,500 |
| NATURALGAS | 1,250 mmBtu | 1 mmBtu | 1,250 |
| GOLDM | 100 g | 10 g | 10 |
| SILVERM | 5 kg | 1 kg | 5 |
| CRUDEOILM | 10 barrels | 1 barrel | 10 |
| NATGASMINI | 250 mmBtu | 1 mmBtu | 250 |

`mcx_lot_multipliers_csv` (`GOLD:100,SILVER:30`) overrides them. If the
instrument master lists a lot that is neither 1 nor the units in use, health
shows a `multiplier_warnings` entry for that root.

**Contracts.** Read from Fyers' `MCX_COM.csv` (same layout as the NSE file).
The underlying column is matched exactly, so GOLDM, SILVERM and CRUDEOILM never
stand in for the full-size contracts. The futures an option series exercises
into comes from the master's underlying FyToken, or else is the nearest futures
expiring on or after the option. A same-day restart reuses the morning's
strikes (`runtime/mcx_contracts.json`), as the MACD lane does. For enabled
commodity roots with a listed mini, the same selection and paper execution rules
apply to mini Calls and Puts. The nearest mini future and the selected options'
underlying future appear in Futures for charting and monitoring.

**Charts.** Each watchlist contract opens the MACD chart. Its OHLC and indicator
history comes from the MCX minute store on the 09:00 IST grid. The shared feed
updates the forming candle from the minutes in the quote stream.

**Expiry.** MCX options devolve into futures. A holding in a contract
expiring today is sold from `mcx_expiry_flatten_ist` (default 23:00). A
holding whose contract expired while the engine was down is closed at its last
mark with reason `EXPIRED_WHILE_OFFLINE_LAST_MARK`.

**Paper only.** The lane's execution settings force `execution_mode=paper`
whatever the engine is set to, and its order surface raises if anything ever
calls it.

## Data

- **Ticks** come from the engine's websocket; the lane's options, their
  futures and every holding are added to the subscription. Every `MCX:` print
  stays out of the NSE candle engine, the desk and the tick bus. Displayed
  contracts send coalesced `mcx_quotes` updates to the terminal every 100 ms
  when quotes are arriving. Futures, Calls and Puts are separate watchlists.
  The last price cell shows the last uptick or downtick with a green or red fill.
  The watchlists show FYERS' previous close, price change, percent change and
  session volume. OI and previous-session OI refresh from FYERS depth in a
  paced background job; missing OI or a missing baseline displays a dash.
- **REST fallback.** During the session, a lane contract the socket has been
  silent on for `mcx_rest_fallback_seconds` (default 60, counted from the later
  of the last tick, the subscription and the open) is quoted over REST, one
  batched request at most every 15 s. A REST quote can trigger a stop or an
  entry only if its own trade time is within the order-quote age limit;
  otherwise it only marks the position.
- **Bar database** (`mcx_store.py`, `mcx_bars.sqlite3` beside the lane's book). One-minute bars of every contract
  the lane watches. A contract is downloaded once, the first time the lane sees it (`mcx_warmup_days`, default 10),
  and from then on its bars are the minutes recorded from live ticks. Nothing is downloaded every day.
  - **Watched:** today's ATM options, the futures each series is priced from, every holding, and a ladder of
    `mcx_ladder_strikes` strikes (default 5) either side of each ATM, calls and puts. The ladder is recorded so
    that tomorrow's ATM contract already has its bars.
  - **Minutes without a trade.** A contract's minute is written when the clock passes it (5 s grace), not on its
    next tick, and a minute with no print gets a flat bar at the last price with volume 0 -- what Fyers' own minute
    history holds for such a minute, so recorded and downloaded stretches read alike. Only while the feed is alive
    and within the day; a print arriving after its minute was written is counted (`quiet_minutes.late_ticks` in
    health), not merged. The 30-minute bars the lane trades on still come from prints alone.
  - **A second download** happens only to fill a stretch of session the lane received nothing for (the engine or
    the socket was down for more than two minutes). Outstanding gaps also
    retry for the watched ladder. The repair preserves existing bars and skips
    malformed or still-forming broker minutes.
  - **The switch** `mcx_history_downloads` decides whether those downloads may pass the app's history hold. Off:
    a new contract has only its live bars, and the warm-up report says it is waiting for the switch.
  - Bars older than `mcx_store_days` (default 60) are dropped at the close.
- **Warm-up** reads the database, folds the minutes onto the lane's 09:00 grid and replays them. The bar forming
  now is left to the live aggregator; live bars that close during warm-up are buffered and replayed after the
  history, never traded on.
- **Futures** are watched, not traded: they get the same bars and MACD and lead their commodity's rows in the
  contracts table. Their signals are not recorded.
- **Feed watchdog.** Once the lane has had a websocket tick today, the
  engine's watchdog treats silence during the MCX session as a dead socket, so
  an evening disconnect is recovered. An account with no MCX data never trips
  it.
- **Feed-time clamp.** Fyers republishes a feed time without a trade time;
  the broker clamps it to the instrument's close. For `MCX:` symbols that close
  is now the MCX close, not NSE F&O's 15:40.

## Signal verdicts

Every zero-cross the strategy fires is saved (one per bar, durable) and given
a verdict, kept in memory for the page and published as `mcx_decision`:

| Verdict | Meaning |
|---|---|
| `TAKEN` | bought in the lane's paper book |
| `AUTO_TRADE_OFF` | valid, and `mcx_auto_trade` is off |
| `ALREADY_HELD` | the lane holds the contract; it never re-enters on a new signal |
| `NOT_SELECTED` | a holding carried from an earlier selection; entries use today's ATM legs only |
| `SESSION_CLOSED` | fired on the closing-bar flush after MCX shut |
| `NO_CASH` | one lot costs more than the free cash |
| `ENTRY_REJECTED` | the paper order was refused (stale quote, position cap, cash) |
| `STALE_BAR` | the bar is from an earlier period than now |
| `DISABLED` | the lane is switched off |

## Operating it

1. Deploy and open **MCX lane** in the terminal. Check the contracts table:
   every root should show a futures price, an ATM CE and PE and lot units.
2. `mcx_auto_trade` starts **off**, like the MACD lane: signals are recorded
   with their verdicts and nothing is bought. Turn it on from **MCX lane →
   Settings** when you are ready to paper-trade it.
3. Stops, trail, scale-in/out and MACD periods are the MACD lane's settings.
   Changing them there changes them here.

## Interfaces

| | |
|---|---|
| `GET /api/mcx/snapshot` | settings, health, session, contracts with live premium and MACD, rules, book, recent verdicts |
| `GET /api/mcx/book` | orders, trades, signals, equity, portfolio, closed positions |
| `PUT /api/mcx/settings` | partial update (`enabled`, `auto_trade`, `roots_csv`, `initial_capital`, `target_notional`, `max_entry_lots`, `max_positions`, `timeframe_seconds`, `min_days_to_expiry`, `expiry_flatten_ist`, `rest_fallback_seconds`, `lot_multipliers_csv`); a change of roots, expiry rule or units re-selects, a new bar length re-warms |
| `POST /api/mcx/reselect` | re-run today's selection now; resubscribes only if the symbols changed |
| `POST /api/mcx/orders` | manual paper ticket against the lane's book (MCX symbols only) |
| `GET /api/system/health` | `mcx` block |
| stream | `mcx_order`, `mcx_trade`, `mcx_signal`, `mcx_position_closed`, `mcx_decision`, `mcx_quotes` |

Settings (environment prefix `MACD_`): `mcx_enabled`, `mcx_auto_trade`,
`mcx_roots_csv`, `mcx_initial_capital`, `mcx_target_notional`,
`mcx_max_entry_lots`, `mcx_max_positions`, `mcx_timeframe_seconds` (0 follows
`timeframe_seconds`), `mcx_min_days_to_expiry`, `mcx_expiry_flatten_ist`,
`mcx_rest_fallback_seconds`, `mcx_warmup_days`, `mcx_lot_multipliers_csv`,
`mcx_holidays_csv`, `mcx_evening_only_csv`; paths `mcx_database_path` and
`mcx_contract_snapshot_path` are environment-only.

## Checked at the port (16 Nov 2026)

The lane was written on the 19 Oct tree and ported onto the current one on 16 Nov 2026.

- **Selection, dry run on the real `MCX_COM.csv` and REST quotes (20:00 IST):** all five roots selected, both legs,
  the right underlying futures (GOLD and SILVER November options on the December futures), no multiplier warnings
  (the master's lot column is 1 for every option).
- **Close time corrected:** 23:30 while the US is on daylight time, 23:55 otherwise. The first version had the two
  the wrong way round.
- **Files:** the book (`mcx_trader.sqlite3`) and the contract snapshot sit beside the MACD book (`/app/data` in
  Docker) unless `MACD_MCX_DATABASE_PATH` / `MACD_MCX_CONTRACT_SNAPSHOT_PATH` say otherwise.
- **Warm-up under the recovery hold:** with `MACD_HISTORY_DOWNLOADS_ENABLED=false` the broker refuses every history
  request, so the lane starts with no bars and its MACD needs about 35 live bars (a little over one MCX session at
  30 minutes) before it can signal. The warm-up report lists each contract as failed with that reason.
  - `mcx_history_downloads` (MCX lane → Settings → "Download MCX history for the warm-up", or
    `PUT /api/mcx/settings {"history_downloads": true}`) lets MCX symbols alone through the hold, straight to Fyers:
    one request of `mcx_warmup_days` per option contract at each warm-up. Switching it on re-runs the warm-up at once.
  - The lane re-warms at every 08:45 selection and after every restart, so with the switch off each morning starts cold.
- **REST quotes carry no trade time for MCX** (the stamp is the start of the day), so a REST fallback quote only
  marks a holding; it never triggers an entry or a stop.

Still to confirm on a live session: `ws_ticks_today` rising (the account receives MCX data on the websocket), and
ticks arriving between 23:30 and 23:55 in the winter schedule.

## Code

- `src/macd_trader/mcx_lane.py` — calendar, bars, master parsing, selection, lane
- `src/macd_trader/engine.py` — wiring: subscription, tick routing, warm-up, watchdog, health
- `src/macd_trader/app.py` — `/api/mcx/*`
- `src/macd_trader/brokers.py` — `MCX_COM.csv`, `instrument_rows`, MCX-aware feed-time clamp
- `frontend/src/McxLane.tsx`, `frontend/src/mcxMath.ts` — the terminal page
- `tests/test_mcx_lane.py`, `frontend/tests/mcxMath.test.ts`
