# Early entry: predicting the 30-min MACD zero cross 5–10 bars ahead

Study date 3 Oct 2026. Analysis only: nothing under the project was changed except this file, and every SQLite file
was opened read-only (`mode=ro`) from throwaway containers. The study follows `precross_model_and_kama.md` and
`empirical_study.md` Task 3, which predicted crosses only up to 30 minutes ahead. The user's point was that 30 minutes
ahead is no better than the cross itself, and that the cross must be predicted 5–10 thirty-minute bars
(2.5–5 trading hours) earlier to capture the move. This study tests that.

## 0. Bottom line

1. **The prize is real and much larger at 5–10 bars, but only with perfect foresight.** Entering k bars before
   crosses that actually happened (grace exits, 5 bps per side):
   - **TradeBot 2025–26** (11,284 crosses, 223 days):
     - at the cross: +29.9 L;
     - k = 1 (≈ 30 min early): +753 L;
     - k = 5: +2,396 L;
     - k = 8: +2,624 L;
     - k = 10: +2,608 L.
   - **Live** (3,419 crosses, 27 days):
     - cross + 5 min (the lane): +121.5 L;
     - k = 1: +593 L;
     - k = 4: +980 L (the peak);
     - k = 5: +958 L;
     - k = 10: +809 L.

   So on TradeBot, 5–10 bars of foresight is worth 3.2–3.5 times 30 minutes of foresight. On live it is worth only
   1.4–1.6 times, and nothing is gained beyond 4 bars. The user is right that 30 minutes early leaves most of the
   foresight value on the table in the longer history. The premium bottom sits a median 9–10 bars before the cross,
   and about half of the bottom-to-peak move happens before the cross.
2. **A cross 5–10 bars away is only weakly predictable.** Walk-forward AUCs look high:

   | horizon | TradeBot AUC | live AUC |
   |---|---|---|
   | P(cross ≤ 5 bars) | 0.92 | 0.93 |
   | P(cross ≤ 8 bars) | 0.87 | 0.88 |
   | P(cross ≤ 10 bars) | 0.84 | 0.85 |

   These numbers are inflated by crosses 1–4 bars away. The AUC for 1–4 bars away vs > 10 bars is 0.94–0.95. The AUC
   for crosses 5–10 bars away vs none within 10 bars is only **0.75–0.78**.
   - Of the rows flagged at C10 ≥ 0.5, 54–55% cross within 1–4 bars, only **17–18% cross 5–10 bars out**, and
     27–29% do not cross within 10 bars.
   - Lead time actually achieved before a real cross: **median 4 bars at p ≥ 0.5** (half of fires ≥ 5 bars early,
     precision 0.71–0.73), and 3 bars at p ≥ 0.7 (26–29% ≥ 5 bars early).
   - The "5–10 bars early" fires carry most of the false positives.
3. **No early or hybrid policy beats the cross entry after multiple-testing correction, on either dataset.** All
   thresholds were chosen on training data only. There were 24 such policies per dataset:
   - **TradeBot** (8 out-of-sample months, Jul 2025 – Feb 2026). The cross entry itself earns nothing here
     (−6.2 L, daily t −0.09).
     - Best: `hybrid Rt` (P&L regressor, grace + 10-bar timeout): +151 L, Δ +157 L, paired t_d 2.24 (p 0.026). Its
       Rs 50 L book makes 189.5 L against 17.8 L.
     - Next: `hybrid C10/HZ10/C8 + timeout`: +100–124 L, t_d 1.83–2.00.
     - Nothing survives BH-FDR at 5% or Bonferroni across the 24 policies.
     - The best regressor's early entries are not cross anticipation: the median time to the cross at entry is 22
       bars, and only 28% cross within 10. Its ranking power comes mainly from `dte`.
   - **Live** (19 out-of-sample days):
     - Every model hybrid lands within −10 to +4 L of the cross+5min baseline (84.7 L), with |t_d| ≤ 0.43.
     - The train-chosen simple rules are worse: −137 to −187 L, nominal p 0.009–0.03.
     - The regressors do not carry over: `hybrid Rt` is −29.6 L.
4. **Verdict: the hypothesis is half right.** The value of early entry does sit 3–10 bars before the cross, so
   "30 min before ≈ the cross itself" is true of what a model can deliver, not of what foresight is worth. But with
   price, spot, higher-timeframe, IV, OI and market-context features, a cross 5–10 bars away is not predictable well
   enough to collect that value net of false positives. Out of sample the models capture **≈ 0–7%** of the
   look-ahead prize:
   - TradeBot: about +110–157 L of the +1,947 L available from oracle 5–10-bar entries;
   - live: about 0 of +574 L.

   Keep cross+5min. Section 11 gives the shadow-test proposal.

## 1. Data and definitions

**MACD.** Same as the lane (`engine/src/macd_trader/indicators.py` `IncrementalMACD(12, 26, 9)`; `strategy.py`
`previous_macd <= 0 < macd`):
- 30-min premium bars aligned to 09:15 IST; EMAs seeded with the first close; signal is an EMA(9) of the MACD.
- The cross fires **intrabar** the first time the forming bar's MACD preview goes from ≤ 0 to > 0. This is when the
  price exceeds p\* = (S(1−β) − F(1−α))/(α − β), with α = 2/13 and β = 2/27.
- A cross also requires more than 26 committed bars.

My 30-min aggregation reproduced the earlier simulator's committed MACD state on all 862,476 live bars (0
mismatches).

**Candidate rows.** Every 30-min bar close (slots up to 15:15) on an in-universe day where the committed MACD is < 0.
Label `tte` is the number of 30-min slots (trading clock) until the next intrabar cross on the same contract.
- y5 / y8 / y10 = tte ≤ 5 / 8 / 10.
- The real target is the lane-exit P&L of entering now at that bar's close.

**Exits.** Smart-lane rules:
- 40% hard stop; trail 30% once +40%; +100% target; staged one-lot exits at +30/50/75%;
- 4-session cap; expiry flatten at 15:10;
- Rs 1 L whole-lot tickets (≤ 125 lots); 5 bps slippage per side.

Invalidation variants:
- `std` (lane): the first closed bar with MACD < 0 while MFE < 10% exits.
- `grace`: invalidation applies only after a closed bar has had MACD > 0. This is required for any early entry,
  because std would cut it at the next close.
- `grace+T10`: grace, plus an exit at the close of the 10th bar after entry if no closed bar has had MACD > 0
  (the "false positive cut").

| | Live project | TradeBot 2025–26 |
|---|---|---|
| period | 25 Aug – 1 Oct 2026 (27 sessions; OOS 4 Sep – 1 Oct, 19) | trading days 28 Mar 2025 – 30 Mar 2026; universe effective May 2025 – Feb 2026 (223 days); OOS Jul 2025 – Feb 2026 (8 months, 163 days) |
| bars | option minute bars (historical.sqlite3, from 1 Aug) aggregated to 30 min; **exits simulated on minute bars** with the earlier `lane_replay.Contract.exit_sim` (extended with timeout) | 30-min OHLCV + OI + IV parquet; bars with volume 0 dropped (TradeBot carries prices forward on them); **exits on 30-min OHLC** (open → low → high → close on up bars, adverse order) |
| universe | the earlier ATM universe (`universe.pkl`, 3,442 contracts, 426 per day), not expiry day | per underlying/day, no look-ahead: reference = previous spot close; nearest monthly expiry with ≥ 7 calendar days left; CE = lowest strike ≥ ref, PE = highest ≤ ref, among strikes with ≥ 7 traded bars the previous session; \|K/ref − 1\| ≤ 3% (median 0.8%). 32,422 contract-days (≈ 165/day), 6,715 contracts, 211 underlyings |
| candidate rows | 82,402 | 244,835 |
| tradable crosses | 3,497 | 11,750 |
| cross baseline | **d5 = cross + 5 min (the lane)**; also x0 (at the cross tick) | **x0 = intrabar at p\*** (or the open if it gapped above p\*). This is optimistic for the baseline, since cross+5min cannot be simulated on 30-min bars. Also xc = close of the cross bar if MACD > 0 |
| extra features | – | IV (level, Δ1, Δ13, z20), OI Δ1 / Δ13 |

**Features** (49 live / 55 TradeBot, all at the bar close; directional spot/market features signed by side):
- *Own MACD*: `dist_pstar` (p\*/price − 1), `macd_pct`, `sig_pct`, `hist_pct`, `hist_slope`, `hist_acc`,
  `macd_slope`, `proj_bars` (−MACD / slope), `macd_rng` (MACD / its 20-bar range), `bars_since_xdown`,
  `dip_depth` (the minimum MACD in the episode / price), `dip_recov`, `bars_since_htrough`, `hist_up_n`.
- *Premium*: `o_z20`, `o_dd20` (drawdown from the 20-bar high), `o_r1/3/13`, `o_rsi` (RSI14 − 50), `log_premium`,
  `o_vrel`.
- *Spot*:
  - 30-min MACD level / histogram / histogram slope;
  - 2-h MACD as MACD(48, 104, 36) on 30-min bars: level, histogram, slope;
  - daily MACD preview at the current price: level and histogram;
  - `s_z20`, `s_r1/3/13`, `s_retp_sig` (move from the previous close / 20-day sigma), `moneyness`.
- *Opposite side*: the mirror contract's MACD and histogram slope (negated), and whether it is > 0.
- *Market*: Nifty move/sigma, Nifty 30-min and 2-h histograms, breadth (share of F&O stocks above the previous
  close), sector return and stock-minus-sector return.
- *Time*: time of day, DTE, day of week, `is_index`.

Live caveat: the daily spot MACD is immature (spot minute data starts 3 Aug).

**Robustness cap.** TradeBot has a few bad or stale prints (for example a Rs 0.05 "ATM" HINDUNILVR print that returned
1,060×; IDEA on 26 Nov 2025 at +1,200–1,700%). Policy results below winsorise each trade at **+300% of its cost**
(`EC_CAP=3`). Raw results are in `eval_*_raw.log`. The cap changes TradeBot policy P&L by 0–35 L and no conclusion.
The upper-bound table shows a capped column.

## 2. Step 1: the upper bound (perfect foresight, by k)

Entry at the close of the last bar ≤ k slots before the bar in which the cross happened, with grace exits. The
common subset is crosses where all 12 earlier entries exist. **These rows know the cross is coming, so they are not
tradable.**

**TradeBot** (11,284 crosses, 223 days):

| entry | trades | win | median ret | P&L L, 5 bps/side | P&L L, no costs | P&L L, +0.5% round-trip extra | P&L L capped +300%/trade | daily t | + days | CE / PE L |
|---|---|---|---|---|---|---|---|---|---|---|
| at the cross x0, std (baseline) | 11,284 | 29% | −0.057 | 29.9 | 39.2 | −26.8 | 20.2 | 0.37 | 48% | 30.3 / −0.4 |
| at the cross, grace | 11,284 | 39% | −0.111 | 36.3 | – | −20.3 | 26.6 | 0.32 | 45% | 5.2 / 31.1 |
| close of the cross bar, std | 6,997 | 37% | −0.124 | −70.9 | −64.1 | −106.0 | −70.9 | −0.95 | 41% | −3.3 / −67.5 |
| k = 1 (30 min) | 11,284 | 46% | −0.051 | 753.2 | 769.9 | 696.6 | 716.5 | 5.90 | 63% | 372.5 / 380.8 |
| k = 2 | 11,284 | 54% | +0.040 | 1,483.4 | 1,491.9 | 1,426.8 | 1,439.7 | 9.83 | 76% | 729.8 / 753.6 |
| k = 3 | 11,284 | 58% | +0.075 | 1,932.1 | 1,942.9 | 1,875.5 | 1,890.3 | 11.52 | 85% | 959.6 / 972.4 |
| k = 4 | 11,284 | 61% | +0.097 | 2,229.3 | 2,246.6 | 2,172.7 | 2,209.6 | 12.24 | 89% | 1,111.7 / 1,117.6 |
| **k = 5 (2.5 h)** | 11,284 | 62% | +0.109 | **2,395.7** | 2,409.3 | 2,339.1 | 2,368.5 | 12.77 | 91% | 1,204.7 / 1,191.0 |
| k = 6 | 11,284 | 63% | +0.119 | 2,546.9 | 2,558.3 | 2,490.3 | 2,520.7 | 13.06 | 91% | 1,281.7 / 1,265.2 |
| k = 7 | 11,284 | 63% | +0.124 | 2,604.1 | 2,625.4 | 2,547.6 | 2,584.0 | 13.27 | 90% | 1,325.4 / 1,278.7 |
| **k = 8 (4 h)** | 11,284 | 64% | +0.122 | **2,623.6** | 2,639.4 | 2,567.1 | 2,605.8 | 13.19 | 93% | 1,347.6 / 1,276.0 |
| k = 9 | 11,284 | 63% | +0.124 | 2,641.6 | 2,658.3 | 2,585.1 | 2,623.8 | 13.35 | 94% | 1,346.7 / 1,295.0 |
| **k = 10 (5 h)** | 11,284 | 63% | +0.121 | **2,607.8** | 2,633.9 | 2,551.2 | 2,586.3 | 12.97 | 93% | 1,327.6 / 1,280.2 |
| k = 12 | 11,284 | 62% | +0.118 | 2,507.8 | 2,528.5 | 2,451.2 | 2,485.4 | 12.96 | 92% | 1,305.2 / 1,202.6 |

**Live** (3,419 crosses, 27 days):

| entry | trades | win | median ret | P&L L, 5 bps | P&L L, no costs | +0.5% rt | capped | daily t | + days | CE / PE L |
|---|---|---|---|---|---|---|---|---|---|---|
| at the cross x0, std | 3,419 | 34% | −0.057 | 116.7 | 120.2 | 99.5 | 105.1 | 1.78 | 56% | −115.9 / 232.6 |
| at the cross, grace | 3,419 | 46% | −0.079 | 163.8 | 167.1 | 146.7 | 150.6 | 1.97 | 56% | −153.7 / 317.5 |
| **cross + 5 min (lane)** | 2,450 | 39% | −0.056 | **121.5** | 123.0 | 109.3 | 117.7 | 2.28 | 59% | −81.4 / 202.9 |
| k = 1 | 3,419 | 57% | +0.071 | 592.8 | 595.1 | 575.7 | 542.4 | 4.01 | 100% | 1.8 / 591.0 |
| k = 2 | 3,419 | 63% | +0.131 | 780.5 | 781.7 | 763.4 | 738.3 | 5.26 | 100% | 122.4 / 658.1 |
| k = 3 | 3,419 | 67% | +0.163 | 940.1 | 944.4 | 923.0 | 893.3 | 5.51 | 100% | 192.4 / 747.7 |
| **k = 4** | 3,419 | 67% | +0.173 | **980.3** | 983.1 | 963.2 | 940.4 | 5.65 | 100% | 235.7 / 744.6 |
| k = 5 | 3,419 | 66% | +0.166 | 957.5 | 957.7 | 940.4 | 926.6 | 6.08 | 100% | 249.6 / 708.0 |
| k = 8 | 3,419 | 64% | +0.149 | 852.8 | 850.9 | 835.7 | 835.1 | 7.08 | 100% | 272.3 / 580.4 |
| k = 10 | 3,419 | 63% | +0.133 | 808.7 | 808.9 | 791.6 | 791.8 | 6.15 | 100% | 259.0 / 549.7 |
| k = 12 | 3,419 | 63% | +0.138 | 807.8 | 813.0 | 790.7 | 793.2 | 6.39 | 100% | 271.9 / 535.9 |

**Same-day subset** (entry bar on the cross day, 5 bps), comparing k against the cross baselines on the same crosses:

| k | TradeBot: crosses / P&L L (x0 on same crosses) | live: crosses / P&L L (d5 on same crosses) |
|---|---|---|
| 1 | 10,073 / 293.8 (−21.6) | 2,858 / 304.5 (112.7) |
| 3 | 7,608 / 826.7 (−15.3) | 2,103 / 389.1 (83.9) |
| 5 | 5,706 / 819.4 (−2.2) | 1,619 / 313.0 (59.7) |
| 8 | 3,332 / 519.8 (−23.4) | 990 / 194.2 (34.8) |
| 10 | 1,978 / 298.6 (−20.8) | 605 / 118.6 (26.4) |

Reading:
- **Costs are irrelevant to the bound.** 5 bps per side and even an extra 0.5% round trip move it by only 2–4%.
- **The gain from k = 1 to k = 5–10 is large on TradeBot (×3.2–3.5) and modest on live (×1.4–1.6, peaking at k = 4).**
  - 30 minutes of foresight already beats the cross by 6–25×, because it buys the bar that pushes the premium through
    p\* and any gap.
  - The median trade gains little from that 30 minutes (TradeBot median return −0.051 at k = 1 vs −0.057 at the
    cross). The 30-minute advantage lives in the right tail.
  - At k ≥ 3 the median return is clearly positive (+0.08 to +0.17).
- **Head start** (cross price / entry price − 1, median [IQR]):

  | k | TradeBot | live |
  |---|---|---|
  | 1 | +1.1% [0, 5.7] | +2.4% [0, 11.9] |
  | 5 | +14.6% [6.2, 27.1] | +20.7% [9.1, 39.7] |
  | 10 | +18.9% [8.4, 32.5] | +24.3% [10.5, 45.1] |

  At k = 10, the MACD negative episode is shorter than k for 23% (TradeBot) and 29% (live) of crosses. There the
  "early" entry was made while MACD was still positive.
- **Bottom of the premium** (lowest low in the negative-MACD episode, ≤ 26 bars back):

  | | TradeBot | live |
  |---|---|---|
  | bars before the cross, median [P25, P75] | 10 [5, 16] | 9 [4, 14] |
  | share within 0 / 1–2 / 3–5 / 6–10 / 11+ bars | 2.5 / 10.4 / 14.5 / 23.6 / 49.0% | 3.2 / 12.7 / 17.1 / 22.1 / 44.9% |
  | rise bottom → cross price, median | +41% | +54% |
  | cross price → max high in the next 27 bars, median | +26% | +34% |
  | share of the bottom → peak move done before the cross, median | 0.52 | 0.49 |

  On live, CE moves after the cross are much smaller (median +20%) than PE moves (+49%).

## 3. Steps 2–4: prediction quality (walk-forward, out of sample)

**Walk-forward setup.**
- TradeBot walks forward by month: train on all earlier months, test Jul 2025 – Feb 2026.
- Live walks forward by day: test the 19 days from 4 Sep, train on earlier days.
- Purging:
  - classifier and hazard training rows need g + 10 < the first slot of the test unit;
  - regressor rows need their exit before it.
- Models: shallow HGB (depth 3, lr 0.05, 150 iterations, leaf 200 TradeBot / 60 live, l2 1).
- The hazard model `HZ` is a discrete-time HGB: one row per decision × step j = 1..10 until the event, with features
  plus j. P(≤ h) = 1 − ∏(1 − λ_j).

**Base rates of the candidate state.** Mean clipped return of entering now (grace exits) by actual bars-to-cross:

| tte | TradeBot share | TradeBot ret | live share | live ret (CE / PE) |
|---|---|---|---|---|
| 1 | 4.1% | +0.023 | 3.5% | +0.092 (−0.04 / +0.24) |
| 2 | 3.1% | +0.119 | 2.6% | +0.213 |
| 3–4 | 5.6% | +0.196 | 4.7% | +0.297 |
| **5–7** | 7.7% | **+0.265** | 6.4% | **+0.347** (+0.24 / +0.55) |
| **8–10** | 7.0% | **+0.306** | 5.7% | **+0.355** |
| 11–20 | 18.0% | +0.275 | 14.2% | +0.302 |
| 21–39 | 21.6% | +0.030 | 16.5% | +0.002 |
| no cross ≤ 39 | 32.9% | −0.353 | 46.4% | −0.302 |

The 5–10-bar band is the most valuable place to enter (+0.27 to +0.36). A 1-bar cross pays almost nothing, which is
why the earlier 30-minute model did not help. The no-cross third to half of the rows lose 30–35%.

### 3.1 Classification

| score | label | TradeBot base / AUC / mean monthly AUC (months > 0.5) / CE–PE AUC / Brier (base-rate Brier) | live base / AUC / mean daily AUC (days > 0.5) / CE–PE / Brier |
|---|---|---|---|
| C5 | cross ≤ 5 | 0.155 / **0.922** / 0.922 (8/8) / 0.920–0.924 / 0.071 (0.131) | 0.125 / **0.925** / 0.928 (19/19) / 0.931–0.915 / 0.060 (0.109) |
| HZ5 | | 0.917 | 0.919 |
| C8 | cross ≤ 8 | 0.229 / **0.871** / 0.871 (8/8) / 0.862–0.880 / 0.111 (0.177) | 0.183 / **0.877** / 0.884 (19/19) / 0.884–0.870 / 0.095 (0.149) |
| HZ8 | | 0.869 | 0.875 |
| C10 | cross ≤ 10 | 0.275 / **0.842** / 0.842 (8/8) / 0.830–0.854 / 0.134 (0.199) | 0.217 / **0.850** / 0.860 (19/19) / 0.858–0.843 / 0.114 (0.170) |
| HZ10 | | 0.842 | 0.850 |

The hazard model adds nothing over the direct classifiers. Calibration is good: TradeBot C10 deciles run from
pred/obs 0.02/0.01 to 0.83/0.85, live from 0.01/0.02 to 0.78/0.79.

**Can it see a cross that is genuinely 5–10 bars away?** AUC on subsets of out-of-sample rows:

| score | 5–10 vs no cross ≤ 39 | 5–10 vs 11–39 | 5–10 vs all > 10 | 1–4 vs > 10 | Spearman(score, tte) when tte ≤ 10 |
|---|---|---|---|---|---|
| C10 TradeBot / live | 0.778 / 0.783 | 0.730 / 0.735 | **0.752 / 0.766** | 0.946 / 0.941 | −0.71 / −0.67 |
| HZ10 TradeBot / live | 0.776 / 0.780 | 0.729 / 0.737 | 0.751 / 0.765 | 0.946 / 0.943 | −0.71 / −0.69 |
| C5 TradeBot / live | 0.762 / 0.768 | 0.722 / 0.737 | 0.740 / 0.757 | 0.951 / 0.951 | −0.74 / −0.73 |

The headline AUCs come from imminent crosses. For a genuine 2.5–5-hour lead, discrimination drops to 0.75–0.77. The
score is strongly monotone in nearness: high probability means the cross is 1–2 bars away.

**Precision and the make-up of flagged rows** (C10, TradeBot; live in brackets):

| threshold | precision | recall | rows flagged | of flagged: tte 5–10 / tte 1–4 / no cross ≤ 10 |
|---|---|---|---|---|
| 0.3 | 0.571 (0.549) | 0.709 (0.670) | 34% (27%) | 0.22 / 0.35 / 0.43 (0.20 / 0.35 / 0.45) |
| 0.5 | 0.734 (0.713) | 0.472 (0.451) | 18% (14%) | 0.18 / 0.55 / 0.27 (0.17 / 0.54 / 0.29) |
| 0.7 | 0.877 (0.872) | 0.266 (0.258) | 8% (6%) | 0.09 / 0.79 / 0.12 (0.09 / 0.78 / 0.13) |
| 0.9 | 0.980 (0.969) | 0.121 (0.126) | 3% (3%) | 0.02 / 0.96 / 0.02 (0.02 / 0.95 / 0.03) |

**Lead time achieved.** The first trigger in the 12 bars before each out-of-sample tradable cross:

| trigger | crosses with a trigger (TradeBot / live) | median lead in bars | share of those with lead ≥ 5 | trigger precision (≤ 10) | triggers per cross |
|---|---|---|---|---|---|
| C10 ≥ 0.5 | 92.4% / 86.9% | 4 / 4 | 49.6% / 46.5% | 0.734 / 0.713 | 3.7 / 3.3 |
| C10 ≥ 0.7 | 86.9% / 80.6% | 3 / 3 | 26.1% / 27.5% | 0.877 / 0.872 | 1.7 / 1.5 |
| C10 ≥ 0.9 | 70.9% / 65.0% | 1 / 1 | 11.3% / 13.4% | 0.980 / 0.969 | 0.7 / 0.7 |
| HZ10 ≥ 0.5 | 92.4% / 87.2% | 4 / 4 | 49.5% / 46.7% | 0.730 / 0.712 | 3.7 / 3.3 |
| C5 ≥ 0.5 | 89.9% / 84.2% | 3 / 3 | 29.7% / 29.7% | 0.841 / 0.834 | 2.1 / 1.9 |

A 4-bar median lead is available only at p ≥ 0.5. There, more than a quarter of the triggers never cross within 10
bars, and those cost about −0.35 each.

### 3.2 Does the score rank the P&L of entering now?

Within-unit Spearman IC (t over units); quintile mean clipped return Q1 → Q5:

| score vs return | TradeBot IC (t) | TradeBot Q1 … Q5 | live IC (t) | live Q1 … Q5 |
|---|---|---|---|---|
| C10 vs grace | +0.038 (+3.4) | −.025 +.003 +.004 +.005 +.001 | +0.088 (+2.3) | −.092 −.072 −.057 −.037 +.013 |
| C10 vs grace+T10 | −0.034 (−1.9) | −.013 +.002 +.004 +.004 +.000 | +0.031 (+0.8) | −.048 −.052 −.037 −.020 +.023 |
| HZ10 vs grace | +0.036 (+3.2) | | +0.080 (+2.2) | |
| Rg (regressor) vs grace | **+0.067 (+6.1)** | −.044 −.010 +.004 +.006 +.033 | +0.043 (+0.7) | −.036 −.061 −.084 −.052 −.012 |
| Rt (regressor) vs grace+T10 | **+0.074 (+4.1)** | −.044 −.003 +.016 +.009 +.021 | +0.093 (+1.4) | −.036 −.036 −.059 −.037 +.034 |

- Calibration deciles of C10 vs the grace return are flat on TradeBot: −0.038 in the lowest decile, then about
  +0.001 to +0.007, and −0.010 in the top decile.
- On live, only the top decile is positive (+0.028).
- **P(cross) separates "worthless" from "breakeven", not "breakeven" from "profitable".** The rows that pay (5–20
  bars out) sit in the middle deciles together with many non-crossers.

## 4. Step 5: policies out of sample vs the cross entry

**Policy setup.**
- Thresholds and rules are chosen per test unit from inner 3-block cross-fitted predictions on training data only,
  by maximising training replay P&L. "Never" is allowed: early-only then does nothing and the hybrid becomes the
  baseline.
- Per contract: no overlapping positions, and an early position blocks the cross entry while it is open.
- Book: Rs 50 L, Rs 1 L tickets, integrated time-ordered replay. An unfunded entry does not block its contract, and
  among same-time entries the cross goes first, then triggers by score.
- t_d = paired t of the daily P&L difference vs the baseline. "tte at entry" = median bars to the next cross at the
  early entries, and the share crossing within 10.
- Trades are capped at +300%.

### 4.1 TradeBot (Jul 2025 – Feb 2026, 163 days, 9,895 tradable crosses)

| policy | trades (early) | hit | P&L L | ΔP&L L (t_d) | daily t | CE L / PE L | max DD L | book 50 L (DD) | tte at entry, share ≤ 10 |
|---|---|---|---|---|---|---|---|---|---|
| **BASELINE x0 (cross)** | 8,747 (0) | 29% | **−6.2** | – | −0.09 | 21.5 / −27.7 | 99.5 | 17.8 (52.6) | – |
| ref: every candidate, grace (early-only) | 14,839 | 45% | 77.6 | +83.8 (+0.49) | 0.49 | 70.0 / 7.6 | 167.4 | −39.1 | 28, 0.21 |
| ref: every candidate, grace+T10, hybrid | 28,180 (27,642) | 42% | 102.2 | +108.4 (+0.67) | 0.66 | 73.5 / 28.7 | 132.2 | 18.5 | 25, 0.26 |
| LOOK-AHEAD hybrid gated by tte 5–10, grace | 8,449 (5,736) | 61% | 1,940.7 | +1,946.8 (+14.4) | 14.1 | 952.6 / 988.1 | 4.5 | 1,890.5 | 10, 1.00 |
| LOOK-AHEAD hybrid gated by y10, grace | 7,752 (7,244) | 70% | 2,155.5 | +2,161.7 (+15.2) | 15.1 | 1,044.7 / 1,110.8 | 2.5 | 2,104.8 | 10, 1.00 |
| early C5 grace | 2,756 | 38% | −99.1 | −92.9 (−1.54) | −2.53 | −9.7 / −89.4 | 116.6 | −36.6 | 3, 0.81 |
| early C10 grace | 4,596 | 45% | −56.8 | −50.7 (−0.64) | −1.01 | 1.1 / −57.9 | 103.9 | −41.3 | 10, 0.51 |
| early C10 grace+T10 | 10,593 | 39% | 54.8 | +61.0 (+0.89) | 0.71 | 38.2 / 16.6 | 86.6 | −19.4 | 9, 0.54 |
| hybrid C10 grace | 8,817 (7,754) | 43% | 47.6 | +53.8 (+0.84) | 0.56 | −22.7 / 70.3 | 136.9 | −3.4 | 6, 0.62 |
| **hybrid C10 grace+T10** | 12,940 (12,317) | 39% | 124.2 | +130.4 (**+2.00**) | 1.43 | 66.3 / 57.9 | 83.5 | −9.7 (63.8) | 9, 0.53 |
| hybrid C8 grace+T10 | 12,086 (11,462) | 38% | 99.8 | +106.0 (+1.89) | 1.21 | 53.2 / 46.7 | 85.0 | −5.8 | 8, 0.57 |
| hybrid HZ10 grace+T10 | 12,730 (12,104) | 38% | 109.2 | +115.4 (+1.83) | 1.29 | 71.3 / 37.9 | 83.9 | 9.3 | 9, 0.54 |
| hybrid C5 grace+T10 | 8,371 (6,299) | 35% | −16.5 | −10.3 (−0.38) | −0.23 | 8.7 / −25.2 | 118.7 | 1.6 | 2, 0.84 |
| hybrid RULE grace+T10 | 14,280 (10,632) | 38% | 132.6 | +138.8 (+1.27) | 1.12 | 119.3 / 13.3 | 97.2 | 55.0 | 15, 0.40 |
| early Rg grace | 3,502 | 48% | 108.0 | +114.2 (+1.28) | 1.73 | 24.8 / 83.2 | 62.1 | 69.6 (38.4) | 25, 0.24 |
| early Rt grace+T10 | 7,068 | 44% | 93.3 | +99.5 (+1.13) | 1.49 | 45.7 / 47.7 | 44.0 | 84.0 (27.1) | 24, 0.27 |
| **hybrid Rt grace+T10** | 15,497 (10,131) | 39% | **151.0** | +157.2 (**+2.24**) | 1.69 | 108.6 / 42.4 | 59.9 | **189.5 (45.3)** | 22, 0.28 |

The other 13 policies range from −86 to +106 L, all with |t_d| ≤ 1.44 (the highest is hybrid Rg grace, +83 L). Full tables are in `eval_tb.log`.

**P&L by month** (L; months beating the baseline):

| policy | Jul | Aug | Sep | Oct | Nov | Dec | Jan | Feb | > base |
|---|---|---|---|---|---|---|---|---|---|
| BASELINE x0 | −12 | −18 | 21 | −37 | −43 | 24 | 72 | −13 | – |
| hybrid C10 grace+T10 | 6 | −37 | 33 | −27 | −35 | 57 | 82 | 44 | 7/8 |
| hybrid HZ10 grace+T10 | −3 | −37 | 34 | −29 | −39 | 60 | 84 | 39 | 7/8 |
| hybrid Rt grace+T10 | 19 | −1 | 4 | −5 | −18 | 68 | 76 | 8 | 7/8 |
| early Rt grace+T10 | 32 | 12 | −7 | 5 | 22 | 26 | 16 | −12 | 6/8 |
| LOOK-AHEAD tte 5–10, grace | 194 | 229 | 217 | 221 | 210 | 339 | 336 | 216 | 8/8 |

- The timeout hybrids win 7 of 8 months, but by small amounts; most of their edge is Dec–Feb.
- Fixed-threshold sensitivity (not a selection; ΔP&L t_d in brackets):
  - C10 hybrid T10: 121 L (+1.9) at 0.3, 67 (+1.6) at 0.5, −18 (−0.4) at 0.7, −22 (−1.2) at 0.9.
  - **Lower thresholds (more, earlier, noisier entries) do better than confident ones.** Confident triggers are
    the 1–2-bar crosses that pay nothing.
  - Rt early: 282 L (+2.7) at a threshold of 0; 16 L at 0.1.

### 4.2 Live (19 days, 4 Sep – 1 Oct; 2,465 tradable crosses, 1,474 lane trades)

| policy | trades (early) | hit | P&L L | ΔP&L L (t_d) | daily t | CE L / PE L | max DD L | book 50 L (DD) | tte at entry, share ≤ 10 |
|---|---|---|---|---|---|---|---|---|---|
| **BASELINE d5 (cross + 5 min)** | 1,474 (0) | 41% | **84.7** | – | 1.86 | −49.5 / 134.2 | 23.2 | 41.2 (15.9) | – |
| ref: every candidate, grace+T10, hybrid | 9,852 (9,684) | 42% | −53.7 | −138.4 (−1.23) | −0.55 | −418.7 / 365.0 | 119.4 | −3.4 | 99, 0.20 |
| LOOK-AHEAD hybrid tte 5–10, grace | 2,147 (1,450) | 68% | 659.0 | +574.3 (+6.12) | 8.21 | 166.9 / 492.2 | 0.0 | 531.1 | 10, 1.00 |
| LOOK-AHEAD hybrid y10, grace | 2,260 (2,103) | 74% | 827.2 | +742.5 (+7.01) | 8.48 | 214.2 / 613.0 | 0.0 | 668.7 | 8, 1.00 |
| early C5 grace | 1,475 | 48% | 74.9 | −9.7 (−0.40) | 1.56 | −67.5 / 142.4 | 36.2 | 30.0 | 2, 0.88 |
| hybrid C5 grace | 1,803 (1,457) | 46% | 88.8 | +4.1 (+0.18) | 1.59 | −77.1 / 165.9 | 38.4 | 45.6 | 2, 0.86 |
| hybrid C10 grace | 1,856 (1,499) | 47% | 75.8 | −8.9 (−0.43) | 1.40 | −77.2 / 153.0 | 43.6 | 43.3 | 2, 0.81 |
| hybrid C10 grace+T10 | 2,280 (1,889) | 43% | 76.4 | −8.3 (−0.33) | 1.36 | −75.9 / 152.3 | 46.1 | 46.1 | 4, 0.68 |
| hybrid HZ10 grace | 1,899 (1,588) | 46% | 82.6 | −2.1 (−0.09) | 1.54 | −81.1 / 163.6 | 45.4 | 46.5 | 2, 0.82 |
| hybrid HZ10 grace+T10 | 2,127 (1,827) | 44% | 83.0 | −1.6 (−0.07) | 1.55 | −81.7 / 164.8 | 43.3 | 54.0 | 3, 0.76 |
| early RULE grace+T10 | 2,319 | 38% | −71.3 | −156.0 (**−2.94**) | −1.15 | −153.7 / 82.4 | 106.4 | −15.5 | 12, 0.48 |
| hybrid RULE grace | 2,817 (2,251) | 39% | −101.0 | −185.7 (**−2.56**) | −1.24 | −229.7 / 128.8 | 127.7 | −10.3 | 21, 0.41 |
| hybrid Rg grace | 2,583 (1,543) | 44% | 75.7 | −9.0 (−0.11) | 0.89 | −83.9 / 159.6 | 87.0 | 5.0 | 32, 0.21 |
| hybrid Rt grace+T10 | 3,887 (2,813) | 43% | 55.1 | −29.6 (−0.35) | 0.67 | −131.2 / 186.2 | 100.6 | 21.6 | 99, 0.22 |

On live, training picks high thresholds (0.5–0.9). The model hybrids therefore end up entering only 2–4 bars
early, which is the earlier study's 30-minute result again. All of them are within noise of the lane:
- the fixed-threshold sweep peaks at +107 L (t_d +0.9), for HZ10 hybrid T10 at 0.7;
- CE loses under every policy (−49.5 L in the baseline, −63 to −230 L in early policies);
- every early policy is long the put-favouring regime.

## 5. Feature importance

Permutation importance on a single split (TradeBot: train before Dec 2025, test Dec–Feb, 68k rows; live: train on
the first 19 universe days, test the last 8). Each cell is the drop in metric when the feature is permuted:

| feature | C10 AUC drop TradeBot / live | lead-subset AUC drop (tte 5–10 vs > 10) TradeBot / live |
|---|---|---|
| `o_rsi` (premium RSI14) | **0.117 / 0.077** | **0.109 / 0.088** |
| `dist_pstar` (distance to the cross price) | 0.016 / 0.022 | 0.014 / 0.025 |
| `proj_bars` | 0.004 / 0.003 | 0.002 / 0.001 |
| `tod` | 0.003 / 0.001 | 0.005 / 0.001 |
| `breadth` | 0.002 / 0.004 | 0.004 / 0.007 |
| Nifty 30-min / 2-h histograms | – / 0.006, 0.005 | – / 0.006 |
| `opp_macd` (mirror contract) | 0.001 / 0.004 | 0.001 / 0.007 |
| spot 2-h slope | 0.000 / 0.004 | 0.000 / 0.006 |
| everything else, including IV, OI, daily spot MACD, sector | ≤ 0.001 | ≤ 0.002 |

- **Single-feature AUCs on y10** (out of sample, TradeBot / live): `o_rsi` 0.83 / 0.85, `dist_pstar` 0.82 / 0.83
  (inverted), `macd_pct` 0.80 / 0.81, `o_z20`, `o_dd20`, `s_z20` and `o_r13` 0.77–0.78. The same features on the lead
  subset score 0.69–0.77.
- **The regressor Rt** gets its TradeBot ranking mainly from `dte` (IC drop 0.080), then Nifty sigma move, breadth,
  sector return, mirror-contract slope and moneyness. It is a "which candidate state pays" model (contract life,
  market tone), not a cross-timing model, and its live IC on the same split is −0.107.
- **Underlying and higher-timeframe confirmation add nothing beyond the premium's own state.** That covers spot 30-min
  / 2-h / daily MACD, OI and IV changes, and the opposite-side MACD. Their information is already in the premium RSI
  and distance-to-cross. The 81-rule family built from them, chosen on training data, was the worst policy family on
  live (t_d −2.3 to −2.9) and only +62 to +139 L on TradeBot (t_d ≤ 1.27).

## 6. Step 6: multiple testing and stability

**Configurations tried.**
- Upper bound: 15 entry variants × 2 cost levels × 2 datasets, plus a same-day subset (12 × 2).
- Models: 7 scores (C5, C8, C10, HZ, Rg, Rt, plus an 81-rule family) × 27 folds, each with 3 inner folds.
- Policies:
  - 24 train-chosen per dataset (48);
  - 8 references per dataset;
  - 112 fixed-threshold sweep cells per dataset;
  - each run twice: raw and capped at +300%.
- Prediction diagnostics: about 60 AUC / IC cells. Permutation importance: 55 + 49 features × 3 metrics.

That is roughly **700 evaluated cells**. About 35 nominal |t| > 2 results are expected by chance.

**Family-wise tests on the 24 train-chosen policies per dataset** (paired daily t vs the baseline):
- TradeBot smallest p: hybrid Rt 0.026; hybrid C10 T10 0.047; hybrid C8 T10 0.061; hybrid HZ10 T10 0.069. Raw
  (uncapped): hybrid Rt p 0.014, +181 L.
- **None pass BH at 5%. None pass Bonferroni** (threshold 0.0021).
- Live: the only nominal p < 0.05 are the rule policies, and they are worse than the baseline (p 0.009–0.031).

**Stability.**
- The classifiers are very stable: AUC > 0.5 in 8/8 months and 19/19 days, and AUC by month ranges 0.82–0.86.
- The P&L results are not stable. Training-chosen thresholds swing between 0.3 and 0.9 from unit to unit on live. On
  TradeBot they settle at 0.3–0.4 for the timeout hybrids.
- The TradeBot "winners" depend on Dec 2025 – Feb 2026, and their Rs 50 L books are negative (−9.7 L) for C10 hybrid.
  Only the Rt hybrid has a book above the baseline (189.5 vs 17.8 L).

**Regimes.**
- TradeBot spans 8 months with both sides mixed: baseline CE +21.5 / PE −27.7 L; 7 of 8 months had a monthly
  baseline below ±45 L.
- Live is one put-favouring month: lane CE −49.5 / PE +134.2 L.
- The bound's live CE number (k = 1: +1.8 L) shows that even perfect 30-minute foresight barely rescues CE there.
  At k = 5 it does (+250 L).

## 7. Caveats

- **The TradeBot simulation is coarser than live.**
  - 30-min OHLC exits with adverse intrabar ordering.
  - The cross baseline is x0 at the exact p\*, not cross+5min. This is optimistic for the baseline; the closed-bar
    entry is −70.9 L.
  - Monthly contracts only, ≥ 7 DTE, and a stricter liquidity universe (≈ 165 contract-days a day vs 426 live).
  - Zero-volume bars are dropped.
- **The TradeBot baseline itself has no edge in 2025–26** (+29.9 L over 11,284 crosses, daily t 0.37). Any policy
  only needs to beat zero there, and the early policies' "+100–150 L" amounts to ≈ +1% per trade on ~10k trades.
- **Bad prints exist in TradeBot.** The +300% cap handles them, and the raw and capped conclusions are the same.
- **The bound uses foresight.** In 23–33% of crosses at k = 10, the negative MACD episode was shorter than k, so the
  "early" entry happened while MACD was still positive.
- **Live is short.** 27 days, one regime, intraday grace holds up to 4 sessions, and open positions at 1 Oct are
  marked at the last close or settlement.
- **Rs 1 L flat tickets**, with no dispersion sizing and no ranker skip.
- **No sector map** for 9 TradeBot stocks, and the live daily spot MACD has ≤ 43 days of history.

## 8. Verdict on the user's hypothesis

- **"The cross needs to be predicted 5–10 bars earlier to capture the full move."** Mostly true as a statement
  about where the money is:
  - the premium bottoms a median 9–10 bars before the cross;
  - half the bottom-to-peak move is done by the cross;
  - with foresight, entries 5–10 bars early are worth 2,400–2,640 L vs 753 L at 30 minutes on TradeBot (live: 960
    vs 593 L, peaking at 4 bars).
- **"30 min before is as good as the cross itself."** True for what can actually be predicted, false for foresight.
  Thirty minutes of foresight is still worth 6–25× the cross entry. But the 30-minute-ahead prediction is
  near-certain only where the payoff is small. The earlier study (P(cross ≤ 30 min) uncorrelated or negatively
  correlated with payoff) and this one agree on that.
- **Can the 5–10-bar cross be predicted well enough to trade?** No, not with these features.
  - Discrimination of a genuine 5–10-bar lead is AUC ~0.75–0.77.
  - Triggers firing that early have about 1 in 4 to 1 in 2 false positives, each costing about 35%.
  - Out of sample, the best train-chosen policies make +0 to +157 L against an oracle prize of ~1,950 L (TradeBot)
    and ~574 L (live). None survives multiple-testing correction, and the live month shows no gain at all.

## 9. Recommendations

1. **Keep the cross+5min entry on the MACD lane.** Do not deploy a 5–10-bar early-entry model.
2. **If anything is shadow-tested, use the TradeBot-best structure.** That is a hybrid with grace + 10-bar timeout
   and a **low** gate (C10 ≥ 0.3–0.4, or a P&L regressor ≥ 0–0.05). It was positive in 7/8 TradeBot months but
   p 0.03–0.05 before correction and flat on live.
   - Log every 30-min bar close in the candidate state with the features above, the bars to the next cross, and the
     grace / T10 outcomes. That is ≈ 3,000 rows per day, at zero risk.
   - Promote only with t_d > 2 over ≥ 60 new sessions spanning a call-favouring month.
3. **Do not use high-confidence early triggers (p ≥ 0.7).** They fire 1–2 bars before the cross, where an entry pays
   ≈ +0.02 to +0.12 and the false positives are the worst trades.
4. **Do not add spot / 2-h / daily MACD confirmation rules or IV / OI to a cross-timing model.** They add nothing
   beyond the premium RSI and distance-to-p\*, and the rule family was the worst performer on live.
5. **A more promising research direction than cross timing.** The look-ahead table shows that the payoff of the
   candidate state is mostly "will this premium be 15–25% higher in 2–5 hours". That is a direct return forecast, and
   the TradeBot regressor (IC +0.07, t 4–6) beat the cross classifiers at it. It did not transfer to live (IC +0.09,
   t 1.4 on 19 days; −0.11 on the permutation split). Re-test it with more live history, and with the regressor
   label used directly rather than as a cross proxy.

## 10. Scripts and outputs

Folder: `/private/tmp/claude-501/-Users-ramachandran-MACD-mini-ADV/77c677ac-a2ad-4b4d-977f-6fcea088fa80/scratchpad/early_cross/`.

Runners:
- `tb.sh`: `tradebot-backend` image with pandas/pyarrow; TradeBot data mounted read-only at `/d`; no network.
- `run.sh`: engine image with the data volume (databases opened `mode=ro`) and the sklearn pylib from the earlier
  research at `/r/pylib`; no network; `EC_CAP` env var (default 3, i.e. +300% per trade; 0 = raw).

| script | does | output |
|---|---|---|
| `core.py` | lane MACD / p\* / cross flags, own-premium features, bars-to-cross, 30-min lane-exit simulator `sim30` | – |
| `tb_build.py` | TradeBot universe, contract series, candidate rows, crosses, x0 / xc / x0g, bound k = 1..12, bottoms | `tb_panel.pkl`, `tb_build.log` |
| `tb_rows.py` + `assemble.py` | spot / market / mirror features and flat arrays | `rows_tb.npz` |
| `live_build.py` | the same for live, on minute bars (reuses `feature_study/lane_replay.py` and `precross_model/grid.npz`) | `rows_live.npz`, `live_build.log` |
| `bound.py tb\|live` | Step 1 tables | `bound_tb.log`, `bound_live.log` |
| `labstats.py` | label base rates and returns by bars-to-cross | `labstats.log` |
| `polib.py` / `polgrid.txt` | shared data, rules, replay, integrated book, cap | – |
| `model.py tb\|live` | walk-forward C5/C8/C10, hazard, Rg/Rt, inner cross-fit threshold / rule choice | `model_*.pkl`, `model_*.log` (`*_raw` = uncapped run) |
| `evalp.py tb\|live` | prediction quality, lead time, policies, books, by-unit P&L, sweep, FDR | `eval_tb.log`, `eval_live.log` (`*_raw.log` uncapped) |
| `perm.py tb\|live` | permutation importance | `perm_tb.log`, `perm_live.log` |
| helpers | `peek_tb.py`, `peek2.py`, `peek_live.py`, `tb_check.py`, `chk_ret.py`, `chk_ret2.py`, `dbg_book.py`, `sectors.json` (engine sector map) | |
