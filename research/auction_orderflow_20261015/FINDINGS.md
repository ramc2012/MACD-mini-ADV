# Auction / order-flow lane: is the data right, and do its readings help entries? (15 Oct 2026)

Read-only study. Scripts:
- `extract_flow.py`: minute flow for 225 underlyings. Condensed days are read as stored; the raw days 12–14 Oct are
  condensed here with the desk's own `condense_ticks`.
- `audit.py`: data quality.
- `usefulness.py`: features vs outcomes.

Outputs are in `out/`. The days covered are 28 Sep – 14 Oct with gaps: 6–7 Oct are missing, and 5 and 8 Oct are
partial because of the host crashes.

## 1. Is the lane getting and reading proper data?

**Stocks: yes.**

| Check (stocks, 210 symbols/day) | Result |
|---|---|
| Volume given a buy/sell side | 100% every full day |
| How the side was decided | quote rule 76–78%, tick rule 3–4%, pending-qty 5%, conflicting votes 14%; median confidence 0.6 |
| Two-sided quote on the tick (raw days) | 97–99% of ticks |
| Minute delta/volume vs the minute's price move | median correlation 0.42–0.49 per symbol-day; same sign 67–69% of minutes; positive on **every** symbol-day (>0.2 on 89%) |
| OFI vs the minute's price move | median correlation 0.38–0.54 |
| Tick-derived volume vs the exchange's 1-minute candle volume | 91% of it (10th–90th pct 87–95%); minute-by-minute correlation only 0.53 (timing smear) |

**Indices.** The index spots carry **no volume and no quotes** (0 classified volume, 0 OFI events). The desk
deliberately reads the auction on the index **futures** (`desk.py`), and their flow is fully classified, so index
setups do see real flow: 52 opening-range breakouts and 63 VWAP reclaims fired on indices, and both need delta to agree.

**Gaps found:**
1. **The stored daily profiles carry no order flow and disagree with the desk's own profile.**
   - All 2,599 `session_profiles` rows since 28 Sep are built from candles (`source = candles / candles_tpo`), with
     buy and sell volume 0, delta 0 and imbalance null.
   - Their levels differ materially from the tick-built profile the desk trades on (14 Oct, 6 symbols at the close):
     - value-area overlap is only **33–80%**;
     - the POC is shifted;
     - the **day-type label differed on all 6**. For NIFTY futures it was "trend_day_down" live vs "balanced_day" stored;
       BANKNIFTY futures was the reverse.
   - The next morning's "prior value area / naked POC" context and the base rates are read from these stored rows. So
     the context the desk reads is not the profile it saw live.
2. **About 9% of traded volume never reaches the tick stream** (Fyers update coalescing). Delta is therefore an
   estimate of most of the volume, not all of it.
3. Sessions lost to host crashes (5–8 Oct) leave holes in every series.

## 2. Do the readings help entries?

**a. Flow does not lead price.**

This was tested on every 5th stock-minute over 9 days, comparing the next 30 minutes' return between the top and bottom
quintiles of each feature:

| Feature | Top − bottom quintile, next 30 min | Day t |
|---|---|---|
| 15-min delta share | +1.0 bp | 0.6 (positive on 4 of 9 days) |
| OFI | +0.8 bp | 0.5 |
| Session cumulative delta | −0.5 bp | −0.35 |
| Distance from VWAP | **−5.4 bp** | **−2.7** |

Flow describes the minute it happens in, not the next one. The only predictive reading is that stretch from VWAP
**reverts**.

**b. The desk's signals: right side, a small edge, and stocks lose.**

1,045 of 1,112 logged signals were scored on real option 1-minute bars, using the lane's current exits (−25% stop,
20% trail from +20%, 60-minute time exit) and 0.5% round-trip cost.

- **All signals:** +0.58% per signal, against −0.77% for the opposite option. The underlying moved the signal's way
  by +4.4 bp over 60 minutes, on 6 of 7 days.
- **By setup:**

| Setup | n | Per signal | Opposite option | Underlying, next 60 min |
|---|---|---|---|---|
| VWAP reclaim | 267 | +1.1% | −0.2% | +2 bp |
| Value-area reclaim | 174 | +0.8% | −1.2% | 0 bp |
| Donchian breakout | 405 | +0.8% | −1.5% | +12 bp |
| Opening-range breakout | 128 | −0.2% | — | — |
| Trend-day continuation | 49 | −0.8% | — | — |
| IB extension | 12 | −3.8% | — | — |
| Value-area breakout | 7 | −4.9% | — | — |

- **Index vs stock:** index signals made **+5.6% before and +3.0% after** the 12 Oct tuning; stock signals made
  **−0.6% and −0.5%**. The same split held in both periods.

**c. Flow confirmation inside the setups adds nothing proven.** On stock signals, with flow agreeing vs against:
- 15-min delta: +0.4 pp (t 0.35);
- 15-min OFI: +1.3 pp (t 1.7);
- session cumulative delta: **−1.9 pp** (t −1.3).

**d. The trades actually taken were worse than the signals.**
- 84 round trips averaged −3.4%: −5.2% before the 12 Oct tuning (48 trades) and −1.0% after it (36 trades, 50% win).
- Taken trades where flow agreed did *worse*:
  - session cumulative delta: −7.6 pp (t −2.1);
  - 30-min delta: −23.5 pp (t −1.9).

  That is chasing.
- The cap of 12 trades a day, filled first-come, still selects a worse subset than the signal population.

**e. Other lanes (570 MACD / smart / blast entries on stock options).**
- Short-window flow agreeing helped slightly but not significantly:
  - 15-min delta: +2.1 pp;
  - 30-min delta: +3.8 pp;
  - OFI: +5.2 pp;
  - all with t ≤ 1.
- **Session cumulative delta agreeing hurt:** −6.6 pp across these lanes, and **−15.7 pp on the MACD lane (t −2.4)**.

### The consistent pattern

Buying in the direction of the session's accumulated order flow is late. This held in every sample (desk signals,
desk trades, MACD lane), and it matches the 2 Oct study's finding that market-context features work contrarian.
Short-window OFI agreeing is mildly positive in three of four samples, but never significant.

## Verdicts

| Question | Verdict |
|---|---|
| Is the desk's order-flow data sound (stocks, index futures)? | **Yes.** Classification is complete and tracks price; about 9% of volume is missing upstream. |
| Are the stored daily profiles / next-day context sound? | **No.** They are candle-built, flow-free, and disagree with the live tick profile (value-area overlap 33–80%, day-type mismatches). |
| Does order flow predict the next 30–60 minutes? | **No** (t < 1). Only VWAP stretch has power, and it reverts (t −2.7). |
| Do flow confirmations improve the desk's setups? | **Not proven.** Session-CVD agreement leans harmful. |
| Should flow gate MACD / blast / smart entries? | **No.** At most, test "don't chase session CVD" in shadow (MACD −15.7 pp, t −2.4, 9 days). |
| Is the desk itself useful? | Index signals have been positive in both periods. Stock signals lose. The taken book was worse than its signals; improved after the tuning, still negative. **Not proven.** |

7 sessions is a short sample. Per the user's rule ([[judging-option-strategies]]) these verdicts mean "not proven",
not "rejected".

## Possible next steps (not done)

1. Build the stored daily profiles from the tick ladder (`tick_session_ladder` holds buy/sell volume by price), so the
   next day's context matches the live desk, and use one day-type vocabulary.
2. Desk book: shadow an index-only book (or index plus the three positive stock setups) next to the current one.
   Re-judge after about 15 sessions.
3. Shadow-log session-CVD alignment on MACD-lane entries (contrarian test).
4. The desk's fills still use 25 bps a side (0.5% round trip), while stock-option spreads are about 1.9%. The
   spread-crossing fill added to the other lanes today is not yet in the desk.

## Done 15 Oct (before the open, 974 → 975 tests passing)

1. **Stored profiles.**
   - Built from the tick ladder, condensing the raw tier when the ladder is not written yet. They fall back to
     minute bars when ticks cover less than 80% of the day.
   - Each row now stores `ib_day_type`, the live desk's rule (`market_profile.ib_day_type`).
   - The nightly job rebuilds candle-built days from the last 21 days. 28 Sep–14 Oct was rebuilt: 213 rows/day
     carry buy/sell volume. The crash days 5–8 Oct stay on candles.
   - 14 Oct vs the live desk:

     | | Before | After |
     |---|---|---|
     | Day type matches | 0 of 6 | 6 of 6 |
     | Value-area overlap | 33–80% | 41–100% (four of six at 91%+) |

     The rest of the gap is by design: the stored tier is volume-based, the live value area is time-based.
2. **Index-only shadow desk** (`mp_index`, `mp_index_shadow.sqlite3`): scoped to NIFTY, BANKNIFTY and SENSEX
   futures, with the same rules and caps as the main desk and no UI events. Read it from `mp_index_shadow` in the
   desk status.
3. **Session-CVD at every paper entry, in every lane:** `entry_context` table, `entry_context.EntryFlow`. Index
   options read their future's flow.
4. **Desk fills cross the quoted spread** (`MPSettings.cross_spread`, capped at 5% from the mark), as the other lanes
   do.
