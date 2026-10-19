# Index lane study: CE / PE / no trade at 14:45 (NIFTY, BANKNIFTY, SENSEX, MIDCPNIFTY)

Study date: 3 Oct 2026. This is analysis only: nothing under `MACD-mini-ADV` was changed, and SQLite was opened with `mode=ro`.
Scripts and outputs are in `…/77c677ac-…/scratchpad/index_study/` (listed at the end).

## TL;DR

1. **Nothing beats the coin flip robustly.** I tested 11 walk-forward decision configurations (19 variants once ungated and gated versions are counted) over 2018-01 → 2026-09: 6,073 index-days, 4 indices. None has a daily t-stat above +1.3 against the coin flip.
   - The best numbers come from two trivial rules: **always-CE** (+1.46%/trade, t 1.26) and the **5-day trend rule** (+0.99%/trade, t 1.14).
   - Always-CE's edge is equity drift plus a known simulator bias: the simulator overstates calls against puts by about 2.8 pp, see §2. On real 2025-26 premiums always-CE is −0.3% (weekly lane) and −4.5% (monthly lane, t −2.0).
   - Every ridge and GBM model is between −1.6% and +0.5% excess pooled. They are also ≈ 0 after Nov 2024.
2. **The live champion's +23.6% is a regime effect, not skill.**
   - On the champion's own 41 validation days, **always-PE** earned +46.6% excess (t 5.2), against the champion's +23.6% (t 1.22).
   - On all 67 live labelled index-days, always-PE earned +34.7%.
   - Aug–Sep 2026 was a falling market. The champion picked puts because it was fitted on that one regime (28 of 36 validation trades were puts).
   - Its closest long-history replica (`ridge_live_id`, trained 2021→) chose puts only 36 of 67 times on the same days and lost −6.3% against the coin flip.
3. **The simulator is good.** Black-Scholes with India VIX × a calibrated multiplier reproduces real premium labels closely.
   - Same-contract correlation is **0.98–0.99 per side** and **0.94 for the coin flip**; bias is under ±1.5 pp. This holds on 736 real 2025-26 labels and on 67 live 2026 labels.
   - It can therefore stand in for the missing history. Its one structural miss is put skew: real puts beat simulated puts by about 1.2 pp/trade, and calls are about 1.5 pp worse.
4. **Theta / structure findings are the only robust results, and they affect the gate, not the side.**
   - **Friday entries** are the worst weekday in the simulation (coin y −2.7%, t −4.85; 10/10 years negative IC) and on real weekly premiums (−6.2%).
   - Skipping Fridays lifts the simulated coin-flip y from −0.58% to 0.00% per trade (2018+), and real weekly from −3.15% to −2.37%.
   - Monday was also bad on real weekly premiums (−9.0%, n=62), but not in the long simulation.
5. **No new feature survived.**
   - None of the price, trend, VIX, participant-OI or 14:45 intraday features shows a stable directional IC. Only two "directional" signals are stable, and they are unsigned: VIX/IV level (mean IC +0.08; 9 of 10 years; real 2025-26 ATM-IV IC +0.24/+0.27) and |1-day return| (+0.04; 10 of 10). Both say "after stress, the index tends to rise", which is a CE tilt.
   - The option-chain features (PCR, OI change, walls, max pain, GEX/DEX, gamma flip, skew) show no IC above |t_adj| 1.4 on real 2025-26 data, apart from IV level.
   - Adding them inside walk-forward models on real labels made results worse (−1% to −8%/trade).
6. **The difference between a 14:45 entry and a close entry is small for direction.** Correlation of 14:45→D+3 and close→D+3 moves is 0.99, and the 3-day sign agrees 96% of the time. Premium y is correlated 0.97–0.99 between the two entries. The 14:45 entry costs about 0.5–1 pp more theta on weekly contracts.

---

## 1. Data and method

| Item | Source / rule |
|---|---|
| Index and VIX history | Fyers daily 2015-01 → 2026-10-01 (NIFTY50, NIFTYBANK, SENSEX, MIDCPNIFTY from 2022-07-18, INDIAVIX). Fyers 5-min bars 2021-01 → 2026-10-01. Yahoo was used only to cross-check: closes agree to <1e-5. |
| Session calendar | Union of the Fyers daily index days minus 7 weekday Muhurat sessions: 2,894 sessions. Fyers has 5 days that Yahoo lacks, e.g. 2019-02-13. The 1 Feb 2026 Sunday Budget session is excluded (4 TradeBot rows dropped). |
| Entry | **14:45 = close of the 14:40 5-min bar** from 2021 (MIDCPNIFTY from Jul 2022), with VIX at 14:45 from the 5-min VIX bars. Before 2021 the entry is the day's close with the VIX close. Fyers 14:45 matches the DB's 1-min 14:44 close to within 0.02 bp. |
| Contract | Nearest expiry strictly after the D+3 session, i.e. the live rule "outlives a 3-session hold". Strike = ATM, rounded to 50 / 100 / 100 / 25. |
| Label | y = clip(mean(r1, r2, r3), −1, 2), where rk = premium at the D+k close ÷ entry premium − 1. Coin flip = (y_CE + y_PE)/2. Excess = y_chosen − coin. |
| Revaluation | Black-Scholes, r = 6.5%, calendar-day T. Exits use that day's VIX close and the remaining DTE, so vega and the term structure are included. |
| Participant OI | NSE `fao_participant_oi`: 1,913 files, 2019-01-01 → 2026-10-01 (re-checked, complete). For decision day D, only the latest file dated before D is used. |
| Real premiums | TradeBot data, read-only. See §2 and §8. |
| Panel | NIFTY 2,854 days, BANKNIFTY 2,810, SENSEX 834 (from 2023-05-15, the weekly relaunch), MIDCPNIFTY 969. Labelled: 7,446 index-days. Walk-forward test set (2018-01 → 2026-09): 6,073. |

**Expiry calendar** (rule-built, with holidays shifted to the prior session):

| Index | Rule assumed |
|---|---|
| NIFTY | Weekly Thursday from 14 Feb 2019 to 28 Aug 2025, then Tuesday. Monthly last Thursday, then last Tuesday from Sep 2025. Monthly-only before Feb 2019. |
| BANKNIFTY | Weekly Thursday 2 Jun 2016 → 31 Aug 2023, Wednesday 6 Sep 2023 → 13 Nov 2024, then no weeklies. Monthly last Thursday (to Feb 2024), last Wednesday (Mar–Dec 2024), last Thursday (Jan–Aug 2025), last Tuesday (Sep 2025→). |
| SENSEX | Weekly Friday 19 May 2023 → 27 Dec 2024, Tuesday 7 Jan → 26 Aug 2025, Thursday from 4 Sep 2025. |
| MIDCPNIFTY (least certain) | Weekly Tuesday to 18 Jul 2023, Monday 24 Jul 2023 → 18 Nov 2024. Monthly last Tuesday, then last Monday, then last Thursday (2025), then last Tuesday (Sep 2025→). |

**How the calendar was verified:**
- Live `agent_candidates` expiries (Aug–Oct 2026) match exactly for all 4 indices. Expiry mismatch on 67 live index-days: 0/67.
- TradeBot 1-min folders match exactly for NIFTY weekly and monthly Apr–Aug 2025 and SENSEX weekly and monthly Apr 2025–Mar 2026. This includes the Tuesday→Thursday switch on 4 Sep 2025 and holiday shifts such as 9 Apr 2025, 1 Oct 2025 and 14 Jan 2026.
- MIDCPNIFTY 2025 monthlies match the TradeBot catalogue.

**Known calendar disagreements:**
- The NIFTY March 2026 monthly was **24 Mar** in the data; the rule gives 30 Mar. This affects about 4 sim days.
- The data folder calls the 30 Apr 2025 NIFTY contract "monthly". It is the 1 May weekly shifted for the holiday, and both views give the same contract.
- Pre-2023 MIDCPNIFTY and pre-Sep-2023 BANKNIFTY weekday rules come from memory and could not be verified offline.

### IV model (calibration)

The ratio ATM IV ÷ VIX was backed out from 3,356 observations:
- 79 live 14:45 entries (Aug–Sep 2026, 4 indices).
- 664 real 14:45 entries, 664 real D closes and 1,949 real exit-close revaluations (TradeBot 2025-26).

Fitted model (`calib3.py`), residual sd 0.102 in log terms (about 10% IV error):

`log(IV/VIX) = a_u + 0.412·log(relvol_u) + 0.341·[DTE≤1] + 0.182·[DTE 2–3] + 0.040·log(max(DTE,4)/7) − 0.012·[next session >1 day away]`

- a_u: NIFTY −0.101 (×0.90), BANKNIFTY −0.068, SENSEX −0.110, MIDCPNIFTY +0.114.
- relvol_u = 60-session realised vol of the index ÷ NIFTY's, known at D. It is needed because a fixed BANKNIFTY/MIDCPNIFTY multiplier is regime-dependent: BANKNIFTY relvol was 1.62 in 2020 and 1.07 in 2025; the MIDCPNIFTY IV/VIX ratio was 1.50 in Jan–Feb 2026 and 1.10–1.20 in Aug–Sep 2026.

Median IV/VIX by DTE (real closes):

| DTE | NIFTY | SENSEX | BANKNIFTY | MIDCPNIFTY |
|---|---|---|---|---|
| 1 | 1.13 | 1.22 | 1.27 (n=1) | 1.95 (n=1) |
| 2–3 | 1.02 | 1.04 | – | – |
| 4–7 | 0.91 | 0.87 | 0.88 | 1.39 |
| 8–14 | 0.92 | 0.91 | 0.92 | 1.35 |
| 15–35 | 0.94 | 0.93 | 1.02 | 1.50 |

The term structure is flat from 4 days out, but ATM IV in calendar time is about 15–35% higher on the last 1–3 days. Without the near-expiry term, the simulator under-prices exits close to expiry.

Mean residual by source is within ±0.02 for every index and source except two live MIDCPNIFTY / SENSEX cells (−0.10 / +0.07; n = 25 / 12).

## 2. Simulation validation

**(A) Same contract (expiry and strike) and 14:45 entry, simulated against REAL premiums (TradeBot 2025-26)**

| Set | n | corr y_CE | corr y_PE | corr coin | corr (CE−PE) | bias CE | bias PE | bias coin | entry premium sim/real (CE, PE) |
|---|---|---|---|---|---|---|---|---|---|
| NIFTY weekly 1m (Apr–Aug 25) | 92 | 0.987 | 0.990 | 0.960 | 0.994 | +3.7pp | −0.8pp | +1.4pp | 0.99, 0.99 |
| NIFTY monthly 1m | 78 | 0.987 | 0.994 | 0.964 | 0.995 | +1.0 | −2.6 | −0.8 | 1.02, 0.99 |
| SENSEX weekly 1m (Apr 25–Feb 26) | 217 | 0.982 | 0.989 | 0.944 | 0.992 | +1.2 | −1.0 | +0.1 | 0.99, 1.04 |
| SENSEX monthly 1m | 216 | 0.983 | 0.985 | 0.919 | 0.991 | +0.3 | −1.2 | −0.4 | 0.98, 1.05 |
| NIFTY monthly 30m (Dec 25–Mar 26) | 57 | 0.992 | 0.982 | 0.951 | 0.990 | +2.6 | −1.7 | +0.5 | 1.01, 1.06 |
| BANKNIFTY monthly 30m | 38 | 0.989 | 0.992 | 0.911 | 0.996 | +2.9 | −0.0 | +1.4 | 0.99, 0.97 |
| MIDCPNIFTY monthly 30m | 38 | 0.992 | 0.989 | 0.953 | 0.993 | +3.2 | −1.9 | +0.7 | 1.03, 0.95 |
| **Pooled (full chains)** | **736** | **0.984** | **0.988** | **0.944** | **0.992** | **+1.5** | **−1.2** | **+0.15** | |
| 2-strike "sparse" 30m files (NIFTY / BANKNIFTY / MIDCPNIFTY 2025) | 97 / 131 / 70 | 0.99 / 0.98 / 0.99 | 0.99 | 0.93–0.95 | 0.99 | ≤0.5 | ≤0.5 | ≤0.2 | |

**(B) Panel simulation (its own expiry and ATM rule) against real weekly-lane labels on the same days.** NIFTY: n = 92, corr CE / PE 0.987 / 0.990, same expiry 100%. SENSEX: n = 217, 0.983 / 0.989.

**(C) Against the live lane's actual ATM labels** (`agent_candidates`, nearest series outliving the hold, |moneyness| ≤ 2%, Aug 24 → late Sep 2026):

| | n | corr CE | corr PE | corr coin | bias coin | real mean CE / PE | sim mean CE / PE |
|---|---|---|---|---|---|---|---|
| All 4 | 67 | 0.945 | 0.971 | 0.962 | −1.0pp | −29.8% / +39.6% | −29.8% / +37.6% |
| NIFTY | 15 | 0.882 | 0.955 | 0.959 | | | |
| BANKNIFTY | 24 | 0.958 | 0.983 | 0.986 | | | |
| MIDCPNIFTY | 23 | 0.963 | 0.975 | 0.960 | | | |
| SENSEX | 5 | 0.940 | 0.979 | 0.931 | | | |

**Caveats on the validation:**
- The IV level was calibrated on these same observations (9 parameters), so (A) and (C) are in-sample for the level. The correlations, however, are driven by spot paths and are not fitted.
- **The systematic miss is skew dynamics.** The simulator overstates calls against puts by about 2.8 pp per trade (CE−PE bias +0.028). In real markets puts gain IV when the index falls and calls lose it, which BS with a single ATM VIX-based IV does not capture. Any CE/PE bias in the simulated results must therefore be shaded about 1.4 pp towards puts.

## 3. Feature evidence (long history, simulated labels)

Each feature's Spearman IC was computed per calendar year (2017–2026, all indices pooled) against three targets:
- **move**: the signed 3-session index move from the entry price.
- **dir**: y_CE − y_PE.
- **coin**: the coin-flip y, i.e. whether buying options pays at all.

Column definitions:
- t_yr = mean of the yearly ICs ÷ sd × √n_years. This is the headline statistic.
- "stable" = number of years whose IC has the sign of the mean.
- **Monthly t-stats (t_mo) are unreliable** for persistent level features. Within a month, a level feature and the forward move are mechanically anti-correlated (a short-window demeaning bias). For example, range20_pos has t_mo −14.8 but t_yr −0.6. Use t_yr.

There are 60 features × 3 targets = **180 tests**, so a Bonferroni 5% threshold is |t_yr| ≈ 3.5. Full table: `out/ic_table.md`. The features that pass, or matter for the decision:

| Feature | IC move | t_yr | stable | IC dir | t_yr | IC coin | t_yr | stable | post-Nov24 IC move / coin |
|---|---|---|---|---|---|---|---|---|---|
| wd4 (Friday entry) | +0.008 | 0.65 | 6/10 | +0.009 | 0.66 | **−0.098** | **−7.94** | **10/10** | +0.022 / −0.088 |
| dte_exit (DTE left at D+3) | +0.011 | 0.96 | 7/10 | +0.005 | 0.23 | **+0.105** | **+5.68** | **10/10** | +0.054 / +0.103 |
| dte (at entry) | +0.011 | 0.78 | 5/10 | 0.000 | 0.01 | **+0.083** | **+4.29** | **10/10** | +0.062 / +0.094 |
| span (calendar days to D+3) | −0.004 | −0.25 | | −0.017 | −1.14 | **−0.086** | **−3.92** | 8/10 | +0.009 / −0.020 |
| abs_ret1d (\|1-day return at 14:45\|) | **+0.043** | **6.28** | **10/10** | +0.047 | 4.30 | +0.014 | 0.89 | 6/10 | +0.041 / +0.017 |
| abs_sig_move_vix | +0.030 | 4.33 | 9/10 | +0.038 | 3.84 | +0.020 | 1.07 | | +0.025 / +0.016 |
| vix_lvl | **+0.082** | **4.16** | **9/10** | +0.061 | 2.63 | −0.042 | −1.63 | 7/10 | +0.096 / −0.017 |
| er_series (series efficiency ratio) | 0.000 | 0.0 | | +0.011 | 0.8 | −0.050 | −3.63 | 9/10 | −0.007 / −0.070 |
| z_ma20 | −0.020 | −0.64 | 5/10 | −0.003 | −0.08 | −0.081 | −3.55 | 8/10 | |
| series_ret (since previous expiry) | +0.010 | 0.36 | 6/10 | +0.017 | 0.64 | −0.053 | −3.54 | 9/10 | +0.064 / −0.022 |
| ret_5d (live and trend rule) | −0.007 | −0.33 | 5/10 | +0.004 | 0.20 | −0.080 | −3.42 | 8/10 | +0.032 / −0.007 |
| range20_pos (live) | −0.018 | −0.57 | 5/10 | −0.001 | −0.04 | −0.077 | −3.25 | 8/10 | |
| id_rv (intraday realised vol to 14:45, 2021+) | +0.076 | 2.94 | 6/6 | +0.063 | 1.69 | +0.024 | 0.64 | | +0.122 / +0.052 |
| gap | +0.034 | 1.76 | 8/10 | +0.054 | 3.39 | −0.062 | −2.74 | 8/10 | |
| ret_1d / ret_20d / macd_d / rs_5d / day_pos / ret_open | \|IC\| ≤ 0.02 | ≤ 1.6 | ~5/10 | | | | | | |
| POI: fii_fut_ratio / chg1 / chg5, client_fut_ratio, fii/client optdir, call/put nets | \|IC\| ≤ 0.046 | ≤ 1.5 | 3–6 of 8 | | | fii_fut_chg1 coin +0.062 | 2.69 | 6/8 | |
| Intraday 14:45: first-30, VWAP-proxy dev, 5m/30m MACD, z20 (5m/30m), VIX intraday change | \|IC\| ≤ 0.033 | ≤ 1.2 | 2–4 of 6 | | | | | | |

**How to read this:**
- **Directional (which side):** no signed price, trend, MACD, positioning or intraday feature is stable. The live lane's directional features (ret_1d, ret_5d, ret_20d, range20_pos, macd, rs_5d, day_pos, ret_open, VWAP deviation, 30-min MACD) all have |t_yr| < 1.6 against the move.
- **Unsigned stress → up:** high VIX, a large |1-day move| and high intraday RV are followed by positive 3-day index moves (CE better). This is the familiar volatility-risk-premium and mean-reversion pattern. It is the only directional evidence that clears Bonferroni, and it appears again on real 2025-26 data (§8: ATM IV IC +0.24 to +0.27, 11 of 12 months positive). It is not a trend signal.
- **Gate (whether to buy at all):** weekday, DTE and span matter strongly. Trend-extension features (ret_5d, range20_pos, z_ma20, series_ret) have a negative coin IC: buying any option after an extended run pays worse, because realised vol drops.

## 4. Walk-forward models (simulated labels)

**Setup:**
- Expanding window, monthly refit, test 2018-01 → 2026-09. Rows are used only if their D+3 close falls before the month starts.
- Two rows per index-day (CE, PE). Directional z-scores are multiplied by the side.
- Ridge has intercept = mean y and λ = frac·n. λ = 0.07n mirrors the live λ = 100 on about 1,350 rows.
- The gate threshold is picked on the training window only, from {none, 0, 0.01, 0.025, 0.05}. An inner split (fit on the first 80% of training days, test on the last 20%) maximises the sum of y over taken trades.
- "+ID" configurations use the 14:45 intraday features, so they train on 2021+ and test from 2022.

**Feature sets:**

| Set | Features |
|---|---|
| live | Directional: ret_open, ret_1d, ret_5d, ret_20d, day_pos, range20_pos, macd_d, rs_5d. Neutral: dte. |
| live+ID | Adds the 14:45 VWAP-proxy deviation and the 30-min MACD. This is the closest replica of the champion. |
| new | live, plus: series_ret, trend_er10, sig_move_vix, z_ma20, gap, vix_chg1, abs_ret1d, vix_lvl, vix_pct1y, 6 participant-OI features (signed); 21 neutral features (DTE, DTE at exit, span, weekday dummies, expiry-day flag, series_next, VIX level and changes, VIX percentile, IV−RV, Bollinger width, efficiency ratios, rv20, \|ret_1d\|); a side term; index dummies. |
| new+ID | new, plus 7 directional and 4 neutral intraday features. |

Results, pooled over all indices. Excess is per trade. t is the daily t-stat (mean excess per day ÷ sd). Drawdowns are on the cumulative-y curve (units of one entry premium) and on a 1-lot rupee curve at current lot sizes (65 / 30 / 20 / 120).

| Config | Trades | CE % | Mean y | Excess | t | Right side | Max DD (units) | 1-lot P&L ₹ | Max DD ₹ | Post-Nov24 excess (t) |
|---|---|---|---|---|---|---|---|---|---|---|
| coin flip | 6073 | 50 | −0.58% | 0 | – | – | – | – | – | 0 |
| always-CE | 6073 | 100 | +0.88% | **+1.46%** | 1.26 | 54.5% | 124 | +3.5 L | 16.6 L | −3.96% (−1.63) |
| always-PE | 6073 | 0 | −2.04% | −1.46% | −1.26 | 45.5% | 286 | −17.1 L | 31.2 L | +3.96% (+1.63) |
| trend5 (sign of 5d return) | 6073 | 55.5 | +0.41% | +0.99% | 1.14 | 51.6% | 105 | −3.9 L | 19.6 L | +1.54% (0.72) |
| ridge live λ0.07 (≈ champion) | 6073 | 50 | −0.93% | −0.35% | −0.90 | 51.0% | 76 | −14.5 L | 16.0 L | −0.87% (−0.41) |
| ridge live λ0.07 gated | 2861 | 50 | −0.10% | +0.94% | −0.54* | 52.8% | 50 | −2.1 L | 6.2 L | +1.59% (0.30) |
| ridge new λ0.07 | 6073 | 57 | −1.75% | −1.17% | −2.22 | 49.6% | 162 | −14.6 L | 18.7 L | +0.65% (0.29) |
| ridge new λ0.07 gated | 4126 | 60.5 | −2.53% | −2.51% | −2.41 | 48.5% | 137 | −11.8 L | 13.6 L | −1.26% (0.28) |
| ridge new λ0.5 | 6073 | 56 | −1.44% | −0.86% | −1.79 | 49.9% | 128 | −16.5 L | 21.0 L | −0.13% (−0.06) |
| ridge new λ0.5 gated | 3776 | 55 | −2.70% | −2.05% | −2.39 | 48.2% | 116 | −14.8 L | 16.2 L | −3.41% (−1.20) |
| GBM new depth 2 | 6073 | 60 | −0.73% | −0.15% | −0.74 | 50.6% | 92 | −5.9 L | 13.2 L | −0.49% (−0.23) |
| GBM new depth 2 gated | 4365 | 60.5 | −2.04% | −1.59% | −1.75 | 49.0% | 107 | −12.2 L | 14.7 L | −3.66% (−0.97) |
| GBM new depth 3 | 6073 | 60 | −2.16% | −1.58% | −1.85 | 49.3% | 140 | −17.5 L | 17.8 L | −1.81% (−0.85) |
| GBM new depth 3 gated | 4498 | 60 | −2.72% | −2.05% | −2.28 | 48.9% | 130 | −16.2 L | 17.4 L | −1.62% (−1.16) |
| ridge new+ID λ0.07 (2022→) | 4126 | 57 | −0.97% | +0.24% | −0.05 | 49.9% | 118 | −11.3 L | 20.2 L | −0.42% (−0.18) |
| ridge new+ID gated | 3022 | 59 | −1.39% | −0.61% | −0.25 | 49.1% | 110 | −8.0 L | 15.6 L | −0.27% (−0.19) |
| GBM new+ID depth 2 (2022→) | 4126 | 59 | −0.75% | +0.46% | −0.13 | 50.4% | 92 | −9.5 L | 16.9 L | −0.56% (−0.26) |
| GBM new+ID gated | 2943 | 58 | −2.02% | −1.19% | −0.64 | 49.5% | 102 | −11.1 L | 15.2 L | −1.29% (−0.05) |
| ridge live+ID λ0.07 (2022→) | 4126 | 49 | −2.32% | −1.11% | −1.31 | 49.7% | 110 | −18.6 L | 19.5 L | −0.71% (−0.34) |
| ridge live+ID gated | 1952 | 48.5 | −3.44% | −3.22% | −2.09 | 48.8% | 75 | −12.4 L | 13.1 L | −1.28% (−0.59) |

\* Per-trade mean and daily t can differ in sign: t is computed on per-day means, which weight days with several indices differently.

**Annual excess (t) for the main configurations:**

| Config | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 (to Sep) |
|---|---|---|---|---|---|---|---|---|---|
| always-CE | +0.6 (0.2) | +0.8 | +1.8 | +4.9 (1.2) | +0.6 | +10.9 (2.8) | +2.2 | +1.3 | −11.6 (−3.1) |
| trend5 | +0.1 | −3.6 | +5.9 (1.4) | +4.0 | +8.9 (2.4) | +2.3 | −6.2 (−1.9) | +0.2 | +2.4 |
| ridge live | −0.4 | −1.3 | −9.8 (−2.8) | −2.4 | +3.0 | +6.3 (1.7) | −2.9 | +2.2 | −2.7 |
| ridge new | −5.9 | −6.7 | −10.7 (−2.6) | −6.0 | −3.2 | +6.3 | +1.3 | 0.0 | +2.9 |
| GBM new d2 | −3.0 | −2.8 | −3.1 | −1.9 | −0.4 | +6.9 | −1.6 | −2.9 | +4.0 |
| ridge new+ID | | | | | +0.8 | +1.1 | +0.2 | −1.0 | +0.7 |
| ridge live+ID | | | | | −3.2 | −0.2 | −1.8 | +1.8 | −3.6 |

**Per index (full test period, ungated excess):**
- MIDCPNIFTY is the only index where several models are positive: ridge new +3.6% (t 2.1), GBM d2 +4.2% (t 2.5), always-CE +4.1% (t 2.4). These are all the same CE tilt in a 2022–2025 midcap bull market. On real 2025-26 MIDCPNIFTY monthly premiums the CE side was −11% against PE +16%.
- BANKNIFTY is negative for every model (−1.7% to −3.6%).

**What the gate does:** the in-sample-chosen gate never helped out of sample. It lowered mean y in 6 of 8 models, because the "predicted y > threshold" days were not better days.

**Configurations counted:** 3 rules plus 8 models, × {ungated, gated} = **19 evaluated variants** on 2018–2026. The same 19 were re-scored on real premiums (§8) and on the live window (§6). The best is an unregularised always-CE rule at t 1.26, well inside what 19 tries produce by chance.

## 5. Theta and expiry structure

Coin-flip y (mean of the ATM CE and ATM PE three-session returns): simulated 2016–2026, and real.

| Entry weekday | Sim 2016–20 | Sim 2021–Nov24 | Sim post-Nov24 | Sim all (t) | Real weekly lane 2025-26 (n) | Real monthly lane | Live Aug–Sep 2026 (n) |
|---|---|---|---|---|---|---|---|
| Mon | −0.4% | +1.0% | −1.9% | −0.3% (−0.7) | **−9.0% (62)** | −3.1% | +1.5% (15) |
| Tue | −0.9% | −0.1% | −0.3% | −0.4% (−1.0) | −2.6% (63) | −2.7% | +2.6% (16) |
| Wed | +1.0% | −0.4% | +1.2% | +0.5% (+1.0) | +4.1% (61) | +3.2% | +11.3% (15) |
| Thu | +1.3% | −1.6% | −0.3% | −0.2% (−0.3) | −1.8% (60) | +1.1% | −4.2% (13) |
| **Fri** | −0.7% | **−4.5%** | **−3.0%** | **−2.7% (−4.85)** | **−6.2% (63)** | −0.8% | +7.9% (12) |

By index, simulated Friday coin y: NIFTY −3.7% (t −4.3), SENSEX −4.1%, MIDCPNIFTY −5.4% (t −4.6). BANKNIFTY is −0.3%: it was monthly or Wednesday/Thursday weekly for most of the history.

| Bucket | Sim all (t) | Real weekly | Real monthly |
|---|---|---|---|
| DTE at entry 4–5 | +8.3% (2.8, n=69) | – | – |
| 6–7 | +0.3% | −3.0% (n=114) | −0.2% |
| 8–10 | −0.2% | −3.8% (n=144) | −1.1% |
| 11–20 | **−3.1% (−6.75)** | −1.6% (n=51) | +0.1% |
| 21–35 | −0.8% (−2.3) | – | −0.8% |
| DTE left at exit 1 | −0.2% | −0.8% | −0.4% |
| 2–3 | **+1.4% (2.6)** | −1.7% | +1.3% |
| 4–7 | −1.5% (−4.6) | **−5.4% (−3.6)** | +0.2% |
| >7 | −1.6% (−5.6) | +8.7% (n=6) | −0.7% |
| Hold span 3 calendar days (Mon–Wed entries) | −0.65% | **−6.6% (−4.1)** | −2.8% |
| Span 5 (holds over a weekend) | −0.8% | −2.0% | +1.0% |
| Span 7 (weekend + holiday) | −5.9% (−3.1) | +0.2% (n=5) | −3.5% |
| Front series expires inside the hold (next series used) | −0.5% | – | – |
| Entry on the index's expiry day | +0.2% | – | – |

Real weekly lane overall: −3.15%/trade (t −2.44, n=309). Real monthly lane: −0.46% (t −0.6, n=427). Simulated 2018+: −0.58% (t −2.2).

**Skip overlays** (`overlay.py`; 2 overlays × 4 strategies; mean y per trade):

| Strategy | Overlay | Sim 2018+ | Sim post-Nov24 | Real weekly | Real monthly |
|---|---|---|---|---|---|
| coin flip | none | −0.58% (t −2.2) | −0.86% | −3.15% | −0.46% |
| coin flip | **skip Friday** | **0.00%** | −0.32% | **−2.37%** | −0.37% |
| coin flip | skip DTE 11–20 | −0.03% | +0.14% | −3.47% | −0.74% |
| trend5 | none | +0.41% | +0.68% | −2.37% | +0.32% |
| trend5 | skip Friday | +0.03% | +0.85% | **+0.28%** | +0.27% |
| ridge new+ID | none / skip Friday | −0.97% / −0.67% | −1.28% / −0.93% | −4.69% / −2.08% | −3.51% / −2.04% |

**Findings:**
- Friday entries are structurally bad (weekend theta). Skipping them is the one change that helps in every sample except live Aug–Sep 2026 (n=12).
- The DTE 11–20 skip helps only in the simulation.
- The simulation's weekday pattern depends on calendar-day theta. The calibration includes a −1.2% IV term before a non-trading gap, so part of the weekend decay is pre-priced, as it is in the real data. Real weekly Monday entries (−9%) were worse than the simulation suggests. That cell has n=62 in one year, so treat it as a hypothesis.

## 6. Live-period check (Aug 24 → Oct 1 2026)

Labels are the live lane's actual ATM premiums (`agent_candidates`). The long-history models are their Aug/Sep/Oct 2026 walk-forward fits, trained only on data closed before each month.

| Strategy | Scope | Trades | Puts | Mean y | Excess vs coin | t | Side agreement with champion |
|---|---|---|---|---|---|---|---|
| **Champion `index-only-l100`**, live emitted trades (latest run per day, own contract) | 68 opportunities | 33 | 25 | +25.1% | +16.4% | 1.38 | – |
| Champion's own walk-forward validation log | 45 opportunities | 33 | 25 | +42.0% | +23.6% | 1.22 | – |
| **always-PE** | all live index-days | 67 | 67 | +39.6% | **+34.7%** | 6.06 | 39/47 |
| always-PE | champion validation days | 38 | 38 | +57.8% | **+46.6%** | 5.19 | 28/36 |
| trend5 | all live | 67 | 47 | +5.3% | +0.3% | 0.05 | 36/47 |
| ridge live+ID (champion replica, long history) | all live | 67 | 36 | −1.4% | **−6.3%** | −0.89 | 16/47 |
| ridge new+ID (long history) | all live | 67 | 65 | +41.2% | +36.3% | 6.5 | 36/47 |
| GBM new+ID d2 | all live | 67 | 27 | +12.4% | +7.4% | 1.05 | 12/47 |

**Interpretation:**
- The live window was a sharp, persistent decline: mean real ATM PE y was +39.6% and CE −29.8%.
- Any put-heavy rule wins in that window. The champion learned "puts" from about 6 weeks of exactly this regime.
- The long-history ridge new+ID happened to be put-heavy here and scores well. Its 2022–2026 out-of-sample record is +0.24%/trade (t −0.05), so this is the same regime luck.
- **A 45-day window cannot tell skill from regime.** The current promotion rule ("beat the coin flip by 1%") compares against the wrong baseline in a trending market.

## 7. 14:45 vs close (`gap.py`)

| Index (Fyers 5-min, 2021–26) | n | corr(14:45→C3, C0→C3) | 3-day sign agreement | sd(14:45→close) ÷ sd(3-day move) | 5d-trend sign flips between 14:45 and close |
|---|---|---|---|---|---|
| NIFTY | 1411 | 0.990 | 96.4% | 0.137 | 3.5% |
| BANKNIFTY | 1411 | 0.991 | 95.2% | 0.131 | 3.3% |
| SENSEX | 831 | 0.991 | 95.5% | 0.131 | 2.8% |
| MIDCPNIFTY | 963 | 0.992 | 96.9% | 0.126 | 4.0% |

Other measurements:
- **Live 1-min DB candles (Aug 3 → Sep 2026, n = 37–38 per index):** corr 0.95 / 0.95 / 0.96 / 0.99 and sign agreement 92 / 82 / 97 / 95%. The live window had unusually large closing moves.
- **Late-session drift:** the 14:45→15:30 move is correlated with the day's return to 14:45 (intraday momentum). Corr by year: −0.03, 0.00, +0.01, 0.00, **+0.10, +0.19** for 2021→2026. This has emerged recently: a 14:45 entry gives up some of the move that a close entry would also miss. It is small in variance terms (1.7% of the 3-day variance).
- **Real contracts (14:45 entry vs close entry y):** corr 0.97–0.99 per side. Weekly coin y is 0.7–1.0 pp worse from 14:45 (NIFTY weekly −4.2% vs −3.2%; SENSEX weekly −2.7% vs −2.0%). Monthly is unchanged. The winning side is the same 92–100% of the time.
- **Conclusion:** using closes for 2016–2020 changes little. The intraday features that 14:45 makes possible added nothing (§3, §4).

## 8. Real-premium study (TradeBot 2025–Mar 2026)

**Data.**
- 1-minute per-contract files: NIFTY weekly and monthly Apr–Aug 2025; SENSEX weekly and monthly Apr 2025–Feb 2026. Entry = close of the 14:44 bar.
- 30-minute monthly parquet: full chains only for the 27 Jan, 24 Feb and 24 Mar 2026 expiries. Earlier index files hold just 1–2 strikes ("sparse"; used for pricing validation only when both legs are within 1% of spot). Entry = open of the 14:45 bar; OI, IV and greeks come from the 14:15–14:45 bar.
- Spot = 1-min spot at 14:44, or Fyers 5-min for MIDCPNIFTY.

**Labels built** (y = clip(mean of D+1..D+3 close returns)):

| Lane | Set | n | Mean y_CE | Mean y_PE | Coin |
|---|---|---|---|---|---|
| Weekly (nearest series) | NIFTY 1m | 92 | 0.0% | −8.4% | −4.2% |
| Weekly | SENSEX 1m | 218 | −4.1% | −0.9% | −2.5% |
| Monthly | NIFTY 1m | 78 | +0.4% | −3.3% | −1.4% |
| Monthly | SENSEX 1m | 217 | −2.1% | −1.8% | −2.0% |
| Monthly | NIFTY 30m | 58 | −19.4% | +29.6% | +5.1% |
| Monthly | BANKNIFTY 30m | 39 | +1.2% | −3.4% | −1.1% |
| Monthly | MIDCPNIFTY 30m | 38 | −11.0% | +16.1% | +2.6% |

**Long-history walk-forward choices on REAL premiums** (out of sample for every model; excess per trade, t per trade):

| Model | Weekly lane (n=309) | Monthly lane (n=427) |
|---|---|---|
| always-CE | −0.3% (−0.09) | −4.5% (−2.02) |
| trend5 | +0.8% (0.23) | +0.8% (0.35) |
| ridge live λ0.07 | +4.4% (1.28) | +1.4% (0.64) |
| ridge live λ0.07 gated | +6.4% (1.49, n=182) | +1.0% |
| ridge new λ0.07 | −3.6% (−1.06) | −1.1% |
| GBM new d2 | −4.6% (−1.35) | −4.5% (−2.04) |
| ridge new+ID | −1.5% | −3.1% (−1.38) |
| GBM new+ID | −1.1% | −2.7% |
| ridge live+ID | +0.8% | −3.4% (−1.52) |

None of these clears 1.5. Ridge-live (the long-history champion analogue) is the least bad. It also tops a list of 19 variants × 2 lanes, so treat it as noise.

**Option-chain features at 14:45** (ICs pooled within each lane after z-scoring within (index, lane); t_adj = IC·√(days/3) for overlapping labels; 20 features × 2 lanes × 3 targets = **120 tests**):

| Feature | Weekly IC move (t_adj) | Weekly IC coin | Monthly IC move (t_adj) | Monthly IC coin | Months +/− (monthly, move) |
|---|---|---|---|---|---|
| **iv_atm (ATM IV level)** | **+0.271 (2.31)** | −0.120 | **+0.238 (2.11)** | −0.133 | **11/1** |
| iv_skew (IV 2% OTM put − 2% OTM call) | +0.125 (1.07) | +0.137 (1.17) | −0.004 | +0.069 | 6/6 |
| gex_abs_log (total gamma; all-short convention) | −0.069 | +0.013 | −0.154 (−1.36) | −0.100 | 4/8 |
| pcr_oi | +0.045 | +0.115 (0.98) | +0.082 (0.73) | +0.048 | 5/7 |
| pcr_oi_chg (vs D-1 14:45) | −0.003 | +0.031 | −0.106 (−0.94) | +0.047 | 3/9 |
| put_wall_dist / call_wall_dist | −0.057 / +0.057 | +0.124 / −0.010 | +0.079 / +0.044 | ≈0 | 9/3, 8/4 |
| maxpain_dist | +0.001 | 0.0 | +0.036 | +0.013 | 10/2 |
| gex_net_norm (call+/put−; the dealer-reversed convention is exactly −1×, so its IC is the same number with the opposite sign) | −0.015 | −0.124 (−1.06) | −0.087 (−0.77) | +0.017 | 5/7 |
| dex_norm (customer-long; dealer convention = −1×) | −0.051 | −0.072 | −0.034 | +0.003 | 2/10 |
| gamma_flip_dist (net GEX zero crossing, ±6% scan) | −0.038 | +0.082 | +0.079 (0.70) | −0.021 | 7/5 |
| ATM / total CE and PE OI change vs D-1 close, oi_chg_tilt, pcr_vol (+chg), iv_pc_atm, wall_mid | all \|t_adj\| ≤ 0.94 | | | | |

**Inside walk-forward models on real rows** (ridge λ=0.5n, monthly refit, first 3 months training only):

| Lane | Model | Trades | Excess | t |
|---|---|---|---|---|
| Weekly | OI features only | 193 | −5.4% | −1.26 |
| Weekly | long-history score + OI | 193 | −1.0% | −0.23 |
| Weekly | long-history score refit alone | 193 | −10.3% | −2.42 |
| Monthly | OI only | 312 | −6.7% | −2.58 |
| Monthly | score + OI | 312 | −7.8% | −3.00 |
| Monthly | score refit alone | 312 | −5.6% | −2.12 |

With 6–9 months of data the refits learn whatever side won recently and lose when it turns. That is the same failure mode as the live champion.

**Earlier TradeBot DEX/GEX filter study** (`index_analytics_data/dex_gex_oos_filter/report.md`, 425 MACD/RSI index-option trades):
- DEX-aligned trades: −8.0%; anti-aligned: −4.6%.
- GEX-aligned: −7.4%; anti-aligned: −6.2%.
- Both aligned: −5.9%; both anti-aligned: −1.0%.

So alignment with dealer flow did not help there either. That is consistent with this study.

**Verdict on OI features:** only IV level (the same thing as VIX level) carries information. PCR, walls, max pain, GEX/DEX and the gamma flip show nothing usable in 9 months of real data.

## 9. Caveats

- **Multiple testing.** 180 long-history IC tests + 120 OI IC tests = **300 IC tests**. 19 walk-forward variants (each scored on sim 2018+, sim post-Nov24, real weekly, real monthly and live), 8 overlay variants and 6 real-data walk-forward configurations bring the total to **33 distinct strategy configurations** across about 120 scorings. There were also 6 IV-calibration variants: v1 chain/candidates, 5 in calib2 and the final calib3. Only the weekday, DTE and |move|/VIX results survive a Bonferroni-sized bar.
- **Simulated premiums.** Correlation 0.98+ per side, but:
  - No skew dynamics: CE is optimistic by about 1.5 pp/trade and PE pessimistic by about 1.2 pp.
  - The IV multiplier was calibrated on 2025-26 and extrapolated back to 2016 through relvol, with partial pass-through c = 0.41.
  - BANKNIFTY and MIDCPNIFTY IV regimes before 2025 are not observed.
  - Before 2019, NIFTY used monthly contracts only (no weeklies existed).
- **Close vs 14:45.** 2016–2020 rows use closes; the effect is negligible (§7).
- **Calendar.** The rule calendar was verified for 2025–2026 only. Earlier BANKNIFTY and MIDCPNIFTY weekday eras are from memory. One known 2026 deviation: the 24 Mar monthly.
- **Live sample.** 67 labelled live index-days in one trending regime; no inference is possible from them alone.
- **Real-data sample.** 9 months on the weekly lane and about 3 months of full 30-minute chains; OI-feature ICs carry standard errors of about ±0.1.

## 10. Recommendations for the index lane

1. **Change the promotion test, not the features.**
   - Promote only if a configuration beats **coin flip, always-CE, always-PE and trend5** on the same validation days, with ≥100 opportunities.
   - Require it to hold up on a simulated-history backtest (`sim.py` labels, 2018→).
   - Today `index-only-l100` would fail: it trails always-PE by 23 pp on its own validation days and its replica loses over 2022–2026.
2. **Pretrain on simulated history.** The 6-week live window is the binding constraint. The simulated labels track real premiums (corr 0.98) and give 7,446 index-days.
   - Fit on the long panel, with recent live days at most up-weighted.
   - Shade the CE/PE intercept about 1.4 pp toward puts to correct the simulator's missing skew dynamics.
3. **Side bias:** do not let the model learn a free side intercept from recent weeks. The live put bias came from about 6 weeks of a falling market.
   - Report the CE/PE share next to each model's result.
   - Treat a share above 75% as regime exposure, not edge.
   - If a tilt is wanted, the only defensible one is "high VIX or IV level / large |1-day move| → lean CE". It is stable 9 of 10 years in the simulation and holds on 2025-26 real data. Forward-test it as one pre-registered rule; do not add it to the ridge.
4. **Gates that are justified:**
   - **Skip Friday 14:45 entries.** This is the only overlay that helps on simulated and real data: about +0.6 to +0.8 pp/trade on the coin, +2.6 pp for trend5 on real weekly.
   - Keep the "outlive the hold" rule.
   - Do not use a "predicted y > 0" gate. It never helped out of sample (6 of 8 models were worse).
   - Consider a weekly-vs-monthly contract choice. On real 2025-26 data the monthly contract's 3-day coin y was −0.5% against −3.2% for the nearest weekly. That is a theta difference, but it is only 9 months of data, so collect more before acting.
5. **Features:**
   - Add vix_lvl (or ATM IV), abs_ret1d / |sigma move|, and dte_exit, span and the Friday flag as gate inputs.
   - Drop nothing yet. The live directional set has no evidence for or against it (|t| < 1.6).
   - Participant-OI, intraday 14:45 features and chain OI/GEX/DEX/PCR/max pain gave nothing. Keep logging them, but do not trade on them.
6. **Data to collect:**
   - Full-chain 14:45 and close snapshots for **all 4 indices**. `chain_snapshots` has only NIFTY and BANKNIFTY today.
   - Daily NSE/BSE F&O bhavcopy, for strike-level OI history back to 2019, so §8 can run on 5+ years instead of 9 months.
   - VIX at 14:45 stored alongside each candidate.
   - Real exit-close premiums of both ATM legs every day, so the coin flip is always defined.
   - Re-run `validate.py` quarterly to re-check the IV multipliers, especially MIDCPNIFTY: its ratio moved from 1.50 to 1.15 within 8 months.

## Scripts (all in `/private/tmp/claude-501/-Users-ramachandran-MACD-mini-ADV/77c677ac-a2ad-4b4d-977f-6fcea088fa80/scratchpad/index_study/`)

| Script | Role |
|---|---|
| `run.sh` / `run_tb.sh` | Throwaway containers: engine image with numpy/scipy/sklearn pylib and the DB volume opened `mode=ro`; tradebot-backend image with pandas/pyarrow, TradeBot data mounted read-only, no network |
| `common.py` | Fyers/Yahoo loaders, session calendar, expiry calendar by era, Black-Scholes and implied vol, participant-OI parser |
| `features.py` | → `out/panel.json`: index-day panel, 14:45 entry and all features |
| `real_labels.py` (+ `fix_real.py`) | → `out/real_labels.json`: real-premium labels and 14:45 chain OI/greeks features |
| `calib.py` (v1), `calib2.py`, `calib3.py` | IV/VIX calibration → `out/calib3.json` |
| `sim.py` | → `out/sim.json`: simulated ATM CE/PE labels (14:45 and close entry) |
| `validate.py` | → `out/validate.json` (§2) |
| `ic.py` | → `out/ic.json`, `out/ic_table.md` (§3) |
| `models.py`, `show_models.py`, `show_annual.py` | → `out/models.json`, `out/preds.json` (§4) |
| `theta.py`, `overlay.py` | → `out/theta.json`, `out/overlay.json` (§5) |
| `live_check.py` | → `out/live_check.json` (§6) |
| `gap.py` | → `out/gap.json` (§7) |
| `real_eval.py` | → `out/real_eval.json` (§8) |
