# Supertrend and pivot levels as an independent option-buying system (no MACD)

Study date 4 Oct 2026. Analysis only. Nothing under the project was changed except this file and the report
`reports/Supertrend and pivot standalone system.md`. Every SQLite file was opened read-only (`mode=ro`) from throwaway
containers with no network.

Previous study, `supertrend_pivot_early_entry.md`, asked whether Supertrend / pivot triggers make a better *early entry*
for the MACD cross. They do not. This study asks the stronger question: **can Supertrend and pivot levels be a
trading system on their own, with their own direction, entries and exits, for NSE stock and index options?**

## 0. Bottom line

1. **No. On real option premiums no Supertrend / pivot system is an independent system.** It does not beat the MACD
   lane, random entries or simply holding puts, out of sample. Walk-forward results (₹1 L tickets, net of all costs,
   level fills):

   | dataset (OOS window) | best walk-forward policy | worst | MACD lane | random entries, same rate (mean) | always-PE at the same times |
   |---|---|---|---|---|---|
   | Live, 214 underlyings (19 days, 4 Sep – 1 Oct 2026) | +30.0 L (`WF_ALL_tsel`, t +0.59) | −26.6 L (`WF_B`) | **+64.6 L** (t +1.70) | −7 to −34 L | +66 to +117 L |
   | TradeBot 30-min, 211 underlyings (Jul 2025 – Feb 2026) | −14.5 L (`WF_ALL_tsel`) | −56.4 L (`WF_B`) | −84.7 L | −75 to +5 L | −148 to +3 L |
   | Index 1-min, NIFTY / SENSEX real premiums (Jun 2025 – Feb 2026) | +5.3 L (`WF_ALL`, t +0.73) | −3.4 L (`WF_C`) | +4.5 L (t +0.75) | −3 to +1 L | −5 to +6 L |

   - Paired daily t against the MACD lane: −2.67 to +0.09 on live and index-1m; on TradeBot +0.49 to +1.41, where the
     lane itself loses −84.7 L.
   - **No fixed configuration passes BH-FDR at 5%** on any real-premium dataset: 0 of 1,296 (live), 0 of 424
     (TradeBot), 0 of 1,296 (index-1m).
     - Live: the 13 nominally significant configurations are all *negative*.
     - TradeBot: the 22 nominally significant are all negative.
     - Index-1m: 65 nominal (51 positive, 14 negative) against 65 expected by chance.
   - Deflated-Sharpe probabilities of the in-sample best configurations: 0.26 (live), 0.06 (TradeBot), 0.23 (index-1m).
2. **Pessimistic fills (buy at the bar's high, sell at the bar's low) turn every policy negative.**
   - Live −23 to −172 L; TradeBot −133 to −854 L; index-1m −2 to −15 L.
   - The MACD lane survives on live (+26.8 L).
3. **What the live and TradeBot P&L really is.**
   - Live (September 2026, NIFTY −6%): every policy is CE −56 to −84 L and PE +48 to +108 L.
   - "Random time, with the policy's own CE/PE mix" makes −13 to +32 L, i.e. as much as the policies.
   - The policies' sign comes from their put tilt in a falling month, not from Supertrend timing.
   - TradeBot: they lose −14.5 to −56.4 L. On TradeBot they are *less bad than random* (share of draws below the
     policy 0.60–1.00 for C / D / ALL), so the signals do hold some timing information. It is not enough to pay for
     theta and costs.
4. **The one robust signal is on index spot, not on premiums, and real premiums only half-confirm it.**
   - Over 2021–2026 (Fyers 5-min), an index Supertrend flip on 5-min bars is followed by a small continuation in the
     flip's direction until the next flip or 15:15, every year: **+2.0 to +5.0 bps per trade, t +1.7 to +3.4 per
     year, +3.3 bps (t +6.0) overall** for ST(10,3).
   - The same holds for 15- and 30-min flips held across sessions: +8 to +11 bps per trade, t +6.3 to +6.5.
   - Black-Scholes/VIX-simulated option P&L of this is large: +213 L walk-forward over 2022–2026, t +4.19, with 504 of
     972 configurations passing BH.
   - **But the simulator is biased toward option buying for intraday holds.**
     - In the simulation even a 09:20→15:15 ATM straddle makes +337 L (t +8.5), and random entries make +29 to +39 L.
     - The same straddle *loses* on every real dataset: −11.5 L (index-1m), −260 L (TradeBot), and only +11.9 L
       (t 0.13) in a volatile live month.
     - On identical signals it overstates real NIFTY / SENSEX intraday option returns by +0.7 to +2 pp per trade
       (ST(10,3) 5-min: real +1.37%, simulated +2.16% per trade).
   - Net of the simulated straddle at the same times, the direction component is +141 L (t +3.43) over 2022–2026,
     positive in each year (+11.5 to +50.6 L).
   - On **real** premiums the walk-forward picks of the long-history study make +3.4 L on NIFTY / SENSEX over
     Apr 2025 – Feb 2026 (517 trades, t +0.33; the simulation says +8.2 L on the same index-quarters) and +1.2 L on
     the live indices (117 trades; simulation +6.3 L).
5. **Verdict.**
   - Supertrend and pivots cannot be run as an independent option-buying system on stock options at any timeframe.
     - Live, TradeBot: negative or no better than random.
     - Premium Supertrend on 5-min bars loses −100 to −260 L in 27 live days.
   - Nor can they be run on index options yet. The only candidate is the index-spot Supertrend direction trade. Its
     edge is a few bps of spot per trade; real premiums confirm it only weakly (t 0.3); and it dies under pessimistic
     fills.
   - Keep the MACD lane.
   - If anything is paper-traded, it should be a zero-capital **shadow log** of the index-only spot-Supertrend trade
     (section 11).

## 1. Data

| | Live project | TradeBot | Index 1-min | Fyers 5-min (simulated premiums) |
|---|---|---|---|---|
| spot | 1-min bars of 214 spots (`historical.sqlite3`), 3 Aug – 1 Oct 2026 | 30-min `spot_candles`, 211 underlyings, 28 Mar 2025 – 30 Mar 2026 | 1-min NIFTY / SENSEX spot, 3 Apr 2025 – 2 Apr 2026 (`index_analytics_data/spot`) | 5-min NIFTY50, NIFTYBANK, SENSEX (2021 →), MIDCPNIFTY (Jul 2022 →) to 1 Oct 2026, India VIX 5-min |
| options | every option symbol with bars since 10 Aug: 6,165 contracts, 33.0 M minute bars (all strikes the selectors captured; median 6 strikes per root / expiry / side) | 30-min `option_candles`, monthly expiries, ~2 strikes per side per expiry: 9,494 contracts, 2.6 M bars (volume-0 bars dropped) | 1-min per-contract files, NIFTY weekly + monthly to 28 Aug 2025, SENSEX weekly + monthly to 26 Feb 2026, strikes within ±4% of the spot range: 11,223 contracts, 22.8 M bars (volume-0 bars dropped) | none: Black-Scholes with IV = VIX × exp(calibrated index / DTE terms) from `index_study/out/calib3.json` |
| test period | 25 Aug – 1 Oct 2026 (27 sessions) | May 2025 – Feb 2026 | 21 Apr 2025 – 26 Feb 2026 | Feb 2021 – 1 Oct 2026 (SENSEX trades from 15 May 2023) |
| walk-forward unit / OOS | day; initial window 25 Aug – 3 Sep (8 sessions); **OOS 4 Sep – 1 Oct (19 sessions)** | month; **OOS Jul 2025 – Feb 2026 (8 months, 166 days)** | month; **OOS Jun 2025 – Feb 2026 (9 months, 187 days)** | quarter; **OOS 2022Q1 – 2026Q3 (19 quarters, 1,169 days)** |
| timeframes | 5, 15, 30 min (HTF 30 / 60) | 30 min only (HTF 60) | 5, 15, 30 min | 5, 15, 30 min |
| universe for premium systems (A, MACD lane) | research `universe.pkl`: day's ATM CE / PE per spot | early-cross rule: ref = previous spot close, nearest monthly ≥ 7 days, strikes with ≥ 7 traded bars on the previous session, ≤ 3% from ref | day's first spot print, nearest expiry ≥ 1 day, strike printed the previous session | – (A not testable) |

## 2. Method

### 2.1 Clock, contract, fills, costs
Code: `spc.py`.

**Clock.** tm = day × 375 + minutes since 09:15 at a bar's end. A signal computed on a bar ending at tm is acted on
at the open of the first option bar starting at tm. It never uses its own signal bar's close.
- 1-min data: the next minute, which must start within 5 minutes.
- 30-min data: the next bar.

**Contract (B / C / D).**
- Expiry:
  - intraday hold: the nearest expiry after the entry day;
  - ≤ 4-session hold: the nearest expiry after the last hold day;
  - if that expiry has no eligible strike, the one after it.
- Strike: the one nearest the spot close at the signal, among strikes that printed in the previous 15 minutes (1-min
  data) or in the signal bar (30-min), with |K/S − 1| ≤ 3%.
- Fyers: the strike-step multiple nearest spot, on the index's expiry calendar.
- Premium must be ≥ ₹1.
- No overlapping positions per (underlying, side).

**Fills.** `lanes_lib` paper fill: raw price +5 bps on buys, −5 bps on sells.
- *Level* model:
  - entry at the next bar's open;
  - signal and time exits at the next bar's open;
  - overlay stops / targets at the level crossed inside a bar, or at the open if the bar gaps through it.
  - Path inside a bar: o→l→h→c on an up bar, o→h→l→c on a down bar.
  - If the next print is more than 15 minutes (1-min) or one bar (30-min) away, the exit is at the last close.
- *Pessimistic* model:
  - entry at the next bar's high;
  - every sell at the exit bar's low (targets at their level);
  - overlay checks start on the bar after entry.

**Costs (each order):**
- brokerage ₹20;
- STT 0.15% of sell premium;
- exchange 0.03503% (NSE) / 0.0325% (BSE);
- SEBI ₹10/cr;
- stamp 0.003% on buys;
- GST 18% on brokerage + exchange + SEBI.

**P&L units.**
- Each trade is a ₹1 L ticket: nearest whole lots, 1–125, capped at 2% of the contract's previous-session volume.
  The trade is skipped if the cap is under one lot; no cap applies where volume is unknown.
- "L" = ₹ lakh, net of costs.
- Cost per trade is ≈ ₹250–280 (0.25–0.28% of the ticket).

**Option overlay** (smart lane, optional per configuration): 40% hard stop; trail 30% off the peak once +40%,
floored at breakeven; +100% target. No staged exits, no pyramiding.

**Hold.**
- 'd': exit by 15:15.
- '4': up to 15:15 of the 4th session, counting the entry day.
- System A / MACD lane: capped at the expiry day's 15:10.

### 2.2 Systems
Code: `systems.py`. Signals use completed bars only. Supertrend is Wilder ATR(n) seeded with the mean of the first
n true ranges, bands hl2 ± m·ATR, with the standard final-band carry, continuous across sessions.

- **A — Supertrend on the premium.**
  - Contract: the day's universe ATM CE and PE.
  - Entry: on a bearish→bullish flip of the premium's ST at a TF close between 09:20 and 14:45; buy that contract.
  - Exit: at the premium's next bullish→bearish flip.
  - Grid: n {7, 10, 14} × m {1.5, 2, 3} × TF {5, 15, 30} × hold {d, 4} × overlay {off, on} = 108
    (TradeBot: TF 30 only, 36).
- **B — Supertrend on the spot gives the direction.**
  - Entry: a bullish spot ST flip buys the ATM CE, a bearish flip buys the ATM PE (09:20 – 14:45).
  - Exit: `s` = the opposite spot flip; `p` = the held premium's own ST (same n, m, TF) flipping bearish.
  - HTF variant `h1`: the flip must agree with the last completed higher-timeframe ST (same n, m; 5→30, 15→60,
    30→60 min).
  - Grid: 27 × HTF {0, 1} × exit {s, p} × hold {d, 4} × overlay = 432 (TradeBot 144; Fyers 216, no `p`).
- **C — Pivots on the underlying.**
  - Levels: from the previous session's spot H / L / C.
    - floor P / R1 / R2 / S1 / S2;
    - CPR BC / TC;
    - Camarilla H3 / H4 / L3 / L4;
    - PDH / PDL;
    - opening range 15 / 30 min.
  - One entry per day per side, from 09:30 (or after the OR window) to 14:45. PE is the exact mirror.
  - Entry rules:
    - `openTC`: first TF close at or after 09:30 above TC;
    - `brkTC`: close crosses TC;
    - `brkR1`: close crosses R1;
    - `brkH4`: close crosses H4;
    - `fadeL3`: bar low ≤ L3 < close with low > L4, i.e. Camarilla L3 reversal into a CE / H3 reversal into a PE;
    - `brkPDH`: close crosses PDH;
    - `orb15` / `orb30`: first close above the OR high.
  - Exits on spot touches (1-min / 30-min / 5-min base bars):
    - `openTC` / `brkTC`: target T1 = next of R1 / R2 above entry, or T2 = R2; stop P or BC.
    - `brkR1`: target R2; stop P or TC.
    - `brkH4`: stop H3.
    - `fadeL3`: target previous close or H3; stop L4.
    - `brkPDH`: a TF close back below PDH.
    - ORB: stop at OR mid.
  - Filter: all days, or narrow-CPR days only (width below the 30th percentile of the same underlying's previous
    ≥ 10 days).
  - Hold: intraday with targets, or ≤ 4 sessions with the stop kept, no target, and trailing on spot ST(10, 3) of the
    same TF.
  - × overlay. 108 per TF, 324 in all (TradeBot 108, of which 16 orb15 are empty).
- **D — Supertrend + pivot location.**
  - D1: the B flip, taken only if spot > P (or > TC) for a CE, < P (or < BC) for a PE.
  - D2: entry when the close crosses P (or TC) while the ST direction agrees; exit at the opposite ST flip.
  - Grid: 27 × {P, TC} × hold × overlay = 216 each.
- **Totals:**
  - live 1,296 configurations;
  - TradeBot 432;
  - index-1m 1,296;
  - Fyers 972;
  - **3,996 in all**, simulated as 13.9 M trades (live 6.08 M, TradeBot 4.11 M, index-1m 0.33 M, Fyers 3.37 M).

### 2.3 Benchmarks
Code: `controls.py`.

- **MACD lane** (live, index-1m): the day's universe ATM CE / PE.
  - Entry: 30-min MACD(12,26) first intrabar zero cross (count ≥ 26, not on expiry day), re-checked 5 minutes later
    (MACD still > 0); buy at the next minute's open.
  - Exits: the smart overlay; MACD invalidation (a closed 30-min bucket with MACD < 0 while the max return is
    < 10%); 4-session hold; expiry 15:10.
  - Not included: staged exits, pyramiding, ranker and blast source. So it is not the full smart lane.
  - TradeBot: a closed-bar cross, entry at the next bar's open (30-min data cannot do cross+5min).
- **Random entries, 30 draws each.** Same number of trades per walk-forward unit and the same holding times, hold
  rule and overlay. The underlying-day is drawn at random within the same unit, from the day's universe (all indices
  on Fyers), at a random TF time in the entry window.
  - *R-side*: random CE / PE.
  - *R-time*: the policy's own side for each trade.
  - System A: a random universe contract.
- **Look-ahead diagnostic:** random time on the *same underlying-day* the signal fired. It is biased because a flip
  implies the day moved that way, and is shown only to measure lateness.
- **Always-CE / always-PE:** each policy trade's exact entry and exit times and hold, with the side forced.
- **Both:** the average of always-CE and always-PE, i.e. a long straddle at the same times. This is the
  time-matched volatility control.
- **09:20→15:15 ATM straddle** on every OOS underlying-day, ₹1 L per leg.
- **Books:** time-ordered cash replay at ₹50 L and ₹1 L.
  - Ticket = 10% of (cash + open positions at cost); whole lots (floor); liquidity cap.
  - A trade is skipped below one lot or when the contract is already held.
  - Daily marks at the last close.

### 2.4 Walk-forward and multiple testing
Code: `analyze.py`.

**Walk-forward selection.** For each OOS unit:
- training data = trades that entered in earlier units **and had exited before the unit started**;
- the configuration with the highest training net P&L is chosen, among those with ≥ 30 (live), 50 (TradeBot),
  20 (index-1m) or 30 (Fyers) training trades;
- if the best training P&L is ≤ 0, "none" (no trades);
- this is done per family (A, B, C, D) and across all (ALL);
- `WF_ALL_tsel` selects on the training daily t-stat instead of P&L;
- OOS P&L = trades entered in the unit.

**Multiple testing.**
- Per dataset, every fixed configuration's OOS daily P&L is tested against 0 (two-sided t), then BH at 5%.
- Deflated Sharpe (Bailey and López de Prado): the in-sample-best daily Sharpe against the expected maximum of N
  null trials, given the cross-configuration Sharpe variance.

## 3. Out-of-sample results by dataset

### 3.1 Live (19 OOS days, 4 Sep – 1 Oct 2026; 214 underlyings)

| policy | trades | net P&L L | win | avg win / loss ₹ | PF | max DD L | daily t | Sharpe (ann.) | green days | CE / PE L | index / stock L | pessimistic fills L |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| WF_A | 2,144 | +28.0 | 0.40 | 36,373 / −21,865 | 1.10 | 28.5 | +0.77 | +2.80 | 10 of 19 | −64.5 / +92.5 | +3.8 / +24.2 | −22.8 |
| WF_B | 8,020 | −26.6 | 0.36 | 14,756 / −8,822 | 0.94 | 61.3 | −0.58 | −2.10 | 8 of 19 | −84.0 / +57.4 | −2.1 / −24.5 | −171.9 |
| WF_C | 2,683 | −12.3 | 0.34 | 29,748 / −15,986 | 0.96 | 60.5 | −0.22 | −0.81 | 10 of 19 | −77.9 / +65.7 | −1.7 / −10.6 | −73.3 |
| WF_D | 1,903 | −7.5 | 0.38 | 29,823 / −18,828 | 0.97 | 35.0 | −0.24 | −0.87 | 9 of 19 | −55.8 / +48.2 | +0.5 / −8.1 | −45.5 |
| WF_ALL | 8,004 | −10.0 | 0.36 | 15,186 / −8,825 | 0.98 | 60.5 | −0.21 | −0.77 | 8 of 19 | −77.4 / +67.3 | −1.0 / −9.1 | −155.7 |
| WF_ALL_tsel | 6,432 | +30.0 | 0.37 | 17,414 / −9,468 | 1.08 | 39.6 | +0.59 | +2.14 | 9 of 19 | −78.0 / +108.0 | +0.9 / +29.1 | −84.5 |
| MACD lane (cross+5min, smart exits) | 1,471 | **+64.6** | 0.38 | 47,455 / −21,924 | 1.32 | 14.1 | +1.70 | +6.17 | 13 of 19 | −52.9 / +117.5 | +7.6 / +57.0 | +26.8 |

By month: September vs 1 Oct, L.

| policy | Sep | 1 Oct |
|---|---|---|
| WF_A | +20.8 | +7.1 |
| WF_B | −28.2 | +1.6 |
| WF_C | −19.0 | +6.7 |
| WF_D | −12.0 | +4.4 |
| WF_ALL | −17.2 | +7.1 |
| WF_ALL_tsel | +21.2 | +8.8 |
| MACD lane | +59.6 | +5.0 |

Benchmarks:

| policy | vs MACD: paired daily t | R-side mean (sd), share below policy, paired t | R-time (policy's sides) mean (sd), share below, paired t | same-day random (look-ahead) | always-CE L | always-PE L | both (matched straddle) L | policy − both: t | corr. with 09:20 straddle | ₹50 L book: return (max DD), taken / skipped | ₹1 L book: return (max DD), taken / skipped |
|---|---|---|---|---|---|---|---|---|---|---|---|
| WF_A | −1.35 | −7.1 (14.8), 0.97, +1.06 | −7.1 (14.7), 0.97, +1.09 | +184.1 | −114.1 | +81.7 | −16.2 | +1.37 | +0.76 | +17% (63%), 539 / 1,605 | −62% (71%), 215 / 1,929 |
| WF_B | −1.82 | −33.6 (14.9), 0.70, +0.18 | −13.0 (11.9), 0.13, −0.56 | +79.1 | −156.5 | +76.4 | −40.1 | +0.38 | +0.51 | −46% (74%), 1,406 / 6,614 | −53% (60%), 465 / 7,555 |
| WF_C | −1.44 | −9.3 (13.4), 0.43, −0.06 | +15.8 (14.5), 0.00, −0.80 | +103.5 | −137.9 | +97.5 | −20.2 | +0.18 | +0.40 | +19% (27%), 552 / 2,131 | +85% (35%), 234 / 2,449 |
| WF_D | −2.67 | −13.7 (8.5), 0.73, +0.20 | −1.5 (10.7), 0.30, −0.28 | +65.9 | −102.7 | +65.9 | −18.4 | +0.39 | +0.40 | −33% (57%), 481 / 1,422 | +89% (38%), 203 / 1,700 |
| WF_ALL | −1.55 | −21.7 (16.2), 0.70, +0.29 | −6.2 (16.1), 0.47, −0.16 | +95.4 | −153.5 | +74.7 | −39.4 | +0.84 | +0.61 | −55% (74%), 1,416 / 6,588 | −53% (60%), 465 / 7,539 |
| WF_ALL_tsel | −0.78 | −11.9 (14.9), 1.00, +0.94 | +31.5 (13.9), 0.43, −0.05 | +129.7 | −165.4 | +117.1 | −24.1 | +1.29 | +0.67 | +119% (33%), 1,211 / 5,221 | +54% (41%), 433 / 5,999 |
| MACD lane | – | – | +3.0 (12.1), 1.00, +1.70 | +127.9 | −80.6 | +83.2 | +1.3 | +1.79 | +0.62 | +69% (38%), 470 / 1,001 | −10% (59%), 215 / 1,256 |

09:20→15:15 straddle on 3,792 OOS underlying-days: +11.9 L (win 0.42, daily t +0.13).

Walk-forward choices (count of days):
- **WF_A:** `A_30_7_2_4_o1` ×6, `A_30_7_3_4_o0` ×4, `A_30_14_1.5_4_o1` ×3, `A_30_7_3_4_o1` ×3, `A_30_10_2_d_o0` ×2,
  `A_15_10_3_d_o0` ×1.
- **WF_B:** `B_15_14_3_h0_s_4_o1` ×7, `B_5_10_1.5_h0_s_4_o0` ×5, `B_5_14_1.5_h0_s_4_o0` ×3, `B_15_10_3_h0_s_d_o0` ×3,
  `B_5_14_1.5_h0_s_4_o1` ×1.
- **WF_C:** `C_30_orb30_-_ORmid_a_4_o1` ×10, `C_15_brkH4_-_H3_a_d_o0` ×3, `C_5_fadeL3_-_L4_a_4_o0` ×2,
  `C_30_orb30_-_ORmid_a_4_o0` ×2, `C_5_orb15_-_ORmid_a_d_o0` ×1, `C_5_orb30_-_ORmid_a_4_o1` ×1.
- **WF_D:** `D1_15_14_3_P_4_o1` ×5, `D2_15_14_2_P_4_o1` ×4, `D1_30_14_3_P_4_o1` ×4, `D2_15_7_2_P_d_o0` ×3, and three
  others ×1.
- **WF_ALL:** `B_15_14_3_h0_s_4_o1` ×6, `B_5_10_1.5_h0_s_4_o0` ×5, `B_5_14_1.5_h0_s_4_o0` ×3, `A_30_7_3_4_o0` ×2,
  and three others ×1.

Reading:
- Every policy loses on calls (−56 to −84 L) and wins on puts (+48 to +108 L) in a month NIFTY fell 6%.
- R-time, which keeps each policy's CE/PE mix but times trades at random, makes −13 to +32 L. That is the policies'
  range. Their timing adds nothing measurable: paired t vs R-time −0.80 to +1.09.
- Index trades are a small part (−2.1 to +3.8 L).
- The MACD lane is better by 35–91 L over 19 days (paired t −0.78 to −2.67).
- The look-ahead diagnostic (random time on the very underlying-days a signal fired) makes +66 to +184 L, far above
  the policies. As found before, the signal prints after much of the day's move.

### 3.2 TradeBot 30-min (8 OOS months, Jul 2025 – Feb 2026; 211 underlyings)

| policy | trades | net P&L L | win | avg win / loss ₹ | PF | max DD L | daily t | monthly t | green months | CE / PE L | index / stock L | pessimistic L |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| WF_A | 1,080 | −27.9 | 0.35 | 31,554 / −20,748 | 0.81 | 29.4 | −1.94 | −1.53 | 0 of 8 | −22.1 / −5.9 | +0.7 / −28.6 | −133.0 |
| WF_B | 3,755 | −56.4 | 0.36 | 31,621 / −19,797 | 0.88 | 68.9 | −1.37 | −2.05 | 2 of 8 | +8.3 / −64.7 | −1.2 / −55.3 | −387.5 |
| WF_C | 4,844 | −17.1 | 0.32 | 27,847 / −13,598 | 0.96 | 49.1 | −0.55 | −0.35 | 3 of 8 | +5.1 / −22.2 | +4.5 / −21.6 | −423.2 |
| WF_D | 9,559 | −35.7 | 0.37 | 24,465 / −15,081 | 0.96 | 64.9 | −0.69 | −0.72 | 2 of 8 | +27.6 / −63.3 | −3.5 / −32.3 | −854.1 |
| WF_ALL | 6,604 | −30.5 | 0.35 | 28,041 / −15,522 | 0.95 | 51.7 | −0.70 | −0.66 | 2 of 8 | +31.2 / −61.8 | +1.8 / −32.4 | −610.0 |
| WF_ALL_tsel | 5,159 | −14.5 | 0.36 | 21,871 / −12,584 | 0.97 | 44.1 | −0.38 | −0.31 | 2 of 8 | +32.2 / −46.7 | +3.3 / −17.8 | −465.1 |
| MACD lane (bar-close cross, smart exits) | 5,172 | −84.7 | 0.35 | 35,838 / −22,125 | 0.89 | 120.5 | −1.66 | −1.43 | 3 of 8 | −17.1 / −67.6 | +3.8 / −88.5 | −511.5 |

| policy | vs MACD t | R-side mean (sd), share below, paired t | R-time mean (sd), share below, paired t | same-day random | always-CE | always-PE | both | policy − both t | corr. straddle | ₹50 L book | ₹1 L book |
|---|---|---|---|---|---|---|---|---|---|---|---|
| WF_A | +1.13 | +5.2 (9.7), 0.00, −1.92 | +3.2 (9.6), 0.00, −1.85 | +36.2 | −43.2 | +2.9 | −20.2 | −0.65 | −0.00 | −88% (88%), 449 / 631 | −27% (33%), 68 / 1,012 |
| WF_B | +0.49 | −33.6 (20.5), 0.07, −0.63 | −29.3 (20.1), 0.07, −0.73 | +152.3 | −5.1 | −95.8 | −50.5 | −0.21 | −0.12 | −40% (71%), 2,025 / 1,730 | −66% (77%), 279 / 3,476 |
| WF_C | +1.41 | −31.9 (15.4), 0.80, +0.49 | −26.6 (14.5), 0.77, +0.32 | +140.1 | −28.6 | −52.5 | −40.6 | +0.97 | +0.27 | −31% (68%), 3,427 / 1,417 | +70% (70%), 1,632 / 3,212 |
| WF_D | +0.83 | −74.5 (22.4), 1.00, +0.79 | −62.5 (21.5), 0.90, +0.55 | +270.7 | −30.3 | −147.5 | −88.9 | +1.27 | +0.13 | −28% (73%), 3,295 / 6,264 | +27% (63%), 1,660 / 7,899 |
| WF_ALL | +0.98 | −62.0 (22.0), 0.90, +0.77 | −65.6 (16.9), 1.00, +0.86 | +203.1 | −15.9 | −124.2 | −70.0 | +1.18 | +0.09 | −25% (67%), 3,118 / 3,486 | +5% (82%), 1,055 / 5,549 |
| WF_ALL_tsel | +1.32 | −20.3 (18.8), 0.70, +0.17 | −19.0 (16.3), 0.60, +0.13 | +125.6 | +22.4 | −89.6 | −33.6 | +0.69 | +0.12 | −25% (68%), 3,454 / 1,705 | −43% (78%), 529 / 4,630 |
| MACD lane | – | – | −14.5 (23.6), 0.00, −1.42 | +285.8 | −47.8 | −82.4 | −65.1 | −0.47 | +0.03 | −97% (98%), 1,451 / 3,721 | −61% (73%), 225 / 4,947 |

09:20→15:15 straddle (opened at the 09:45 bar for 30-min data) on 19,808 underlying-days: **−260.0 L** (t −4.52).

By month, L:

| policy | Jul | Aug | Sep | Oct | Nov | Dec | Jan | Feb |
|---|---|---|---|---|---|---|---|---|
| WF_B | −13.9 | −27.2 | +2.7 | −7.3 | −3.6 | −7.4 | 0.0 | +0.3 |
| WF_C | +4.9 | −6.6 | +27.8 | −30.4 | −5.3 | −3.9 | +9.5 | −13.1 |
| WF_D | −11.7 | −12.3 | +32.2 | −19.0 | −23.9 | −3.2 | +4.9 | −2.7 |
| WF_ALL | −11.7 | −6.6 | +27.8 | −19.0 | −23.9 | −3.9 | +9.5 | −2.7 |
| MACD lane | −18.2 | −27.0 | +9.6 | −35.2 | −25.7 | +3.8 | +25.0 | −17.1 |

Walk-forward choices:
- **WF_A:** "none" in 6 of 8 months; training P&L was negative.
- **WF_B:** 30-min ST(7–10, 1.5–3), mostly 4-session; HTF variants appear in 3 months.
- **WF_C:** `C_30_brkR1_-_TC_n_4_o1` ×4, `C_30_brkPDH_-_PDHclose_n_d_o0` ×2.
- **WF_D:** `D1_30_7_1.5_TC_4_o1` ×3 and other D1-TC 4-session variants.
- **WF_ALL:** a mix of C and D.

Reading:
- Everything loses on TradeBot, including the MACD lane (a closed-bar cross is later than the live intrabar cross).
- C, D and ALL are less bad than random (R-side share below 0.80–1.00), but paired t vs the random mean is only
  +0.49 to +0.79.
- System A is worse than every random draw.
- Time-matched straddles (`both`) lose −20 to −89 L. Option buying itself lost money in 2025–26 at 30-min
  granularity.

### 3.3 Index 1-min, real NIFTY / SENSEX premiums (9 OOS months, Jun 2025 – Feb 2026)

| policy | trades | net P&L L | win | avg win / loss ₹ | PF | max DD L | daily t | monthly t | green months | CE / PE L | pessimistic L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| WF_A | 363 | +3.6 | 0.35 | 38,350 / −19,133 | 1.08 | 9.7 | +0.42 | +0.35 | 6 of 9 | +4.1 / −0.6 | −15.4 |
| WF_B | 186 | +3.9 | 0.39 | 43,934 / −24,921 | 1.14 | 6.5 | +0.56 | +0.76 | 6 of 9 | −1.2 / +5.1 | −6.0 |
| WF_C | 129 | −3.4 | 0.40 | 28,026 / −22,694 | 0.81 | 7.7 | −0.74 | −2.08 | 4 of 9 | −2.4 / −1.0 | −10.0 |
| WF_D | 108 | +3.9 | 0.44 | 42,499 / −26,354 | 1.24 | 3.7 | +0.71 | +0.74 | 6 of 9 | +1.3 / +2.6 | −2.4 |
| WF_ALL | 199 | +5.3 | 0.41 | 43,834 / −26,212 | 1.17 | 6.8 | +0.73 | +0.88 | 6 of 9 | +1.6 / +3.7 | −6.8 |
| WF_ALL_tsel | 136 | +4.2 | 0.45 | 35,083 / −22,966 | 1.24 | 3.3 | +0.84 | +0.85 | 5 of 9 | −1.4 / +5.6 | −2.9 |
| MACD lane (cross+5min, smart exits) | 115 | +4.5 | 0.38 | 55,656 / −28,086 | 1.23 | 3.5 | +0.75 | +1.21 | 8 of 9 | −2.3 / +6.8 | −1.1 |

| policy | vs MACD t | R-side mean (sd), share below, paired t | R-time mean (sd), share below, paired t | same-day random | always-CE | always-PE | both | policy − both t | corr. straddle | ₹50 L book | ₹1 L book |
|---|---|---|---|---|---|---|---|---|---|---|---|
| WF_A | −0.11 | −3.2 (5.3), 0.93, +0.86 | −2.6 (7.3), 0.80, +0.77 | +23.1 | +4.8 | −5.0 | −0.1 | +0.49 | +0.37 | +67% (48%), 317 / 46 | +46% (33%), 182 / 181 |
| WF_B | −0.07 | −2.8 (5.1), 0.90, +0.97 | +1.4 (4.8), 0.73, +0.38 | +18.9 | −6.7 | +4.1 | −1.3 | +0.83 | +0.40 | +51% (36%), 168 / 18 | +49% (32%), 126 / 60 |
| WF_C | −1.14 | +0.8 (4.4), 0.17, −0.88 | +1.5 (3.3), 0.07, −1.02 | +2.1 | −2.4 | −1.7 | −2.1 | −0.32 | +0.15 | −35% (58%), 120 / 9 | −4% (27%), 68 / 61 |
| WF_D | −0.09 | −2.1 (3.6), 0.97, +1.10 | −1.9 (3.2), 0.97, +1.06 | +10.0 | −1.0 | −0.2 | −0.6 | +0.89 | +0.36 | +74% (20%), 96 / 12 | +34% (17%), 74 / 34 |
| WF_ALL | +0.09 | −1.2 (4.7), 0.93, +0.95 | −2.7 (4.3), 1.00, +1.19 | +16.3 | −0.1 | +2.7 | +1.3 | +0.64 | +0.43 | +95% (36%), 181 / 18 | +10% (31%), 134 / 65 |
| WF_ALL_tsel | −0.05 | −1.2 (3.4), 0.90, +1.11 | −0.4 (4.0), 0.90, +0.98 | +9.7 | −6.4 | +6.3 | −0.0 | +1.07 | +0.37 | +52% (24%), 128 / 8 | +51% (14%), 98 / 38 |
| MACD lane | – | – | −3.6 (3.6), 1.00, +1.36 | +14.3 | −6.0 | +5.2 | −0.4 | +0.93 | +0.13 | +44% (30%), 110 / 5 | +24% (22%), 70 / 45 |

09:20→15:15 straddle on 246 OOS index-days: **−11.5 L** (t −2.10).

By month, L:

| policy | Jun | Jul | Aug | Sep | Oct | Nov | Dec | Jan | Feb |
|---|---|---|---|---|---|---|---|---|---|
| WF_B | +2.1 | +0.6 | +0.4 | +1.1 | −2.4 | −0.5 | −1.6 | +1.0 | +3.1 |
| WF_D | +2.2 | +1.4 | −1.8 | +1.0 | −1.9 | +0.4 | +0.6 | −1.2 | +3.1 |
| WF_ALL | +2.2 | +0.6 | +0.4 | +1.1 | −2.4 | −0.5 | −1.6 | +1.0 | +4.4 |
| MACD lane | +0.2 | +1.7 | +0.1 | +1.7 | +0.4 | +1.4 | −2.4 | +0.1 | +1.3 |

Walk-forward choices:
- **WF_A:** `A_15_10_2_4_o1` ×7.
- **WF_B:** `B_30_7_1.5_h0_s_4_o0` ×4, `B_15_14_2_h0_s_d_o0` ×3, and two others.
- **WF_D:** `D1_15_14_3_TC_4_o0` ×3, `D1_15_14_2_TC_d_o0` ×2, `D1_15_14_2_P_d_o0` ×2, and others.
- **WF_ALL:** `B_15_14_2_h0_s_d_o0` ×3, `B_30_7_1.5_h0_s_4_o0` ×2, `A_15_10_2_4_o1` ×2.

Reading:
- On real index options the Supertrend policies are about level with the MACD lane (+3.6 to +5.3 vs +4.5 L), and
  above random by about 1 sd (paired t +0.4 to +1.2).
- The pivot-only policy (C) loses.
- With only 108–363 trades over 9 months none of this is distinguishable from zero.

### 3.4 Fyers 5-min, simulated premiums (19 OOS quarters, 2022Q1 – 2026Q3; four indices)

| policy | trades | net P&L L | win | avg win / loss ₹ | PF | max DD L | daily t | monthly t | green months | CE / PE L | pessimistic L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| WF_B | 8,346 | +213.2 | 0.39 | 32,963 / −16,977 | 1.25 | 16.4 | +4.19 | +4.67 | 43 of 58 | +77.9 / +135.2 | −564.2 |
| WF_C | 4,201 | +74.3 | 0.35 | 38,911 / −18,643 | 1.15 | 18.0 | +1.87 | +2.34 | 32 of 58 | +26.2 / +48.1 | −268.4 |
| WF_D | 2,496 | +96.0 | 0.34 | 54,481 / −22,722 | 1.26 | 15.6 | +2.46 | +2.80 | 34 of 58 | +27.8 / +68.2 | −120.7 |
| WF_ALL | 8,228 | +216.8 | 0.39 | 33,016 / −16,910 | 1.26 | 11.5 | +4.29 | +4.82 | 43 of 58 | +78.5 / +138.3 | −551.6 |
| WF_ALL_tsel | 5,160 | +79.0 | 0.49 | 22,041 / −17,864 | 1.17 | 9.8 | +2.97 | +3.98 | 42 of 58 | +18.0 / +61.0 | −369.7 |

| policy | R-side mean (sd), share below, paired t | R-time mean (sd), share below, paired t | always-CE | always-PE | both (matched straddle) | policy − both t | corr. straddle |
|---|---|---|---|---|---|---|---|
| WF_B | +28.9 (25.9), 1.00, +4.01 | +38.8 (23.5), 1.00, +3.90 | +27.3 | +117.7 | +72.5 | +3.43 | +0.61 |
| WF_C | +5.0 (19.7), 1.00, +1.82 | +28.5 (19.6), 0.97, +1.23 | +0.7 | +25.8 | +13.2 | +1.84 | +0.55 |
| WF_D | −12.2 (21.4), 1.00, +2.93 | +5.0 (19.4), 1.00, +2.46 | −18.2 | +35.0 | +8.4 | +2.83 | +0.26 |
| WF_ALL | +35.9 (22.8), 1.00, +3.99 | +39.1 (30.4), 1.00, +3.98 | +29.2 | +119.3 | +74.3 | +3.51 | +0.62 |
| WF_ALL_tsel | +10.6 (15.2), 1.00, +2.71 | +10.4 (17.0), 1.00, +2.71 | −27.8 | +69.8 | +21.0 | +2.45 | +0.21 |

**09:20→15:15 ATM straddle, simulated, 4,206 index-days: +336.7 L (t +8.47).** This is the tell-tale of the
simulator's bias (§6).
- The cash books compound this bias into meaningless numbers: ₹50 L book +4,759% (WF_B).
- They are not reported further.

Walk-forward choices:
- **WF_B:** `B_5_10_3_h0_s_d_o0` ×9, which is every quarter from 2024Q3 on; `B_5_14_2_h0_s_d_o0` ×6 (2023Q1 – 2024Q2);
  `B_15_14_2_h0_s_4_o0` ×4 (2022).
- **WF_D:** `D1_30_7_1.5_P_4_o0` ×7, `D2_5_7_3_TC_d_o0` ×4, and others.
- **WF_C:** `C_30_orb15_-_ORmid_a_d_o0` ×9, plus ORB / H4 variants.

By year, WF_B. "Excess" = policy − the time-matched simulated straddle, i.e. the direction component.

| year | raw L | excess vs matched straddle L (t) | always-PE L | always-CE L |
|---|---|---|---|---|
| 2022 | +15.8 | +17.8 (+1.17) | −4.0 | −0.2 |
| 2023 | +52.8 | +30.5 (+1.38) | +19.7 | +24.9 |
| 2024 | +88.4 | +50.6 (+2.14) | +61.6 | +14.1 |
| 2025 | +16.0 | +11.5 (+0.80) | +19.5 | −10.4 |
| 2026 (to 1 Oct) | +40.2 | +30.3 (+2.11) | +20.9 | −1.1 |
| **all** | +213.2 | **+140.7 (t +3.43)** | +117.7 | +27.3 |

Excess vs matched straddle for the other policies: WF_D +87.6 L (t +2.83), WF_C +61.1 L (t +1.84).

Regimes (WF_B):
- NIFTY up months (30): +2.82 L / month raw, +1.91 L excess.
- Down months (28): +4.59 L raw, +2.98 L excess.
- corr(|NIFTY monthly move|, excess) = −0.09.
- Green months: raw 74%, excess 69%.
- VIX terciles (previous close), excess: low +37.6 L, mid +56.6 L, high +46.4 L.

The direction component is not a one-regime effect.

## 4. Full grids (in-sample transparency; NOT the headline)

Each cell is full test-period P&L L / OOS P&L L. Every configuration is in `res/grid_<ds>.csv` (n, P&L, win, PF,
t, max DD, CE / PE, index / stock, OOS n / P&L / t / win / CE / PE).

### 4.1 B: spot Supertrend direction (no HTF, exit on the opposite spot flip unless stated)

**Live:**

| TF | ATR | mult | intraday | intraday + overlay | ≤4 sessions | ≤4 sessions + overlay | HTF-confirmed intraday | premium-flip exit intraday |
|---|---|---|---|---|---|---|---|---|
| 5 | 7 | 1.5 | +88.2 / +59.8 | +80.2 / +49.7 | +112.8 / +69.3 | +100.9 / +62.5 | +78.3 / +66.5 | −56.2 / −50.7 |
| 5 | 7 | 2 | +16.3 / +4.3 | +4.1 / −8.8 | +70.1 / +30.0 | +58.2 / +24.7 | +21.7 / +23.1 | −59.3 / −46.9 |
| 5 | 7 | 3 | −7.8 / −10.8 | −32.3 / −34.7 | +62.4 / +28.3 | +28.7 / +0.3 | +11.4 / +17.1 | −51.8 / −42.9 |
| 5 | 10 | 1.5 | +71.1 / +43.1 | +72.0 / +42.0 | +104.3 / +58.2 | +98.4 / +57.6 | +71.7 / +61.3 | −53.7 / −48.0 |
| 5 | 10 | 2 | +12.0 / +0.1 | −0.3 / −12.4 | +77.6 / +36.2 | +64.9 / +30.8 | +9.3 / +14.1 | −67.2 / −52.2 |
| 5 | 10 | 3 | −7.7 / −10.5 | −34.1 / −37.0 | +71.0 / +34.3 | +35.1 / +3.8 | +15.0 / +21.4 | −41.7 / −35.6 |
| 5 | 14 | 1.5 | +77.1 / +50.3 | +75.4 / +46.6 | +113.7 / +68.8 | +109.6 / +69.9 | +70.4 / +60.9 | −54.8 / −52.1 |
| 5 | 14 | 2 | +30.7 / +19.1 | +14.4 / +2.1 | +85.1 / +43.2 | +70.8 / +36.4 | +11.7 / +15.0 | −64.3 / −50.8 |
| 5 | 14 | 3 | −15.1 / −12.8 | −42.8 / −40.8 | +85.6 / +52.5 | +46.7 / +20.0 | +9.0 / +18.5 | −57.5 / −47.5 |
| 15 | 7 | 1.5 | −20.0 / −15.6 | −50.1 / −45.0 | +58.7 / +24.6 | +39.6 / +9.9 | −19.4 / −6.7 | −50.0 / −40.2 |
| 15 | 7 | 2 | +42.5 / +43.9 | +7.6 / +9.4 | +52.1 / +23.2 | +68.8 / +40.0 | +19.7 / +27.6 | +11.2 / +15.9 |
| 15 | 7 | 3 | +68.0 / +66.8 | +29.4 / +28.8 | +29.6 / +4.6 | +38.6 / +16.4 | +22.6 / +26.7 | +59.5 / +60.9 |
| 15 | 10 | 1.5 | −9.2 / −6.8 | −40.2 / −37.2 | +69.0 / +36.5 | +46.5 / +18.4 | −11.2 / −1.0 | −41.8 / −34.6 |
| 15 | 10 | 2 | +33.5 / +33.8 | −1.3 / −0.7 | +53.0 / +23.0 | +52.8 / +25.0 | +21.0 / +28.4 | −8.7 / −1.8 |
| 15 | 10 | 3 | +74.1 / +72.0 | +37.8 / +36.4 | +27.3 / +2.0 | +45.0 / +20.9 | +24.6 / +28.4 | +61.0 / +62.1 |
| 15 | 14 | 1.5 | −15.7 / −9.4 | −44.4 / −37.2 | +71.2 / +41.4 | +45.1 / +19.8 | −22.0 / −10.7 | −47.4 / −39.2 |
| 15 | 14 | 2 | +42.6 / +40.3 | +11.3 / +9.4 | +59.5 / +30.1 | +57.3 / +32.8 | +32.6 / +39.3 | −7.2 / −2.9 |
| 15 | 14 | 3 | +71.5 / +68.1 | +41.2 / +38.7 | +38.5 / +8.3 | +59.4 / +29.5 | +23.9 / +26.3 | +60.0 / +59.9 |
| 30 | 7 | 1.5 | +57.1 / +54.7 | +29.9 / +27.4 | +23.1 / +0.1 | +47.5 / +26.6 | +9.5 / +13.8 | +42.5 / +41.8 |
| 30 | 7 | 2 | +52.0 / +51.7 | +27.6 / +28.2 | +24.3 / +0.6 | +45.2 / +23.7 | +18.0 / +19.1 | +48.3 / +48.5 |
| 30 | 7 | 3 | +26.5 / +28.6 | +20.9 / +22.7 | +32.4 / +5.4 | +45.9 / +27.0 | +9.0 / +11.9 | +23.8 / +26.6 |
| 30 | 10 | 1.5 | +57.0 / +53.0 | +27.9 / +23.9 | +25.2 / −3.4 | +54.2 / +27.9 | +8.1 / +12.2 | +33.2 / +31.9 |
| 30 | 10 | 2 | +62.2 / +58.7 | +39.0 / +36.5 | +18.1 / −8.0 | +54.2 / +28.9 | +27.7 / +26.6 | +56.3 / +55.0 |
| 30 | 10 | 3 | +27.1 / +31.8 | +20.6 / +25.2 | +34.7 / +6.1 | +39.5 / +19.6 | +2.3 / +6.6 | +24.0 / +29.3 |
| 30 | 14 | 1.5 | +52.7 / +50.3 | +24.5 / +22.1 | +17.8 / −7.4 | +51.7 / +26.2 | +5.7 / +8.9 | +31.8 / +31.0 |
| 30 | 14 | 2 | +64.8 / +60.3 | +40.7 / +37.1 | +22.8 / −6.7 | +55.5 / +27.1 | +31.2 / +30.3 | +57.2 / +54.8 |
| 30 | 14 | 3 | +29.9 / +31.3 | +22.6 / +23.9 | +46.5 / +13.0 | +46.0 / +22.1 | +7.6 / +9.7 | +28.9 / +30.4 |

These are 27-day totals in a month in which puts paid. None is significant: the best OOS daily t is +1.12, and no
configuration passes BH.

**TradeBot (30-min only):**

| ATR | mult | intraday | intraday + overlay | ≤4 sessions | ≤4 sessions + overlay | HTF intraday | premium-flip intraday |
|---|---|---|---|---|---|---|---|
| 7 | 1.5 | +4.8 / +11.8 | −4.5 / +2.5 | +70.6 / +73.5 | +46.9 / +36.3 | −3.6 / +1.1 | −14.9 / −4.5 |
| 7 | 2 | +9.5 / +18.8 | +1.1 / +8.0 | +19.7 / +28.2 | +21.8 / +25.1 | +18.5 / +19.9 | −0.0 / +10.1 |
| 7 | 3 | +4.3 / +5.2 | −2.8 / −5.8 | −47.7 / −48.2 | +11.0 / −9.6 | +12.0 / +13.9 | +2.4 / +4.5 |
| 10 | 1.5 | −3.8 / +2.3 | −13.6 / −7.0 | +76.6 / +71.5 | +52.6 / +38.6 | −5.4 / +0.0 | −27.0 / −18.1 |
| 10 | 2 | +13.2 / +25.6 | +7.8 / +17.9 | +54.4 / +49.1 | +38.4 / +37.0 | +12.9 / +18.1 | +2.7 / +14.2 |
| 10 | 3 | −3.3 / +1.5 | −11.3 / −10.1 | −46.9 / −46.6 | +16.4 / −10.7 | +11.5 / +15.1 | −6.9 / −1.1 |
| 14 | 1.5 | −8.2 / −0.9 | −13.6 / −5.5 | +67.8 / +68.3 | +51.5 / +48.3 | +8.3 / +11.4 | −23.6 / −15.0 |
| 14 | 2 | +3.6 / +16.1 | +4.1 / +14.8 | +64.3 / +66.0 | +57.7 / +53.9 | +8.0 / +15.6 | −9.1 / +1.8 |
| 14 | 3 | −3.6 / +0.3 | −7.0 / −6.6 | −34.6 / −32.0 | +23.0 / +1.9 | +11.7 / +14.6 | −10.9 / −5.1 |

The 4-session ST(·, 1.5) cells are +68 to +77 L over ~16,600 trades: +0.4% per trade, t 0.69–0.75, not significant.
They transfer to live as −3.4 L (§7).

**Index 1-min (real NIFTY / SENSEX):**

| TF | ATR | mult | intraday | intraday + overlay | ≤4 sessions | ≤4 sessions + overlay | HTF intraday | premium-flip intraday |
|---|---|---|---|---|---|---|---|---|
| 5 | 7 | 1.5 | −3.4 / −1.8 | −0.4 / +1.2 | −6.0 / −3.5 | −3.8 / −2.4 | +4.8 / +6.4 | −3.0 / +0.3 |
| 5 | 7 | 2 | −2.7 / −1.9 | +4.6 / +3.0 | −3.1 / +0.2 | −1.9 / +0.1 | −0.1 / +0.4 | −6.9 / −5.4 |
| 5 | 7 | 3 | +4.0 / +2.4 | +4.1 / +3.3 | +2.1 / +8.8 | +3.8 / +6.6 | −4.6 / +0.8 | +4.4 / +1.1 |
| 5 | 10 | 1.5 | −5.1 / −0.2 | −1.1 / +3.5 | −7.1 / −3.6 | −5.1 / −2.4 | +4.3 / +8.2 | −8.4 / −4.0 |
| 5 | 10 | 2 | −1.3 / −0.9 | +4.9 / +2.4 | −4.6 / −2.4 | −1.9 / −1.4 | +1.8 / +3.4 | −5.6 / −4.8 |
| 5 | 10 | 3 | +3.4 / +2.3 | +2.1 / +3.0 | +1.8 / +7.4 | +4.3 / +6.9 | −6.1 / −0.4 | +1.6 / −0.6 |
| 5 | 14 | 1.5 | −4.9 / +0.0 | −0.1 / +4.2 | −6.7 / −2.4 | −4.6 / −1.4 | +5.7 / +10.6 | −9.9 / −3.8 |
| 5 | 14 | 2 | −2.3 / −1.6 | +5.5 / +3.3 | −6.0 / −3.3 | −3.4 / −1.7 | +2.1 / +4.3 | −3.2 / −4.1 |
| 5 | 14 | 3 | +5.7 / +2.8 | +3.8 / +2.4 | +4.2 / +7.8 | +6.9 / +7.4 | −6.5 / −1.8 | +5.5 / +1.1 |
| 15 | 7 | 1.5 | +9.5 / +7.3 | +8.5 / +6.8 | +11.1 / +13.5 | +9.7 / +9.8 | +0.9 / +2.1 | +10.8 / +8.9 |
| 15 | 7 | 2 | +10.8 / +10.5 | +10.2 / +11.0 | +18.1 / +22.2 | +12.8 / +14.4 | −2.2 / +2.8 | +11.3 / +9.6 |
| 15 | 7 | 3 | −1.0 / −0.1 | +1.7 / +3.4 | +7.5 / +12.4 | +2.2 / +5.0 | −6.8 / −2.4 | −0.5 / +0.2 |
| 15 | 10 | 1.5 | +6.1 / +7.0 | +4.9 / +6.5 | +11.7 / +16.4 | +8.3 / +10.2 | −2.2 / +0.2 | +7.3 / +8.0 |
| 15 | 10 | 2 | +10.6 / +7.9 | +10.2 / +9.0 | +19.1 / +21.6 | +13.6 / +13.7 | −2.4 / +2.1 | +12.7 / +9.1 |
| 15 | 10 | 3 | +2.7 / +2.1 | +4.6 / +4.5 | +9.9 / +13.1 | +5.8 / +7.1 | −4.3 / −0.5 | +3.1 / +2.5 |
| 15 | 14 | 1.5 | +5.1 / +7.0 | +3.6 / +5.7 | +10.9 / +17.5 | +8.5 / +14.0 | −2.3 / +0.3 | +5.2 / +6.4 |
| 15 | 14 | 2 | +12.2 / +8.1 | +9.7 / +7.3 | +19.6 / +21.8 | +11.6 / +11.4 | −2.5 / +1.1 | +13.8 / +9.3 |
| 15 | 14 | 3 | +7.5 / +5.7 | +8.3 / +7.1 | +15.0 / +18.7 | +8.0 / +9.7 | −4.0 / −0.4 | +7.1 / +4.7 |
| 30 | 7 | 1.5 | +3.7 / +4.1 | +4.2 / +4.7 | +17.8 / +20.8 | +11.2 / +12.2 | +2.4 / +3.4 | +3.0 / +2.4 |
| 30 | 7 | 2 | +0.6 / −1.1 | +1.7 / +0.2 | +6.2 / +9.0 | +4.5 / +5.3 | −3.2 / −0.6 | +0.2 / −1.5 |
| 30 | 7 | 3 | +3.6 / −0.8 | +2.1 / +0.2 | −11.8 / −6.2 | −4.4 / −1.7 | −1.4 / +0.5 | +3.6 / −0.9 |
| 30 | 10 | 1.5 | +1.4 / +1.1 | +3.0 / +2.8 | +13.6 / +18.0 | +6.3 / +9.2 | +0.2 / +1.1 | +2.2 / +1.0 |
| 30 | 10 | 2 | +2.6 / +0.7 | +4.1 / +1.9 | +3.0 / +9.5 | +2.4 / +5.5 | −3.5 / −0.8 | +1.2 / −0.8 |
| 30 | 10 | 3 | +4.1 / −1.1 | +3.4 / +0.7 | −12.3 / −7.1 | −4.5 / −2.1 | −0.4 / +0.9 | +4.0 / −1.3 |
| 30 | 14 | 1.5 | +5.4 / +4.2 | +5.6 / +4.8 | +16.9 / +20.9 | +12.1 / +13.8 | −0.2 / +0.4 | +5.3 / +3.5 |
| 30 | 14 | 2 | +1.8 / −0.2 | +2.0 / −0.1 | +3.4 / +9.6 | +2.5 / +5.1 | −4.0 / −1.3 | −0.1 / −2.0 |
| 30 | 14 | 3 | +3.1 / −1.5 | +2.1 / +0.4 | −14.2 / −8.1 | −5.7 / −2.8 | −0.0 / +1.2 | +3.1 / −1.3 |

The 15-min ≤4-session column is uniformly positive: +7.5 to +19.6 L full period on 170–410 trades. The best is
`B_15_14_2_h0_s_4_o0`: +19.6 L, t 1.75 full, OOS +21.8 L, t 2.03. That column is in-sample hindsight among 1,296
configurations, and it gives −3.8 L on the live indices (§7).

**Fyers simulated:** the same grid is +9 to +269 L in every cell; 5-min ST(·, 2–3) intraday is the best,
+218 to +269 L.
- Mult 1.5 intraday: +120 to +134 L.
- 15-min / 30-min ≤4 sessions: +9 to +219 L.
- HTF confirmation intraday: +10 to +118 L.

### 4.2 A: premium Supertrend

**Live** (full / OOS L; intraday trades):

| TF | ATR | mult | intraday | intraday + overlay | ≤4 sessions | ≤4 sessions + overlay | trades |
|---|---|---|---|---|---|---|---|
| 5 | 7 | 1.5 | −255.4 / −184.6 | −242.4 / −173.3 | −259.3 / −190.2 | −242.6 / −175.2 | 32,074 |
| 5 | 7 | 2 | −207.3 / −159.1 | −185.8 / −140.8 | −190.0 / −144.8 | −179.4 / −136.4 | 23,703 |
| 5 | 7 | 3 | −121.8 / −102.3 | −115.4 / −95.4 | −101.4 / −90.0 | −103.1 / −89.3 | 14,713 |
| 5 | 10 | 1.5 | −247.7 / −179.9 | −235.3 / −168.5 | −251.3 / −184.3 | −232.9 / −166.8 | 31,546 |
| 5 | 10 | 2 | −204.3 / −153.5 | −189.9 / −141.6 | −177.5 / −128.7 | −176.3 / −129.5 | 23,191 |
| 5 | 10 | 3 | −133.3 / −111.2 | −123.8 / −102.8 | −104.6 / −94.7 | −113.5 / −102.7 | 14,232 |
| 5 | 14 | 1.5 | −233.8 / −171.0 | −220.1 / −158.0 | −231.2 / −169.4 | −214.9 / −153.6 | 31,070 |
| 5 | 14 | 2 | −198.4 / −147.1 | −182.6 / −133.6 | −173.7 / −124.5 | −168.9 / −122.7 | 22,851 |
| 5 | 14 | 3 | −126.9 / −99.9 | −121.3 / −95.6 | −92.2 / −83.7 | −105.7 / −95.0 | 14,001 |
| 15 | 7 | 1.5 | −44.6 / −29.8 | −47.3 / −34.0 | −15.5 / −32.5 | −22.0 / −36.3 | 9,381 |
| 15 | 7 | 2 | +6.2 / +10.7 | −13.2 / −8.4 | +0.5 / −15.2 | +14.9 / +0.0 | 6,727 |
| 15 | 7 | 3 | +84.7 / +80.9 | +44.3 / +40.5 | +53.4 / +47.1 | +54.8 / +43.3 | 4,126 |
| 15 | 10 | 1.5 | −41.4 / −24.3 | −43.9 / −28.1 | −15.8 / −26.8 | −16.6 / −23.8 | 9,336 |
| 15 | 10 | 2 | +6.6 / +11.3 | −3.2 / +0.9 | +3.2 / −14.3 | +29.2 / +11.8 | 6,768 |
| 15 | 10 | 3 | +87.9 / +87.6 | +50.1 / +49.9 | +65.7 / +58.0 | +63.8 / +51.8 | 4,145 |
| 15 | 14 | 1.5 | −41.7 / −25.1 | −42.5 / −27.0 | −8.3 / −20.2 | −1.1 / −10.5 | 9,369 |
| 15 | 14 | 2 | +6.0 / +14.9 | −0.8 / +7.6 | +21.3 / +10.5 | +33.6 / +20.5 | 6,850 |
| 15 | 14 | 3 | +77.4 / +76.7 | +46.0 / +46.1 | +76.5 / +65.9 | +65.9 / +50.9 | 4,172 |
| 30 | 7 | 1.5 | +45.7 / +48.4 | +19.0 / +21.4 | +50.6 / +40.2 | +46.9 / +32.2 | 4,744 |
| 30 | 7 | 2 | +79.5 / +81.1 | +45.3 / +46.3 | +81.0 / +75.0 | +69.3 / +58.1 | 3,439 |
| 30 | 7 | 3 | +67.6 / +70.2 | +49.6 / +51.5 | +135.2 / +115.7 | +82.3 / +72.7 | 2,153 |
| 30 | 10 | 1.5 | +36.5 / +41.4 | +13.7 / +18.5 | +57.5 / +45.8 | +49.0 / +35.2 | 4,753 |
| 30 | 10 | 2 | +75.0 / +75.5 | +45.1 / +45.7 | +67.7 / +63.1 | +55.1 / +47.6 | 3,471 |
| 30 | 10 | 3 | +58.5 / +63.4 | +37.9 / +42.1 | +128.0 / +110.5 | +72.7 / +65.1 | 2,193 |
| 30 | 14 | 1.5 | +36.1 / +39.2 | +12.1 / +15.3 | +69.4 / +52.3 | +50.9 / +32.2 | 4,765 |
| 30 | 14 | 2 | +69.5 / +73.8 | +42.2 / +46.2 | +81.5 / +77.6 | +57.3 / +51.6 | 3,516 |
| 30 | 14 | 3 | +55.4 / +61.1 | +36.6 / +41.7 | +107.4 / +90.5 | +68.4 / +60.1 | 2,239 |

- 5-min premium flips lose −92 to −259 L in 27 days. They fire after the move (the early-entry study's lateness
  finding).
- 30-min ST(·, 3) on the premium is a slower, MACD-like trigger. It made +55 to +135 L here without the overlay, mostly PE.
- Not significant: deflated-Sharpe probability 0.26 for the in-sample best (`A_30_10_3_4_o1`).
- On TradeBot all 36 A cells are −66 to +4 L full period (8% positive). On index-1m they are −20 to +21 L.

### 4.3 C: pivots, per entry rule (full-period P&L L over its variants)

| entry rule | live: median / best (OOS of best) / share OOS > 0 | TradeBot: median / best / share OOS > 0 | index-1m: median / best / share OOS > 0 | Fyers sim: median / best |
|---|---|---|---|---|
| openTC (open above TC → CE) | −9.0 / +33.9 (+45.9) / 0.54 | −69.6 / −23.0 / 0.00 | +3.0 / +6.6 / 0.53 | +17.2 / +93.3 |
| brkTC (break TC after 09:30) | −5.0 / +35.7 (+23.6) / 0.32 | −22.7 / −4.0 / 0.00 | −0.9 / +7.9 / 0.51 | +17.2 / +109.7 |
| brkR1 (target R2) | +5.2 / +24.4 (+16.3) / 0.85 | −12.1 / +15.9 / 0.19 | +3.3 / +9.5 / 0.88 | +53.3 / +135.7 |
| brkH4 (Camarilla breakout) | +9.4 / +61.0 (+82.1) / 0.96 | −0.9 / +16.8 / 0.50 | +1.0 / +5.2 / 0.67 | +83.0 / +151.4 |
| fadeL3 (Camarilla H3 / L3 fade) | −8.7 / +37.5 (+27.6) / 0.31 | −85.3 / −33.3 / 0.00 | −3.6 / +4.6 / 0.17 | +31.5 / +79.4 |
| brkPDH (previous-day high / low) | −3.2 / +14.5 (+21.4) / 0.33 | −20.6 / +18.8 / 0.25 | +1.1 / +5.4 / 0.46 | +47.3 / +110.4 |
| orb15 | +7.6 / +44.6 (+17.2) / 0.58 | – (30-min data) | +4.3 / +9.9 / 1.00 | +102.5 / +166.6 |
| orb30 | +16.3 / +51.3 (+30.8) / 1.00 | −3.4 / +20.4 / 0.25 | +1.7 / +11.1 / 1.00 | +103.1 / +148.6 |

Reading:
- The CPR "open above TC / break TC, target R1 / R2, stop P / BC" family is the worst on real data. TradeBot:
  0% of OOS cells positive, medians −23 to −70 L.
- The Camarilla H3 / L3 fade is also among the worst.
- Breakout-continuation rules (H4, ORB, R1→R2) are around zero to mildly positive on live and index-1m and negative on
  TradeBot.
- Lowest p-values (all negative signs): live `C_15_fadeL3_H3_L4_a_d_o0` p 0.015; TradeBot
  `C_30_fadeL3_-_L4_a_4_o1` p 0.0003.

### 4.4 D: Supertrend + pivot location

Median full-period P&L over the 27 ST cells, and the share of configurations with OOS > 0:

| family / location / hold / overlay | live | TradeBot | index-1m | Fyers sim |
|---|---|---|---|---|
| D1 P d o0 | +45.4 (1.00) | +22.5 (0.89) | +4.7 (0.85) | +99.0 (1.00) |
| D1 P 4 o0 | +20.3 (0.63) | +30.7 (0.67) | +4.4 (0.89) | +122.3 (1.00) |
| D1 P 4 o1 | +27.6 (0.78) | +45.5 (0.78) | +3.6 (0.89) | +111.4 (1.00) |
| D1 TC d o0 | +39.7 (1.00) | +26.5 (0.89) | +5.3 (0.85) | +89.5 (1.00) |
| D2 P d o0 | +53.5 (1.00) | −4.0 (0.56) | +0.7 (0.78) | +91.5 (1.00) |
| D2 TC 4 o0 | +10.6 (0.44) | +4.7 (0.44) | +8.5 (0.89) | +80.8 (0.96) |

Family-wide (all D1 configurations):
- Median OOS P&L: live +20.1 L (77% > 0), TradeBot +24.3 L (75%), index-1m +4.3 L (92%).
- **But no D1 configuration has OOS t > 2 on live (0 / 216) or TradeBot (0 / 72); 9 / 216 on index-1m.**
- TradeBot median D1 OOS +24.3 L is on ~9,000 trades, i.e. +0.27% per trade, t 0.54.
- The location filter removes some counter-trend flips, which is why D1 sits above B. It does not create an edge
  that survives selection or costs: WF_D is −7.5 (live) / −35.7 (TradeBot) / +3.9 L (index-1m).

### 4.5 Levers

Each row compares configurations that differ only in that lever. Values are the median P&L difference in L; the
share of pairs where the lever helped is in brackets. Format: full period / OOS.

| lever | live | TradeBot | index-1m | Fyers sim |
|---|---|---|---|---|
| option overlay (40% stop, trail 30% from +40%, +100%) on vs off | −5.9 (0.31) / −5.5 (0.33) | 0.0 (0.50) / −3.1 (0.36) | +0.6 (0.61) / +0.8 (0.66) | −7.1 (0.29) / −5.0 (0.33) |
| HTF confirmation on vs off | −14.8 (0.36) / −0.4 (0.50) | +4.6 (0.53) / +0.9 (0.51) | −5.2 (0.25) / −2.8 (0.34) | −74.6 (0.09) / −68.3 (0.09) |
| premium-flip exit vs opposite spot flip | −17.0 (0.08) / −12.9 (0.07) | −3.5 (0.33) / −2.3 (0.35) | −0.7 (0.33) / −0.8 (0.27) | – |
| ≤4 sessions vs intraday | +5.8 (0.60) / −4.4 (0.42) | +5.9 (0.56) / −1.7 (0.45) | −0.3 (0.47) / +1.3 (0.59) | +6.1 (0.58) / −0.5 (0.49) |
| narrow-CPR days only vs all (C) | +0.4 (0.51) / −1.1 (0.45) | +43.5 (0.76) / +26.8 (0.72) | +1.2 (0.64) / −0.2 (0.48) | −30.5 (0.21) / −24.5 (0.19) |
| D location TC vs P | −8.6 (0.25) / −7.1 (0.24) | +1.0 (0.54) / −2.4 (0.33) | +0.4 (0.62) / +0.2 (0.53) | +7.6 (0.67) / +5.5 (0.64) |

- The overlay hurts in three of four datasets, consistent with the 2 Oct finding that tight exits lose.
- HTF confirmation fires later and hurts.
- Exiting on the premium's own flip is worse than exiting on the spot's opposite flip in 92% (live) of pairs.
- The narrow-CPR filter helps only TradeBot pivots, and only because it skips the worst days; those configurations
  still lose.

## 5. Index vs stock, CE vs PE

- **Live:**
  - the policies' index trades net −2.1 to +3.8 L; stock trades −24.5 to +29.1 L;
  - MACD lane: index +7.6 L on 21 trades, stock +57.0 L;
  - every policy: CE −56 to −84 L, PE +48 to +108 L (falling month).
- **TradeBot:**
  - index −3.5 to +4.5 L, stock −17.8 to −55.3 L;
  - CE / PE mixed: B and D lose on PE (−63 to −65 L), A loses on CE.
- **Index-1m (all index):** CE −2.4 to +4.1 L, PE −1.0 to +5.6 L.
- **Fyers (all index):** CE +18 to +79 L, PE +48 to +138 L (simulated).

## 6. Is the Black-Scholes/VIX long history believable? Validation against real premiums

Code: `validate_bs.py`. Trades of the 972 common configurations were matched between the Fyers simulation and the
index-1m real-premium data by (index, side, entry date, entry minute), for NIFTY Apr – Aug 2025 and SENSEX
Apr 2025 – Feb 2026.

- Pooled: 216,687 trade-pairs (repeating across configurations), corr 0.927, mean return real +1.37% vs sim
  +1.58%.
- By group: corr 0.82–0.98. The simulation **overstates intraday holds** and is roughly unbiased for ≤ 4-session
  holds:

  | group | matched trades / config | corr | mean return real / sim | P&L real / sim (L per config) |
  |---|---|---|---|---|
  | B intraday | 323 | 0.953 | −0.25% / +1.53% | +0.87 / +3.49 |
  | B intraday + overlay | 323 | 0.916 | +0.02% / +1.91% | +1.74 / +4.48 |
  | B ≤4 sessions | 308 | 0.969 | −0.38% / −1.42% | +1.69 / −1.22 |
  | D1 intraday | 258 | 0.946 | +3.24% / +4.17% | +5.20 / +6.71 |
  | D1 ≤4 sessions | 248 | 0.978 | +3.12% / +2.22% | +4.42 / +2.33 |
  | C intraday | 167 | 0.943 | +0.33% / +0.48% | −0.38 / +0.27 |

- Headline configuration `B_5_10_3_h0_s_d_o0`, 509 matched trades:
  - corr 0.951;
  - real +1.37% vs sim +2.16% per trade;
  - P&L real +4.63 L vs sim +9.38 L;
  - CE bias +0.60 pp, PE bias +0.98 pp.
- `B_15_14_2_h0_s_4_o0`: real +7.05% vs sim +5.08% (P&L +17.3 vs +12.8 L).
- **Why.**
  - The simulator prices the session with calendar-time decay: 375 trading minutes ≈ 0.26 of a day's theta.
  - Real ATM options lose most of their daily decay during trading hours.
  - Holding over the session is therefore too cheap in the simulation, which also shows as +337 L for a plain
    09:20→15:15 straddle.
  - Real straddles: −11.5 L (index-1m, t −2.10), −260 L (TradeBot, t −4.52), +11.9 L (live, t 0.13).
  - The 3-day validation in `index_lane_study.md` (corr 0.98–0.99) does not cover this, because overnight theta is in
    both the simulated and real numbers there.
- **Pricing-free check: the signed spot move per trade.** Side × (spot at exit / spot at entry − 1), on index trades.

  | configuration | Fyers 2021–26 (all years) | per year (bps, t) | index-1m 2025–26 (real) | live Sep 2026 indices |
  |---|---|---|---|---|
  | B_5_10_3 intraday | +3.3 bps, t +6.0, hit 45% | 2021 +4.2 (2.0), 2022 +2.7 (1.7), 2023 +2.8 (2.7), 2024 +5.0 (3.4), 2025 +2.0 (2.1), 2026 +3.6 (2.8) | +1.5 bps (t 1.0), option +1.10% / trade, +3.4 L / 517 trades | +0.3 bps (t 0.1), option −0.46%, −1.4 L / 173 |
  | B_5_14_3 intraday | +2.8 bps, t +5.2 | +1.6 to +4.2 every year | +1.5 bps (t 1.0), +5.7 L / 508 | +0.1 bps, −1.1 L / 176 |
  | B_15_14_2 ≤4 sessions | +9.0 bps, t +6.5 | +6.2 to +15.2 every year | +9.6 bps (t 2.8), option +7.8%, +19.6 L / 274 | −6.5 bps (t −1.0), −4.6 L / 95 |
  | B_30_10_1.5 ≤4 sessions | +10.9 bps, t +6.3 | +6.5 to +15.2 every year | +8.4 bps (t 2.0), +13.6 L / 214 | −3.3 bps, −4.3 L / 66 |
  | B_15_10_3 intraday | +3.9 bps, t +3.5 | +0.6 to +6.1 | +0.1 bps, +2.7 L / 187 | +12.4 bps (t 2.2), +5.3 L / 66 |
  | B_30_10_3 intraday | +2.2 bps, t +1.4 | −7.2 to +9.9 (three negative years) | +7.7 bps (t 2.0), +4.1 L / 115 | +7.0 bps, +1.3 L / 35 |

  - Index Supertrend flips do carry a small, persistent continuation.
  - On TradeBot indices the same holds: B_30_10_1.5 ≤4 sessions +10.3 bps (t 2.7) on 478 trades, option P&L only
    +7.5 L because 30-min fills are coarse.
  - The continuation is a few bps of spot. ATM weekly premiums are about 0.4–0.8% of spot, theta and spread take most
    of it, and it does not show up in 19 live days.

## 7. Transfer tests (configurations chosen on one dataset, applied unchanged to another)

Code: `transfer.py`.

| source → target | configuration(s) | real P&L | simulation, same index-quarters |
|---|---|---|---|
| Fyers WF_B / WF_ALL quarterly picks → index-1m (NIFTY / SENSEX, Apr 2025 – Feb 2026) | `B_5_10_3_h0_s_d_o0` | +3.37 L, 517 trades, t +0.33, +1.10% / trade | +8.18 L, 528 trades |
| Fyers WF_B → live indices (4 Sep – 1 Oct) | `B_5_10_3_h0_s_d_o0` | +1.18 L, 117 trades, t +0.22 | +6.34 L |
| Fyers WF_D → index-1m | D1 / D2 picks | +14.00 L, 186 trades, t +1.41 | +14.64 L |
| Fyers WF_D → live indices | `D1_30_7_1.5_P_4_o0` | −2.81 L, 28 trades | −2.59 L |
| Fyers WF_C → index-1m / live | ORB / H4 picks | +1.60 L (t 0.20) / −2.04 L | +6.23 / +4.85 L |
| TradeBot final ALL / B pick → live OOS, all underlyings | `B_30_10_1.5_h0_s_4_o0` | −3.36 L, 2,835 trades (CE −103, PE +100) | – |
| TradeBot final D → live | `D1_30_10_1.5_P_4_o0` | −18.23 L | – |
| TradeBot final C → live | `C_30_orb30_-_ORmid_n_d_o0` | +8.17 L (t 0.42) | – |
| TradeBot final A → live | `A_30_10_2_4_o1` | +47.55 L (t 0.94; CE −72.6, PE +120.1) | – |
| index-1m final ALL / A → live indices | `A_15_10_2_4_o1` | +2.15 L, 69 trades | – |
| index-1m final B → live indices | `B_15_14_2_h0_s_4_o0` | −3.81 L, 67 trades | – |
| index-1m final D → live indices | `D2_15_10_2_P_4_o0` | −1.31 L | – |

Live MACD lane on indices only, same days: +7.61 L on 21 trades (t 1.55).

## 8. Pessimistic fills (bar-extreme)

Code: `pess_eval.py`. The same walk-forward selections as the level-fill policies, re-simulated with entry at the
next bar's high and exits at the bar's low:

| policy | live | TradeBot | index-1m | Fyers sim |
|---|---|---|---|---|
| WF_A | −22.8 (level +28.0) | −133.0 (−27.9) | −15.4 (+3.6) | – |
| WF_B | −171.9 (−26.6) | −387.5 (−56.4) | −6.0 (+3.9) | −564.2 (+213.2) |
| WF_C | −73.3 (−12.3) | −423.2 (−17.1) | −10.0 (−3.4) | −268.4 (+74.3) |
| WF_D | −45.5 (−7.5) | −854.1 (−35.7) | −2.4 (+3.9) | −120.7 (+96.0) |
| WF_ALL | −155.7 (−10.0) | −610.0 (−30.5) | −6.8 (+5.3) | −551.6 (+216.8) |
| MACD lane | **+26.8** (+64.6) | −511.5 (−84.7) | −1.1 (+4.5) | – |
| `B_5_10_3_h0_s_d_o0` | −158.2 (−10.5) | – | −19.5 (+2.3) | −425.8 (+238.6) |

- Bar-extreme fills cost about one 1-min high-low range per side. For ATM index options that is ≈ 2–3% of premium per
  side, for stock options more.
- This is a stress test, not the expected cost.
- The headline index candidate makes +1.1% per trade at level fills on real premiums. Its break-even extra slippage is
  ≈ 0.5% of premium per side.
- NIFTY / SENSEX ATM weekly spreads are usually inside that; stock-option spreads usually are not.

## 9. Multiple testing

| item | count |
|---|---|
| fixed configurations | live 1,296 + TradeBot 432 + index-1m 1,296 + Fyers 972 = **3,996** (+ 6 MACD-lane variants) |
| walk-forward policies | 6 + 6 + 6 + 5 = 23 (incl. the 4 `tsel` selection variants) |
| benchmark cells | 23 policies + 3 MACD × (CE, PE, both, 3 × 30 random draws) and 4 straddles |
| transfer / validation tests | ≈ 30 |

Per-dataset results (fixed configurations, OOS daily t vs 0):

| | live | TradeBot | index-1m | Fyers sim |
|---|---|---|---|---|
| configurations with ≥ 10 OOS trades | 1,296 | 424 | 1,296 | 972 |
| nominal p < 0.05 (+ / −) | 13 (0 / 13) | 22 (0 / 22) | 65 (51 / 14) | 599 (599 / 0) |
| expected by chance | 65 | 21 | 65 | 49 |
| BH 5% passes | **0** | **0** | **0** | 504 (all positive) |
| in-sample best (daily Sharpe) | `A_30_10_3_4_o1` 0.360 / day | `B_30_7_2_h1_s_d_o0` 0.068 | `D2_15_10_2_TC_4_o1` 0.160 | `B_5_10_3_h0_s_d_o0` 0.142 |
| expected max of N null trials | 0.480 | 0.170 | 0.205 | 0.096 |
| deflated-Sharpe probability | 0.26 | 0.06 | 0.23 | 0.98 |

- The Fyers pass rate is not evidence for the systems. A simulated straddle with no signal is t +8.5 there.
- The meaningful Fyers statistic is the direction excess over the time-matched simulated straddle: +141 L, t 3.43,
  one test.
- After the ≈ 4,000 configurations it would survive a Bonferroni-style haircut, but real premiums do not reproduce it
  (§6–7).

## 10. Verdict

- **Stock options:** no.
  - On live and TradeBot every walk-forward policy is ≤ the MACD lane (live) or loses (TradeBot).
  - They are no better than random entries with the same CE/PE mix.
  - They die under pessimistic fills.
  - 0 configurations pass FDR, and the nominal hits are all negative.
- **Premium Supertrend (A):** no.
  - Fast flips (5-min) are the costliest family in the study (−92 to −259 L in 27 live days).
  - Slow flips (30-min ST(·, 3)) behave like a lagged MACD cross and are not significant.
- **Pivots (C):** no.
  - CPR open / break with R1 / R2 targets and Camarilla fades are reliably negative on real premiums.
  - Breakout continuation (H4, ORB) is around zero.
- **Supertrend + location (D):** typical configurations are slightly positive out of sample on all three real datasets.
  But no configuration is significant, and walk-forward selection does not capture it.
- **Index options with spot-Supertrend direction (B / D on NIFTY, BANKNIFTY, SENSEX, MIDCPNIFTY):** the only live
  question.
  - The spot continuation after a flip is real over 2021–26: a few bps intraday, ~10 bps over multi-session holds.
  - Real premiums capture part of it: index-1m +3.4 L (5-min intraday) to +19.6 L (15-min ≤4 sessions, hindsight).
  - It is not significant on real premiums, flat on the live indices, and negative with bar-extreme fills.
  - It is not an independent system today.

## 11. Recommendations

1. **Do not deploy any Supertrend / pivot lane with capital, and do not replace or gate the MACD lane with them.**
2. **Never trade stock options on these signals.** Do not use 5-min premium Supertrend flips.
3. **Shadow-log only (zero capital)** an index-only spot-Supertrend trade.
   - Instruments: NIFTY, BANKNIFTY, SENSEX, MIDCPNIFTY ATM weekly / nearest options.
   - Two variants:
     - **(a)** ST(10, 3) on 5-min spot bars, intraday: buy ATM CE on a bullish flip, ATM PE on a bearish flip
       (09:20–14:45 signals); exit at the opposite flip or 15:15; no option stop.
     - **(b)** ST(14, 2) on 15-min spot bars, the same but held up to 4 sessions.
   - Log the bid / ask at signal time and at exit.
   - Promote only if, over ≥ 60 sessions, all of the following hold:
     - net P&L at bid / ask is > 0;
     - paired daily t > 2 against a time-matched ATM straddle *and* against same-rate random entries;
     - it is not worse than the MACD lane's index trades on the same days.
4. **Do not add the 40% / 30% / +100% overlay, HTF confirmation or premium-flip exits** to any Supertrend system. They
   lowered P&L in most paired comparisons.
5. **Fix the Black-Scholes simulator before using it for intraday option research.**
   - Use trading-time theta: put ≥ 75–80% of a day's decay inside the session.
   - Add an intraday IV path, e.g. calibrated from the 1-min index chains.
   - Re-validate on intraday holds, including a no-signal straddle.
   - The 3-day validation does not cover intraday use.

## 12. Scripts and outputs

Folder: `/private/tmp/claude-501/-Users-ramachandran-MACD-mini-ADV/77c677ac-a2ad-4b4d-977f-6fcea088fa80/scratchpad/st_pivot_system/`.
- Copied unchanged from earlier folders: `lanes_lib.py`, `books.py`, `st_lib.py`, `bs_sim.py`. `spc.py` re-implements
  the fill / cost rules in the same way.
- Runners:
  - `run.sh`: engine image, data volume, every SQLite opened `mode=ro`, `--network none`.
  - `tb.sh`: tradebot-backend image, TradeBot data and `index_analytics_data` read-only.

| script | does | output |
|---|---|---|
| `build_live.py` | live spot + every captured option contract (1-min), universe | `ds_live.pkl` |
| `build_tb.py tb \| ix` | TradeBot 30-min panel; index 1-min NIFTY / SENSEX chains | `ds_tb.pkl`, `ds_ix.pkl` |
| `build_fy.py` | Fyers 5-min indices + VIX, expiry calendars, IV-model inputs | `ds_fy.pkl` |
| `spc.py` | clock, ATM chooser, entry / exit / overlay simulation (level & pessimistic), costs, trade runner | – |
| `spc_bs.py` | Black-Scholes / VIX option store for the Fyers data | – |
| `systems.py` | Supertrend / pivot / CPR / Camarilla / ORB signal → trade-spec generators (A–D), MACD-lane benchmark | – |
| `run_grid.py <ds> [--pess list]` | every configuration (A–D + MACD) per dataset, parallel | `out/<ds>[_pess]/grp_*.pkl` |
| `analyze.py <ds>` | walk-forward selection, OOS metrics, full grid, BH-FDR, deflated Sharpe | `res/<ds>_ana.pkl`, `.log` |
| `controls.py <ds> 30` | random (R-side, R-time, look-ahead diagnostic), always-CE / PE, matched straddle, 09:20 straddle, ₹50 L / ₹1 L books | `res/<ds>_ctrl.pkl`, `.log` |
| `pess_list.py`, `pess_eval.py <ds>` | pessimistic-fill re-runs of the selected configurations | `res/<ds>_pess.pkl` |
| `validate_bs.py` | simulated vs real premiums on matched trades | `res/validate_bs.log`, `.pkl` |
| `fy_detail.py` | per-year / regime / signed-spot-move analysis | `res/fy_detail.log` |
| `transfer.py` | cross-dataset transfer tests | `res/transfer.log` |
| `levers.py` | lever pairs, family summaries, full per-configuration CSVs | `res/levers.log`, `res/grid_<ds>.csv` |
| `idx_check.py` | index vs stock split of key configurations | stdout |
| `tables.py` | markdown tables used here | `res/tables.md` |
| helpers | `probe_*.py`, `verify.py` (hand-check of sample trades against raw bars), `peek.py`, `extra.py` | |
