# Empirical study: option-trade features beyond MACD, return labels, and pre-cross entries

Study date 3 Oct 2026. Data: the user's NSE minute bars, 25 Aug – 1 Oct 2026 (27 sessions). Analysis only:
no engine code or live config was changed, and every SQLite database was opened read-only
(`file:...?mode=ro`) from a separate throwaway container. Companion literature notes in this folder:
`timing_and_selection.md`, `volatility_and_location.md`, `oi_pcr_gex.md`, `sentiment_and_flow.md`.

## 0. Bottom line

1. **On the smart-lane return label, the current 22-feature ranker is close to useless out of sample.** Walk-forward
   (19 test days) gives AUC 0.540, within-day rank IC on return +0.020 (t 0.8), and top-quintile mean return
   +0.085 against +0.076 for the bottom quintile. Within each side the IC is negative (CE −0.052, PE −0.030).
   The exported live model scores AUC 0.738 / IC +0.26 on these same signals, but only because it was trained on
   them (in-sample).
2. **Market-context features carry the most information, and the sign is contrarian.** Within a side and within
   a day, a signal does worse when the market has already moved its way. The features are `breadth_prev` (share of
   ~210 F&O stocks above the previous close, signed by side), `breadth_open`, `nifty_ret_prev`, `n_sig_prev`
   (Nifty move in 20-day sigmas) and `n_z5` (Nifty z-score against its 20 five-minute bars). Each has t −2.9 to −5.3,
   holds in both halves and under both exit labels, and reaches |t| ≥ 2 on both sides for breadth_prev, nifty_ret_prev and n_sig_prev. Bottom vs top within-day-side quintile of
   `breadth_prev`: mean return +0.157 vs +0.021 per trade (CE −0.003 vs −0.130; PE +0.278 vs +0.129). In plain terms,
   buy before the crowd moves, not after.
3. **For picking the symbol, relative strength against the sector helps and stretched premium/spot hurts.** `rs_sector`
   (stock move minus its sector's mean move, signed) has IC +0.056 (t 2.4) and a quintile spread of +0.10 per trade in
   both halves. Stretch against the 20-bar mean hurts: `o_z5`, `s_z5`, `o_z30` have IC −0.05 to −0.06 (t −2.3 to
   −2.7), mostly on CE. Series trend (`ser_ret`, `ser_er_signed`) is mildly negative, PE-driven and H1-heavy.
4. **Adding the new features to the ranker raises return ranking, but the gain mostly comes from choosing the side.**
   A regressor on clipped return with base + new features gets walk-forward day IC +0.159 (t 3.5) and a Q5−Q1 spread
   of +0.173 per trade. Its IC beats the current setup by +0.139 (bootstrap 95% CI +0.045 to +0.238; better on 14 of
   19 days). Within one side, though, its IC is only +0.05 (t 1.5 CE, 1.3 PE), and the Rs 50 L ranked + dispersion
   book changes by only +3 L (78.3 vs 75.3 L), which is within noise. With the intrabar-entry label, the classifier
   instead wins the book (91.4 vs 73.3 L). The lift is real for ranking but unproven for allocation P&L.
5. **Pre-cross entry, verdict: not as a forward-only rule.** Entering 5 to 30 minutes before a cross you already
   know will happen is worth 2–3x more P&L, but that is pure look-ahead. Every forward-only anticipation trigger I
   could build (18 configurations) earned less than waiting for the cross. The best was +107 L unlimited-cash with
   t 1.89 and a 39 L Rs 50 L book, against `cross_d5` (enter 5 min after the cross, the current smart lane) at
   +118 L, t 2.31 and an 85 L book with drawdown 9.8 L. Only 40–72% of triggers are followed by a cross within
   30 min. Triggers that do cross are very profitable (+0.06 to +0.11 per trade); the false positives eat the edge.
   The "earlier is better" idea does survive in another form, as the contrarian breadth effect in point 2.
6. **OI, PCR, GEX and VIX: no usable evidence, because the sample is too small.** Only 12 index signals have
   own-chain data (9 traded by the lane). NIFTY-chain context covers 1,601 signals but only 15 sessions. No
   chain feature passes |t| ≥ 2 with a stable sign. VIX change is +0.13 on CE and −0.08 on PE over 12 days, which
   cancels. **Start recording stock-option OI.**
7. **Regime caveat.** One regime only: PE made +202.6 L and CE −79.2 L under the lane label, and CE lost on 22 of
   27 days. About 330 tests were run, so roughly 16 nominal |t| > 2 results are expected by chance. Only the
   breadth/Nifty contrarian family and `rs_sector` are robust across sides, halves and both labels.

## 1. Data coverage (read-only)

| source | coverage used | notes |
|---|---|---|
| historical_candles 1-min, options | 3,447 ATM-universe contracts (prior study's `universe.pkl`), bars from 1 Aug | universe = ATM CE+PE per spot per day, front series ≥ 1 day out |
| historical_candles 1-min, spots | 214 spots (210 stocks + NIFTY, BANKNIFTY, MIDCPNIFTY, SENSEX), **3 Aug** – 1 Oct | 20-day sigma has only 15–16 prior sessions for 25–31 Aug signals (min 9 required) |
| session_profiles | 3 Aug – 1 Oct, 224 symbols | **missing 21–25 Sep**, so prior-day `day_type`/`value_migration` is missing for signals on 22–25 and 28 Sep (78% coverage). Prior-day POC/VAH/VAL was **recomputed from minute bars** for all days (0.1% bins, 70% VA; TPO weighting for indices) |
| dispersion_history (1800 s) | reconstructed 25 Aug – 28 Sep 14:45 (311 bars) + live 28 Sep 15:15 – 1 Oct (40 bars) | CE/PE breadth at the last completed 30-min bar |
| chain_snapshots | NIFTY + BANKNIFTY, **15 sessions** (3, 4, 7–11, 15–18, 28–30 Sep, 1 Oct), ~360 snapshots/day, ~50 strikes of the front expiry, VIX 10.6–15.6 | 1,601 of 3,298 signals (49%) have NIFTY-chain context; only **12** index signals have their own chain (9 entered by the lane) |
| whale_eod | 14 days per index | not used (too few days) |
| stock-option OI | **does not exist** (tick `oi` is null) | OI/GEX tests are index-only |

Signal set (reused, not rebuilt): prior study's `entries.pkl`, 3,298 MACD zero-cross signals on 30-min option-premium
bars (intrabar cross), 25 Aug – 1 Oct, 1,560 contracts. Its 22 features are the live `entry_scorer.build_features`.
The MACD state was re-derived per contract for this study and matched `entries.pkl` exactly (0 mismatches of 3,298).

## 2. Method

**Label (Task 1).** Each signal was replayed alone under the **smart-lane rules** with the prior study's simulator
(`oldapp/sim.py`; replay logic copied from `levers.py`). The entry is 5 min after the cross, taken only if MACD is
still > 0. Exits: hard stop 40%, trail 30% off peak once +40%, all out at +100%, 4-session cap (first bar ≥ 15:25 of the
4th session after entry), staged one-lot exits at +30/50/75% while > 1 lot, no pyramiding, MACD invalidation (a closed
30-min bar with MACD < 0 while MFE < 10%), and expiry-day flatten at 15:10. Ticket is Rs 1 L in whole lots with 5 bps
slippage per side. Positions still open at 1 Oct 15:30 are marked at the last close. Label = **realised return
(P&L / entry cost)** and P&L > 0.

- `d5` (the lane, the primary label): **2,363** of 3,298 signals pass the 5-min re-check. Rs **123.4 L**, win
  39.7%, mean return +5.2% per trade, CE −79.2 L, PE +202.6 L.
- `x0` (intrabar entry at the cross, same exits; robustness label): 3,295 signals, Rs 115.8 L, win 34.3%, mean +3.5%.

**Features (Task 2).** All are computed at the signal minute with no look-ahead. Spot "now" = open of the signal
minute. Bar statistics use bars completed before it, daily statistics use prior sessions, and profile data is
prior day only. Directional features are signed by side (+ = in the trade's favour). Definitions:

- *Series*: the series starts after the previous monthly expiry (last Tuesday). The anchor is that expiry day's
  close, or the previous close on the expiry day itself. `ser_ret` is the spot return since the anchor (signed).
  `ser_ret_sig` is that return / (sigma20·√sessions). `ser_pos` is the position in the series high–low range (−0.5..0.5,
  signed). `ser_er` / `ser_er_signed` is the efficiency ratio |net| / Σ|daily moves| over the series (≥ 3 sessions).
  `er10` / `er10_signed` / `ret10_sig` cover the last 10 sessions, `pos20` is the position in the 20-session range,
  and `ser_sessions` counts sessions into the series.
- *Prior-day profile*: `pd_trend`, `pd_balanced`, `pd_trend_dir` (trend-day direction, signed) and `pd_migration`
  (higher/overlapping_higher = +1/+0.5, signed) come from session_profiles. `open_vs_pva`, `spot_vs_pva` and
  `open_outside_va` are measured against the prior-day POC/VA in VA widths, signed.
- *Sigma*: `sig20` (20-day daily log-return sigma), `u_sig_prev`, `u_sig_open`, `gap_sig` (spot move / sigma20, signed),
  `u_range_sig` (day range / sigma20), `u_day_pos`. Option: `o_sig_day` (premium move vs its own daily sigma, ≥ 5
  prior sessions), `o_dsig` (that sigma), `o_sig_5m` (last 5-min premium move vs its 5-min sigma). `vix` and
  `vix_chg` (vs the prior day's last snapshot) are available on chain days only.
- *Distance from the 20-period mean*: `s_z5` / `s_z30` (spot z vs 20 five-min / thirty-min closes, signed), `o_z5` /
  `o_z30` (premium), `o_bbw5`, `o_bbw30` and `s_bbw30` (Bollinger width = 4·sd/mean). %b is a linear transform of
  z, so it was not repeated.
- *Market*: `breadth_prev` and `breadth_open` (share of stocks above the previous close / today's open − 0.5,
  signed), `sector_breadth` (same within the stock's sector, `sectors.py`), `rs_nifty` and `rs_sector` (relative
  move, signed), `n_sig_prev` and `n_z5` (Nifty), `disp_own`, `disp_other` and `disp_diff` (dispersion_history CE/PE
  breadth, own side vs other), `u_rvol` (spot cumulative volume vs the prior-10-session mean at the same minute,
  log) and `o_rvol` (the option's, vs prior 5 sessions).
- *Chain (NIFTY context for all signals on chain days; own chain for NIFTY/BANKNIFTY signals)*: PCR (OI) of the
  front expiry, PCR change vs `prev_oi`, `oi_tilt` (put-OI % change − call-OI % change, signed), `room` (distance
  to the max-OI call wall for CE / put wall for PE, as a fraction of spot), traded-strike OI change, and ATM IV.
  GEX = Σ sign·γ·OI·S²·0.01 under the **dealer short-call / long-put** convention, with Black-Scholes IV backed
  out of ltp (r = 6%, q = 0; OI taken as units, so no lot multiplier). It is normalised by Σ OI·S·0.01. The
  gamma-flip distance is where the GEX profile (±4% grid, same IVs) crosses zero nearest to spot (signed).

**Evaluation.** Each feature gets a within-side Spearman IC with return per day (≥ 8 signals in the day-side cell),
averaged into a daily value. The table reports the mean over days with t = mean/(sd/√n), split into halves (H1 =
25 Aug – 10 Sep, H2 = 11 Sep – 1 Oct). Walk-forward by day trains HGB on all earlier days and tests the next day
(first test day 4 Sep, 19 out-of-sample days), using the live hyper-parameters (depth 3, lr 0.05, 150 iterations,
leaf 60, l2 1.0). Reported per model: AUC (ret > 0); daily rank IC on return (all signals that day, and within
side); within-day quintile mean return; "skip30" (drop signals below the 30th percentile of that fold's training
scores, as live); and the **Rs 50 L book** from the prior study's allocation code (best-first at the same
timestamp, Rs 1 L tickets, capital-limited, optional dispersion sizing, min 0.25x). Permutation importance comes from
the out-of-sample folds (drop in test-day IC, 3 permutations per fold).

## 3. Task 1: return label, and how the current 22-feature GBM ranks on return

Walk-forward, 19 OOS days, label `d5`. OOS P&L of all signals = **Rs 97.6 L**.

| model | AUC | day IC on return (t) | within CE IC (t) | within PE IC (t) | within-day quintile mean return Q1 … Q5 | Q5−Q1 | skip-30% kept P&L | Rs 50 L book: first-come / ranked / ranked+disp (max DD) |
|---|---|---|---|---|---|---|---|---|
| **base22 classifier (live config)** | 0.540 | +0.020 (+0.8) | −0.052 (−1.3) | −0.030 (−0.7) | +0.076 +0.038 +0.027 +0.070 +0.085 | +0.009 | 74.2 L | 53.7 (11.9) / 63.3 (10.6) / 75.3 (10.3) |
| base22 regressor (ret clipped −0.5..1.0) | 0.527 | +0.032 (+1.0) | −0.049 (−1.1) | +0.017 (+0.3) | +0.062 +0.070 +0.019 +0.066 +0.078 | +0.016 | 86.5 L | 63.0 (8.5) / 63.7 (7.9) / 72.7 (8.5) |
| base22 + all new, classifier | 0.567 | +0.081 (+1.9) | −0.022 (−0.6) | −0.052 (−1.6) | +0.011 +0.032 +0.062 +0.085 +0.104 | +0.092 | 93.2 L | 56.0 (12.6) / 60.4 (18.8) / 70.3 (16.9) |
| base22 + in-fold IC-selected new (|t| ≥ 2 on training days only) | 0.558 | +0.065 (+1.6) | −0.018 (−0.3) | +0.011 (+0.3) | +0.017 +0.036 +0.077 +0.093 +0.071 | +0.053 | 84.9 L | 64.2 (9.7) / 56.7 (14.3) / 66.7 (15.7) |
| **base22 + all new, regressor** | **0.573** | **+0.159 (+3.5)** | +0.050 (+1.5) | +0.046 (+1.3) | −0.028 +0.049 +0.018 +0.107 +0.145 | **+0.173** | **100.0 L** | 63.6 (10.4) / 67.4 (10.4) / **78.3 (9.9)** |
| new features only, classifier | 0.564 | +0.094 (+2.1) | −0.029 (−0.7) | +0.005 (+0.1) | −0.025 +0.054 +0.089 +0.069 +0.106 | +0.132 | 91.5 L | 65.9 (11.6) / 66.1 (14.5) / 68.8 (15.6) |

- Paired day-bootstrap (5,000 draws) of the daily-IC difference vs the base22 classifier: base+new regressor +0.139
  [+0.045, +0.238], better on 74% of days; base+new classifier +0.061 [−0.018, +0.142]; new-only +0.074 [−0.020,
  +0.178]; base22 regressor +0.012 [−0.044, +0.067]. **Only the base+new regressor is clearly better.**
- Daily P&L t-stat of the kept 70% vs all OOS signals: 2.46 vs 2.04 (base+new regressor), 2.01 vs 2.04 (base22).
- Robustness label `x0` (intrabar entry, 3,295 signals). base22 classifier: AUC 0.569, day IC −0.027 (t −1.4), Q5−Q1
  +0.032, ranked+disp book 73.3 L. base+new regressor: AUC 0.574, day IC +0.120 (t 3.4), Q5−Q1 +0.105, book 75.4 L.
  base+new classifier: AUC 0.608, day IC +0.058, book **91.4 L**. base22 regressor: book 89.6 L. The book ranking of
  models flips between labels, so **book differences of ±15 L are noise at this sample size**.
- **The exported live model (`smart_model.json`) on the lane label, in-sample** (it was trained on all 27 days with
  the old-rules label): AUC 0.738, day IC +0.261 (t 11.8), Q1..Q5 −0.099 −0.011 +0.042 +0.087 +0.235. Compare with
  walk-forward AUC 0.540, which is what to expect live. The 0.587 AUC quoted in `smart_model.py` was for the
  old-rules label; on the lane-rules label it is 0.540.
- Why the pooled day IC is much higher than the within-side IC: the new features (dispersion, breadth) mostly help
  the model rank **PE over CE**, which in this window was the right call. That is a regime bet, not stock selection.

Permutation importance (base+new regressor; drop in test-day IC, mean over 19 folds, standard error in brackets):
breadth_open +0.070 (0.029), disp_own +0.054 (0.023), disp_other +0.029 (0.021), o_vol_ratio +0.015 (0.010),
log_premium +0.012 (0.006), nf_flip +0.009 (0.009), vix_chg +0.008 (0.005), u_rvol +0.008 (0.004), pos20 +0.007
(0.004), n_z5 +0.006 (0.005). Every other feature is < 0.005. Several have negative importance: breadth_prev −0.012,
o_ret_prev −0.010, dip_depth_pct −0.006, moneyness −0.005 (these overlap with breadth_open). In the classifier the
leaders are again breadth_open (+0.041) and disp_other / disp_own.

In-fold IC selection picked these features (number of 19 folds): breadth_open 19, breadth_prev 19, n_z5 19, ser_ret
19, ser_ret_sig 19, ser_er_signed 18, o_bbw5 17, o_z30 15, n_sig_prev 15, sector_breadth 13, s_z30 10, s_z5 10, o_z5 8,
nf_oi_tilt 7, rs_sector 4, other ≤ 3.

## 4. Task 2: per-feature evidence

Within-side daily rank IC vs lane return (`d5`), with the `x0` label as a robustness column. Sorted by |t| on `d5`.
In the "stable?" column, **yes** means |t| ≥ 2 on `d5`, the same sign in both halves, and |t| ≥ 1.5 with the same
sign on `x0`. "Pooled" is the IC across all signals ignoring day and side, which mixes in the side/regime effect.
`ser_sessions` is constant within a day, so it has no within-day IC (pooled −0.052 / −0.065, AUC 0.52; with two series
in the window it cannot be tested).

| feature | set | cov | d5 CE IC (t) | d5 PE IC (t) | d5 both IC (t) [days] | d5 H1 / H2 | d5 pooled IC | x0 both IC (t) | stable? |
|---|---|---|---|---|---|---|---|---|---|
| breadth_prev | new | 100% | -0.093 (-2.7) | -0.114 (-3.5) | -0.103 (-5.0) [27] | -0.093 / -0.111 | +0.092 | -0.119 (-5.3) | **yes** |
| nifty_ret_prev | base | 100% | -0.136 (-3.9) | -0.080 (-2.1) | -0.105 (-4.4) [27] | -0.094 / -0.115 | +0.075 | -0.086 (-3.9) | **yes** |
| n_z5 | new | 100% | -0.082 (-1.8) | -0.125 (-4.0) | -0.102 (-4.0) [27] | -0.087 / -0.117 | -0.005 | -0.069 (-2.9) | **yes** |
| breadth_open | new | 100% | -0.098 (-2.4) | -0.077 (-2.0) | -0.086 (-3.9) [27] | -0.104 / -0.069 | +0.112 | -0.094 (-5.0) | **yes** |
| ser_ret | new | 100% | -0.039 (-1.3) | -0.082 (-2.4) | -0.060 (-3.4) [27] | -0.086 / -0.037 | +0.075 | -0.040 (-1.8) | **yes** |
| n_sig_prev | new | 100% | -0.109 (-3.0) | -0.082 (-2.1) | -0.093 (-3.4) [27] | -0.078 / -0.108 | +0.064 | -0.089 (-3.6) | **yes** |
| ser_er_signed | new | 78% | -0.011 (-0.3) | -0.112 (-3.8) | -0.061 (-3.4) [21] | -0.100 / -0.026 | +0.110 | -0.036 (-1.5) | weak |
| ser_ret_sig | new | 100% | -0.015 (-0.5) | -0.081 (-2.7) | -0.048 (-3.2) [27] | -0.076 / -0.022 | +0.078 | -0.042 (-2.0) | **yes** |
| macd_slope_pct | base | 100% | -0.087 (-2.4) | -0.046 (-1.2) | -0.063 (-3.1) [27] | -0.077 / -0.051 | -0.067 | -0.073 (-3.4) | **yes** |
| crowd_same_side | base | 100% | -0.081 (-2.4) | -0.053 (-1.8) | -0.070 (-3.1) [27] | -0.080 / -0.060 | -0.008 | -0.023 (-1.1) | weak |
| s_z5 | new | 100% | -0.109 (-2.8) | -0.019 (-0.5) | -0.062 (-2.6) [27] | -0.053 / -0.070 | -0.020 | -0.042 (-2.7) | **yes** |
| o_z5 | new | 100% | -0.114 (-2.9) | -0.021 (-0.5) | -0.064 (-2.5) [27] | -0.043 / -0.083 | -0.026 | -0.056 (-2.7) | **yes** |
| o_range_pos | base | 100% | -0.092 (-2.6) | -0.037 (-1.1) | -0.063 (-2.4) [27] | -0.072 / -0.054 | -0.000 | -0.043 (-2.1) | **yes** |
| rs_sector | new | 97% | +0.070 (+2.0) | +0.043 (+1.1) | +0.056 (+2.4) [27] | +0.033 / +0.077 | +0.009 | +0.035 (+1.7) | **yes** |
| o_z30 | new | 100% | -0.097 (-2.9) | -0.017 (-0.5) | -0.052 (-2.3) [27] | -0.076 / -0.030 | -0.073 | -0.052 (-2.0) | **yes** |
| sector_breadth | new | 97% | -0.161 (-4.2) | +0.002 (+0.0) | -0.076 (-2.3) [27] | -0.139 / -0.018 | +0.001 | -0.052 (-1.8) | **yes** |
| o_dsig | new | 85% | +0.036 (+0.7) | +0.079 (+1.9) | +0.057 (+2.0) [25] | +0.061 / +0.054 | +0.040 | +0.044 (+1.9) |  |
| nf_oi_tilt | new | 45% | -0.093 (-1.4) | -0.062 (-1.1) | -0.076 (-1.8) [14] | -0.013 / -0.111 | +0.010 | -0.073 (-2.4) |  |
| o_sig_5m | new | 100% | -0.037 (-1.0) | -0.043 (-1.2) | -0.041 (-1.8) [27] | -0.007 / -0.072 | -0.032 | -0.027 (-1.2) |  |
| disp_diff | new | 100% | -0.050 (-1.1) | -0.073 (-1.8) | -0.060 (-1.8) [27] | +0.015 / -0.131 | +0.158 | -0.062 (-2.5) |  |
| disp_other | new | 100% | +0.041 (+0.9) | +0.072 (+1.8) | +0.056 (+1.7) [27] | -0.032 / +0.138 | -0.083 | +0.060 (+2.5) |  |
| pd_migration | new | 78% | -0.038 (-1.0) | -0.059 (-1.5) | -0.049 (-1.7) [22] | -0.041 / -0.060 | -0.040 | -0.057 (-2.5) |  |
| log_premium | base | 100% | +0.038 (+1.1) | +0.047 (+1.0) | +0.039 (+1.6) [27] | +0.024 / +0.054 | +0.048 | +0.016 (+0.8) |  |
| o_bbw5 | new | 100% | -0.045 (-1.4) | -0.024 (-0.8) | -0.032 (-1.6) [27] | -0.097 / +0.028 | -0.042 | -0.025 (-1.5) |  |
| s_z30 | new | 100% | -0.061 (-1.7) | -0.008 (-0.2) | -0.033 (-1.5) [27] | -0.068 / -0.002 | -0.055 | -0.018 (-0.8) |  |
| o_ret_open | base | 100% | -0.071 (-2.2) | +0.001 (+0.0) | -0.034 (-1.4) [27] | -0.049 / -0.019 | -0.057 | +0.002 (+0.1) |  |
| ser_er | new | 78% | +0.038 (+1.1) | -0.091 (-3.4) | -0.029 (-1.3) [21] | -0.018 / -0.038 | +0.018 | -0.034 (-1.5) |  |
| is_index | base | 100% | -0.039 (-1.1) | +0.152 (+4.4) | +0.036 (+1.2) [17] | +0.048 / +0.023 | +0.041 | +0.036 (+1.5) |  |
| rs_nifty | new | 100% | -0.014 (-0.3) | +0.077 (+1.8) | +0.032 (+1.2) [27] | -0.047 / +0.106 | -0.045 | +0.035 (+1.6) |  |
| pd_trend_dir | new | 78% | -0.047 (-1.2) | -0.018 (-0.5) | -0.032 (-1.2) [22] | -0.014 / -0.058 | -0.013 | -0.016 (-0.7) |  |
| sig20 | new | 100% | +0.047 (+1.0) | +0.024 (+0.5) | +0.035 (+1.2) [27] | -0.012 / +0.079 | +0.015 | +0.020 (+0.9) |  |
| u_vwap | base | 100% | -0.046 (-1.2) | -0.029 (-0.6) | -0.036 (-1.1) [27] | -0.037 / -0.035 | -0.035 | -0.003 (-0.1) |  |
| dip_bars | base | 100% | -0.055 (-1.4) | -0.009 (-0.2) | -0.029 (-1.0) [27] | -0.024 / -0.034 | -0.069 | -0.039 (-1.6) |  |
| disp_own | new | 100% | -0.014 (-0.3) | -0.055 (-1.2) | -0.033 (-1.0) [27] | +0.029 / -0.092 | +0.166 | -0.057 (-2.4) |  |
| macd_preview_pct | base | 100% | -0.045 (-1.1) | -0.012 (-0.3) | -0.023 (-1.0) [27] | -0.041 / -0.007 | -0.036 | -0.069 (-2.9) |  |
| open_outside_va | new | 100% | -0.025 (-0.6) | -0.023 (-0.6) | -0.023 (-1.0) [27] | -0.009 / -0.037 | -0.002 | -0.015 (-0.7) |  |
| o_bbw30 | new | 100% | +0.002 (+0.1) | +0.044 (+1.0) | +0.022 (+0.9) [27] | +0.031 / +0.014 | -0.008 | -0.011 (-0.5) |  |
| pd_balanced | new | 78% | +0.012 (+0.3) | -0.057 (-1.4) | -0.024 (-0.9) [21] | -0.008 / -0.051 | -0.003 | -0.018 (-0.9) |  |
| o_sig_day | new | 85% | -0.078 (-2.4) | +0.026 (+0.6) | -0.024 (-0.9) [25] | -0.058 / +0.003 | -0.051 | -0.022 (-1.0) |  |
| s_bbw30 | new | 100% | -0.006 (-0.2) | +0.050 (+1.0) | +0.021 (+0.9) [27] | +0.019 / +0.023 | -0.007 | -0.017 (-0.7) |  |
| u_day_pos | new | 100% | -0.069 (-1.7) | +0.010 (+0.3) | -0.027 (-0.9) [27] | -0.052 / -0.003 | +0.005 | -0.017 (-0.8) |  |
| nf_atm_iv | new | 49% | +0.013 (+0.2) | -0.058 (-1.1) | -0.028 (-0.7) [15] | +0.011 / -0.054 | +0.074 | -0.010 (-0.3) |  |
| spot_vs_pva | new | 100% | -0.046 (-1.4) | +0.075 (+1.6) | +0.018 (+0.6) [27] | -0.017 / +0.050 | +0.010 | +0.018 (+0.8) |  |
| u_ret_5d | base | 100% | -0.009 (-0.3) | -0.024 (-0.7) | -0.016 (-0.6) [27] | -0.098 / +0.061 | +0.050 | -0.018 (-0.9) |  |
| nf_room | new | 49% | +0.032 (+0.7) | +0.008 (+0.2) | +0.015 (+0.5) [15] | +0.079 / -0.028 | -0.091 | -0.009 (-0.3) |  |
| pd_trend | new | 78% | -0.016 (-0.4) | -0.003 (-0.1) | -0.009 (-0.5) [22] | -0.010 / -0.009 | +0.002 | -0.002 (-0.1) |  |
| o_vol_ratio | base | 100% | +0.034 (+1.0) | -0.011 (-0.2) | +0.010 (+0.4) [27] | -0.023 / +0.040 | -0.076 | +0.056 (+2.4) |  |
| dip_depth_pct | base | 100% | +0.052 (+1.2) | -0.024 (-0.6) | +0.012 (+0.4) [27] | -0.003 / +0.025 | +0.061 | +0.023 (+0.9) |  |
| ret10_sig | new | 100% | +0.024 (+0.9) | -0.007 (-0.2) | +0.008 (+0.4) [27] | -0.035 / +0.049 | +0.095 | +0.031 (+1.7) |  |
| vix_chg | new | 37% | +0.129 (+3.4) | -0.081 (-1.5) | +0.013 (+0.4) [12] | +0.056 / -0.008 | -0.011 | +0.003 (+0.1) |  |
| bars_seen | base | 100% | +0.025 (+0.6) | -0.011 (-0.3) | +0.009 (+0.4) [27] | +0.020 / -0.002 | +0.006 | +0.044 (+2.8) |  |
| pos20 | new | 100% | +0.037 (+1.3) | -0.020 (-0.6) | +0.009 (+0.4) [27] | -0.018 / +0.034 | +0.105 | +0.028 (+1.3) |  |
| dte | base | 100% | -0.033 (-0.5) | -0.011 (-0.1) | -0.019 (-0.4) [8] | -0.069 / +0.011 | +0.045 | -0.029 (-0.6) |  |
| er10 | new | 100% | +0.012 (+0.3) | +0.009 (+0.3) | +0.008 (+0.3) [27] | -0.019 / +0.033 | +0.014 | -0.002 (-0.1) |  |
| nf_flip | new | 49% | -0.017 (-0.3) | -0.021 (-0.4) | -0.011 (-0.3) [15] | +0.001 / -0.019 | -0.044 | -0.038 (-1.4) |  |
| nf_pcr_chg | new | 45% | -0.113 (-1.7) | +0.064 (+1.2) | -0.014 (-0.3) [14] | -0.012 / -0.015 | -0.092 | +0.018 (+0.6) |  |
| open_vs_pva | new | 100% | -0.027 (-0.7) | +0.038 (+1.0) | +0.007 (+0.3) [27] | +0.036 / -0.019 | +0.029 | +0.020 (+0.9) |  |
| o_rvol | new | 93% | +0.004 (+0.1) | +0.014 (+0.4) | +0.006 (+0.3) [27] | -0.026 / +0.037 | -0.072 | +0.008 (+0.5) |  |
| moneyness | base | 100% | -0.058 (-2.1) | +0.035 (+1.0) | -0.006 (-0.3) [27] | -0.023 / +0.010 | +0.022 | +0.010 (+0.5) |  |
| u_rvol | new | 92% | +0.009 (+0.2) | +0.010 (+0.3) | +0.007 (+0.3) [27] | -0.006 / +0.020 | -0.054 | +0.016 (+0.7) |  |
| u_sig_prev | new | 100% | -0.046 (-1.3) | +0.055 (+1.7) | +0.006 (+0.3) [27] | -0.042 / +0.050 | -0.001 | +0.016 (+0.8) |  |
| o_ret_prev | base | 100% | -0.060 (-1.7) | +0.043 (+1.2) | -0.006 (-0.3) [27] | -0.035 / +0.020 | -0.032 | -0.011 (-0.5) |  |
| u_ret_open | base | 100% | -0.025 (-0.6) | +0.033 (+1.0) | +0.006 (+0.3) [27] | -0.028 / +0.038 | -0.041 | +0.017 (+0.8) |  |
| gap_sig | new | 100% | -0.030 (-0.8) | +0.014 (+0.4) | -0.007 (-0.3) [27] | -0.028 / +0.013 | +0.028 | -0.002 (-0.1) |  |
| ser_pos | new | 100% | -0.010 (-0.3) | -0.003 (-0.1) | -0.006 (-0.2) [27] | -0.045 / +0.030 | +0.123 | +0.009 (+0.4) |  |
| minutes_since_open | base | 100% | +0.034 (+0.7) | -0.023 (-0.5) | +0.005 (+0.2) [27] | +0.021 / -0.010 | +0.001 | +0.026 (+1.3) |  |
| u_ret_prev | base | 100% | -0.044 (-1.1) | +0.047 (+1.2) | +0.005 (+0.2) [27] | -0.061 / +0.065 | -0.004 | +0.033 (+1.4) |  |
| nf_pcr | new | 49% | -0.041 (-0.7) | +0.059 (+1.1) | +0.007 (+0.2) [15] | +0.038 / -0.013 | -0.086 | +0.036 (+1.1) |  |
| crowd_other_side | base | 100% | -0.008 (-0.2) | +0.023 (+0.7) | +0.005 (+0.2) [27] | +0.021 / -0.010 | -0.030 | +0.025 (+1.1) |  |
| u_range_sig | new | 100% | +0.018 (+0.6) | -0.004 (-0.1) | +0.005 (+0.2) [27] | -0.008 / +0.017 | -0.057 | +0.014 (+0.6) |  |
| er10_signed | new | 100% | +0.011 (+0.4) | -0.005 (-0.1) | +0.003 (+0.1) [27] | -0.049 / +0.052 | +0.092 | +0.031 (+1.5) |  |
| u_sig_open | new | 100% | -0.041 (-1.1) | +0.035 (+1.0) | -0.002 (-0.1) [27] | -0.048 / +0.041 | -0.037 | +0.003 (+0.1) |  |
| nf_gex | new | 49% | -0.022 (-0.4) | +0.025 (+0.5) | -0.001 (-0.0) [15] | +0.060 / -0.042 | -0.030 | +0.016 (+0.5) |  |
| vix | new | 45% | +0.104 (+2.4) | -0.083 (-1.7) | +0.001 (+0.0) [14] | +0.003 / -0.000 | +0.058 | -0.012 (-0.4) |  |
| premium_pct_spot | base | 100% | -0.001 (-0.0) | -0.002 (-0.0) | -0.001 (-0.0) [27] | -0.051 / +0.046 | +0.010 | -0.005 (-0.2) |  |
| ser_sessions | new | 100% | nan (nan) | nan (nan) | nan (nan) [0] | nan / nan | -0.052 | nan (nan) |  |

### 4.1 Size of the effects (within-day, within-side quintiles; `d5` mean return per trade)

| feature | Q1 (least in trade's favour) … Q5 | CE Q1 / Q5 | PE Q1 / Q5 | Q1−Q5 H1 / H2 | P&L Q1 / Q5 |
|---|---|---|---|---|---|
| breadth_prev | +0.157 +0.071 +0.020 +0.005 +0.021 | −0.003 / −0.130 | +0.278 / +0.129 | +0.083 / +0.184 | 69.7 L / 9.9 L |
| breadth_open | +0.108 +0.082 +0.061 +0.035 −0.011 | −0.039 / −0.146 | +0.223 / +0.095 | +0.092 / +0.148 | 48.8 L / −6.4 L |
| nifty_ret_prev | +0.121 +0.088 +0.035 +0.019 +0.011 | −0.002 / −0.128 | +0.215 / +0.122 | +0.118 / +0.102 | 54.8 L / 5.1 L |
| n_sig_prev | +0.117 +0.078 +0.042 +0.031 +0.005 | −0.001 / −0.123 | +0.206 / +0.105 | +0.132 / +0.093 | 51.9 L / 2.7 L |
| n_z5 | +0.111 +0.083 +0.052 +0.039 −0.008 | −0.022 / −0.120 | +0.216 / +0.085 | +0.095 / +0.142 | 48.9 L / −4.9 L |
| o_z5 (premium stretch) | +0.073 +0.039 +0.081 +0.049 +0.029 | −0.046 / −0.106 | +0.164 / +0.133 | +0.036 / +0.052 | 32.9 L / 13.5 L |
| s_z5 (spot stretch) | +0.078 +0.057 +0.037 +0.083 +0.017 | −0.052 / −0.118 | +0.178 / +0.120 | +0.023 / +0.098 | 34.9 L / 7.5 L |
| crowd_same_side | +0.069 +0.099 +0.040 +0.046 +0.017 | −0.043 / −0.127 | +0.161 / +0.130 | +0.053 / +0.052 | 31.9 L / 8.6 L |
| rs_sector (higher = better) | +0.003 +0.009 +0.082 +0.058 +0.103 | −0.112 / −0.012 | +0.088 / +0.191 | −0.094 / −0.106 | 0.7 L / 49.0 L |
| ser_ret_sig | +0.058 +0.092 +0.010 +0.062 +0.046 | −0.112 / −0.086 | +0.188 / +0.147 | +0.054 / −0.030 | not stable |
| disp_diff | +0.075 +0.079 +0.046 +0.030 +0.038 | −0.066 / −0.110 | +0.181 / +0.164 | −0.019 / +0.091 | not stable |

How to read the contrarian family: these are *within-day* rankings, so they measure timing inside the day. A CE
signal that fires while most stocks and the Nifty are already up (in sigma or z-score terms) is late. The best
signals are option crosses that lead the tape. That is the user's "buy before the crowd" thesis, measured
without look-ahead.

A transparent side-neutral skip rule (rank within day and side, signs fixed from H1 and confirmed in H2): score =
rank(−breadth_prev) + rank(rs_sector) [− rank(n_z5)], skip the bottom 30%.
- `d5`, 2-feature score: kept 70% earns +0.076 per trade in H1 and +0.080 in H2. The skipped 30% earns +0.002 and
  −0.016 (all signals: +0.052).
- 3-feature score: the skipped trades lose 13.5 L in H2.
- The daily-P&L t-stat of the skipped bucket is only −0.4 to −1.5. The effect is real in per-trade terms, but
  removing 30% of trades does not remove much P&L, because the returns are skewed (the best trades sit in Q1–Q2).

### 4.2 OI / PCR / GEX / VIX (index chains only)

- **Own-chain features** (NIFTY/BANKNIFTY signals on the 15 chain days): **n = 12** signals (10 with OI change),
  10 BANKNIFTY and 2 NIFTY, 9 entered by the lane. Spearman with return: PCR −0.16, PCR change −0.73 (p 0.02,
  n = 10, 1 of 7 tests, pairs of signals on the same day), strike OI change −0.18, GEX +0.16, flip distance
  −0.03, wall room −0.20, OI tilt +0.36. **Nothing can be concluded.** The three big winners (BANKNIFTY PE 7 Sep
  +0.80, NIFTY PE 7 Sep +1.21, NIFTY PE 30 Sep +0.88) all had falling PCR and a positive put-minus-call OI-change
  tilt. That is an anecdote, not evidence.
- **NIFTY chain as market context for every signal** (1,601 signals, 15 sessions): within-side IC with t (d5 / x0).
  `nf_oi_tilt` −0.076 (t −1.8) / −0.073 (−2.4); `nf_pcr` +0.007 / +0.036; `nf_pcr_chg` −0.014 / +0.018; `nf_gex`
  −0.001 / +0.016; `nf_flip` −0.011 / −0.038; `nf_room` +0.015 / −0.009; ATM IV −0.028 / −0.010. Only
  `nf_flip` appears in permutation importance, at +0.009 ± 0.009, which is noise. With 15 independent days these
  tests have almost no power: an IC of ±0.05 needs about 40+ days to reach t = 2.
- **VIX** (12–14 days): level IC CE +0.104 (t 2.4) vs PE −0.083 (t −1.7). Change: CE +0.129 (t 3.4) vs PE −0.081.
  Rising VIX helped CE and hurt PE in this window, the opposite of the usual "VIX up = puts work". With 12 days this
  is most likely the regime: CE won only on the few up-days, which are also when VIX moved.
- GEX sign convention: dealer short calls / long puts (as requested). Under the more common SpotGamma convention
  (dealers long calls / short puts), every GEX IC flips sign. Neither convention gives |t| > 1.

### 4.3 Requested features that look useless here (|t| < 2 on both labels, no consistent sign)

gap_sig, u_sig_prev, u_sig_open, u_range_sig, u_day_pos, o_sig_day, o_sig_5m, o_rvol, u_rvol, er10, er10_signed,
ret10_sig, pos20, ser_pos, pd_trend, pd_balanced, pd_trend_dir, open_vs_pva, spot_vs_pva, open_outside_va, sig20,
s_bbw30, o_bbw30, rs_nifty, and the chain set (nf_oi_tilt is borderline: t −1.8 / −2.4 on 15 days). Partly or side-specific: sector_breadth (CE only, −0.161,
t −4.2, H1-heavy), o_dsig (+0.057, t 2.0, PE), pd_migration (−0.049 / −0.057, t −1.7 / −2.5), and ser_er_signed /
ser_ret (PE only, H1-heavy).

## 5. Task 3: pre-cross entry

### 5.1 Look-ahead upper bound: the same crosses entered k × 5 min earlier

Same lane exits. Two invalidation treatments: `e` uses the standard rule, which fires at the first 30-min close
while MACD is still < 0, so an entry in an earlier bucket is usually closed at that bar's close near cost. `g`
suspends invalidation until a closed bar has MACD > 0. `g0` is the cross entry with that same suspension, which
isolates the effect of the invalidation tweak alone. Rs 1 L tickets, unlimited cash. **These variants know that the
cross will happen, so they are not tradable.**

Intraday subset only (signal after the 09:15 minute, entry on the same day; 2,633 signals). The full set adds 228
overnight-gap crosses, where "earlier" means buying the previous afternoon before an unknown gap, which inflates
`g1` to 510.6 L.

| variant | n | win | mean ret | P&L L | H1 / H2 | CE / PE | daily t | positive days | max DD L | Rs 50 L book (DD) |
|---|---|---|---|---|---|---|---|---|---|---|
| x0 (at the cross) | 2633 | 34% | +0.040 | 104.8 | 51.7 / 53.1 | −82.0 / 186.8 | 2.06 | 56% | 27.4 | 65.9 (17.0) |
| g0 (at the cross, invalidation after the confirmed close) | 2633 | 45% | +0.051 | 132.8 | 68.5 / 64.4 | −116.7 / 249.5 | 1.98 | 59% | 38.7 | 71.6 (19.2) |
| d5 (lane: cross + 5 min) | 1923 | 39% | +0.057 | 108.2 | 53.5 / 54.8 | −60.7 / 168.9 | 2.47 | 56% | 17.4 | 66.6 (11.0) |
| e1 (5 min early) | 2633 | 50% | +0.056 | 146.5 | 36.1 / 110.4 | 5.0 / 141.5 | 3.66 | 81% | 0.9 | 139.2 (5.2) |
| e3 (15 min early) | 2633 | 55% | +0.055 | 144.2 | 36.9 / 107.3 | 12.9 / 131.3 | 3.90 | 93% | 1.5 | 137.2 (8.6) |
| e6 (30 min early) | 2633 | 55% | +0.089 | 235.2 | 75.0 / 160.2 | 44.7 / 190.5 | 4.15 | 100% | 0.0 | 222.9 (5.9) |
| g1 | 2633 | 52% | +0.107 | 281.5 | 116.5 / 165.0 | −56.3 / 337.8 | 3.68 | 81% | 6.4 | 178.3 (21.3) |
| g3 | 2633 | 56% | +0.129 | 339.3 | 132.2 / 207.1 | −33.5 / 372.8 | 4.28 | 81% | 2.4 | 228.5 (17.0) |
| g6 | 2633 | 59% | +0.155 | 407.0 | 166.8 / 240.2 | 13.4 / 393.6 | 5.28 | 100% | 0.0 | 289.3 (15.6) |

Perfect foresight of the cross is worth roughly 3x the P&L and makes even CE break even. That is the prize, and it is
what a "will this cross within N bars" model would be trying to capture.

### 5.2 Forward-only anticipation rule (tradable)

Scan of all 3,447 ATM contracts on in-universe days, excluding expiry day and requiring MACD count > 26, with at most
one trigger per contract per 30-min bucket and no new position while the previous one on that contract is open.

Trigger: the committed MACD is ≤ 0, and the forming bar's preview is still ≤ 0 but the premium is within x% of the
exact **cross price** p\* = (slow·(1−β) − fast·(1−α)) / (α − β), with α = 2/13 and β = 2/27. That is the premium at
which the intrabar MACD turns positive. Filters:
- `none`
- `rise5`: price > close 5 min ago > close 10 min ago
- `hist2`: the 30-min MACD histogram (signal line 9) rose over the last 2 closed bars

Invalidation is either `std` or `grace`, as in 5.1. Exits are the same lane exits. Precision = share of triggers
followed by an actual intrabar cross on the same contract within 30 / 60 min.

| strategy | trades | win | mean ret | P&L L | H1 / H2 | CE / PE | mean daily L | daily t | + days | max DD L | Rs 50 L book (DD) | precision 30 / 60 min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| cross_x0 (at cross) | 3168 | 34% | +0.035 | 112.0 | 56.9 / 55.0 | −106.8 / 218.8 | 4.15 | 1.80 | 59% | 32.6 | 37.2 (20.1) | – |
| cross_x0_grace | 2312 | 46% | +0.050 | 115.6 | 59.6 / 56.0 | −106.4 / 222.0 | 4.28 | 1.85 | 52% | 33.7 | 70.5 (22.8) | – |
| **cross_d5 (current lane)** | 2348 | 39% | +0.050 | **118.0** | 59.7 / 58.3 | −82.7 / 200.7 | 4.37 | **2.31** | 59% | **20.7** | **85.3 (9.8)** | – |
| ant 3% none std | 2833 | 29% | +0.006 | 15.8 | 17.7 / −1.9 | −65.5 / 81.3 | 0.58 | 0.74 | 70% | 22.0 | 9.9 (18.8) | 66% / 71% |
| ant 3% none grace | 1865 | 46% | +0.039 | 72.1 | 48.6 / 23.5 | −97.4 / 169.5 | 2.67 | 1.80 | 70% | 25.5 | 39.2 (20.3) | 70% / 75% |
| ant 3% rise5 std | 1836 | 28% | −0.003 | −5.5 | 2.4 / −7.9 | −45.9 / 40.5 | −0.20 | −0.37 | 52% | 16.9 | −2.8 (19.1) | 69% / 74% |
| ant 3% rise5 grace | 1419 | 45% | +0.031 | 43.3 | 40.5 / 2.8 | −76.5 / 119.8 | 1.60 | 1.40 | 63% | 26.1 | 16.2 (26.2) | 72% / 77% |
| ant 3% hist2 std | 1515 | 29% | +0.002 | 2.4 | 4.2 / −1.8 | −39.2 / 41.6 | 0.09 | 0.18 | 52% | 11.8 | 2.0 (12.1) | 67% / 72% |
| ant 3% hist2 grace | 1182 | 47% | +0.025 | 29.1 | 19.2 / 9.9 | −62.4 / 91.5 | 1.08 | 1.17 | 52% | 16.9 | 21.8 (19.0) | 71% / 75% |
| ant 7% none std | 4185 | 34% | +0.010 | 40.7 | 15.8 / 24.9 | −73.4 / 114.1 | 1.51 | 1.53 | 67% | 11.8 | 34.9 (15.0) | 56% / 64% |
| ant 7% none grace | 2249 | 47% | +0.044 | 98.1 | 56.4 / 41.7 | −113.6 / 211.7 | 3.63 | 2.16 | 67% | 27.9 | 18.6 (19.4) | 62% / 70% |
| ant 7% rise5 std | 3002 | 32% | +0.002 | 7.9 | −2.0 / 10.0 | −51.9 / 59.8 | 0.29 | 0.50 | 52% | 10.1 | 17.6 (13.4) | 60% / 67% |
| ant 7% rise5 grace | 1883 | 46% | +0.030 | 54.6 | 34.1 / 20.5 | −101.2 / 155.8 | 2.02 | 1.53 | 63% | 28.7 | −10.3 (30.3) | 65% / 72% |
| ant 7% hist2 std | 2139 | 33% | +0.008 | 17.5 | 6.6 / 10.9 | −41.6 / 59.1 | 0.65 | 1.07 | 52% | 9.1 | 11.6 (12.3) | 61% / 69% |
| ant 7% hist2 grace | 1480 | 47% | +0.034 | 49.8 | 28.3 / 21.5 | −74.3 / 124.1 | 1.84 | 1.54 | 44% | 21.9 | 27.0 (18.6) | 64% / 72% |
| ant 15% none std | 6777 | 37% | +0.007 | 49.7 | 22.0 / 27.7 | −99.9 / 149.6 | 1.84 | 1.16 | 56% | 29.1 | 46.7 (18.2) | 36% / 48% |
| ant 15% none grace | 2780 | 48% | +0.039 | 107.2 | 46.2 / 61.0 | −155.3 / 262.6 | 3.97 | 1.89 | 63% | 28.6 | 39.3 (20.1) | 40% / 51% |
| ant 15% rise5 std | 5429 | 36% | +0.001 | 6.4 | −0.1 / 6.5 | −75.9 / 82.3 | 0.24 | 0.26 | 44% | 23.3 | 12.2 (22.1) | 39% / 51% |
| ant 15% rise5 grace | 2508 | 48% | +0.030 | 75.8 | 36.7 / 39.1 | −148.3 / 224.1 | 2.81 | 1.57 | 59% | 30.3 | 20.7 (28.7) | 43% / 55% |
| ant 15% hist2 std | 3426 | 37% | +0.009 | 31.4 | 14.0 / 17.4 | −50.0 / 81.4 | 1.16 | 1.30 | 48% | 12.7 | 14.0 (17.3) | 43% / 55% |
| ant 15% hist2 grace | 1848 | 48% | +0.045 | 84.0 | 44.8 / 39.1 | −97.1 / 181.0 | 3.11 | 2.05 | 52% | 20.4 | 42.0 (12.2) | 45% / 58% |

Recall (share of actual crosses with a trigger on the same contract in the prior 30 min) and median lead:
3% none std 61.5% / 7 min; 7% none std 75.6% / 12 min; 15% none std 78.3% / 19 min; grace variants 29–50% (fewer
triggers fire because positions stay open longer).

P&L of triggers split by whether a cross followed within 30 min:

| config | crossed: n, mean ret, P&L | not crossed: n, mean ret, P&L |
|---|---|---|
| 7% none std | 2354, +0.041, +98.3 L | 1831, −0.031, −57.5 L |
| 15% none std | 2464, +0.071, +176.8 L | 4313, −0.029, −127.2 L |
| 15% none grace | 1116, +0.109, +122.1 L | 1664, −0.009, −14.9 L |
| 15% hist2 grace | 825, +0.093, +77.2 L | 1023, +0.006, +6.7 L |

**Verdict.**
- Every forward-only anticipation rule makes **less** total P&L, has a lower per-trade return (best +0.045 vs +0.050)
  and a weaker daily t-stat (best 2.16 vs 2.31) than `cross_d5`, the current lane entry. Most rules also have a
  larger drawdown.
- In the capital-limited Rs 50 L book, `cross_d5` wins easily: 85.3 L, DD 9.8 L, against ≤ 46.7 L for any trigger.
  Triggers tie up capital in false starts.
- Waiting for the cross and then 5 more minutes acts as a free false-positive filter. It is worth more than the
  cheaper entry price.
- Where the money would come from: triggers that do cross return 2–3x the cross entry, and the false ones are only
  mildly negative. A **cross-probability model** on the trigger (features from section 4: breadth against the
  trade, `rs_sector`, distance to p\*, the speed of approach) is the one avenue left to test. It would need precision
  well above 70% at reasonable recall to beat `cross_d5`.
- Separately, suspending invalidation until the confirmed close (`grace`) helps every anticipation rule and gives
  +3.6 L at the cross (`cross_x0_grace` vs `cross_x0`). It is not a significant change for the lane.
- Configurations tried in Task 3: 15 look-ahead variants × 2 subsets + 21 forward strategies.

## 6. Task 4: right symbol (cross-sectional within the same day and 30-min bucket)

Spearman IC of feature vs lane return among signals in the same (day, 30-min bucket), using buckets with ≥ 5
signals (190 buckets in total, 145 within-side). Directional features are signed, so the all-sides column also
captures side choice inside the bucket. t_day = t over days of the per-day mean bucket IC, the honest clustering.

| feature | all-sides bucket IC | t | t_day | within-side bucket IC | t | t_day |
|---|---|---|---|---|---|---|
| o_z5 (premium stretched vs 20 × 5-min) | −0.061 | −2.3 | **−3.1** | −0.055 | −1.6 | **−2.5** |
| pos20 (spot in 20-day range, signed) | +0.080 | +3.2 | **+2.8** | +0.015 | +0.5 | −0.0 |
| o_dsig (option's own daily sigma) | +0.089 | +3.1 | **+2.7** | +0.029 | +0.8 | +1.1 |
| MODEL base22+new regressor (walk-forward) | +0.136 | +3.7 | **+2.6** | +0.042 | +1.1 | +1.7 |
| log_premium | +0.061 | +2.5 | **+2.5** | +0.061 | +2.1 | +1.6 |
| macd_slope_pct | −0.067 | −2.5 | −2.4 | −0.007 | −0.2 | −0.9 |
| o_z30 | −0.090 | −3.4 | −2.3 | −0.030 | −0.9 | −1.2 |
| ser_pos | +0.064 | +2.5 | +2.0 | +0.012 | +0.4 | +0.1 |
| er10_signed | +0.068 | +2.8 | +1.9 | −0.012 | −0.4 | −0.4 |
| s_z30 | −0.096 | −3.5 | −1.8 | −0.057 | −1.7 | −2.1 |
| er10 (trend vs bracketing, unsigned) | −0.030 | −1.2 | −1.3 | −0.090 | −2.9 | **−2.4** |
| u_ret_5d | −0.001 | −0.0 | −0.6 | −0.058 | −1.9 | **−2.4** |
| o_bbw30 | +0.037 | +1.4 | +1.2 | +0.075 | +2.3 | +1.9 |
| rs_sector | +0.005 | +0.2 | +0.7 | +0.044 | +1.3 | +1.1 |
| u_rvol (relative spot volume) | −0.069 | −2.5 | −1.3 | −0.012 | −0.4 | −0.4 |
| o_rvol / o_vol_ratio | −0.032 / −0.056 | −1.1 / −1.9 | −0.4 / −1.7 | +0.048 / +0.010 | +1.4 / +0.3 | +1.6 / +0.4 |
| gap_sig | +0.007 | +0.3 | +0.1 | −0.007 | −0.2 | −0.1 |
| u_sig_prev / u_sig_open | −0.043 / −0.056 | −1.7 / −2.1 | −0.3 / −0.6 | −0.034 / −0.020 | −1.1 / −0.6 | −1.0 / −0.1 |
| rs_nifty | −0.066 | −2.7 | −1.4 | −0.039 | −1.2 | −1.3 |
| MODEL base22 classifier (live config) | +0.002 | +0.1 | −0.2 | −0.017 | −0.5 | −0.3 |

Reading:
- The current ranker has **zero** ability to pick the better symbol among simultaneous signals (bucket IC +0.002).
- The base+new regressor has some (+0.136 across sides, t_day 2.6), but within one side it is weak (+0.042, t_day 1.7).
- Within a side, the most consistent symbol pickers are:
  - a premium that is **not** stretched above its 20 × 5-min mean (`o_z5`);
  - an underlying that has **not** been trending smoothly over 10 sessions (`er10` negative, i.e. bracketing names do
    better than trending ones);
  - an underlying that has **not** run in the trade direction over 5 days (`u_ret_5d`).
- Relative volume and gap/sigma moves are not useful for symbol choice here.
- Bucket tests: 48 features × 2 groupings.

## 7. Statistical hygiene and fragility

- Tests run:
  - feature ICs: 76 features × 2 labels = 152;
  - walk-forward models: 6 × 2 labels = 12, with permutation importance on 2;
  - look-ahead entry variants: 15 × 2 subsets = 30;
  - forward anticipation strategies: 21;
  - bucket ICs: 48 × 2 = 96;
  - quintile tables: 13;
  - simple scores: 3 × 2 = 6.

  That is **about 330**. At 5% two-sided, about 16 nominal |t| > 2 results are expected by chance. Treat single
  results with |t| between 2 and 3 as unconfirmed.
- Only **27 sessions** and two expiry series (Aug→Sep, Sep→Oct), in one regime. Put-favouring: lane P&L PE
  +202.6 L vs CE −79.2 L, and CE lost on 22 of 27 days (PE lost on 5). Every pooled result partly measures "was it a
  PE". This is why within-side ICs are reported throughout. Features that mainly help pick the side (dispersion,
  breadth level, VIX) will reverse value in a call-favouring month.
- The chain data covers 15 days; own-chain index signals number 12. Series-level features (`ser_sessions`, series
  return as a day-level regime variable) have effectively 2 independent observations.
- The look-ahead variants in 5.1 are not tradable and are reported only as an upper bound.
- Walk-forward AUC of the live configuration on the lane label (0.540) is below the 0.587 quoted for it. Expect the
  live ranker's skip-30% to add little: 74.2 L kept vs 97.6 L for all OOS signals.

## 8. Recommendations

**Ranker (smart lanes).** Shadow-test first, then replace if confirmed live:
1. Switch the label from P&L > 0 to **realised lane return** (clipped −0.5..+1.0) with a regressor. This was the only
   model with a CI-confirmed IC gain: +0.139 [+0.045, +0.238].
2. Add these features, all computable live from data the engine already streams:
   - `breadth_prev` and `breadth_open` (share of F&O stocks above the previous close / open, signed by side). Most
     robust single feature (t −5.0 / −5.3 on the two labels).
   - `n_sig_prev` = Nifty move from the previous close / Nifty 20-day sigma (signed) and `n_z5` (Nifty z vs 20 × 5-min
     bars). These replace or complement `nifty_ret_prev`.
   - `rs_sector` (stock move minus sector mean, signed): the best within-side symbol picker among the new features.
   - `o_z5` and `s_z5` (premium and spot z vs 20 × 5-min bars): stretch penalty, mostly CE.
   - `disp_own` and `disp_other` (dispersion_history breadth): important in the model, but mostly a side/regime
     selector. Keep it, and keep dispersion sizing, knowing it is regime-dependent.
   - Optional and weaker: `er10` (bracketing vs trend over 10 sessions, for symbol choice), `o_dsig`, `pd_migration`.
3. Do **not** add: gap/sigma-move features, relative volume, prior-day day_type, VA location, series position or
   sessions-into-series, VIX, or any OI/PCR/GEX feature. None showed evidence here, and the chain features cannot
   be validated on 15 days.
4. A simple side-neutral guard can run alongside the model: skip a signal in the bottom 30% of its side's same-day
   rank on rank(−breadth_prev) + rank(rs_sector). Per-trade return of the kept signals was +0.076 / +0.080 (H1 / H2)
   vs +0.052 for all.
5. Keep the entry at **cross + 5 min**. Do not switch to anticipation entries. If anticipation is pursued, build it
   as a separate shadow lane gated by a cross-probability model, and require precision > 70% before risking capital.
6. Retrain nightly on lane-rule outcomes (still not implemented per the smart-lanes notes). Report walk-forward AUC,
   within-side IC, and Q5−Q1 return, not in-sample AUC (the in-sample 0.738 vs walk-forward 0.540 gap is the warning).

**Data to start recording now:**
- **Stock-option OI and OI change per minute** (or at least every 5 min) for every universe contract, and ideally the
  ATM ±5-strike chain for each F&O stock at 1–5 min. Without it no OI, PCR or GEX hypothesis can be tested on the
  ~97% of signals that are stock options.
- Full NIFTY/BANKNIFTY chain snapshots **every session**. chain_days is missing 21–25 Sep, so the series has gaps.
  Include all expiries, not only the front one, plus futures OI.
- India VIX as a 1-min series (currently only inside chain_snapshots).
- Fix the session_profiles gap: rows for 21–25 Sep are missing. Make the nightly profile build fill missing days
  automatically.
- Persist the live ranker inputs and score for every signal (including skipped ones) together with the realised lane
  outcome, so the label/feature table grows without replays.
- Log anticipation triggers (premium within x% of the cross price p\*) in shadow mode, with whether and when the
  cross happened. That supplies training data for a cross-probability model at no risk.

## 9. What could not be done (time-boxed)

- The cross-probability model for anticipation triggers (5.2) was not built. The features above would have to be
  recomputed per trigger (about 2,000–7,000 triggers).
- Bollinger features were computed from bars directly rather than read from historical_indicators (z-score and width
  are equivalent to %b and bandwidth). KAMA was not tested.
- Expiry-series features were defined on the monthly series for all underlyings, including weekly-expiry
  NIFTY/SENSEX (8 signals).
- GEX uses BS with r = 6%, q = 0, the front expiry only, and assumes chain OI is in units. The chain stores ~50
  strikes, so walls and flip points outside that window are invisible.
- Sector breadth uses the hand-maintained `sectors.py` map ("Other" is excluded).
- The Rs 50 L book uses Rs 1 L tickets (the prior study's convention), not the live lanes' 10%-of-equity sizing.

## 10. Scripts and outputs

All scripts and outputs are in
`/private/tmp/claude-501/-Users-ramachandran-MACD-mini-ADV/77c677ac-a2ad-4b4d-977f-6fcea088fa80/scratchpad/feature_study/`.
They run with `run.sh <script>`, which starts a throwaway `macd-mini-parallel-engine` container. That container
mounts the data volume (opened `mode=ro`), the prior study at `/r` (read-only, for `entries.pkl`, `universe.pkl`,
`dispersion.pkl`, `smart_model.json` and `pylib/` numpy/scipy/sklearn), `oldapp` at `/old` (`sim.py`,
`universe.py`) and `engine/src` (read-only).

| script | does | output |
|---|---|---|
| `lane_replay.py` | per-contract MACD state; lane-rule replays of the 3,298 signals (x0, g0, d5, e1–e6, g1–g6); forward-only scan of 3,447 contracts (cross_x0 / x0_grace / d5 + 18 anticipation configs) | `sig_variants.pkl`, `scan.pkl`, `lane_replay.log` |
| `features.py` | the 54 new features + 7 own-chain index features at signal time | `newfeat.pkl`, `features.log` |
| `eval_features.py <d5\|x0>` | Task 1/2/4: IC tables, walk-forward models, books, permutation importance, bucket ICs | `eval_d5.log`, `eval_x0.log`, `ic_*.json`, `wf_preds_*.pkl` |
| `extra.py` | live exported model in-sample on the lane label; day-bootstrap of model differences | stdout (in section 3) |
| `quint.py`, `simple_score.py` | quintile effect sizes; transparent side-neutral skip score | stdout (in 4.1) |
| `precross_eval.py` | Task 3 tables | `precross_eval.log` |
| `ix.py` | the 12 own-chain index signals | stdout (in 4.2) |
| `mktable.py` | builds the IC table in section 4 | `ic_table.md` |
| helpers | `run.sh`, `schema.py`, `coverage.py`, `peek.py`, `chk.py`, `patch_feat.py`, `patch_eval.py` | |
