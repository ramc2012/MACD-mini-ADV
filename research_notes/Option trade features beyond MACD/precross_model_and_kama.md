# Pre-cross cross-probability model, and KAMA features

Study date 3 Oct 2026. This follows up `empirical_study.md` (same folder) on two items that study left undone:

1. A model that predicts whether an early "pre-cross" trigger will actually become a MACD zero cross, and an
   entry policy built on it.
2. KAMA features.

Data: 25 Aug – 1 Oct 2026 (27 sessions). This was analysis only. No engine code, config or database was changed,
and every SQLite file was opened `mode=ro` from a throwaway container. Exits, costs and the simulator are the
earlier study's, reused unchanged (`feature_study/lane_replay.py`, `oldapp/sim.py`).

## 0. Bottom line

1. **The cross is predictable, but predicting it does not make money.** A shallow HGB classifier predicts "cross
   within 30 min" with walk-forward AUC **0.832** (better than 0.5 on all 19 test days; CE 0.818, PE 0.848) and is
   well calibrated (Brier 0.165 against 0.258 for the base rate). It reaches **precision 83% at p ≥ 0.7** (recall
   43%) and 88% at p ≥ 0.8, so the earlier study's "> 70% precision" bar is met. Yet predicted P(cross) is
   *negatively* related to the payoff of entering at the trigger:
   - std exits: day IC −0.067, t −3.4;
   - grace exits: IC −0.005.

   High-probability triggers are the ones already sitting on the cross price, so entering there gains almost
   nothing over cross+5min. The money is in crosses nobody expected. Among triggers that did cross, those the model
   gave p < 0.2 returned +0.206 (grace) / +0.089 (std) per trade, against +0.101 / +0.023 for p ≥ 0.8.
2. **No model-gated policy beats the cross+5min baseline with any significance.** The 16 policies below use
   thresholds chosen on training days only, over 19 out-of-sample days (OOS), against a baseline of **98.2 L,
   daily t 1.96, Rs 50 L book 46.8 L (DD 11.1)**:
   - The 8 hybrids gain −6.7 to +18.2 L of unlimited-cash P&L. The paired daily-difference t is ≤ 1.08 for every
     one of them.
   - Every trigger-only policy loses to the baseline, by 3.7 to 89.6 L.
   - Ungated triggers with grace exits (+24.1 L, t_d 0.98) do as well as any gated version. So the small gain
     comes from the grace exit rule (holding through a failed first bar), and almost all of it is PE in a
     put-favouring month. It does not come from the model.
3. **The capital-limited book is mixed.** Two std-exit hybrids, the trigger-P&L regressor (`Rs`) and the
   delta-vs-waiting regressor (`Ds`), give a Rs 50 L book of 76.6 / 83.0 L against 46.8 L, at the same drawdown. They
   are never more than 0.6 L below the baseline in 5 capital and half-period checks, and up to +36 L above it.
   - These are 2 of 16 configurations.
   - Their unlimited-cash gain is only +8.1 / +7.4 L (t_d 0.80 / 0.61).
   - Book results swing ±30 L with the capital level.

   Treat this as a reason to shadow-log, not to trade.
4. **The earlier study's book comparison for anticipation rules was biased against them.** Its convention was to
   replay each contract first and then apply the cash filter. Under it, an entry that could not be funded still
   blocked that contract's later cross. On the same trades:
   - look-ahead "oracle" hybrid: −1.2 L that way, 105.8 L with the integrated replay used here;
   - baseline over 27 days: 85.1 L vs 74.9 L.

   The unlimited-cash and t-stat conclusions of that study are unaffected.
5. **Verdict on "enter before the cross with a model": no, not now.** Keep the cross+5min entry. If anything is
   pursued, it is the shadow lane in section 6.
6. **KAMA: no incremental value.**
   - Of 33 KAMA features (option and spot, 30-min and 5-min, plus the table values), two pass |t| ≥ 2 on both
     labels with a stable sign: `s5_er` (the spot 5-min efficiency ratio, IC −0.064, t −3.2 / x0 −0.039, t −2.1)
     and `o30_dist` (premium vs 30-min KAMA, −0.053 / −0.067).
   - `o30_dist` is 0.90 rank-correlated with the existing `macd_slope_pct`. `s5_er` repeats the earlier "trending
     = late" finding (`er10`, contrarian stretch) and is CE-driven.
   - Adding KAMA to the earlier best walk-forward regressor changes day IC by −0.015 [−0.042, +0.011] on d5 and
     +0.010 [−0.013, +0.030] on x0.
   - The engine's optional KAMA entry filter (`require_kama_*`) is true on only **14 of 3,295** MACD-cross signals
     (0.4%). Do not enable it on this lane.

## 1. Method: Task 1 (cross-probability model)

**Trigger events.** I used the widest member of the earlier family (`ant 15% none`), evaluated at every completed
5-min window, so that the model, not a fixed rule, decides how close is close enough. `dist_pstar`, `rise5` and
`hist_up` are features rather than filters. A window end on an ATM-universe contract counts as a trigger when all of
these hold:
- it is an in-universe day and not the expiry day;
- the committed 30-min MACD has count > 26 and is ≤ 0;
- the current 30-min bucket has **not yet crossed intrabar**;
- the premium is within 15% below the exact cross price p\*;
- the decision is before 15:30.

That gives **24,275** triggers in 27 days, 13,891 of them on the 19 OOS days (4 Sep – 1 Oct).

**Entry and labels.**
- Entry is at the window's last close, and the entry time is the end of that minute.
- Exits are the smart-lane rules: 40% hard stop, 30% trail once +40%, +100% target, staged one-lot exits at
  +30/50/75%, 4-session cap, expiry flatten 15:10, Rs 1 L whole lots, 5 bps slippage per side, and
  `exit_sim` reused unchanged.
- Each trigger is replayed under two invalidation modes:
  - `std`: the first 30-min close with MACD < 0 and MFE < 10% exits;
  - `grace`: invalidation applies only after a closed bar has had MACD > 0.
- `y6` / `y12` = an intrabar cross on the same contract within 30 / 60 trading minutes.
- Base rates: y6 0.374 (CE 0.337, PE 0.410); y12 0.513 (CE 0.460, PE 0.565).
- Mean trigger return under std is +0.0013 overall (y6 = 1: +0.032; y6 = 0: −0.017). Under grace it is +0.045
  (y6 = 1: +0.103; y6 = 0: +0.011).

**Features at trigger time** (34 features; side is deliberately excluded; directional features are signed by side):
- *MACD state*: `dist_pstar` (p\*/price − 1), `macd_pct` (committed MACD / price), `pv_pct` (intrabar MACD at the
  current price / price), `pv_rng` (intrabar MACD / range of the last 20 committed MACD values), `h_last`,
  `h_slope`, `h_acc` (histogram level, slope and acceleration of the last 3 closed bars / price), `hist_up`,
  `bars_since_pos` (30-min bars since the committed MACD was last > 0), `min_since_cross`.
- *Projection*: `pv_slope` (slope of the intrabar MACD over the last three 5-min samples / price) and `proj_bars`
  (−MACD / slope, the projected number of 5-min bars to the cross, capped at 99).
- *Premium*: `o_r5`, `o_r15`, `o_r30` (log returns), `rise5`, `o_z5`, `o_z30` (z vs 20 five-min / thirty-min
  closes), `o_rvol`, `log_premium`.
- *Underlying*: `u_r15`, `u_r30`, `u_sig_prev`, `s_z5`, `u_rvol`.
- *Market context* (the robust set from the earlier study): `breadth_prev`, `breadth_open`, `n_sig_prev`, `n_z5`,
  `rs_sector`, `sector_breadth`.
- *Other*: `tod`, `dte` and `is_index`.

The market grid was rebuilt per minute (`grid.py`) with the same definitions as `features.py`.

**Models.** All use the live hyper-parameters: depth 3, lr 0.05, 150 iterations, leaf 60, l2 1.0.
- `C6` / `C12`: HGB classifiers on y6 / y12.
- `Rs` / `Rg`: HGB regressors on the trigger return clipped to −0.5..1.0, std / grace.
- `Ds` / `Dg` (an extra I added): a regressor on the *incremental* value of entering early, i.e. clipped trigger
  return minus the clipped return of the lane's cross+5min trade on that contract if a cross follows within
  60 min (0 otherwise).

**Walk-forward by day.** For test day t (day index ≥ 8), each model is trained on earlier days, **purged** so that no
training label depends on day-t prices:
- regressors keep only triggers whose exit (and, for D, the alternative's exit) happened before day t's open;
- classifiers drop the last 60 minutes of day t−1.

The earlier study did not purge, because its 4-session holds can reach into the test day.

**Thresholds** are chosen per test day on training days only. Inner 3-block (by day) cross-fitted predictions on
the training days are replayed on days ≤ t−5, which are fully resolved under the 4-session cap. The threshold that
maximises that policy's P&L is picked from these grids:
- classifiers: {0.4, 0.5, …, 0.9, never};
- regressors: {0, 0.025, 0.05, 0.1, 0.15, 0.2, never}.

"never" turns the hybrid into the baseline.

**Policies** (per contract, no overlapping positions, never two entries in one contract):
- `hybrid`: enter at a trigger whose score clears the threshold; otherwise wait for the cross and enter cross+5min.
- `trig`: gated trigger entries only.
- Baseline: cross+5min only. Over all 27 days the replay reproduces the earlier `cross_d5`: 2,349 trades, 117.9 L,
  t 2.31, CE −82.7 / PE +200.6, against 2,348 / 118.0 / 2.31.

**Book.** Rs 50 L, Rs 1 L tickets, run as an integrated chronological replay across contracts:
- an entry that cannot be funded does not block its contract;
- exits come before entries in the same second;
- among same-second entries, lane crosses go first and triggers follow best-first by model score.

The earlier convention ("bk2") is shown in 2.4 for comparison. Paired test: the daily P&L difference against the
baseline on the same 19 days (`t_d`).

## 2. Results: Task 1

### 2.1 Cross classifiers (OOS, 19 days, 13,891 triggers)

| model | base rate | AUC pooled | mean daily AUC (days > 0.5) | CE / PE AUC | Brier (base-rate Brier) |
|---|---|---|---|---|---|
| C6 (cross ≤ 30 min) | 0.424 | **0.832** | 0.808 (19/19) | 0.818 / 0.848 | 0.165 (0.258) |
| C12 (cross ≤ 60 min) | 0.564 | 0.809 | 0.779 (18/19) | 0.800 / 0.823 | 0.177 (0.261) |

Precision and recall of C6 (C12 in brackets):

| threshold | precision | recall | share of triggers flagged |
|---|---|---|---|
| p ≥ 0.4 | 0.703 (0.734) | 0.744 (0.826) | 0.449 (0.635) |
| p ≥ 0.5 | 0.742 (0.775) | 0.657 (0.747) | 0.376 (0.544) |
| p ≥ 0.6 | 0.786 (0.817) | 0.556 (0.663) | 0.300 (0.458) |
| p ≥ 0.7 | 0.829 (0.854) | 0.425 (0.554) | 0.218 (0.366) |
| p ≥ 0.8 | 0.879 (0.887) | 0.269 (0.400) | 0.130 (0.254) |
| p ≥ 0.9 | 0.920 (0.932) | 0.076 (0.181) | 0.035 (0.109) |

Calibration of C6 by decile of predicted p. Each cell is mean predicted / observed; the last column is the mean
trigger return std / grace. The model is calibrated, slightly under-confident in the middle deciles, and **the
return is flat or U-shaped across deciles**.

| decile | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| pred / obs | .044/.052 | .083/.096 | .130/.166 | .197/.231 | .287/.341 | .400/.436 | .534/.562 | .662/.671 | .779/.790 | .887/.896 |
| ret std / grace | +.004/+.075 | −.003/+.044 | +.001/+.052 | +.003/+.047 | +.008/+.031 | +.003/+.011 | −.005/+.041 | +.005/+.051 | −.003/+.044 | +.018/+.081 |

- Single-feature AUCs on y6:
  - `proj_bars` (projected bars to the cross): 0.698;
  - `dist_pstar`: 0.685;
  - `pv_rng`: 0.666;
  - `o_r15`: 0.620;
  - `breadth_prev`: 0.593 (crosses come more readily when the market is already moving the trade's way, which is
    the opposite of its contrarian effect on return).
- Permutation importance (drop in test-day AUC): o_z30 0.036, dist_pstar 0.034, pv_pct 0.027, tod 0.023,
  macd_pct 0.020, pv_rng 0.005, h_slope 0.004, pv_slope 0.003, s_z5 0.003. Everything else is ≤ 0.002.

### 2.2 Does the score rank trigger returns? (OOS, within-day rank IC; within-day quintile mean return Q1 → Q5)

| score vs return | day IC (t) | CE IC (t) | PE IC (t) | Q1 … Q5 |
|---|---|---|---|---|
| C6 vs std return | **−0.067 (−3.4)** | −0.086 (−4.2) | −0.086 (−3.9) | +.013 −.003 −.002 +.003 +.005 |
| C6 vs grace return | −0.005 (−0.2) | +0.028 (+0.6) | −0.052 (−1.4) | +.068 +.045 +.030 +.037 +.058 |
| C12 vs grace return | +0.003 (+0.1) | +0.011 (+0.2) | −0.033 (−0.8) | +.057 +.034 +.032 +.053 +.063 |
| Rs vs std return | +0.039 (+1.5) | −0.019 (−1.0) | +0.049 (+2.3) | +.003 +.002 −.002 +.006 +.008 |
| Rg vs grace return | +0.075 (+1.0) | +0.092 (+2.8) | +0.046 (+1.1) | +.031 +.033 +.032 +.066 +.076 |
| Ds vs std return | +0.000 (+0.0) | +0.001 | +0.005 | +.004 +.001 +.001 +.002 +.008 |
| Dg vs grace return | +0.063 (+0.8) | +0.058 (+1.5) | +0.051 (+1.1) | +.036 +.041 +.037 +.025 +.100 |

Why P(cross) does not pay: OOS triggers by predicted band and actual outcome. Each cell is n / mean return std /
mean return grace / median distance to p\* / median grace MFE.

| C6 band | crossed ≤ 30 min | not crossed |
|---|---|---|
| p < 0.2 | 597 / **+0.089 / +0.206** / 13.1% / +0.51 | 4,345 / −0.012 / +0.035 / 13.9% |
| 0.2–0.5 | 1,421 / +0.044 / +0.102 / 10.5% / +0.42 | 2,308 / −0.017 / −0.021 / 11.3% |
| 0.5–0.8 | 2,286 / +0.023 / +0.079 / 8.7% / +0.40 | 1,129 / −0.049 / −0.040 / 8.4% |
| p ≥ 0.8 | 1,587 / +0.023 / +0.101 / 4.9% / +0.38 | 218 / −0.056 / −0.031 / 5.7% |

- Crossed triggers carry 614 L of grace P&L (counted with overlaps). Of that, 272.5 L sits in p < 0.5 triggers and
  only 160.5 L in p ≥ 0.8.
- The false positives the model does let through at high p are the worst trades (−0.05 std).
- **CE triggers lose under grace exits even when the cross happens**: CE crossed-trigger grace mean is −0.026 to
  −0.088 across distance bands, against PE +0.167 to +0.263. In this window, perfect cross foresight does not
  rescue CE.

### 2.3 Policies vs baseline (OOS 4 Sep – 1 Oct, 19 days; thresholds chosen on training days)

Columns:
- P&L: unlimited cash.
- ΔP&L (t_d): paired daily difference vs the baseline.
- t: daily-P&L t; tCE / tPE: daily t per side.
- DD: max drawdown of cumulative daily P&L.
- book: Rs 50 L integrated book (DD).
- prec: share of trigger entries followed by a cross within 30 min.

| policy | trades (trigger) | win | mean ret | P&L L | ΔP&L (t_d) | t | CE L (tCE) | PE L (tPE) | DD | book (DD) | prec |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **BASELINE cross+5min** | 1,657 (0) | 40% | +0.059 | **98.2** | – | **1.96** | −56.0 (−2.59) | +154.1 (3.32) | 20.7 | **46.8 (11.1)** | – |
| ungated trigger-only, std | 4,896 (4,896) | 26% | +0.005 | 24.4 | −73.7 (−1.94) | 1.21 | −44.0 | +68.5 | 13.9 | 18.4 (12.2) | 43% |
| ungated trigger + cross, std | 5,715 (4,683) | 29% | +0.016 | 95.0 | −3.2 (−0.18) | 1.69 | −70.4 | +165.4 | 25.8 | 45.5 (15.6) | 44% |
| ungated trigger-only, grace | 1,618 (1,618) | 48% | +0.065 | 106.6 | +8.4 (+0.30) | 2.41 | −89.6 (−3.07) | +196.2 (3.90) | 27.5 | 63.4 (17.3) | 48% |
| ungated trigger + cross, grace | 1,955 (1,606) | 48% | +0.062 | 122.3 | +24.1 (+0.98) | 2.24 | −98.1 (−3.12) | +220.3 (3.79) | 30.3 | 48.7 (17.5) | 48% |
| hybrid C6 std | 2,772 (1,386) | 33% | +0.036 | 99.4 | +1.2 (+0.10) | 1.76 | −57.6 (−2.51) | +157.0 (3.02) | 22.1 | 53.2 (9.1) | 76% |
| trig C6 std | 2,326 (2,326) | 27% | +0.008 | 18.9 | −79.3 (−1.95) | 1.04 | −18.9 | +37.8 | 6.5 | 20.8 (11.8) | 73% |
| hybrid C6 grace | 1,763 (1,344) | 47% | +0.063 | 111.8 | +13.7 (+0.72) | 1.99 | −78.3 (−2.75) | +190.1 (3.54) | 27.8 | 48.2 (17.7) | 69% |
| trig C6 grace | 1,376 (1,376) | 48% | +0.068 | 94.4 | −3.7 (−0.18) | 2.03 | −71.1 | +165.5 | 25.7 | 43.9 (18.5) | 68% |
| hybrid C12 std | 3,456 (2,172) | 32% | +0.026 | 91.4 | −6.7 (−0.56) | 1.75 | −60.0 | +151.4 | 19.7 | 47.6 (11.3) | 64% |
| trig C12 std | 2,559 (2,559) | 28% | +0.008 | 20.0 | −78.2 (−1.96) | 1.14 | −22.5 | +42.5 | 9.9 | 20.0 (15.5) | 65% |
| hybrid C12 grace | 1,772 (1,353) | 47% | +0.064 | 114.5 | +16.4 (+0.91) | 2.02 | −78.5 (−2.64) | +193.1 (3.55) | 26.7 | 42.0 (19.6) | 62% |
| trig C12 grace | 1,387 (1,387) | 48% | +0.067 | 93.5 | −4.7 (−0.25) | 1.97 | −68.3 | +161.8 | 25.2 | 42.1 (19.5) | 61% |
| hybrid Rs std | 2,073 (524) | 38% | +0.051 | 106.2 | +8.1 (+0.80) | 1.93 | −57.8 (−2.62) | +164.0 (3.22) | 20.7 | **76.6 (10.0)** | 61% |
| trig Rs std | 1,149 (1,149) | 29% | +0.011 | 12.4 | −85.8 (−1.94) | 1.00 | −11.2 | +23.7 | 4.7 | 12.4 (9.8) | 55% |
| hybrid Rg grace | 1,717 (623) | 45% | +0.067 | **116.4** | **+18.2 (+1.08)** | **2.30** | −68.2 (−2.40) | +184.6 (3.84) | 22.1 | 58.2 (18.9) | 44% |
| trig Rg grace | 813 (813) | 50% | +0.061 | 50.2 | −47.9 (−0.96) | 1.47 | −41.2 | +91.5 | 12.3 | 23.6 (12.9) | 44% |
| hybrid Ds std | 2,350 (842) | 36% | +0.045 | 105.6 | +7.4 (+0.61) | 1.99 | −59.5 (−2.55) | +165.1 (3.34) | 20.2 | **83.0 (9.1)** | 48% |
| trig Ds std | 367 (367) | 30% | +0.024 | 8.6 | −89.6 (−1.95) | 1.12 | −5.5 | +14.1 | 2.2 | 8.6 (2.9) | 50% |
| hybrid Dg grace | 1,728 (644) | 45% | +0.066 | 114.9 | +16.8 (+1.01) | 2.21 | −70.2 (−2.27) | +185.1 (3.74) | 24.9 | 44.4 (23.0) | 48% |
| trig Dg grace | 902 (902) | 50% | +0.052 | 47.1 | −51.1 (−1.14) | 1.30 | −54.2 | +101.3 | 21.8 | 31.6 (19.0) | 46% |

- CE lost on 15 of 19 OOS days in the baseline, and on 14–17 of 19 under every hybrid. Every grace hybrid loses
  more on CE than the baseline (−68 to −98 L vs −56 L) and gains on PE (+185 to +220 L vs +154 L). The whole grace
  "gain" is extra PE exposure in a falling month.
- The chosen thresholds are unstable from day to day. For example, hybrid C6 std chose: never, never, 0.4, 0.5, 0.5,
  0.9, 0.9, never, 0.9, 0.4, 0.4, 0.8, 0.9, 0.8, 0.4, 0.5, 0.5, 0.8, 0.8. The full lists are in `precross_model.log`.
- Fixed-threshold sweep (48 hybrids, 6 thresholds × 8 score/exit combinations; this shows the whole curve and is not
  a selection). Unlimited-cash P&L ranges:

  | combination | unlimited-cash P&L range L | best ΔP&L vs baseline (t_d), at threshold |
  |---|---|---|
  | C6 std | 99.0–108.8 | +10.7 (1.26) at p ≥ 0.8; best t_d 1.41 at p ≥ 0.9 |
  | C6 grace | 104.6–114.5 | +16.3 (0.85) at p ≥ 0.5 |
  | C12 std | 98.6–103.9 | +5.8 (0.77) at p ≥ 0.9 |
  | C12 grace | 102.6–119.2 | +21.1 (1.05) at p ≥ 0.5 |
  | Rs std | 97.1–105.4 | +7.3 (0.61) at ≥ 0 |
  | Rg grace | 112.7–120.0 | +21.6 (1.23) at ≥ 0.05 |
  | Ds std | 97.6–109.3 | +11.1 (1.08) at ≥ 0.05 |
  | Dg grace | 114.1–122.0 | +23.9 (1.47) at ≥ 0.15 |

  No fixed threshold reaches t_d 2.
- Look-ahead reference (**not tradable**; gate = the actual cross within 30 min):
  - oracle hybrid, std: 159.3 L (+61.1, t_d 5.37), book 105.8 L (DD 10.5);
  - oracle hybrid, grace: 178.1 L (+80.0, t_d 4.35), CE −40.5 / PE +218.7.

  With y12 as the gate: 149.0 L (std) and 177.0 L (grace). The prize is real. The classifier captures roughly
  0–20% of it, because the predictable part of "will it cross" is not where the payoff is.

### 2.4 Book robustness (integrated replay; P&L L (DD))

| policy | Rs 25 L | Rs 50 L | Rs 100 L | Rs 50 L, OOS H1 only | Rs 50 L, OOS H2 only |
|---|---|---|---|---|---|
| BASELINE | 37.0 (10.0) | 46.8 (11.1) | 86.8 (22.7) | 12.8 (8.1) | 26.1 (19.1) |
| ungated trigger + cross, grace | 19.1 (16.1) | 48.7 (17.5) | 75.9 (29.7) | 10.8 (9.5) | 20.0 (23.2) |
| ungated trigger-only, grace | 38.0 (17.4) | 63.4 (17.3) | 68.3 (29.1) | 14.7 (8.6) | 42.4 (14.3) |
| hybrid C6 std | 38.3 (7.7) | 53.2 (9.1) | 83.6 (22.2) | 11.8 (9.1) | 36.3 (14.8) |
| hybrid C6 grace | 27.7 (12.5) | 48.2 (17.7) | 73.7 (29.4) | 15.6 (8.8) | 21.3 (22.1) |
| hybrid C12 std | 15.6 (8.6) | 47.6 (11.3) | 79.9 (21.8) | 15.6 (8.1) | 27.6 (16.7) |
| hybrid C12 grace | 25.9 (14.7) | 42.0 (19.6) | 83.2 (30.2) | 14.9 (9.2) | 7.9 (24.0) |
| **hybrid Rs std** | **46.5 (6.9)** | **76.6 (10.0)** | **92.8 (23.0)** | 22.5 (8.1) | 25.5 (21.8) |
| hybrid Rg grace | 34.6 (9.8) | 58.2 (18.9) | 93.0 (21.6) | 31.0 (4.7) | 13.8 (22.9) |
| **hybrid Ds std** | 37.9 (7.7) | **83.0 (9.1)** | 90.6 (22.8) | 21.9 (6.4) | 31.4 (18.0) |
| hybrid Dg grace | 29.2 (16.5) | 44.4 (23.0) | 86.6 (24.6) | 26.4 (6.1) | 7.8 (24.2) |

- Book rankings reshuffle with the capital level. For example, ungated trigger + cross grace makes 19.1 L at Rs 25 L
  and 48.7 L at Rs 50 L.
- Only `Rs std` and `Ds std` are within 0.6 L of, or above, the baseline in all five cells (margins −0.6 to +36 L), with similar or lower
  drawdown. Both pick few triggers (524 / 842 of 13,891) and add little unlimited-cash P&L. That is consistent with
  "early entry in std mode is a cheap, quickly-cut option that frees no capital and costs little". It is not
  evidence of edge.
- **Book convention matters.** Under the earlier study's convention (replay per contract first, then the cash
  filter, so an unfunded trigger still blocks the later cross):
  - hybrid C6 std falls from 53.2 to 1.2 L;
  - Rs std from 76.6 to 8.5 L;
  - Ds std from 83.0 to 11.1 L;
  - the oracle-y6-std from 105.8 to −1.2 L;
  - the 27-day baseline goes 74.9 L (integrated) vs 85.1 L (old).

  The earlier "anticipation books ≤ 47 L vs 85 L" were therefore partly an artifact of that convention, but its
  unlimited-cash and t-stat ranking stands.
- Over 27 days, the 5-min-grid ungated triggers in grace mode give 120.4 L, t 2.62 (trigger-only) and 137.2 L, t 2.47
  (+ cross), against the baseline's 117.9 L, t 2.31 (t_d 0.09 / 0.74). This is slightly better than the earlier
  first-tick version (`ant 15% none grace` 107.2 L, t 1.89). It is still not significant, the integrated book is
  63.5 / 60.4 L vs 74.9 L, and the drawdown is higher (27.5–30.3 vs 20.7 L).

## 3. Method and results: Task 2 (KAMA)

**Coverage of `historical_indicators`.** Columns: macd, signal, histogram, bb_*, kama, kama_rsi, kama_roc.

| timeframe | symbols | of which options / equities / indices | date range |
|---|---|---|---|
| 60 s | 1,659 | 1,442 / 210 / 7 | 29 Jul – 30 Sep |
| 300 s | 9 | | |
| 900 s | 10 | | |
| 1800 s | 1,258 | 1,041 / 210 / 7 | 29 Jul – 1 Oct |

Signal contracts with at least 12 bars of history before the signal: **340 of 3,298 at 1800 s and 82 at 60 s**.
Spots are covered: 3,297 at 1800 s and 3,018 at 60 s.

The table is written by `chart_history.load_chart_history`:
- the timestamp is the bar start, and the value is known only at the bar close, so the last *completed* bar was used;
- the series is seeded from the first stored minute, with `in_session` close times.

**Recomputed.** KAMA(10, 2, 30), RSI(14) of KAMA and ROC(5) of KAMA were recomputed with the engine's exact
`IncrementalKAMA` / `IncrementalRSI` / `IncrementalROC` logic. This was done from minute bars (options since 1 Aug,
spots since 1 Jun), on 30-min and 5-min bars aligned to 09:15, completed bars only, for the option premium and the
underlying.
- Agreement with the table on spot 30-min: rank corr 0.978 for distance, 0.940 RSI, 0.960 ROC, 0.868 slope. The
  differences come from seeding and session-close handling.
- Features per series:
  - `dist` = now / KAMA − 1;
  - `slope` = 1-bar KAMA change;
  - `rsi` = KAMA-RSI − 50;
  - `roc` = KAMA-ROC %;
  - `er` = efficiency ratio over the last 10 closes (KAMA's own ER), and `erd` = the signed ER.
- Spot features are signed by side. `o30_conf` = the engine's KAMA entry filter (close > KAMA > previous KAMA,
  RSI ≥ 65, ROC > 0.5) on 30-min premium bars.
- The table values were tested too: `t_s30_*` and `t_s1_*`.

**Within-side daily rank IC vs lane return.** Method as in the earlier study: the `d5` lane label (2,363 signals)
and the `x0` label (3,295). The table shows every feature with |t| ≥ 1.5 on either label. ✓ means |t| ≥ 2 and the
same sign in both halves.

| feature | d5 CE IC (t) | d5 PE IC (t) | d5 both IC (t) | d5 H1 / H2 | x0 both IC (t) | max \|ρ\| with an existing feature |
|---|---|---|---|---|---|---|
| s5_er (spot 5-min ER) | −0.097 (−2.6) | −0.035 (−0.9) | **−0.064 (−3.2) ✓** | −0.078 / −0.050 | **−0.039 (−2.1) ✓** | 0.42 (s_z5) |
| o30_dist (premium vs 30-min KAMA) | −0.094 (−2.8) | −0.020 (−0.6) | **−0.053 (−2.7) ✓** | −0.078 / −0.031 | **−0.067 (−3.4) ✓** | **0.90 (macd_slope_pct)** |
| s5_erd | −0.071 (−1.9) | −0.022 (−0.6) | −0.044 (−2.1) ✓ | −0.060 / −0.030 | −0.014 (−0.7) | 0.51 (s_z5) |
| s5_slope | −0.070 (−2.3) | −0.004 (−0.1) | −0.034 (−1.9) | −0.062 / −0.008 | −0.013 (−0.7) | 0.56 (s_z5) |
| t_s1_dist (table, 1-min spot) | −0.066 (−1.7) | −0.032 (−0.8) | −0.047 (−1.8) | −0.024 / −0.069 | −0.024 (−1.2) | – |
| o5_erd | −0.092 (−2.5) | +0.010 (+0.3) | −0.037 (−1.7) | −0.098 / +0.020 | −0.009 (−0.3) | 0.42 (s_z5) |
| o5_dist | −0.088 (−2.9) | +0.005 (+0.1) | −0.041 (−1.7) | −0.047 / −0.034 | −0.045 (−2.2) | 0.80 (o_z5) |
| t_s1_rsi | −0.072 (−1.9) | −0.003 (−0.1) | −0.035 (−1.6) | −0.059 / −0.013 | −0.010 (−0.6) | – |
| t_s30_dist (table) | −0.050 (−1.2) | −0.014 (−0.4) | −0.031 (−1.5) | −0.085 / +0.018 | −0.040 (−2.3) | – |
| o30_rsi | +0.010 (+0.2) | +0.052 (+1.5) | +0.032 (+1.5) | +0.042 / +0.022 | +0.029 (+1.4) | 0.36 |
| s30_roc | +0.015 (+0.4) | +0.067 (+1.3) | +0.040 (+1.5) | +0.065 / +0.018 | +0.011 (+0.4) | 0.55 |
| s30_dist | −0.051 (−1.3) | −0.004 (−0.1) | −0.026 (−1.3) | −0.085 / +0.028 | −0.032 (−2.0) | 0.67 (s_z30) |
| o5_slope | **−0.092 (−3.2)** | +0.038 (+1.0) | −0.023 (−1.1) | −0.079 / +0.029 | −0.000 (−0.0) | 0.47 |
| o5_er | −0.079 (−2.4) | +0.011 (+0.3) | −0.032 (−1.3) | −0.099 / +0.031 | −0.012 (−0.5) | 0.31 |
| o30_conf (engine KAMA filter) | −0.048 (−1.1) | −0.023 (−0.4) | −0.032 (−0.7) [12 days] | | −0.038 (−0.8) | 0.06; true on 14 of 3,295 signals |

The other 18 have |t| < 1.5 on both labels: KAMA-RSI/ROC/slope/ER of the 30-min spot and premium, `o5_rsi`,
`o5_roc`, `s5_rsi`, `s5_roc`, `s5_dist`, `t_s30_rsi`/`roc`/`slope`, `t_s1_roc`/`slope`.

Reading:
- Every KAMA signal points the same way as the earlier contrarian/stretch findings. A premium or spot already far
  above its KAMA, or a spot trending efficiently on 5-min bars, means a later and worse entry.
- The effects are CE-heavy.
- KAMA "trend confirmation" (RSI/ROC/slope) carries no information.

**Walk-forward** (19 OOS days, same code and hyper-parameters as the earlier study; the base row reproduces the
earlier 0.573 / +0.159 / 78.3 L exactly). Books are the earlier first-come / ranked / ranked + dispersion books
(DD), so they are directly comparable to the earlier study.

| label | model | AUC | day IC (t) | CE / PE IC | Q5−Q1 | skip-30% P&L | book first / ranked / ranked+disp |
|---|---|---|---|---|---|---|---|
| d5 | reg base22+new (earlier best) | 0.573 | +0.159 (3.5) | +0.050 / +0.046 | +0.173 | 100.0 L | 63.6 (10.4) / 67.4 (10.4) / 78.3 (9.9) |
| d5 | reg + KAMA (all 25) | 0.576 | +0.144 (3.2) | +0.027 / +0.007 | +0.119 | 102.3 L | 61.1 (11.1) / 74.8 (11.8) / 84.5 (10.1) |
| d5 | reg + KAMA (option + spot ER, 17) | 0.567 | +0.130 (2.8) | −0.011 / +0.025 | +0.123 | 103.7 L | 56.2 (11.8) / 79.4 (7.4) / 84.1 (8.7) |
| d5 | clf base22+new | 0.567 | +0.081 (1.9) | −0.022 / −0.052 | +0.092 | 93.2 L | 56.0 / 60.4 / 70.3 (16.9) |
| d5 | clf + KAMA (all) | 0.566 | +0.089 (2.2) | −0.063 / −0.045 | +0.077 | 92.6 L | 60.6 / 58.5 / 72.0 (14.7) |
| d5 | clf + KAMA (17) | 0.570 | +0.097 (2.4) | −0.054 / −0.059 | +0.126 | 86.2 L | 63.3 / 55.2 / 65.1 (18.7) |
| x0 | reg base22+new | 0.574 | +0.120 (3.4) | +0.006 / +0.022 | +0.105 | 94.3 L | 60.3 (20.0) / 69.6 (14.4) / 75.4 (14.0) |
| x0 | reg + KAMA (all) | 0.575 | +0.130 (3.8) | +0.053 / −0.009 | +0.106 | 109.8 L | 82.8 (14.8) / 78.3 (11.5) / 85.7 (9.9) |
| x0 | reg + KAMA (17) | 0.570 | +0.120 (3.5) | +0.041 / −0.015 | +0.093 | 111.9 L | 74.7 / 84.7 / 85.7 (13.4) |
| x0 | clf base22+new | 0.608 | +0.058 (1.5) | −0.046 / −0.012 | +0.059 | 103.8 L | 60.7 / 77.0 / 91.4 (17.6) |
| x0 | clf + KAMA (all) | 0.603 | +0.056 (1.4) | −0.053 / −0.022 | +0.055 | 100.0 L | 64.0 / 82.3 / 86.7 (18.6) |
| x0 | clf + KAMA (17) | 0.606 | +0.057 (1.5) | −0.059 / −0.014 | +0.074 | 101.9 L | 70.5 / 82.3 / 85.9 (18.4) |

Paired day-bootstrap (5,000 draws) of the daily-IC difference vs base22+new, 95% interval:

| label | model kind | + all KAMA | + KAMA subset (17) |
|---|---|---|---|
| d5 | regressor | −0.015 [−0.042, +0.011] | −0.029 [−0.049, −0.010] (worse) |
| d5 | classifier | +0.009 [−0.014, +0.032] | +0.016 [−0.008, +0.043] |
| x0 | regressor | +0.010 [−0.013, +0.030] | +0.000 [−0.020, +0.024] |
| x0 | classifier | −0.002 [−0.022, +0.017] | −0.001 [−0.020, +0.018] |

The +6 L (d5) / +10 L (x0) ranked + dispersion book gains are inside the ±15 L book noise established earlier.

**KAMA verdict:**
- KAMA adds no ranking information beyond the base22 + new features. Ranking IC is unchanged or worse.
- The one non-redundant stable feature, `s5_er`, restates "trending spot = late entry", and is mostly CE.
- Do not add KAMA to the ranker.
- Do not turn on `require_kama_confirmation` / `require_kama_rsi_confirmation` / `require_kama_roc_confirmation` for
  MACD zero-cross lanes. At the cross, the premium's KAMA-RSI averages 38 (o30_rsi mean −11.8), so the filter would
  block 99.6% of signals.

## 4. Caveats and hygiene

- **Configurations tried.**
  - Task 1:
    - 1 trigger definition;
    - 6 models × 19 folds, with 3 inner folds each;
    - 16 train-chosen policies, 48 fixed-threshold hybrids, 8 ungated, 4 oracle and 1 baseline;
    - 11 × 5 book-robustness cells and 2 book conventions;
    - plus an earlier run of the same models (12 train-chosen + 36 fixed policies under the old book convention,
      superseded and identical in unlimited-cash P&L).
  - Task 2: 33 features × 2 labels = 66 IC tests, 12 walk-forward models and 8 bootstrap comparisons.
  - Roughly **270 tests** in all, so about 13 nominal |t| > 2 results are expected by chance. The only results
    that are far beyond chance are the classifier AUC (19/19 days > 0.5) and the C6-vs-std-return IC (t −3.4).
- **One regime.** PE favoured, CE lost on 15 of 19 OOS days (22 of 27 overall). CE and PE are reported separately
  throughout. Every grace-mode gain is PE. CE triggers lose under grace even when the cross does happen, so any
  "early entry" result here is mostly a statement about PE in a falling September.
- **Thresholds were never tuned on test days.** The fixed-threshold sweep is shown only as a sensitivity curve.
- **Purged walk-forward is stricter than the earlier study.** Rows whose outcome resolved after the test day's open
  were dropped from training, and threshold replays used days ≤ t−5.
- **Simplifications.**
  - Trigger decisions sit on a 5-min grid at the window's last trade price. In an illiquid contract this can be up
    to 5 min stale.
  - The 4-session cap keeps many grace trades open, and positions still open at 1 Oct 15:30 are marked at the last
    close.
  - Rs 1 L tickets (not the live 10%-of-equity sizing); no dispersion sizing in the policy books.
- **Book P&L is path-dependent.** Book results move ±30 L with the capital level or the start date. The integrated
  replay is the more realistic convention, but book differences under about 30 L are not evidence.

## 5. What this means

- "Will it cross?" is easy: AUC 0.83, calibrated, and it is mostly distance to p\*, projected bars to the cross,
  premium stretch and time of day.
- "Is entering early worth it?" is hard, and the two questions are nearly orthogonal. The early-entry prize found in
  the earlier study (2–3x P&L with foresight) comes mostly from **large, unexpected moves**: crosses from 10–15%
  below p\*, with high MFE. Those are exactly the triggers a cross-probability model scores low.
- A trigger the model is sure about is one where the premium already sits at the cross price. There, entering
  5–15 minutes early saves little, and the false positives at that level are the worst trades (−0.05 to −0.06 std).

## 6. Recommendations

1. **Keep cross+5min as the lane entry.** Do not deploy a model-gated pre-cross entry.
2. **If anything, run a zero-risk shadow lane: "hybrid Rs std".**
   - Model: a regressor on the std-exit trigger return.
   - Rule: enter at a 5-min trigger when the predicted return ≥ about 0.025–0.05, otherwise take the normal
     cross+5min.
   - Std invalidation: a trigger entry with no cross by the bucket close is cut at about cost.
   - Rationale: it was one of the two families never more than 0.6 L below the baseline book in any capital or half-period check
     (76.6 vs 46.8 L at Rs 50 L) without hurting CE.
   - Promote only if it shows t_d > 2 on unlimited-cash daily P&L over ≥ 40 new sessions, **including a
     call-favouring month**.
3. **Do not adopt grace-mode early entries.** Their gain is extra PE holding time in a put month; CE loses more.
4. **Log every 5-min trigger in shadow**, live: the features in section 1, plus whether and when the cross came and
   both exit outcomes. 24k triggers per 27 days is enough to retrain and re-test monthly at no risk.
5. **Use the integrated capital replay** (unfunded entries do not block a contract) for any future book
   comparisons. Re-read the earlier study's anticipation-book numbers with that bias in mind.
6. **KAMA:** do not add it to the ranker. Do not enable the `require_kama_*` filters on MACD lanes. If one feature
   is kept for monitoring, use `s5_er`, the 5-min spot efficiency ratio, which is CE-heavy.

## 7. Scripts and outputs

Everything is in `/private/tmp/claude-501/-Users-ramachandran-MACD-mini-ADV/77c677ac-a2ad-4b4d-977f-6fcea088fa80/scratchpad/precross_model/`.

Run with `run.sh /w/<script>`. It starts a throwaway `macd-mini-parallel-engine` container with this folder at `/w`,
the earlier `feature_study/` at `/fs` (read-only), the prior study at `/r` and `oldapp` at `/old` (read-only), and
the data volume (databases opened `mode=ro`).

| script | does | output |
|---|---|---|
| `grid.py` | per-minute spot grid and market context (breadth, Nifty sigma/z, sector returns, spot z5, cumulative volume) | `grid.npz`, `grid.log` |
| `trig_scan.py` | 5-min pre-cross triggers with 34 features, y6/y12 labels, std/grace lane exits, and the cross+5min candidates (reuses `lane_replay.Contract`) | `triggers.pkl`, `trig_scan.log` |
| `precross_model.py` | Task 1: walk-forward C6/C12/Rs/Rg/Ds/Dg, training-day thresholds, policies, books, oracle, robustness | `precross_model.log` (final), `pm_preds.pkl`, `pm_rows.pkl`; `precross_model_v1.log` / `_v2.log` / `_v3.log` = earlier runs (old book convention, then the added columns) |
| `diag.py` | P(cross) band × outcome table; crossed triggers by distance to p\* | stdout (in 2.2) |
| `kama_feat.py` | KAMA/RSI/ROC/ER features (engine logic) for the 3,298 signals, plus table values and the self-vs-table check | `kamafeat.pkl`, `kama_feat.log` |
| `kama_eval.py d5\|x0` | Task 2: KAMA IC tables, redundancy, walk-forward with and without KAMA, bootstrap | `kama_eval_d5.log`, `kama_eval_x0.log`, `kama_ic_*.pkl` |
| helpers | `run.sh`, `hi_schema.py`, `kama_cov.py` (indicator-table coverage), `peek.py`, `dbg.py`, `rows.py`, `edit_pm.py` / `edit2.py` / `edit3.py` (in-place edits of `precross_model.py`) | |
