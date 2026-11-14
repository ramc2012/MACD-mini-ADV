# Options & flow desk

Auction desk → **Options & flow** (`#/AUCTION/OPTIONS`) and the MI console → Market data → **Options**. Both draw the
same two endpoints. Added 14 Nov 2026.

## The ten tools and where each lives

| # | Tool | Where | Built from |
|---|---|---|---|
| 1 | Market Profile | Auction desk → Profile / Flow (existing) | session profile |
| 2 | Order-flow footprint | Auction desk → Profile / Flow, Live workstation (existing) | tick store |
| 3 | NTM VolX | Options & flow | `chain_snapshots` |
| 4 | Gamma (GEX by strike, zero gamma, walls, net GEX through the day) + expected-move band | Options & flow | `chain_snapshots` |
| 5 | Spectrum (cumulative CE vs PE volume, top-volume strikes as zones) | Options & flow | `chain_snapshots` |
| 6 | MFLOW (futures volume by what OI did) | Options & flow | `tick_minute_flow` + `oi_minutes` |
| 7 | Smart candles (IB box, delta-strength colour, balance clusters) | Options & flow | `tick_minute_flow` |
| 8 | Options table (LTP, bid/ask, IV, OI, OI change, volume, Greeks) | Options & flow | `chain_snapshots` |
| 9 | Gamma derivatives (writers' P&L on OI added today) | Options & flow | `chain_snapshots` |
| 10 | IV range (close IV per session, percentile, rank, projected moves) | Options & flow | `chain_snapshots` |

Nothing is downloaded. No Redis, no second socket. The engine's existing collectors already store everything:
- index chains about once a minute (±8% of spot, the front series);
- F&O stock chains every 15 minutes (ATM ±8 strikes);
- futures minute flow (volume, buy/sell volume, delta);
- futures OI about once a minute for the NIFTY, BANKNIFTY and SENSEX current-month contracts, since 26 Oct 2026.

## API (desk process; the gateway routes `/api/auction/*` there)

- `GET /api/auction/options/{underlying}?expiry=&day=&at=`: tools 3, 4, 5, 8, 9 and 10. `at` (epoch seconds) picks the
  moment the table, gamma and writers describe. The default is the session's last snapshot by 15:30.
- `GET /api/auction/futures-flow/{underlying}?day=&tf=`: tools 6 and 7 for the current-month futures. With no flow on
  the requested day, it falls back up to 7 days to the last session that has flow.
- The instrument list reuses `GET /api/auction/straddle/instruments`.
- MI console proxy routes are `/api/options-desk/{instruments,options/{u},futures-flow/{u}}`, which call MACD_API.

Code: `engine/src/macd_trader/options_desk.py` (tests in `engine/tests/test_options_desk.py`),
`frontend/src/OptionsDesk.tsx`, `frontend/src/optionsDeskMath.ts`, and the MI `console/app.py` and
`console/static/index.html` (`optionsView`).

## Definitions

- **Premium for IV:** the bid/ask mid when the quote is sane (spread ≤ 20% of mid), else the last price. IV is
  Black-Scholes on spot with r = 6.5% and calendar days (`option_greeks`).
- **NTM IV:** the mean CE and PE IV of the 5 strikes nearest spot (ATM ±2), one point per minute, compared with the
  snapshot's India VIX.
- **Expected move:** a band of open ± open × IV₀ × √(1/365) × √(elapsed/375). The moves from the moment are
  spot × IV × √(d/365) for 1 day, 1 week and to expiry.
- **GEX:** gamma × OI × spot² × 1%, calls counted +, puts − (dealers assumed long calls, short puts).
  - OI is stored in shares, so there is no lot multiplier.
  - Zero gamma: total GEX re-priced over spot ±5%, each contract's IV held fixed; the crossing nearest spot.
  - Walls: the largest call and put GEX strikes.
  - This is a model, not observed dealer books.
- **Spectrum:** each contract adds the volume it traded since its own previous snapshot. A strike first seen
  mid-day adds nothing for that first sighting. Zones are the 5 highest-volume CE and PE strikes of the day.
- **Writers' P&L:**
  - Formula: net OI added since the open × (average premium while OI was being added − premium now).
  - Assumption: the added OI was written (sold to open); OI cannot say which side opened it.
  - Sign: positive means the sellers of the added contracts are ahead.
- **IV range:**
  - Each stored session's NTM IV comes from the last chain by 15:25. On older sessions, which were pruned to their
    last snapshot, it comes from that snapshot.
  - The series is the nearest expiry at least a day away. Expiry days are left out, because only the expiring
    series is stored on those days.
  - Percentile is the share of sessions at or below now. Rank is (now − low) / (high − low).
- **MFLOW:**
  - Volume accumulates until OI changes, then goes to the class given by the sign of ΔOI and Δprice:
    - long build-up: OI ↑, price ↑;
    - short build-up: OI ↑, price ↓;
    - short covering: OI ↓, price ↑;
    - long unwinding: OI ↓, price ↓.
  - New business is volume while OI rises; leaving is volume while OI falls.
  - It is only available where OI is captured (NIFTY, BANKNIFTY and SENSEX futures). `tick_minute_flow.oi` is empty.
- **Smart candles:**
  - Candles are tf-minute buckets of the futures minute flow. Strength = delta / volume.
  - The IB is 09:15–10:15.
  - A balance cluster is 3 or more consecutive bars, each overlapping the cluster by at least 60% of its own range,
    with the cluster at most 2.5 × the median bar range tall.

## Limits

- Buy/sell volume is classified from quotes, not exchange tick-by-tick data. OI updates more slowly than price.
- An illiquid strike's IV can be stale (its last trade). Stock chains are 15-minute sweeps, so their curves are coarse.
- Snapshots after 15:30 are left out of the curves: quotes go stale while time to expiry keeps shrinking.
- None of these readings has been tested as a trading signal. Treat GEX, writers' P&L and MFLOW as context, not
  proven edges.
