# Supertrend and pivot levels on the option premium as an early entry before the 30-min MACD zero cross

Study date 3 Oct 2026. Analysis only. Nothing under the project was changed except this file, and every SQLite file
was opened read-only (`mode=ro`) from throwaway containers. This follows `early_cross_5_10_bars.md`, which found:
- perfect-foresight entries 4–10 bars before the cross are worth 3–25× the cross entry;
- the premium bottoms a median 9–10 bars before the cross;
- models reach only AUC 0.75–0.78 for crosses genuinely 5–10 bars away.

The question here is whether classic chart triggers on the premium itself give that early entry better than the
models did:
- Supertrend flips on 5/15/30-min bars;
- floor, Camarilla and CPR pivots, previous-day high/low and the opening range;
- combinations of these with RSI and the MACD histogram.

Rows, exits, costs, walk-forward folds and baselines are reused unchanged from the earlier study.

## 0. Bottom line

1. **Every trigger lies on the same lead-versus-precision frontier as the models.** No trigger gives both a long lead
   and good precision:

   | trigger type | median lead | share crossing within 10 bars | recall in the 4–10-bar window |
   |---|---|---|---|
   | 5-min Supertrend flips (live) | 10–11.5 bars | 26–31% | 33–44% |
   | 30-min Supertrend(·, 3) flips | 1.8–2.5 bars | 72–77% | 8–11% |
   | pivot breaks (P / TC / opening range) | 3.5–9.7 bars | 34–61% | 15–27% |
   | support bounces (S1 / L3 / PDL) | 11.5–16.5 bars | 16–28% | 12–22% |

   - "Recall" is the share of crosses preceded by a trigger in the 4–10-bar window.
   - The model in the earlier study had a median lead of 4 bars at precision 0.71–0.73. That is about where the
     P/TC breaks sit.
   - When a trigger does fire 4–10 bars before a real cross, entering there makes +0.05 to +0.44 per trade (mostly +0.13 to +0.36) against
     −0.01 to +0.10 for the cross entry on the same crosses. This is the foresight prize, and it is real.
   - The false positives cost −0.06 to −0.21 each, and they are 21–83% of the fires.
2. **Live (19 out-of-sample days): nothing beats cross+5min.**
   - Of 1,434 fixed cells (239 triggers × 3 exits × early/hybrid), 309 are nominally significantly *worse* and 2 are
     better (+11 L, `sp30_brkR2` hybrid).
   - All 10 walk-forward-selected policies lose to the 88.0 L baseline, by −20 to −115 L (t_d −0.6 to −2.8).
   - The triggers do carry timing information: they beat random entries made at the same rate. But random entries
     in the candidate state lose −5 to −222 L here, and the triggers only recover part of that.
3. **TradeBot (8 out-of-sample months): small gains, no better than ungated entry or the earlier models.**
   - The walk-forward hybrids made +106 to +117 L against a −8.7 L baseline (t_d 1.94–2.13, p 0.03–0.05):
     - spot-pivot family (`sp30_bncS1` / `sp30_brkTC` with a 10-bar cut);
     - Supertrend-state family (`ST30_14_2_above` / `ST30_7_2_above`).
   - Their Rs 50 L books end at 100–114 L against 30 L for the baseline.
   - They beat same-rate random entries in 30 of 30 draws (random mean +29 to +49 L).
   - But they equal the ungated "enter at every 30-min candidate close" reference (+108.8 L, t_d 0.67) and the
     earlier study's model hybrids (C10 +130 L, Rt +157 L).
   - None of the 570 cells or 10 walk-forward policies passes BH-FDR at 5%.
   - Nothing transfers to live: the TradeBot-chosen structures make −15 to −53 L there.
4. **The triggers capture 0–6% of the perfect-foresight prize out of sample.**
   - TradeBot: at most +116.5 of +1,941 L (6.0%).
   - Live: −20.5% to −3.6% of +562 L.
   - Even the best fixed cells chosen *with hindsight* reach only 8.5% (TradeBot) and 7.6% (live).
5. **As model features they add nothing.** Adding 22 (TradeBot) / 28 (live) Supertrend and pivot features to the
   early-cross walk-forward model changes the 5–10-bar AUC as follows:

   | dataset | BASE → AUG AUC | mean per-unit change |
   |---|---|---|
   | TradeBot | 0.7522 → 0.7522 | −0.0001 (t −0.24) |
   | live | 0.7662 → 0.7667 | −0.0019 (t −0.90) |

   Policy P&L moves by −49 to +52 L, all with |t| ≤ 1.65. The pivot features look strong on their own (`pv_P`
   single-feature lead-subset AUC 0.71–0.72), but they duplicate the premium-RSI and distance-to-cross
   information the model already has.
6. **Verdict: it does not help.**
   - Supertrend and pivot triggers on the premium are coarse, lagging versions of "the premium has started to rise",
     which the model already sees through RSI and distance-to-p\*.
   - Diagnostic (look-ahead, not tradable): on the very contract-days where a breakout or Supertrend trigger fired,
     entering at a *random* time that day would have earned +122 to +942 L (TradeBot) and +20 to +285 L (live) more
     than entering at the trigger. Most of the day's rise had already happened when the breakout or flip printed.
     Support bounces fire near the low and do not show this (−103 to +210 L).
   - Keep cross+5min. Section 9 says what is worth shadow-logging.

## 1. Data, rows and exits (same as the early-cross study)

| | Live project | TradeBot 2025–26 |
|---|---|---|
| period | 25 Aug – 1 Oct 2026, 27 sessions; out of sample 4 Sep – 1 Oct (19 days, test units 8–26) | universe May 2025 – Feb 2026; out of sample Jul 2025 – Feb 2026 (8 months, 163 days) |
| premium bars | 1-min option bars (historical.sqlite3) → 5 / 15 / 30 / 60-min bars aligned to 09:15 | 30-min OHLC parquet (volume-0 bars dropped) → 30 / 60-min bars |
| candidate rows | **every 5 / 15 / 30-min bar close** while the committed 30-min MACD < 0 and the lane has not crossed yet in the forming 30-min bar. 476,078 rows; the 82,402 at 30-min closes match the earlier study row for row | every 30-min close with MACD < 0: 244,835 rows (identical to the earlier study) |
| exits | minute-bar lane simulator (`lane_replay`), smart-lane rules: 40% stop, trail 30% once +40%, +100% target, staged +30/50/75%, 4-session cap, expiry flatten 15:10, Rs 1 L whole lots, 5 bps per side | 30-min OHLC version of the same (adverse intrabar order) |
| exit variants for early entries | **grace**: MACD invalidation only after a closed bar has had MACD > 0. **T5 / T10**: grace, plus exit at the 5th / 10th 30-min close if no closed bar has had MACD > 0 (the "no cross within N bars" cut) | same |
| baseline | lane **cross + 5 min (d5)**: 2,508 traded of 3,497 tradable crosses | **x0** intrabar at p\* (optimistic for the baseline): 11,750 crosses |
| cap | each trade winsorised at +300% of cost, as before | same |

Checks against the earlier study:
- Live grace and T10 P&L on the 82,402 30-min rows are identical (0 differences). T10 here comes from a one-pass
  snapshot simulator.
- TradeBot rows, prices and P&L are identical.
- Re-running the earlier model hybrids inside this study's code reproduces the earlier numbers exactly:
  - TradeBot C10 T10 +130.4 L, Rt +157.2 L;
  - live C10 T10 76.4 L.

**Bookkeeping.**
- Each fixed policy is replayed once over the whole sample:
  - per contract, no overlapping positions;
  - an early position blocks the cross entry while it is open;
  - the cross goes first at equal times.
- Out-of-sample metrics then use the trades whose entry falls in the test units. Because of that, the baselines read
  −8.7 L (TradeBot) and 88.0 L (live) instead of the earlier −6.2 L and 84.7 L.
- The Rs 50 L book is the earlier study's time-ordered cash replay: Rs 1 L tickets, and an unfunded entry does not
  block its contract.
- The trading clock is 375 minutes per session.

**Lead and labels.**
- Lead = trading minutes from the trigger bar's close to the next intrabar cross on the same contract. Live uses the
  minute of the tick that crossed p\*. TradeBot uses (bars to cross) × 30 − 15.
- Lead in bars = lead / 30.
- "Precision ≤ N" = share of fires with a cross within N × 30 minutes.
- "Recall 4–10" = share of tradable crosses that had at least one fire whose next cross is that cross, with a lead of
  4–10 bars (90 < lead ≤ 300 min).
- "Earliest lead" = median lead of the earliest fire within 12 bars before the cross, among crosses that had one.
- "TP pairs" pairs two things on the same cross: the earliest fire 4–10 bars before a baseline-traded cross, with
  grace or T10 exits, and the baseline trade itself.
- "FP" = fires not followed by a cross within 10 bars.

## 2. The triggers (all on completed bars only; no look-ahead)

**Supertrend.**
- Wilder ATR(n), bands at hl2 ± m·ATR, the standard final-band carry rules; the series runs continuously across
  sessions.
- Grid: n ∈ {7, 10, 14} × m ∈ {1.5, 2, 3} × bar ∈ {5, 15, 30} min (TradeBot: 30 only).
- Variants:
  - `flip`: bearish → bullish at this bar's close;
  - `above`: close above the Supertrend line, i.e. the bullish state; fires on every bar while bullish, and the
    no-overlap replay re-enters whenever flat;
  - `flipHTF`: flip while the higher-timeframe Supertrend with the same (n, m) is bullish on its last completed bar.
    Mapping: 5 → 30 min, 15 → 60 min, 30 → 60 min.

**Pivots.**
- Levels for day d come from the same series' session d−1 H/L/C: P, R1, R2, S1, S2; Camarilla H3/H4/L3/L4; CPR
  BC/TC and its width; PDH/PDL.
- Opening-range high of the first 15 or 30 minutes (TradeBot: 30 only), usable only after the window closes.
- Triggers:
  - `brkX`: close > X and the previous close ≤ X; `brkP` is the "reclaim P" trigger;
  - `bncX`: low ≤ X < close, i.e. touch, then close back above.
- `+nCPR` filter: narrow-CPR day, i.e. width below the 30th percentile of contract-day widths on *previous* days
  only.
- Three level sources:
  1. `pv`: the premium's own pivots;
  2. `pt`: theta-adjusted premium pivots. Levels are scaled by √(T_d / T_{d−1}), where T = calendar days to expiry
     + 0.25. This removes the overnight decay that makes yesterday's premium levels sit too high;
  3. `sp`: the **underlying's** pivots mapped through the sign of delta. A CE fires when spot crosses its level
     upward. A PE fires on the mirror (spot below S1 = the put's "R1", and so on). Any monotone delta mapping fires at
     the same moments, so this is the delta-mapped variant without needing a delta model.
- Bar sizes: live 5 and 30 min; TradeBot 30 min.

**Combinations.**
- `ST flip+P`: flip with the premium above its P.
- `ST flip+rsiUp`: flip with RSI14 of the same bars rising. **This is identical to `flip` in 99.9% of fires**, because
  a bullish flip bar almost always raises RSI. It adds nothing.
- `pv {brkP, brkR1, brkTC, brkPDH, bncS1, bncL3}+hist`: the trigger plus a rising committed 30-min MACD histogram.

**Families and counts:**

| family | TradeBot | live |
|---|---|---|
| ST | 27 | 81 |
| PIV-prem (`pv`, `pt`) | 28 | 58 |
| PIV-spot (`sp`) | 16 | 34 |
| COMBO | 24 | 66 |
| total | **95** | **239** |

An earlier pass had a sign bug in the spot narrow-CPR filter: PE widths were negative, so every PE day counted as
narrow. It was fixed before the results below. The buggy run had made `sp5_brkP+nCPR` look like +81 L on live
through PE-only exposure.

## 3. Lead time, precision and recall

### 3.1 Representative triggers

TradeBot (full sample; 1 bar = 30 min; "h" = hours):

| trigger | fires | per day | median lead bars (h) | no cross ≤ 39 bars | prec ≤ 5 / 10 / 20 | recall 4–10 | recall ≤ 12, earliest lead | TP pairs: trigger grace / T10 vs cross ret (n) | FP ret grace / T10 |
|---|---|---|---|---|---|---|---|---|---|
| ST30_10_3_flip | 5,521 | 24 | 2.5 (1.2) | 8% | 0.62 / 0.75 / 0.84 | 10.7% | 34.1%, 1.5 | +0.076 / +0.074 vs −0.001 (1,255) | −0.162 / −0.174 |
| ST30_10_2_flip | 9,163 | 39 | 4.5 (2.2) | 14% | 0.43 / 0.59 / 0.74 | 19.1% | 44.2%, 2.5 | +0.133 / +0.132 vs −0.006 (2,244) | −0.130 / −0.139 |
| ST30_10_1.5_above | 71,515 | 307 | 6.5 (3.2) | 19% | 0.36 / 0.52 / 0.67 | 35.9% | 78.4%, 2.5 | +0.179 / +0.178 vs +0.000 (4,218) | −0.124 / −0.128 |
| pv30_brkP (reclaim P) | 13,788 | 59 | 5.5 (2.8) | 17% | 0.38 / 0.54 / 0.70 | 24.4% | 49.2%, 3.5 | +0.144 / +0.144 vs +0.002 (2,862) | −0.132 / −0.136 |
| pv30_brkTC | 11,520 | 49 | 4.5 (2.2) | 15% | 0.44 / 0.59 / 0.74 | 20.5% | 45.9%, 2.5 | +0.132 / +0.132 vs +0.006 (2,412) | −0.134 / −0.144 |
| pv30_brkR1 | 6,315 | 27 | 2.5 (1.2) | 9% | 0.64 / 0.76 / 0.85 | 9.6% | 34.1%, 1.5 | +0.053 / +0.051 vs −0.005 (1,128) | −0.156 / −0.177 |
| pv30_brkPDH | 5,092 | 22 | 1.5 (0.8) | 8% | 0.69 / 0.79 / 0.87 | 7.6% | 29.7%, 1.5 | +0.052 / +0.051 vs −0.005 (891) | −0.174 / −0.188 |
| pv30_brkOR30 | 11,904 | 51 | 6.5 (3.2) | 19% | 0.37 / 0.52 / 0.67 | 18.5% | 40.1%, 3.5 | +0.147 / +0.147 vs −0.008 (2,176) | −0.120 / −0.118 |
| pv30_bncS1 | 17,408 | 75 | 14.5 (7.2) | 34% | 0.10 / 0.22 / 0.42 | 14.3% | 21.2%, 6.5 | +0.287 / +0.290 vs −0.005 (1,675) | −0.093 / −0.084 |
| pv30_bncL3 | 20,630 | 89 | 12.5 (6.2) | 30% | 0.16 / 0.28 / 0.48 | 19.5% | 32.0%, 5.5 | +0.246 / +0.252 vs −0.002 (2,289) | −0.102 / −0.096 |
| pt30_brkP (theta-adj.) | 14,232 | 61 | 7.5 (3.8) | 20% | 0.31 / 0.48 / 0.65 | 24.6% | 45.1%, 4.5 | +0.172 / +0.170 vs −0.001 (2,886) | −0.123 / −0.124 |
| sp30_brkP (spot P) | 15,094 | 65 | 8.5 (4.2) | 21% | 0.27 / 0.44 / 0.62 | 25.5% | 45.5%, 4.5 | +0.196 / +0.195 vs −0.002 (3,001) | −0.114 / −0.113 |
| sp30_brkTC | 13,438 | 58 | 6.5 (3.2) | 19% | 0.33 / 0.50 / 0.66 | 23.4% | 45.2%, 3.5 | +0.174 / +0.174 vs −0.003 (2,749) | −0.130 / −0.126 |
| sp30_bncS1 | 18,881 | 81 | 16.5 (8.2) | 36% | 0.07 / 0.17 / 0.39 | 11.9% | 17.5%, 7.5 | +0.324 / +0.326 vs −0.003 (1,401) | −0.063 / −0.064 |
| sp30_bncL3 | 25,987 | 112 | 13.5 (6.8) | 32% | 0.13 / 0.25 / 0.45 | 22.0% | 34.1%, 6.5 | +0.271 / +0.274 vs −0.001 (2,587) | −0.077 / −0.078 |
| ST30_10_3_flip+P | 5,226 | 22 | 2.5 (1.2) | 8% | 0.64 / 0.77 / 0.85 | 10.0% | 33.0%, 1.5 | +0.066 / +0.065 vs −0.001 (1,178) | −0.165 / −0.179 |
| pv30_brkP+hist | 12,916 | 55 | 5.5 (2.8) | 16% | 0.39 / 0.55 / 0.70 | 23.2% | 47.7%, 3.5 | +0.141 / +0.140 vs −0.000 (2,731) | −0.127 / −0.135 |
| pv30_brkP+nCPR | 4,205 | 18 | 5.5 (2.8) | 16% | 0.42 / 0.56 / 0.71 | 7.5% | 16.7%, 2.5 | +0.129 / +0.129 vs −0.002 (881) | −0.136 / −0.135 |

Live (full sample, 27 days):

| trigger | fires | per day | median lead bars (h) | no cross ≤ 39 | prec ≤ 5 / 10 / 20 | recall 4–10 | recall ≤ 12, earliest lead | TP pairs: trigger grace / T10 vs d5 ret (n) | FP ret grace / T10 |
|---|---|---|---|---|---|---|---|---|---|
| ST5_10_1.5_flip | 22,575 | 836 | 11.4 (5.7) | 43% | 0.16 / 0.26 / 0.41 | 43.5% | 61.5%, 7.0 | +0.362 / +0.329 vs +0.044 (1,075) | −0.142 / −0.123 |
| ST5_10_3_flip | 10,469 | 388 | 10.2 (5.1) | 39% | 0.19 / 0.30 / 0.45 | 33.9% | 55.7%, 5.0 | +0.317 / +0.303 vs +0.036 (845) | −0.157 / −0.133 |
| ST5_10_3_flipHTF | 1,153 | 43 | 2.9 (1.4) | 22% | 0.50 / 0.61 / 0.73 | 4.9% | 15.2%, 1.9 | +0.175 / +0.151 vs +0.095 (111) | −0.203 / −0.197 |
| ST15_10_3_flip | 3,138 | 116 | 5.5 (2.8) | 26% | 0.35 / 0.49 / 0.63 | 18.4% | 42.3%, 2.8 | +0.273 / +0.268 vs +0.058 (464) | −0.175 / −0.168 |
| ST30_10_3_flip | 1,513 | 56 | 2.0 (1.0) | 12% | 0.61 / 0.74 / 0.82 | 9.6% | 31.4%, 1.1 | +0.254 / +0.237 vs +0.076 (255) | −0.190 / −0.190 |
| ST30_10_2_flip | 2,557 | 95 | 4.0 (2.0) | 20% | 0.43 / 0.58 / 0.70 | 15.9% | 41.0%, 2.0 | +0.248 / +0.239 vs +0.063 (407) | −0.174 / −0.170 |
| ST30_10_3_above | 12,061 | 447 | 3.6 (1.8) | 24% | 0.43 / 0.57 / 0.68 | 18.4% | 60.4%, 1.0 | +0.245 / +0.196 vs +0.060 (444) | −0.183 / −0.168 |
| pv5_brkP | 7,850 | 291 | 6.7 (3.4) | 27% | 0.30 / 0.46 / 0.61 | 24.9% | 50.0%, 3.6 | +0.245 / +0.231 vs +0.028 (614) | −0.182 / −0.165 |
| pv30_brkP | 3,703 | 137 | 4.5 (2.3) | 23% | 0.40 / 0.55 / 0.67 | 19.7% | 45.5%, 2.6 | +0.230 / +0.218 vs +0.034 (486) | −0.191 / −0.168 |
| pv30_brkTC | 3,077 | 114 | 3.5 (1.8) | 20% | 0.46 / 0.61 / 0.73 | 16.8% | 42.3%, 2.0 | +0.213 / +0.204 vs +0.032 (427) | −0.203 / −0.185 |
| pv30_brkR1 | 1,634 | 61 | 1.5 (0.8) | 12% | 0.66 / 0.77 / 0.84 | 8.8% | 30.1%, 1.0 | +0.165 / +0.153 vs +0.025 (223) | −0.186 / −0.199 |
| pv30_brkPDH | 1,321 | 49 | 1.2 (0.6) | 9% | 0.68 / 0.79 / 0.86 | 7.0% | 25.5%, 1.0 | +0.158 / +0.157 vs −0.002 (184) | −0.187 / −0.191 |
| pv5_brkOR15 | 7,713 | 286 | 7.4 (3.7) | 32% | 0.27 / 0.40 / 0.54 | 21.6% | 42.9%, 3.5 | +0.273 / +0.256 vs +0.060 (527) | −0.170 / −0.143 |
| pv30_brkOR30 | 3,168 | 117 | 5.8 (2.9) | 28% | 0.33 / 0.47 / 0.59 | 15.0% | 33.1%, 3.0 | +0.233 / +0.228 vs +0.036 (374) | −0.179 / −0.142 |
| pv30_bncS1 | 6,044 | 224 | 13.5 (6.8) | 46% | 0.10 / 0.20 / 0.36 | 13.6% | 21.9%, 6.5 | +0.367 / +0.331 vs −0.000 (339) | −0.143 / −0.106 |
| pv30_bncL3 | 6,857 | 254 | 11.5 (5.8) | 41% | 0.15 / 0.27 / 0.42 | 18.0% | 30.3%, 5.5 | +0.325 / +0.291 vs +0.014 (449) | −0.149 / −0.111 |
| pt30_brkP | 3,958 | 147 | 6.0 (3.0) | 27% | 0.33 / 0.47 / 0.61 | 20.2% | 42.0%, 3.0 | +0.237 / +0.221 vs +0.025 (510) | −0.191 / −0.163 |
| sp5_brkP | 9,433 | 349 | 9.7 (4.8) | 34% | 0.20 / 0.34 / 0.51 | 26.9% | 46.5%, 4.9 | +0.301 / +0.287 vs +0.029 (658) | −0.150 / −0.137 |
| sp30_brkP | 4,534 | 168 | 7.5 (3.8) | 31% | 0.27 / 0.41 / 0.56 | 21.6% | 42.8%, 4.0 | +0.290 / +0.274 vs +0.045 (529) | −0.155 / −0.137 |
| sp30_brkOR30 | 3,683 | 136 | 6.2 (3.1) | 30% | 0.31 / 0.45 / 0.57 | 17.3% | 36.1%, 3.0 | +0.257 / +0.245 vs +0.049 (440) | −0.176 / −0.134 |
| ST30_10_3_flip+P | 1,403 | 52 | 2.0 (1.0) | 10% | 0.63 / 0.76 / 0.84 | 9.0% | 30.3%, 1.0 | +0.263 / +0.240 vs +0.082 (239) | −0.186 / −0.197 |
| pv30_brkP+hist | 3,455 | 128 | 4.2 (2.1) | 22% | 0.41 / 0.56 / 0.68 | 18.8% | 44.6%, 2.5 | +0.231 / +0.222 vs +0.036 (469) | −0.191 / −0.168 |
| pv30_brkP+nCPR | 761 | 28 | 4.5 (2.3) | 26% | 0.38 / 0.53 / 0.65 | 4.0% | 9.6%, 2.4 | +0.115 / +0.118 vs +0.001 (101) | −0.213 / −0.183 |

Family medians (live; TradeBot in brackets):

| family | median lead bars | prec ≤ 10 | recall 4–10 | FP ret (grace) |
|---|---|---|---|---|
| ST | 5.4 (4.5) | 0.49 (0.59) | 19.9% (19.1%) | −0.169 (−0.135) |
| PIV-prem | 6.0 (5.5) | 0.47 (0.55) | 12.7% (12.8%) | −0.187 (−0.129) |
| PIV-spot | 7.4 (7.0) | 0.41 (0.49) | 12.4% (10.1%) | −0.158 (−0.123) |
| COMBO | 4.4 (4.5) | 0.55 (0.60) | 15.8% (15.1%) | −0.175 (−0.130) |

### 3.2 Reading

- **The frontier.**
  - Precision ≤ 10 falls steadily as the median lead rises:

    | median lead | precision ≤ 10 |
    |---|---|
    | 1.5–2.5 bars | 0.72–0.79 |
    | 4.5–6.5 bars | 0.46–0.61 |
    | 10–16 bars | 0.17–0.30 |

  - This holds across every family, bar size and parameter.
  - Tighter Supertrend multipliers and shorter bars move a trigger along the curve, not off it. For example, live
    30-min flips have a lead/precision of 5.3–5.6 bars / 0.47–0.50 at m = 1.5 and 1.8–2.0 / 0.72–0.76 at m = 3; 5-min flips sit at
    10–11.5 / 0.26–0.31.
  - The earlier C10 model sits on the same curve: 4 bars at 0.71–0.73, 1 bar at 0.97–0.98.
- **The prize is visible in the true positives and wiped out by the false ones.**
  - When a 5-min ST flip *is* followed by a cross 4–10 bars later (live, 1,075 crosses), entering at the flip returns
    +0.362 (grace) against +0.044 for cross+5min on the same crosses.
  - But 74% of its fires do not cross within 10 bars, and those return −0.142.
  - Support bounces (S1 / L3 / PDL) have the best true-positive returns (+0.27 to +0.44) because they fire nearest
    the bottom. They also have the worst precision (≤ 10 bars: 0.16–0.28).
- **Higher-timeframe and pivot confirmation raise precision only by firing later.**
  - `flipHTF` lifts 5-min precision from 0.26–0.31 to 0.49–0.61, but cuts the lead to 2.9–5.7 bars and recall to
    4–17%.
  - `+P` cuts the 5-min flip lead from ~11 to 4.5 bars.
  - `+nCPR` keeps lead and precision about the same but cuts recall to 2–9%. Narrow-CPR days are simply rarer.
- **Pivot source barely matters.**
  - Theta-adjusted premium pivots (`pt`) fire 1–2 bars earlier than raw pivots (`pv`), with 0.05–0.08 lower
    precision.
  - Spot pivots (`sp`) fire 2–3 bars earlier, with 0.09–0.14 lower precision.
  - Same frontier.

## 4. P&L against the cross baseline (out of sample)

### 4.1 Supertrend grid

Each cell: median lead bars / precision ≤ 10 / recall 4–10 / **hybrid T10 ΔP&L L (t_d)** / early-only grace ΔP&L L.

**TradeBot, 30-min Supertrend** (baseline −8.7 L):

| ATR, mult | flip | above (state) | flipHTF (60-min) |
|---|---|---|---|
| 7, 1.5 | 6.5 / 0.49 / 24% / +114 (+1.5) / +94 | 6.5 / 0.51 / 36% / +135 (+1.7) / +118 | 2.5 / 0.69 / 3% / +17 (+1.1) / +33 |
| 7, 2 | 5.5 / 0.57 / 19% / +75 (+1.3) / +92 | 5.5 / 0.54 / 32% / +111 (+1.8) / +119 | 1.5 / 0.78 / 2% / +6 (+0.6) / +1 |
| 7, 3 | 2.5 / 0.72 / 11% / +37 (+1.3) / +29 | 4.5 / 0.57 / 23% / +53 (+1.4) / +40 | 0.5 / 0.89 / 1% / −1 (−0.3) / −5 |
| 10, 1.5 | 6.5 / 0.50 / 23% / +121 (+1.7) / +112 | 6.5 / 0.52 / 36% / +129 (+1.7) / +127 | 2.5 / 0.70 / 3% / +14 (+0.9) / +30 |
| 10, 2 | 4.5 / 0.59 / 19% / +87 (+1.7) / +89 | 5.5 / 0.55 / 31% / +125 (+2.2) / +110 | 1.5 / 0.79 / 2% / +6 (+0.7) / +9 |
| 10, 3 | 2.5 / 0.75 / 11% / +27 (+1.0) / +20 | 4.5 / 0.58 / 21% / +52 (+1.5) / +33 | 1.5 / 0.86 / 1% / −3 (−0.5) / −2 |
| 14, 1.5 | 6.5 / 0.51 / 23% / +110 (+1.6) / +110 | 6.5 / 0.52 / 35% / +118 (+1.6) / +118 | 2.5 / 0.70 / 3% / +19 (+1.1) / +21 |
| 14, 2 | 4.5 / 0.60 / 18% / +84 (+1.8) / +77 | 5.5 / 0.56 / 30% / +118 (+2.2) / +98 | 1.5 / 0.77 / 2% / +4 (+0.4) / +14 |
| 14, 3 | 2.5 / 0.77 / 10% / +32 (+1.3) / +29 | 4.5 / 0.59 / 19% / +65 (+1.9) / +41 | 1.5 / 0.85 / 1% / −1 (−0.1) / +10 |

**Live, Supertrend flip** (baseline 88.0 L):

| ATR, mult | 5-min | 15-min | 30-min |
|---|---|---|---|
| 7, 1.5 | 11.5 / 0.26 / 43% / −188 (−2.1) / −302 | 9.0 / 0.34 / 28% / −69 (−1.1) / −161 | 5.6 / 0.47 / 20% / −42 (−1.5) / −120 |
| 7, 2 | 11.2 / 0.27 / 41% / −144 (−1.5) / −259 | 8.0 / 0.38 / 24% / −59 (−1.1) / −145 | 4.3 / 0.56 / 16% / −15 (−0.6) / −56 |
| 7, 3 | 10.5 / 0.29 / 34% / −141 (−1.3) / −256 | 5.6 / 0.48 / 19% / −52 (−1.7) / −108 | 2.0 / 0.72 / 10% / −2 (−0.1) / −25 |
| 10, 1.5 | 11.4 / 0.26 / 43% / −189 (−2.0) / −314 | 8.8 / 0.35 / 28% / −73 (−1.2) / −154 | 5.5 / 0.49 / 19% / −47 (−1.7) / −116 |
| 10, 2 | 11.0 / 0.27 / 41% / −153 (−1.5) / −264 | 7.7 / 0.39 / 24% / −43 (−0.9) / −124 | 4.0 / 0.58 / 16% / −23 (−1.0) / −68 |
| 10, 3 | 10.2 / 0.30 / 34% / −125 (−1.2) / −239 | 5.5 / 0.49 / 18% / −42 (−1.6) / −86 | 2.0 / 0.74 / 10% / −8 (−0.7) / −32 |
| 14, 1.5 | 11.2 / 0.27 / 44% / −197 (−2.0) / −310 | 8.7 / 0.36 / 27% / −58 (−1.0) / −144 | 5.3 / 0.49 / 19% / −34 (−1.2) / −106 |
| 14, 2 | 10.9 / 0.28 / 40% / −143 (−1.4) / −265 | 7.5 / 0.40 / 24% / −43 (−1.0) / −126 | 3.6 / 0.60 / 16% / −9 (−0.5) / −58 |
| 14, 3 | 10.0 / 0.31 / 33% / −104 (−1.0) / −224 | 5.4 / 0.50 / 18% / −32 (−1.3) / −79 | 1.8 / 0.76 / 8% / −0 (−0.0) / −27 |

The other live Supertrend variants (hybrid T10 ΔP&L L):

| variant | 5-min | 15-min | 30-min |
|---|---|---|---|
| `above` | −134 to −236 | −46 to −88 | −11 to −53 |
| `flipHTF` | −27 to +6 | −14 to +2 | −9 to +3 |
| `flip+P` | −12 to +6 | −21 to −6 | −14 to −1 |

The early-only grace results for these variants are all −21 to −334 L.

### 4.2 Pivot triggers

Same cell format.

**TradeBot** (30-min bars):

| trigger | premium `pv30` | theta-adj. `pt30` | spot `sp30` |
|---|---|---|---|
| brkP (reclaim P) | 5.5 / 0.54 / 24% / +64 (+1.5) / +76 | 7.5 / 0.48 / 25% / +63 (+1.4) / +74 | 8.5 / 0.44 / 26% / +124 (+2.2) / +147 |
| brkTC | 4.5 / 0.59 / 21% / +31 (+0.9) / +40 | 5.5 / 0.54 / 22% / +54 (+1.4) / +63 | 6.5 / 0.50 / 23% / +135 (+2.7) / +136 |
| brkR1 | 2.5 / 0.76 / 10% / −3 (−0.1) / +7 | 2.5 / 0.71 / 14% / +23 (+0.9) / +20 | 2.5 / 0.73 / 10% / +31 (+1.4) / +31 |
| brkR2 | 1.5 / 0.88 / 2% / −7 (−0.9) / +18 | 1.5 / 0.84 / 3% / −13 (−1.2) / +9 | 0.5 / 0.87 / 1% / +3 (+0.4) / +16 |
| brkH3 | 4.5 / 0.61 / 21% / +67 (+1.5) / +71 | 5.5 / 0.55 / 25% / +71 (+1.5) / +57 | 5.5 / 0.56 / 22% / +121 (+2.3) / +74 |
| brkH4 | 2.5 / 0.74 / 10% / +7 (+0.3) / +42 | 3.5 / 0.69 / 14% / +20 (+0.7) / +33 | 2.5 / 0.72 / 10% / +36 (+1.6) / +66 |
| brkPDH | 1.5 / 0.79 / 8% / +6 (+0.3) / −13 | 2.5 / 0.74 / 11% / +2 (+0.1) / −22 | 2.5 / 0.73 / 10% / +24 (+1.0) / −0 |
| brkOR30 | 6.5 / 0.52 / 19% / +16 (+0.3) / −3 | – | 5.5 / 0.52 / 18% / +74 (+1.4) / +68 |
| bncS1 | 14.5 / 0.22 / 14% / +62 (+1.1) / +42 | 15.5 / 0.20 / 12% / +75 (+1.4) / +48 | 16.5 / 0.17 / 12% / +135 (+2.2) / +88 |
| bncS2 | 18.5 / 0.14 / 4% / +32 (+0.9) / +73 | 18.5 / 0.12 / 3% / +27 (+0.9) / +56 | 19.5 / 0.10 / 2% / +26 (+0.7) / +43 |
| bncL3 | 12.5 / 0.28 / 19% / +54 (+0.9) / +67 | 13.5 / 0.26 / 17% / +74 (+1.3) / +57 | 13.5 / 0.25 / 22% / +164 (+2.4) / +111 |
| bncL4 | 14.5 / 0.22 / 11% / +35 (+0.7) / +7 | 15.5 / 0.20 / 9% / +47 (+1.0) / +36 | 16.5 / 0.17 / 10% / +77 (+1.4) / +78 |
| bncPDL | 13.5 / 0.25 / 20% / +70 (+1.0) / +84 | 14.5 / 0.21 / 17% / +48 (+0.7) / +38 | 15.5 / 0.19 / 16% / +92 (+1.4) / +86 |
| brkP+nCPR | 5.5 / 0.56 / 7% / +26 (+1.4) / −15 | – | 7.5 / 0.46 / 9% / +20 (+0.9) / +15 |
| brkTC+nCPR | 4.5 / 0.58 / 7% / +20 (+1.1) / −18 | – | 7.5 / 0.48 / 9% / +17 (+0.8) / +17 |
| brkP+hist | 5.5 / 0.55 / 23% / +57 (+1.4) / +80 | – | – |
| bncL3+hist | 11.5 / 0.31 / 15% / +47 (+0.9) / +22 | – | – |

**Live**, selected rows (hybrid T10 ΔP&L L (t_d) / early grace ΔP&L L):

| trigger | pv5 | pv30 | pt30 | sp5 | sp30 |
|---|---|---|---|---|---|
| brkP | −33 (−0.9) / −107 | −21 (−0.7) / −86 | −34 (−1.2) / −119 | −4 (−0.1) / −90 | −25 (−0.8) / −98 |
| brkTC | −30 (−1.2) / −92 | −14 (−0.6) / −73 | −40 (−1.5) / −120 | −6 (−0.2) / −69 | −15 (−0.6) / −76 |
| brkR1 | −13 (−0.9) / −67 | −0 (−0.0) / −40 | −6 (−0.4) / −60 | +15 (+0.5) / −39 | −2 (−0.1) / −51 |
| brkR2 | −0 / −72 | +1 / −71 | −1 / −65 | +13 (+1.6) / −59 | +11 (+2.3) / −69 |
| brkH3 | −28 (−0.8) / −96 | −3 (−0.1) / −62 | −7 (−0.2) / −99 | +43 (+1.0) / −33 | +7 (+0.3) / −50 |
| brkPDH | −2 / −62 | +3 / −55 | +8 / −39 | +15 (+0.7) / −39 | +13 (+0.7) / −43 |
| brkOR15 / OR30 | −49 / −35 | −21 / −24 | – | −7 / −10 | −8 / −18 |
| bncS1 | −89 (−1.7) / −211 | −50 (−1.2) / −193 | −29 (−0.8) / −152 | −62 (−1.1) / −178 | −32 (−0.7) / −168 |
| bncL3 | −95 (−2.2) / −221 | −69 (−1.9) / −206 | −59 (−1.5) / −189 | −2 (−0.1) / −149 | −25 (−0.9) / −152 |
| brkP+nCPR | −25 (−2.9) / −108 | −25 (−3.0) / −104 | – | −9 (−0.6) / −82 | −8 (−0.5) / −85 |
| brkP+hist | −17 (−0.7) / −100 | −17 (−0.6) / −88 | – | – | – |

In the brkOR15 / OR30 row the two figures are hybrid T10 ΔP&L for the 15-min and 30-min opening range.

### 4.3 Exits: the "no cross within N bars" cut is regime-dependent

Median over all triggers:

| dataset | mode | grace ΔP&L | T5 ΔP&L | T10 ΔP&L | T10 − grace (share of triggers improved) |
|---|---|---|---|---|---|
| TradeBot | early-only | +42.0 | +21.9 | +29.6 | −12.3 (23%) |
| TradeBot | hybrid | +41.9 | +22.2 | +32.6 | −5.1 (31%) |
| live | early-only | −92.9 | −85.1 | −75.7 | +25.6 (94%) |
| live | hybrid | −47.4 | −20.1 | −19.4 | +26.7 (96%) |

- The cut rescues part of the false-positive cost in the live put month. In 2025–26 it costs money, because slow
  crosses still pay there.
- Hybrid beats early-only for 75% (TradeBot) and 100% (live) of triggers, by a median of +13 / +48 L. Taking the
  cross whenever no trigger fired is always better than trigger-only.

### 4.4 Best fixed cells (hindsight: the best of 570 / 1,434, NOT tradable results)

| dataset | cell | trades (early) | hit | P&L L | ΔP&L L (t_d, p) | daily t | max DD L | CE / PE L | book 50 L (DD) | prize captured | random same-rate ΔP&L mean (share below cell) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TradeBot | sp30_bncL3 hybrid T10 | 15,638 (9,983) | 38% | 155.6 | +164.3 (+2.41, 0.017) | 1.95 | 59.7 | 55.8 / 99.8 | 103.7 (35.9) | 8.5% | +47.1 (1.00) |
| TradeBot | sp30_brkTC hybrid T5 | 14,141 (8,939) | 37% | 122.1 | +130.8 (+2.98, 0.003) | 1.58 | 58.7 | 82.9 / 39.2 | 79.8 (33.2) | 6.7% | +15.4 (1.00) |
| TradeBot | sp30_brkP early grace | 7,098 | 46% | 138.0 | +146.7 (+1.69, 0.094) | 1.55 | 50.5 | 80.5 / 57.6 | 61.7 (25.8) | 7.6% | +61.1 (1.00) |
| TradeBot | ST30_10_1.5_above hybrid grace | 9,739 (8,444) | 43% | 136.4 | +145.1 (+1.57, 0.117) | 1.30 | 95.7 | 120.3 / 16.2 | 142.5 (41.4) | 7.5% | +70.6 (1.00) |
| TradeBot | BASELINE x0 | 8,735 | 29% | −8.7 | – | −0.13 | – | 19.0 / −27.7 | 30.0 (47.7) | – | – |
| live | sp5_brkH3 hybrid T10 | 3,278 (2,512) | 44% | 130.9 | +42.8 (+0.99, 0.334) | 1.63 | 34.7 | −101.1 / 231.9 | 69.6 (23.6) | 7.6% | −78.6 (1.00) |
| live | sp30_bncL4 hybrid T5 | 3,891 (2,349) | 42% | 112.6 | +24.6 (+0.81, 0.428) | 2.26 | 19.4 | −68.5 / 181.1 | 50.7 (17.1) | 4.4% | −36.6 (1.00) |
| live | sp30_brkR2 hybrid grace | 1,660 (197) | 41% | 98.9 | +10.9 (+2.45, 0.025) | 2.02 | 19.8 | −55.8 / 154.7 | 42.7 (11.6) | 1.9% | −13.0 (1.00) |
| live | BASELINE d5 | 1,640 | 40% | 88.0 | – | 1.87 | – | −56.8 / 144.9 | 40.4 (9.7) | – | – |

Every live "winner" is a spot-pivot breakout that adds PE exposure in the put month. CE is negative in all of them.

## 5. Controls: is the trigger's timing worth anything?

Two random controls per cell, 30 draws each:
- **(a) same-rate random.** The same number of entries, drawn uniformly among all candidate rows at the trigger's
  bar size (full sample). No look-ahead.
- **(b) same contract-day random.** The same number of entries on each contract-day where the trigger fired, at
  random times that day. This one *uses look-ahead*: it knows the day on which the premium broke out. It is a
  diagnostic of how late the trigger is, not a null.

| | TradeBot | live |
|---|---|---|
| random same-rate entries in candidate state, ΔP&L | +12 to +75 L (sd 15–38) | −5 to −222 L (sd 6–28) |
| cells beating (a) in ≥ 90% of draws | 34 of 93 (9 of the 68 representative cells; the rest are hindsight-best cells) | 93 of 117 (68 of 92 representative) |
| (b) same contract-day random, ΔP&L | +52 to +1,071 L; breakout / ST cells +130 to +1,071, i.e. +122 to +942 above the trigger | −156 to +231 L; breakout / ST cells −65 to +231, i.e. +20 to +285 above the trigger; bounces −103 to +94 |

**Walk-forward policies vs (a), with the same per-unit trigger rate and exit:**

| policy | TradeBot WF ΔP&L | TradeBot random mean (sd) | share of draws below WF | live WF ΔP&L | live random mean (sd) | share below |
|---|---|---|---|---|---|---|
| ST hybrid | +106.0 | +48.7 (23.5) | 1.00 | −38.2 | −27.4 (15.1) | 0.27 |
| PIV-spot hybrid | +116.5 | +31.9 (26.0) | 1.00 | −36.1 | −86.4 (17.3) | 1.00 |
| COMBO hybrid | +78.5 | +37.2 (16.9) | 1.00 | −36.7 | −60.1 (14.3) | 0.97 |
| ALL hybrid | +116.5 | +29.3 (24.3) | 1.00 | −32.6 | −90.4 (21.0) | 1.00 |
| ST early | −12.5 | +8.0 (20.6) | 0.17 | −73.8 | −136.2 (16.3) | 1.00 |
| PIV-prem early | −28.1 | +24.6 (26.7) | 0.00 | −115.0 | −157.9 (17.6) | 1.00 |

Reading:
- The triggers are better than random entries in the candidate state. They pick rows that are less bad, which is
  some real timing information. On TradeBot the walk-forward hybrids beat random by +41 to +87 L, 2.4–3.6 sd.
- But the candidate state itself is a losing place to enter on live, so better-than-random is still well below the
  cross.
- The look-ahead control shows why. On the contract-days where a breakout or flip happened, entering at a random
  time that day earns several times more than entering at the trigger. The trigger prints after most of that day's
  move.

## 6. Walk-forward selection (no hindsight)

Per test unit (TradeBot month / live day), the (trigger, exit) pair with the highest *training* P&L is chosen within
each family:
- training data = all earlier units;
- only trades that had exited before the unit started count;
- "never" is allowed, which means no trades (early-only) or the baseline (hybrid).

**TradeBot** (baseline −8.7 L, book 30.0 L; prize +1,941 L):

| policy | trades (early) | hit | P&L L | ΔP&L L (t_d, p) | daily t | max DD L | CE / PE L | book 50 L (DD) | prize captured | median bars to cross at early entry |
|---|---|---|---|---|---|---|---|---|---|---|
| WF ST hybrid | 13,303 (11,355) | 39% | 97.3 | +106.0 (+1.94, 0.054) | 1.15 | 70.0 | 82.6 / 14.7 | 100.4 (37.0) | 5.5% | 5.5 |
| WF PIV-spot hybrid (= WF ALL hybrid) | 13,811 (7,248) | 37% | 107.9 | +116.5 (+2.13, 0.034) | 1.44 | 68.3 | 84.2 / 23.6 | 113.9 (47.3) | 6.0% | 12.5 |
| WF COMBO hybrid | 10,646 (4,727) | 34% | 69.8 | +78.5 (+1.20, 0.232) | 0.77 | 110.4 | 50.5 / 19.3 | 30.7 (53.4) | 4.0% | 7.5 |
| WF PIV-prem hybrid | 11,022 (4,568) | 35% | −19.8 | −11.1 (−0.23, 0.817) | −0.28 | 153.5 | −22.9 / 3.1 | 79.3 (47.9) | −0.6% | 12.5 |
| WF PIV-spot early (= ALL early) | 7,863 | 44% | 13.3 | +22.0 (+0.24, 0.813) | 0.18 | 87.4 | 37.4 / −24.1 | 5.4 (30.3) | 1.1% | 11.5 |
| WF ST early | 5,287 | 40% | −21.2 | −12.5 (−0.17, 0.869) | −0.39 | 86.2 | 46.6 / −67.7 | −7.1 (22.7) | −0.6% | 4.5 |
| WF PIV-prem early | 4,158 | 43% | −36.8 | −28.1 (−0.34, 0.737) | −0.75 | 70.2 | −30.9 / −5.9 | −5.2 (37.5) | −1.4% | 13.5 |
| WF COMBO early | 2,829 | 41% | −77.0 | −68.3 (−0.99, 0.322) | −1.92 | 102.2 | −5.8 / −71.2 | −13.5 (29.5) | −3.5% | 6.5 |
| *ref: every 30-min candidate close, hybrid T10* | 28,077 (27,543) | 42% | 100.1 | +108.8 (+0.67) | – | – | – | −24.3 | 5.6% | – |
| *earlier study: hybrid C10 T10 / hybrid Rt T10* | | | 124.2 / 151.0 | +130.4 (+2.00) / +157.2 (+2.24) | | | | −9.7 / 189.5 | 6.7% / 8.1% | |

Choices:
- ST hybrid: `ST30_14_2_above` or `ST30_7_2_above`, exits g / T5 / T10.
- PIV-spot hybrid: `sp30_brkOR30/T5`, then `sp30_bncS1/T10` (×4), `sp30_bncL4/T10`, `sp30_brkTC/T10` (×2).
- COMBO hybrid: "never" in Aug–Oct, then `ST30_xx_1.5_flip+rsiUp/T10`.

By month (L):

| policy | Jul | Aug | Sep | Oct | Nov | Dec | Jan | Feb | months > base |
|---|---|---|---|---|---|---|---|---|---|
| BASELINE | −14 | −18 | 21 | −37 | −43 | 24 | 72 | −13 | – |
| WF ST hybrid | −15 | −17 | 26 | −29 | −30 | 46 | 85 | 32 | 7/8 |
| WF PIV-spot hybrid | −11 | −20 | 34 | −7 | −44 | 44 | 91 | 21 | 6/8 |
| WF COMBO hybrid | −40 | −13 | 21 | −37 | −30 | 36 | 88 | 45 | 5/8 |
| WF PIV-spot early | −6 | −27 | −2 | −32 | −9 | 24 | 22 | 43 | 5/8 |

As in the earlier study, the gain is concentrated in Dec–Feb.

**Live** (baseline 88.0 L, book 40.4 L; prize +562 L):

| policy | trades (early) | hit | P&L L | ΔP&L L (t_d, p) | daily t | max DD L | CE / PE L | book 50 L (DD) | prize captured | days > base |
|---|---|---|---|---|---|---|---|---|---|---|
| WF ST early | 1,399 | 44% | 14.3 | −73.8 (−2.15, 0.046) | 0.34 | 40.3 | −65.0 / 79.3 | 8.7 (28.6) | −13.1% | 7/19 |
| WF ST hybrid | 2,989 (2,098) | 42% | 49.9 | −38.2 (−1.67, 0.111) | 1.04 | 41.4 | −101.2 / 151.1 | 26.1 (18.7) | −6.8% | 9/19 |
| WF PIV-prem early | 1,614 | 44% | −27.0 | −115.0 (−2.80, 0.012) | −0.66 | 36.8 | −103.4 / 76.3 | 2.0 (44.9) | −20.5% | 8/19 |
| WF PIV-prem hybrid | 2,673 (1,760) | 42% | 28.8 | −59.2 (−2.33, 0.032) | 0.49 | 53.4 | −159.5 / 188.3 | 8.4 (24.8) | −10.5% | 6/19 |
| WF PIV-spot early | 2,190 | 42% | −15.2 | −103.2 (−2.19, 0.042) | −0.29 | 68.9 | −135.3 / 120.1 | 14.5 (30.4) | −18.4% | 6/19 |
| WF PIV-spot hybrid | 3,170 (2,321) | 42% | 51.9 | −36.1 (−0.73, 0.475) | 0.68 | 60.7 | −138.6 / 190.5 | 69.4 (24.6) | −6.4% | 7/19 |
| WF COMBO early | 1,152 | 47% | 43.5 | −44.5 (−1.85, 0.082) | 0.99 | 38.4 | −43.1 / 86.6 | 24.3 (26.4) | −7.9% | 8/19 |
| WF COMBO hybrid | 2,040 (1,138) | 43% | 51.3 | −36.7 (−1.82, 0.085) | 1.08 | 33.9 | −104.4 / 155.7 | 10.3 (18.9) | −6.5% | 8/19 |
| WF ALL early | 1,895 | 42% | −16.2 | −104.2 (−2.40, 0.027) | −0.36 | 56.6 | −117.3 / 101.1 | 13.1 (17.7) | −18.5% | 6/19 |
| WF ALL hybrid | 3,217 (2,268) | 42% | 55.4 | −32.6 (−0.68, 0.502) | 0.74 | 60.4 | −154.9 / 210.3 | 42.3 (21.5) | −5.8% | 7/19 |
| *ref: every 30-min candidate close, hybrid T10* | 9,847 (9,509) | 42% | −68.2 | −156.2 (−1.40) | – | – | – | 18.8 | – | – |

- On live, training mostly picks 30-min ST(7, 3) flips with grace exits (ST early), 5-min spot opening-range
  breaks and then spot H3 breaks (PIV-spot / ALL), 5-min premium OR15 breaks, theta-adjusted L4 bounces and PDH breaks
  (PIV-prem), and 30-min ST flips + P or + rsiUp (COMBO).
- Every walk-forward policy loses to cross+5min. CE loses more than in the baseline in 9 of 10 policies (−43 to −160 L vs −56.8 L).
- **Transfer:** the TradeBot walk-forward winners make the following on live (hybrid T10):

  | trigger | ΔP&L L (t_d) |
  |---|---|
  | `sp30_brkTC` | −14.5 (−0.6) |
  | `sp30_bncS1` | −31.5 (−0.7) |
  | `sp30_bncL3` | −24.7 (−0.9) |
  | `ST30_10_1.5_above` | −52.9 (−1.5) |
  | `ST30_14_2_above` | −12 (−0.4) |

## 7. As features in the early-cross walk-forward model

Same rows, folds, purging, HGB settings and inner-CV threshold choice as `early_cross/model.py`. BASE uses the
earlier 55 (TradeBot) / 49 (live) features. AUG adds:
- TradeBot (22): 30-min Supertrend(10, 3) and (7, 2) direction, distance and signed bars-since-flip; 60-min
  Supertrend(10, 3); premium vs P / R1 / S1 / TC / PDH / PDL, CPR width, vs OR30 high, vs theta-adjusted P; spot
  vs spot P / R1 / S1 (signed by side), spot CPR width.
- Live (28): the same, plus 5-min and 15-min Supertrend(10, 3).

| dataset | set | AUC C10 | **AUC tte 5–10 vs > 10** | AUC 5–10 vs none ≤ 39 | AUC 1–4 vs > 10 | Rt IC (t) |
|---|---|---|---|---|---|---|
| TradeBot | BASE | 0.8422 | 0.7522 | 0.7783 | 0.9458 | +0.0745 (+4.09) |
| TradeBot | AUG | 0.8421 | **0.7522** | 0.7786 | 0.9457 | +0.0753 (+4.15) |
| live | BASE | 0.8499 | 0.7662 | 0.7826 | 0.9414 | +0.0926 (+1.45) |
| live | AUG | 0.8502 | **0.7667** | 0.7836 | 0.9414 | +0.1027 (+1.57) |

Per-unit change in the lead-subset AUC:
- TradeBot: mean −0.0001 (t −0.24), 5 of 8 months improved;
- live: −0.0019 (t −0.90), 9 of 19 days improved.

Policies (AUG − BASE ΔP&L, paired t_d):

| policy | TradeBot | live |
|---|---|---|
| hybrid C10 T10 | +127.8 vs +130.4 L: −2.6 (−0.13) | −11.0 vs −8.3 L: −2.8 (−0.17) |
| hybrid C10 grace | +4.7 (+0.37) | −3.8 (−0.32) |
| hybrid Rt T10 | −2.5 (−0.08) | −48.9 (−1.65) |
| early Rt T10 | +52.4 (+1.21) | −84.4 (−0.82) |

Single-feature AUC on the lead subset (out-of-sample rows):

| features | TradeBot | live |
|---|---|---|
| `pv_P`, `pv_TC`, `pt_P` (premium vs its pivot) | 0.709–0.710 | 0.718–0.722 |
| `st30_10_3_dist` | 0.696 | 0.695 |
| Supertrend direction / bars since flip | 0.51–0.63 | 0.53–0.66 |
| CPR width | 0.43–0.47 (inverted, ≈ no information) | same |

Two comparisons:
- The earlier study's `o_rsi` alone scored 0.69–0.77 on the same subset and `dist_pstar` was strong too. The pivot
  distances are another measure of "premium up from its recent range", so the model already has this information.
- The Rt IC moves of +0.001 / +0.010 are noise.

## 8. Multiple testing

**Cells evaluated:**

| item | count |
|---|---|
| fixed cells (triggers × {grace, T5, T10} × {early, hybrid}), TradeBot | 95 × 6 = 570 |
| fixed cells, live | 239 × 6 = 1,434 |
| walk-forward policies | 10 + 10 |
| model policies (4 × BASE / AUG × 2 datasets) | 16 |
| random-control cells | ≈ 2 × 81 per dataset |
| AUC / feature diagnostics | ≈ 70 |
| **total P&L tests** | **≈ 2,050**; ≈ 100 nominal p < 0.05 expected by chance |

| | TradeBot | live |
|---|---|---|
| fixed cells nominally p < 0.05 | 18, all better than baseline (28 expected by chance) | 311 (2 better, 309 worse); 72 expected |
| BH(5%) passes over fixed cells | **0** | **0** (the 19-day sample cannot push even the many negatives through) |
| Bonferroni | 0 (threshold 8.8e-5) | 0 (3.5e-5) |
| smallest p, better side | `sp30_brkTC` hybrid T5 p 0.0033 (+130.8 L) | `sp30_brkR2` hybrid grace p 0.025 (+10.9 L) |
| walk-forward policies, smallest p | ALL / PIV-spot hybrid 0.034 (+), ST hybrid 0.054 (+) | PIV-prem early 0.012 (−) |
| BH over the 10 walk-forward policies | 0 pass | 0 pass |

The cells are heavily correlated:
- ST `flip` and `flip+rsiUp` are near-duplicates;
- neighbouring (n, m) values and P / TC / H3 overlap.

So the effective number of independent tests is much smaller than 2,000. But the conclusion does not depend on the
correction. On live the sign is uniformly negative. On TradeBot the positive walk-forward results only match an
ungated reference and the earlier models.

## 9. Verdict and recommendations

**Verdict.**
- Supertrend and pivot levels on the option premium (or on the underlying mapped through delta) do not give an
  early entry that beats cross+5min:
  - live: worse in every walk-forward variant;
  - TradeBot: about +110 L with p ≈ 0.03–0.05 that does not survive correction, equals ungated candidate entry,
    and does not transfer to live.
- They see the same thing the model sees, "the premium has started to rise from its range", only more coarsely.
- They obey the same trade-off. Fires 5–10 bars before a cross have 25–45% precision, and each false positive costs
  10–20%. Fires with good precision come 1–3 bars before the cross, where little prize is left.
- As model features they add zero AUC for the 5–10-bar window.

**Recommendations:**
1. **Keep cross+5min.** Do not add a Supertrend, pivot or CPR entry, nor a flip / pivot confirmation filter.
2. **Do not add these as model features.** Lead-subset AUC change is 0.0000 / +0.0005, and policy P&L is unchanged.
3. **Shadow-log only, at zero risk and low cost**, if the early-entry research continues. Add to the 30-min
   candidate-row log proposed in `early_cross_5_10_bars.md` §9:
   - 30-min Supertrend(10, 3) and (7, 2) direction, distance and bars since flip;
   - 5-min Supertrend(10, 3) direction;
   - premium vs P / TC / R1 / PDH and vs the 30-min opening-range high;
   - spot vs spot P / TC / R1 (signed by side);
   - CPR width.

   That lets the TradeBot-best structure be re-tested on live data covering a call-favouring month. That structure is
   a hybrid: enter on a spot S1 bounce or a spot TC break while MACD < 0, cut at 10 bars without a cross, otherwise
   take the cross.
   - Promote only with paired t_d > 2 against cross+5min over ≥ 60 new sessions.
   - It must also beat the same-rate random-entry control by ≥ 2 sd.
4. **If any early-entry lane is ever tried, it must be hybrid, not trigger-only.** Hybrid beats trigger-only for
   75–100% of triggers. The 10-bar no-cross cut helps in a put-trend month (live: +26 L median, 94–96% of triggers)
   but costs in mixed regimes (TradeBot: −5 to −12 L median). Choose the cut by regime only with more data.
5. **Do not use high-precision versions as "early" entries.** `flipHTF`, R2/H4/PDH breaks and ST(·, 3) on 30-min
   bars fire 0.5–2.5 bars before the cross, which is just the cross entry made slightly earlier.

## 10. Scripts and outputs

Folder: `/private/tmp/claude-501/-Users-ramachandran-MACD-mini-ADV/77c677ac-a2ad-4b4d-977f-6fcea088fa80/scratchpad/supertrend_pivot/`.
- Copied from `early_cross/` (unmodified): `core.py`, `assemble.py`, `tb_build.py`, `live_build.py`, `tb_rows.py`,
  `model.py`, `sectors.json`.
- `polib.py` is copied too, with one change: it reads `early_cross/rows_*.npz` via a read-only mount.
- Runners: `run.sh` (engine image, data volume, databases opened `mode=ro`, `early_cross` mounted read-only at
  `/ec`) and `tb.sh` (tradebot-backend image, TradeBot data read-only). No network.

| script | does | output |
|---|---|---|
| `st_lib.py` | Supertrend, RSI, k-min bar builder, floor / Camarilla / CPR levels, level triggers, higher-timeframe state; `compute()` = all triggers + model features for one contract | – |
| `tb_st_build.py` | TradeBot rows (same universe / rows as before), 95 triggers, grace / T5 / T10 sims, x0 crosses, new features | `tb_st.npz` |
| `live_st_build.py` | live rows at every 5 / 15 / 30-min close, 227 base triggers, one-pass grace / T5 / T10 minute-bar sims (`exit_sim3`), d5 crosses, spot H/L from the DB, new features | `live_st.npz`, `live_st_build.log` |
| `chk_join.py` | row-for-row match with the earlier `rows_*.npz` (prices and P&L identical) | `join_*.npy` |
| `stdata.py` | loader, narrow-CPR derivation (prior-days 30th percentile), families, replay, Rs 50 L book | – |
| `trig_eval.py tb\|live` | lead / precision / recall / TP-FP pairs, per-cell P&L, walk-forward selection, books, oracle and references | `trig_*.pkl`, `trig_*.log` |
| `ctrl.py`, `ctrl_wf.py` | random same-rate and same contract-day controls | `ctrl_*.pkl`, `ctrl_*.log`, `ctrl_wf_*.log` |
| `trig_report.py tb\|live [all]` | markdown tables (representative or all triggers), FDR | `trig_report_*.md`, `trig_report_*_all.md` (full per-trigger tables) |
| `grids.py` | Supertrend and pivot grids | `grids.md` |
| `cut.py` | grace vs T5 / T10 and hybrid vs early summary | – |
| `model_aug.py tb\|live` | BASE vs AUG early-cross model on identical folds | `model_aug_*.log`, `model_aug_*.pkl` |
| helpers | `t_live.py`, `peek.py`, `peek_res.py` | |
