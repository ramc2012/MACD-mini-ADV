# Blast lane

A third paper book beside the MACD lane and the auction desk. It exists to
reach the option moves the MACD lane structurally cannot buy, and it ships
**journalling only** until its own shadow record says otherwise.

## Why it is a separate lane

Measured over Jun–Sep 2026, the MACD lane's zero-cross entry with its
+30/+50/+75 ladder is close to a coin flip: its 311 closed positions averaged
−0.16% per position, and five trades produced 76% of the month's rupees. Three
properties of those tail moves do not fit the MACD lane's shape.

| | MACD lane | Blast lane | Evidence |
|---|---|---|---|
| Entry event | MACD crosses zero | MACD crosses its **signal line** | Of 10,739 screened candidates, 10,544 were signal-line crosses with MACD still below zero; 192 were zero-crosses. By the time MACD reaches zero the contract has rallied out of the screen. |
| Selector | none | premium ÷ **spot** | Raw rupee premium looked predictive only because it stood in for this ratio. |
| Exit | ladder + 25% trail | −50% stop, 40% trail, no ladder | Screened candidates averaged close to +100% forward excursion; the ladder gave nearly all of it back. |

### What the walk-forward did and did not establish

Thresholds were frozen from the Jun–Aug bins and scored once on eight unseen
sessions (31 Aug – 9 Sep), with a two-session forward window.

| | picks | doubled within 2 sessions | return per pick (wide exit) |
|---|---|---|---|
| all candidates, unfiltered | 1,427 | 7.9% | −2.0% |
| premium ≤ 1.2% of spot | 164 | 14.6% | +1.8% |
| full screen | 57 | **17.5%** | **+5.7%**, 95% CI [−1.4, +18.1], permutation p = 0.080 |

The lift in the probability of a pick doubling is established and replicated.
The rupee edge is the right sign but not a proven size. Removing the
premium ÷ spot leg halves the doubling rate (to 9.8%); it is the load-bearing
leg. That is why `blast_auto_trade` defaults to **off**.

## The screen

Evaluated on every **closed one-minute** premium bar of every tradable option
contract, with the lane's own MACD(12,26,9), in this order. The strategy
timeframe does not change this: the study measured one-minute bars, and fed
30-minute bars on 15 Sep 2026 the lane refused 65% of candidates as
`NO_HISTORY` and judged crosses the research never tested. A minute bar closed
more than two minutes after its minute ended (an illiquid contract's next tick)
still advances the window and MACD but is not judged, and is counted in health
as `late_bars_skipped`. The first failing leg is the verdict.

| Verdict | Meaning |
|---|---|
| `ALREADY_HELD` | the lane already holds the contract, so the screen never judged it |
| `NO_SPOT` | no price for the underlying on the stream yet |
| `NO_BREADTH` | fewer than `blast_min_breadth_cohort` tracked contracts on that side |
| `NO_HISTORY` | fewer than `blast_min_lookback_bars` closed bars to measure the recent high |
| `PREMIUM_TOO_RICH` | premium above `blast_max_premium_pct` % of spot |
| `BREADTH_TOO_THIN` | same-side share with MACD > 0 below `blast_min_breadth` |
| `NOT_OFF_HIGH` | premium less than `blast_min_off_high_pct` % below the high of the last `blast_high_lookback_bars` bars (the judged bar excluded) |
| `TOO_ILLIQUID` | one lot would exceed `blast_max_volume_share_pct` % of the contract's own selection-time liquidity |
| `AUTO_TRADE_OFF` | passed; shadow mode, so journalled and not bought |
| `ENTRY_REJECTED` | passed; the paper order was refused (cash, position cap, stale quote) |
| `TAKEN` | passed and entered |

`ALREADY_HELD` is settled **before** any leg and is not a screen decision: the
lane never pyramids, and a holding's excursion is the position's own record. It
is journalled but not watched forward. Judged last, as it was until 18 Sep 2026,
a holding's re-signals were filed under whichever leg they happened to fail —
76% of the `BREADTH_TOO_THIN` comparison group was contracts the lane already
owned, including its best winners, which made every control group unreadable.

Entry size is then capped at `blast_max_volume_share_pct` % of the contract's
liquidity, read the way the ATM ladder reads it (`max(volume, oi/100)` from the
selection-time chain), and the contract is declined outright when one lot will
not fit. A flat rupee ticket ignores what the contract trades: on 17 Sep 2026 a
₹1 lakh entry into BAJAJFINSV 1960CE at a ₹2.05 premium was 81% of everything
that traded in that contract all day. Fills like that are fiction, and a lane
measured on fictional fills measures nothing. Liquidity the selector did not
supply leaves the entry uncapped — a missing field is a data gap, not a verdict.

Breadth is read across the whole tradable option band from the lane's own
one-minute MACD, counting only contracts with a bar in the last five minutes,
so a contract that stopped ticking cannot freeze its value into the fraction.

## Risk overlay

- hard stop at `blast_hard_stop_pct` below entry (default 50%)
- trailing stop `blast_trail_pct` below the peak (default 40%), armed once the
  gain reaches `blast_trail_activation_pct` (default 30%) and never placed
  below the round-trip breakeven
- no scale-outs and no pyramiding
- contracts expiring today are flattened by the 15:10 IST expiry sweep and, per
  tick, from 15:20 IST — judged from the lane's **own** contract record, so a
  holding that has rolled out of today's selection is still closed
- risk parameters cannot be changed while the lane holds positions (HTTP 409)

## Data policy: no broker requests

The lane never asks the broker for data.

- **Bars** are the engine's live one-minute aggregation — the same bars the
  candle writer stores — and the lane computes MACD from them itself.
- **Warm-up reads stored minute bars** from the local `historical_candles`
  table on every connect (startup, the daily contract re-selection, a feed
  reconfigure), before the stream starts: the newest 1,000 bars per contract
  (the 750-bar window plus 250 to settle the EMAs), and on a re-warm only bars
  newer than those already held.
- **Spot prices** come from the tick stream; **breadth** from the lane's own
  MACD state.
- **The lookback window is incremental.** A bar at or before the newest one
  the lane holds is ignored, so a re-warm neither duplicates bars nor appends
  old ones out of order, and a bar is never judged twice.
- **The broker handle is `OrderOnlyBroker`.** Any attribute other than
  `place_order` raises `BrokerAccessDenied`, so a history or quote call from
  the lane fails loudly instead of becoming an extra REST request.
- **The engine does not fetch on the lane's behalf.** Blast holdings are
  excluded from the warm-up list and from the startup REST quote refresh; they
  are valued from quotes already in memory and stay live through the existing
  websocket subscription.

## Journal

`runtime/blast_trader.sqlite3` holds the lane's own `orders`, `trades`,
`signals`, `closed_positions` (lane `blast`) and equity points, plus:

- `blast_journal` — one row per evaluated candidate: every rule input
  (premium, spot, premium %, breadth, distance off high, lookback bars, MACD and
  signal), the verdict, and the order id when taken. Each candidate that clears
  the premium gate — taken **or declined** — is tracked forward for
  `blast_journal_horizon_hours` (default 48); the high, low, MFE and MAE are
  written back every 30 seconds, and `resolved` flips when the window closes.
  Tracking resumes after a restart.
- `blast_contracts` — the contract behind every entry (underlying, side,
  strike, expiry), which is what the expiry exit reads.

The declined rows are the control group. Comparing the forward excursion of
`AUTO_TRADE_OFF`/`TAKEN` rows with `BREADTH_TOO_THIN` and `NOT_OFF_HIGH` rows is
how to tell whether each leg of the screen earns its place.

## Operating it

1. Leave it in shadow mode and let the journal accumulate. The walk-forward
   produced 7–20 passing candidates a session.
2. After a few weeks, compare mean MFE and the share of passed candidates that
   doubled against the declined premium-cleared rows, using resolved windows
   only. Several hundred passed candidates is the scale at which the earlier
   interval would narrow enough to decide.
3. Turn on auto-trade from the **Blast lane → Settings** tab. The page asks for
   confirmation and restates the caveat.

## Interfaces

| | |
|---|---|
| `GET /api/blast/snapshot` | settings, health, screen, risk, book, today's journal summary |
| `GET /api/blast/journal?day=&reason=&taken_only=&limit=` | rows, summary, sessions with rows |
| `GET /api/blast/book` | orders, trades, signals, equity, portfolio, closed positions |
| `PUT /api/blast/settings` | partial update; returns the new snapshot |
| `POST /api/blast/orders` | manual paper ticket against the lane's book |
| `GET /api/system/health` | `blast` block: watchers, open positions, last candidate, data policy, error |
| stream | `blast_candidate`, `blast_order`, `blast_trade`, `blast_portfolio`, `blast_position_closed` |

Settings (environment prefix `MACD_`): `blast_enabled`, `blast_auto_trade`,
`blast_initial_capital`, `blast_max_positions`, `blast_target_notional`,
`blast_max_entry_lots`, `blast_max_premium_pct`, `blast_min_breadth`,
`blast_min_breadth_cohort`, `blast_min_off_high_pct`, `blast_high_lookback_bars`,
`blast_min_lookback_bars`, `blast_hard_stop_pct`, `blast_trail_activation_pct`,
`blast_trail_pct`, `blast_journal_horizon_hours`, `blast_journal_max_watchers`,
`blast_database_path`.

## Code

- `src/macd_trader/blast_engine.py` — screen, journal, forward tracking, risk overlay, data policy
- `src/macd_trader/engine.py` — wiring: one-minute bars, ticks, minute-bar warm-up, expiry, health
- `frontend/src/BlastLane.tsx`, `frontend/src/blastMath.ts` — the terminal page
- `tests/test_blast_lane.py`, `frontend/tests/blastMath.test.ts`
