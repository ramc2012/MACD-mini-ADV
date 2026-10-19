# Levels vs changes vs relative levels, and lead-lag (index options)

Study date: 3 Oct 2026. Analysis only: nothing under `MACD-mini-ADV` was changed except this file. SQLite was opened with `mode=ro`, and TradeBot data was mounted read-only.
This follows `index_lane_study.md` and reuses its data, panel and simulated labels. Scripts and outputs are in `…/77c677ac-…/scratchpad/levels_changes/` (listed at the end).

## TL;DR: answer to "levels vs changes"

1. **Yes, both were studied, systematically.** I tested 13,358 IC tests (plus 1,970 lead-lag/Granger tests):
   - 23 feature families.
   - Representations: level; relative level (z20, z60, 1-year percentile); changes over 30m, 1h, since open, 1d, 3d and 5d (OI changes matched by strike, so % of level); acceleration; sign(price move) × change; OI-buildup classes.
   - Horizons: 30m, 1h, 2h and close-of-day intraday; D+1 and D+3 from 14:45.
2. **Changes add nothing tradable beyond levels.**
   - Across the 1,572 "change" tests with index-move targets, 6.6% have |t|>2. The null expectation is about 4.6%, and the median |t| is 0.71.
   - Levels and relative levels show more |t|>2 (15.7% / 16.1%), but only one level test survives FDR, and it does not replicate (item 4).
   - Acceleration is pure noise (3.1% |t|>2).
3. **Option-chain changes are confirmation, not leading signals.** OI, PCR, IV, skew, GEX/DEX, walls and max pain move strongly *with* price over the same window:
   - OI-change tilt vs the 1-day move: IC **+0.78**.
   - Total CE OI: −0.33. Total PE OI: +0.30.
   - FII futures long-ratio change vs that day's move: +0.46.
   - VIX vs the move: −0.46 to −0.49.
   - At 5-minute resolution, **price leads OI/PCR by 5–20 minutes**: the matched OI tilt has r = +0.47–0.49 one bar after the index move, +0.27–0.29 after two bars, +0.15 after three and ≈0.09 after four.
   - In the other direction, feature-leads-price correlations are ≈0 (|r| ≤ 0.03 at +1..+6 bars).
   - A Granger-style out-of-sample test (lagged Δfeature added to lagged Δprice, refit monthly) **never** helps: 18 of 88 chain tests have positive gain, the best DM t is 2.28, and most are negative. VIX adds ≤0.05% of variance.
4. **FDR survivors on index-move targets (4 at BH q<0.05 out of 3,282 tests):**
   - **Three are one effect: intraday momentum.** The since-open change of the z-distance from the 20-day MA is the vol-scaled index move since the open.
     - IC vs the next 1 h is +0.051 (t 4.55), positive in 6 of 6 years.
     - It decays out of sample: 2025–26 earns +0.2 bp per trade on the index (t 0.46).
     - On real option premiums after costs the signal side loses (−0.6%/trade at 60 min) and beats the coin flip by only +0.8 pp (t 1.6). **Not tradable.**
   - **The fourth is the dealer-DEX level from 30-min chains vs the D+3 move** (IC −0.31; 98 days, 22 weekly clusters). It **does not replicate** on the 1-minute chains (IC −0.035, t −0.4), so it is a likely false positive.
5. **On option-target tests (coin / CE−PE), 106 survive BH, but almost all are the expiry cycle.** High total or ATM OI means near expiry, which means faster theta. Within DTE buckets they vanish (e.g. total CE OI vs coin30: −0.154 → −0.019).
   - The only long-history survivor that is a *change* is **the 1-day change in FII and client net index-PUT positions vs the 3-day coin-flip y** (t −4.76 / +4.53; 8 of 8 years; survives partialling out the price move, ΔVIX and |move|).
   - As a walk-forward 14:45 gate it lifts simulated coin y from −0.51% to +0.16% (2020+), but only from −0.86% to −0.79% after Nov 2024. On real premiums it moves the weekly lane from −3.15% to −2.14% and the monthly lane from −0.46% to −0.26%.
   - It hurts always-CE and most models, because it mainly removes "calls win" days. **Modest, not robust enough to adopt.**
6. **Practical answer:** keep using levels (VIX/IV, |move|) as the earlier study recommended. Do not build signals on OI/PCR/GEX changes. They tell you what price already did 5–20 minutes ago.

---

## 1. Data and method

| Dataset (script) | Content | Coverage | Grid |
|---|---|---|---|
| `idx5` (`targets.py`, `ic_idx5.py`) | Fyers 5-min index + India VIX | NIFTY/BANKNIFTY/SENSEX 2021-01-01 → 2026-10-01 (1,415 days); MIDCPNIFTY from 2022-07-18 (1,034) | price at T = close of the 5-min bar ending at T |
| `daily` (`ic_daily.py`) | index_study 14:45 panel (14:45 entry from 2021, close before) + NSE participant OI (file of D-1) + simulated ATM labels (`sim.json`) | 2017-01 → 2026-10-01; 6,585 index-days (POI rows 5,587, from 2019) | one row per index-day at 14:45 |
| `chain1m` (`build_chain1m.py`) | TradeBot 1-min per-contract OHLCV+OI, all strikes in the files, front expiry (nearest with DTE≥1; weekly) | NIFTY 95 days (2025-04-03 → 08-20); SENSEX 222 days (2025-04-03 → 2026-02-25) | 5-min (09:20–15:25); state = 1-min bar ending at T |
| `chain30m` (`build_chain30m.py`) | TradeBot `option_candles` 30-min full chains (≥30 strikes/day), front = nearest monthly with a full chain (DTE median 22) | NIFTY 98 days (2025-10-30 → 2026-03-23), BANKNIFTY 65, MIDCPNIFTY 41 | 30-min bar closes 09:45–15:15 |
| `live` (`dump_live.py`, `build_live.py`) | DB `chain_snapshots`, 25 strikes × 2, one expiry, + VIX, futures price | NIFTY 12 days, BANKNIFTY 14 days (3 Sep → 1 Oct 2026) | 1-min and 5-min, lead-lag only |

**Chain features** (`chainlib.py`, the same definitions as `real_labels.py`):
- **OI quantities** (log totals within ±4% of spot): total CE and PE OI, and 3-strike ATM CE and PE OI.
- **PCR:** OI-based, cumulative-volume-based and last-30-min-volume-based.
- **IV:** ATM IV; skew = IV(0.98S) − IV(1.02S); term = next-expiry ATM IV − front ATM IV.
- **Strike levels** (within ±6%), as distance to spot: call wall, put wall, max pain.
- **Greeks:**
  - GEX net, call+/put− convention. The dealer convention is exactly −1×, so it is one test, not two.
  - |GEX|.
  - DEX, customer-long convention.
  - Gamma-flip distance (scan over ±6%).

**Long-history features:**
- `idx5`: log VIX, realised vol since the open, |move from the previous close|, z-distance from the 20-day MA, today's efficiency ratio.
- `daily`: VIX, rv20, id_rv, |1-day return|, z_ma20, series ER and return, ER10; participant OI (FII/client/pro futures long ratio, FII/client call/put nets and option direction, FII futures long and short contracts, total index-futures OI).

**Representations** (`stats.add_reps`):

| Code | Meaning |
|---|---|
| `lvl` | absolute level |
| `z20`, `z60` | (level − mean of the previous 20/60 observations **at the same time of day**) / sd. Rolling front series. Needs ≥75% of the window. |
| `pct1y` | percentile within the previous 250 sessions, same time of day (long-history data only; the chain data is < 1 year) |
| `d30m`, `d1h`, `dopen` | change over 30 min, 1 h, since the first grid point (09:20 / 09:45) |
| `d1d`, `d3d`, `d5d` | change vs D-1 / D-3 / D-5 at the same time, **same expiry** |
| `acc` | d30m − d30m 30 min earlier (intraday); d1d − previous d1d (daily) |
| `sx30m`, `sx1d` | sign(index move over the same window) × change. This is the price/OI interaction. For OI it encodes the long-buildup / short-covering / short-buildup / long-unwinding classes. |
| OI changes | **strike-matched** log change Σ OI_now / Σ OI_then over strikes present at both times, i.e. Δ as % of level. Unmatched band totals drift as spot moves the ±4% band. |
| `oi_tilt` | matched (ΔPE OI − ΔCE OI) / (PE + CE OI then) |
| `chainoi_build` | sign(Δspot) × sign(Δ total chain OI) |
| POI interaction | sign(that file's own session move) × ΔPOI, i.e. FII/market futures buildup classes |

**Targets:**
- **Intraday** (decision times 10:15, 10:45 … 14:45): signed index move to T+30m, T+1h, T+2h (NaN past 15:30), and to the close.
- **Option targets on the real premiums** (ATM front, entry one minute after T, exit at the same horizons):
  - dir = y_CE − y_PE
  - coin = (y_CE + y_PE)/2
- **Multi-day** (14:45): index to the close of D+1 and D+3. On `daily`, also the simulated D+3 dir and coin labels from the earlier study.
- **Contemporaneous** (explanatory only, not a signal): Δfeature vs the index move over the same 30m, 1d or 3d window.

**Statistics:**
- **Spearman IC within (index × time-of-day) groups, pooled.** This is the n-weighted mean of within-group rank ICs.
- **Cluster-robust t:**
  - Clustered by day for intraday horizons, so overlapping slots within a day are handled.
  - Clustered by ISO week for D+1 / D+3, which handles the overlapping 3-day labels and same-day rows across indices.
- **p-values** use a t distribution with (clusters − 1) degrees of freedom. That matters for the 30-min chains (22 weekly clusters).
- **Stability:**
  - Months with the sign of the pooled IC (chain data).
  - Years (`idx5`, `daily`).
  - `daily` also reports t_yr (mean yearly IC / sd × √years) like the earlier study.
- **Splits:** pre / post 2024-11-20 where history allows, and per index.
- **Multiple testing:**
  - Family **M** = predictive tests on index-move targets, pooled across indices: 3,282 tests. BH-FDR is applied within the family, and Bonferroni 5% needs |t| > 4.33.
  - Family **O** = option dir/coin targets: 3,760 tests (Bonferroni |t| > 4.49 for M+O).
  - Robustness rows (pre/post and per-index; 6,155) and contemporaneous rows (271) are reported but not used for discovery.

## 2. Master results

### 2.1 Test counts

| Block | Tests |
|---|---|
| Predictive IC, index move, pooled (family M) | 3,282 (chain1m 1,276; chain30m 1,268; idx5 378; daily 360) |
| Predictive IC, option dir/coin (family O) | 3,760 (chain1m 1,704; chain30m 1,696; daily 360) |
| Contemporaneous IC | 271 (+110 pre/post) |
| Robustness (pre/post, per-index) | 6,045 |
| **IC total** | **13,358** |
| Lead-lag cross-correlations (per lag) | 1,808 intraday + 66 daily |
| Granger out-of-sample | 96 |
| Within-DTE retests of O survivors | 106 |
| Rule configurations | intraday 8 pooled (+20 per index); 14:45 gate 12 strategies × 4 overlays × 4 samples |

### 2.2 Which representation carries information? (family M, index-move targets)

| Representation class | Tests | Median \|t\| | % \|t\|>2 | % \|t\|>3 | BH q<0.05 |
|---|---|---|---|---|---|
| level (`lvl`) | 274 | 1.01 | 15.7 | 1.8 | 1 (chain30m DEX → D+3; does not replicate) |
| relative (z20/z60/pct1y) | 610 | 1.09 | 16.1 | 2.0 | 0 |
| change (30m/1h/open/1d/3d/5d) | 1,572 | 0.71 | 6.6 | 0.9 | 3 (all = intraday momentum, idx5) |
| acceleration | 286 | 0.72 | 3.1 | 0.0 | 0 |
| sign(Δprice) × change | 540 | 0.86 | 8.7 | 2.6 | 0 |

By dataset, % |t|>2:

| Representation | chain1m | chain30m | idx5 | daily |
|---|---|---|---|---|
| level | 18.6 | 12.7 | 13.3 | 17.5 |
| relative | 18.6 | 24.0 | 7.8 | 5.0 |
| change | 7.2 | 3.9 | 14.3 (momentum) | 6.7 |
| acceleration | 4.6 | 0.9 | 3.3 | 5.0 |
| × price | 2.3 | 10.5 | 30.0 | 2.5 |

**Reading:**
- Option-chain *changes* sit at the null rate everywhere.
- Levels and relative levels have about 3× the null rate of |t|>2, but almost nothing reaches FDR. In 3–11-month samples, persistent level features pick up regime co-trends (the short-window bias warned about in the earlier study), so the excess should be read as "possibly something, unproven".
- The `idx5` change excess is the single momentum effect (§5.1).

### 2.3 Max |t| per dataset × family × representation (index-move targets; best horizon in brackets)

Under the null, the maximum of about 6 correlated horizons commonly reaches 2–2.5. Bonferroni needs 4.33.

**chain1m** (NIFTY Apr–Aug 2025 + SENSEX Apr 2025–Feb 2026, weekly front):

| family | lvl | z20 | z60 | d30m | d1h | dopen | d1d | d3d | d5d | acc | sx30m | sx1d |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| atm_ce_oi | -1.9 [fD3] | +1.6 [f120] | +1.5 [f30] | -1.3 [f30] | +1.6 [fD1] | -1.5 [f30] | -0.8 [f30] | +1.6 [fD1] | -0.8 [fD3] | +0.9 [f60] | +1.5 [fD3] | -1.7 [fD3] |
| atm_pe_oi | -1.7 [fD3] | +1.8 [f120] | +2.0 [fclose] | +1.9 [f30] | +1.1 [f30] | +1.1 [fclose] | +0.8 [fD1] | -0.7 [f30] | +0.8 [f120] | +1.6 [fclose] | +1.7 [f30] | -1.6 [fD3] |
| call_wall_dist | +2.0 [fD3] | -1.3 [fD1] | -1.2 [fD1] | -1.4 [fD1] | -1.8 [fD1] | +1.3 [fD3] | +3.1 [fD3] | +1.3 [f60] | +1.2 [fD3] | -1.7 [fD3] | -1.4 [fD3] | -2.4 [fclose] |
| dex_norm | -2.4 [fclose] | -2.0 [fclose] | -2.2 [fclose] | +1.4 [fD1] | +0.6 [f60] | -1.4 [f30] | -1.6 [fclose] | -2.1 [fclose] | -2.7 [f60] | +2.1 [f120] | +1.7 [fclose] | +2.6 [f60] |
| gamma_flip_dist | +2.8 [f30] | +3.1 [f30] | +2.7 [f30] | +1.9 [f30] | -0.8 [f60] | +2.2 [fclose] | +2.8 [fclose] | +3.0 [fclose] | +2.0 [f60] | +1.1 [f30] | -1.1 [fD3] | -1.4 [f30] |
| gex_abs_log | -2.0 [fD3] | +1.9 [f30] | +1.8 [f30] | -1.9 [fclose] | +2.1 [fD1] | -0.8 [fD3] | +0.5 [f120] | +0.6 [fD1] | +1.8 [f120] | -2.0 [fclose] | +1.5 [f30] | +1.4 [f120] |
| gex_net_norm | -2.7 [f30] | -3.1 [f30] | -2.8 [f30] | -2.6 [f30] | +0.7 [f60] | -2.4 [fclose] | -2.8 [fclose] | -2.9 [fclose] | -3.0 [f60] | -1.6 [f30] | +1.3 [f120] | +1.6 [fD1] |
| iv_atm | +2.7 [fD3] | +1.6 [fD3] | +2.2 [fD3] | +1.7 [f60] | +3.0 [f30] | -2.1 [fD1] | +1.6 [f30] | +1.2 [fclose] | +2.2 [fclose] | -1.5 [fD3] | -1.3 [f60] | -1.5 [fD1] |
| iv_skew | +2.1 [f120] | +1.9 [f120] | +2.3 [f120] | -0.9 [fD1] | -1.4 [fD1] | +2.9 [f120] | +2.1 [f120] | -1.9 [fD1] | +1.4 [f120] | -1.6 [f120] | +1.4 [f60] | -1.3 [fD3] |
| iv_term | -2.2 [f30] | -2.2 [f30] | -2.5 [f120] | -1.8 [f30] | -1.6 [f30] | +2.0 [fD1] | -2.0 [f30] | -2.0 [f30] | -1.6 [fclose] | -2.1 [fclose] | +1.2 [f60] | +1.2 [fD1] |
| maxpain_dist | +1.3 [fclose] | -0.5 [f60] | -0.8 [f30] | -1.3 [f60] | -1.5 [fD3] | -1.7 [fclose] | -0.9 [f60] | -0.9 [fD3] | +0.8 [f60] | -1.6 [f120] | -2.0 [fclose] | -1.7 [f30] |
| oi_tilt | | | | +1.9 [f30] | +0.9 [f30] | +1.5 [f30] | +1.5 [f30] | | -1.4 [fD1] | +1.5 [fclose] | +1.1 [f30] | +1.9 [fD3] |
| pcr_oi | +3.2 [f30] | +3.7 [f30] | +3.1 [f30] | +2.4 [f30] | +1.5 [f30] | +2.7 [fclose] | +2.8 [fclose] | +2.7 [fclose] | +2.1 [f30] | +1.5 [f30] | +0.8 [f30] | -1.5 [fD1] |
| pcr_vol30 | -1.8 [fD3] | -1.4 [fD3] | +1.1 [fD1] | -0.9 [fD1] | -1.1 [fD3] | +1.5 [fclose] | -1.2 [fD3] | -2.1 [fD3] | +2.1 [fclose] | -2.2 [fclose] | -1.7 [fclose] | -1.6 [f60] |
| pcr_vol | +1.2 [fclose] | +0.6 [f30] | +0.7 [fD3] | -1.7 [fD3] | +1.9 [fclose] | +1.3 [f120] | -1.4 [fD3] | +1.1 [fclose] | +1.9 [f30] | -2.0 [fclose] | -1.8 [fclose] | -2.0 [f60] |
| put_wall_dist | -2.1 [fclose] | -2.4 [fclose] | -1.3 [fD1] | -1.8 [f120] | -1.2 [f60] | -0.7 [fD1] | -2.3 [fclose] | -2.4 [fD1] | -1.6 [fD1] | +0.8 [fD3] | -1.1 [fclose] | +1.2 [f120] |
| tot_ce_oi | -1.6 [fD3] | +1.8 [fclose] | +1.7 [fclose] | -2.0 [f30] | -1.4 [f120] | -1.3 [f30] | +1.6 [fD3] | -1.1 [f30] | +1.8 [f120] | -1.5 [fclose] | +1.0 [f120] | -1.4 [fD3] |
| tot_pe_oi | +2.0 [fclose] | +2.3 [fclose] | +2.1 [f30] | +1.4 [f30] | +0.7 [f30] | +1.9 [fclose] | +1.2 [f30] | +0.7 [f120] | +2.0 [f120] | +1.2 [f30] | +2.2 [f30] | +1.2 [f120] |

`chainoi_build` (sign Δspot × sign Δ chain OI): sx30m +1.2 [f60]; sx1d \|t\| < 1.

**chain30m** (NIFTY / BANKNIFTY / MIDCPNIFTY monthly, Nov 2025–Mar 2026). D+1/D+3 cells have only 19–22 weekly clusters.

| family | lvl | z20 | z60 | d30m | d1h | dopen | d1d | d3d | d5d | acc | sx30m | sx1d |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| atm_ce_oi | -1.0 [f60] | -2.6 [f60] | -1.4 [fclose] | +1.4 [fD1] | +1.7 [fD1] | +1.5 [fD1] | +1.4 [fD1] | +0.5 [fD3] | -0.6 [f120] | +1.5 [fD3] | +1.7 [fD1] | -3.5 [fD3] |
| atm_pe_oi | -0.7 [f60] | -2.5 [f30] | +1.5 [fD3] | -1.0 [fD3] | +0.9 [f30] | +0.9 [f30] | -1.5 [fD3] | +1.1 [fD3] | -1.0 [fclose] | -1.7 [fD3] | -2.8 [f30] | -3.9 [fD3] |
| call_wall_dist | +2.4 [fD3] | +1.8 [fclose] | +1.8 [f120] | -1.9 [fD1] | -1.8 [fD1] | -1.3 [fD1] | -1.2 [fD1] | +0.7 [fclose] | +1.5 [fclose] | +1.7 [fclose] | +1.7 [fD1] | -1.2 [fclose] |
| dex_norm | **-5.3 [fD3]** | -1.7 [fD3] | -3.6 [fD3] | +1.8 [fD1] | -1.3 [f60] | +2.2 [fD1] | -1.4 [fD3] | -2.1 [fD1] | -1.4 [fD1] | +1.7 [fD1] | +2.3 [f60] | +1.3 [fD3] |
| gamma_flip_dist | +3.9 [fD3] | +2.0 [fclose] | +4.0 [f30] | -1.8 [fD1] | -2.0 [fD1] | -2.2 [fD3] | -2.6 [fD1] | +0.5 [fclose] | +0.7 [fclose] | -1.8 [fD1] | -2.8 [f60] | -2.5 [fD3] |
| gex_abs_log | -1.0 [f120] | -2.0 [f60] | -1.1 [fD1] | -1.8 [f60] | -2.5 [f60] | -1.4 [f60] | -0.5 [f120] | -1.1 [fD1] | -0.7 [fD1] | +1.2 [fD1] | +1.6 [fclose] | -3.4 [fD3] |
| gex_net_norm | -4.0 [fD3] | -2.3 [fD3] | -3.1 [f30] | +2.0 [fD1] | -2.1 [fD3] | +2.1 [fD1] | +1.5 [fD1] | -1.0 [fclose] | -0.8 [fclose] | +1.6 [fD1] | +2.3 [f60] | +1.3 [f30] |
| iv_atm | +2.5 [f120] | +1.7 [fclose] | +2.4 [f120] | -1.8 [fD1] | +1.3 [f30] | -1.3 [fclose] | -1.4 [f30] | +2.2 [fD1] | +1.9 [fclose] | -2.0 [fD3] | -1.7 [f60] | -1.6 [f120] |
| iv_skew | -2.2 [fD3] | -2.0 [fD3] | -1.5 [fD3] | -1.5 [f30] | +1.5 [f120] | +1.8 [fD1] | +2.8 [fD1] | -2.0 [fD3] | -3.0 [fD3] | -1.4 [fclose] | -1.8 [fD1] | -1.2 [f120] |
| iv_term | -2.0 [f120] | -0.9 [f120] | | -2.7 [f120] | -2.0 [f120] | -1.9 [f60] | -1.2 [f120] | -1.9 [f30] | -2.4 [f30] | -0.6 [fclose] | -0.7 [f30] | +1.8 [f120] |
| maxpain_dist | +2.3 [fD3] | +1.5 [fclose] | +2.2 [fclose] | -2.1 [fD1] | +1.5 [f30] | -2.1 [fD1] | +1.2 [f120] | +1.3 [fD1] | +1.3 [fclose] | -1.8 [fD1] | -2.7 [f60] | -1.6 [f120] |
| oi_tilt | | | | +1.7 [f30] | +1.5 [f120] | +1.2 [f30] | -1.6 [fD3] | | -0.7 [fD1] | -2.7 [fD3] | +1.3 [fD3] | +1.7 [fD1] |
| pcr_oi | +3.4 [fD3] | +2.2 [f30] | +2.8 [f60] | -1.3 [fD1] | +2.7 [fD3] | -3.0 [fD3] | -1.5 [fD1] | +0.6 [fclose] | -1.8 [fD3] | +0.9 [fclose] | -3.4 [f60] | -2.2 [f60] |
| pcr_vol30 | +2.4 [fD3] | +1.6 [fD3] | +3.2 [fD3] | +1.6 [fD3] | -1.2 [fD1] | -1.1 [f30] | -1.0 [f30] | -1.0 [fclose] | -1.8 [fclose] | +1.6 [fD3] | +4.3 [fD3] | -0.9 [fD3] |
| pcr_vol | +1.8 [fD3] | +2.0 [fD3] | +3.7 [fD3] | +1.5 [fD3] | +1.3 [fD3] | -1.3 [fD1] | -1.2 [fclose] | -1.3 [fclose] | -2.3 [f60] | -1.0 [f30] | +1.6 [fD3] | -1.6 [fD1] |
| put_wall_dist | +2.4 [fclose] | +2.5 [fclose] | +3.5 [fD3] | -1.5 [fD1] | +1.7 [f30] | +0.7 [fD3] | +2.4 [fD3] | +1.7 [fD1] | +1.7 [fD1] | +1.5 [f60] | -2.0 [f60] | -2.3 [f30] |
| tot_ce_oi | -1.1 [f60] | -2.3 [f30] | -1.4 [fclose] | -1.1 [f120] | -1.1 [fD3] | +1.0 [fD1] | +1.7 [fD1] | -0.6 [fD1] | +0.9 [fclose] | +1.5 [fD3] | +1.9 [fD1] | -4.3 [fD3] |
| tot_pe_oi | -1.0 [f60] | -1.8 [f60] | +1.3 [fD3] | +0.9 [f120] | +0.8 [f120] | -1.4 [fD3] | -1.5 [fD3] | +0.4 [fD3] | +0.6 [f60] | -1.8 [fD3] | -1.9 [f30] | -3.5 [fD3] |

**idx5** (long-history intraday, 2021–2026, 4 indices, 52,800 decision rows):

| family | lvl | z20 | z60 | pct1y | d30m | d1h | dopen | d1d | d3d | d5d | acc | sx30m | sx1d |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| absret | +2.3 [fclose] | +2.4 [fclose] | +2.5 [fclose] | +2.0 [f30] | +1.7 [fclose] | +1.5 [fclose] | +1.7 [fD1] | +1.8 [fD3] | +2.8 [fclose] | +1.4 [fclose] | +1.2 [fD3] | +2.5 [f60] | +3.6 [f60] |
| er_today | +1.4 [fD3] | +1.4 [fD3] | +1.4 [fD3] | +1.2 [fD3] | +1.4 [fclose] | +1.6 [fclose] | | +0.8 [fD3] | +1.6 [fD3] | -1.5 [f120] | +1.4 [f60] | +3.6 [f60] | +2.8 [f60] |
| rv_id | +2.1 [fD3] | +2.1 [fD3] | +1.7 [fclose] | +1.6 [f30] | +1.1 [fD3] | +1.0 [f60] | | +1.6 [fD3] | +1.9 [fclose] | +1.5 [fclose] | +1.8 [fD3] | -3.2 [fD3] | -1.2 [fclose] |
| vix | +1.8 [fD3] | +2.2 [fD3] | +2.0 [f120] | +1.6 [f120] | -2.2 [fD3] | -1.9 [fD1] | -2.6 [fclose] | -1.0 [fD1] | +0.6 [fD3] | +0.8 [fD3] | -2.0 [f30] | +2.8 [f120] | -1.0 [fD3] |
| zma20 | -1.1 [fclose] | -1.7 [fclose] | +1.4 [f30] | +1.1 [f30] | +3.9 [fclose] | +3.6 [f120] | **+4.5 [f60]** | +3.4 [f60] | +2.8 [f60] | +1.9 [f60] | -1.2 [f60] | +1.6 [f30] | +1.9 [f30] |

**daily** (14:45 → D+1 / D+3, 2017–2026; max |t| over the two horizons):

| family | lvl | z20 | z60 | pct1y | d1d | d3d | d5d | acc | sx1d |
|---|---|---|---|---|---|---|---|---|---|
| vix | +2.7 [fD3] | +1.4 [fD3] | +1.5 [fD3] | +2.2 [fD3] | -2.0 [fD1] | +0.3 [fD3] | +0.5 [fD3] | -0.9 [fD3] | -1.6 [fD3] |
| abs_ret1d | +2.4 [fD1] | +1.9 [fD1] | +1.9 [fD1] | +2.3 [fD1] | +1.4 [fD3] | +2.0 [fD1] | +0.9 [fD1] | +1.0 [fD3] | +0.3 [fD1] |
| id_rv | +2.3 [fD3] | +2.3 [fD1] | +1.7 [fD1] | +2.2 [fD3] | +2.1 [fD3] | +2.0 [fD1] | +0.7 [fD3] | -0.5 [fD1] | +0.6 [fD1] |
| rv20 | +1.1 [fD1] | +0.2 [fD1] | -0.7 [fD3] | +0.3 [fD1] | +0.9 [fD1] | +1.3 [fD1] | +0.9 [fD1] | +0.8 [fD3] | +0.8 [fD3] |
| z_ma20 | -1.5 [fD3] | -0.8 [fD3] | +0.6 [fD1] | -1.1 [fD3] | +2.1 [fD1] | +0.6 [fD1] | +0.5 [fD1] | +2.0 [fD1] | +1.7 [fD3] |
| er10 | -1.5 [fD3] | -1.4 [fD3] | -1.4 [fD3] | -1.7 [fD3] | +2.5 [fD1] | +2.0 [fD1] | -0.7 [fD3] | +1.8 [fD3] | -1.0 [fD3] |
| er_series | +0.4 [fD1] | +0.6 [fD1] | -0.3 [fD3] | +0.4 [fD1] | +1.0 [fD3] | +0.9 [fD1] | +0.1 [fD3] | +0.7 [fD3] | +2.6 [fD1] |
| series_ret | +0.4 [fD1] | +0.5 [fD1] | +1.0 [fD1] | +0.7 [fD1] | +0.8 [fD1] | +0.7 [fD3] | +1.1 [fD1] | -0.5 [fD3] | +1.7 [fD3] |
| fii_fut_ratio | -1.7 [fD3] | +0.9 [fD1] | +0.8 [fD1] | -0.7 [fD3] | +1.5 [fD1] | +0.7 [fD1] | +0.5 [fD1] | +1.9 [fD1] | +0.5 [fD1] |
| client_fut_ratio | +1.6 [fD3] | +1.6 [fD3] | +0.8 [fD3] | +1.3 [fD3] | -1.4 [fD1] | +0.9 [fD3] | +1.3 [fD3] | -2.3 [fD1] | -1.0 [fD1] |
| pro_fut_ratio | -0.7 [fD1] | -1.4 [fD3] | -1.6 [fD3] | -0.8 [fD1] | +0.2 [fD1] | -0.6 [fD3] | -0.5 [fD1] | +0.3 [fD3] | +1.0 [fD3] |
| fii_fut_long | -0.7 [fD3] | +1.3 [fD3] | +1.3 [fD1] | +1.0 [fD1] | +1.0 [fD1] | +0.6 [fD3] | +1.0 [fD3] | +1.4 [fD1] | -0.1 [fD1] |
| fii_fut_short | +2.3 [fD3] | +1.1 [fD3] | +1.1 [fD3] | +2.0 [fD3] | -0.8 [fD1] | +0.2 [fD3] | +1.5 [fD3] | -0.9 [fD1] | -2.0 [fD1] |
| fut_oi_tot (buildup class = sx1d) | -0.2 [fD1] | -0.6 [fD1] | +0.3 [fD3] | +0.9 [fD1] | -0.7 [fD1] | -1.5 [fD1] | -1.4 [fD1] | -1.0 [fD1] | -0.9 [fD1] |
| fii_call_net | -1.0 [fD3] | +1.5 [fD1] | +1.9 [fD1] | +1.0 [fD1] | +1.4 [fD3] | +2.3 [fD1] | +1.2 [fD3] | +1.4 [fD3] | +0.3 [fD1] |
| fii_put_net | +0.1 [fD3] | -0.0 [fD1] | -0.2 [fD1] | +0.1 [fD3] | +1.8 [fD3] | +1.9 [fD1] | +0.6 [fD3] | -1.2 [fD1] | -1.4 [fD3] |
| client_call_net | -0.5 [fD1] | -1.6 [fD3] | -1.9 [fD3] | -0.9 [fD3] | -1.6 [fD3] | -1.5 [fD1] | -2.1 [fD3] | +1.3 [fD1] | -0.9 [fD1] |
| client_put_net | -0.7 [fD1] | -0.4 [fD1] | -0.4 [fD1] | -0.8 [fD1] | -1.1 [fD3] | -1.1 [fD1] | +0.3 [fD1] | +1.6 [fD1] | +0.5 [fD3] |
| fii_optdir / client_optdir | ≤ \|1.3\| | | | | | | | | |

The daily VIX level replicates the earlier finding: IC vs D+3 +0.084, clustered t 2.65, **t_yr 4.20**, 9 of 10 years, post-Nov 2024 +0.119. Its relative versions (z20 +0.042, z60 +0.045) and its changes (d1d −0.010, d5d +0.015) are weaker. **For VIX, the level is the information and the change adds nothing.** The |1-day move| level is the same (IC vs D+3 +0.040, t_yr 5.09, 10/10; its changes ≤ |t| 2.0).

### 2.4 All index-move tests with BH q < 0.10 (family M)

| Dataset | Feature \| representation | Target | IC | t | n (clusters) | Stable | q |
|---|---|---|---|---|---|---|---|
| idx5 | zma20 \| dopen | f60 | +0.051 | 4.55 | 47,313 (1,416 d) | 6/6 yrs | **0.019** |
| idx5 | zma20 \| dopen | f30 | +0.034 | 4.26 | 52,570 | 6/6 | **0.032** |
| chain30m | dex_norm \| lvl | fD3 | −0.310 | −5.31 | 204 (22 wk) | 4/4 mo | **0.032** |
| idx5 | zma20 \| dopen | f120 | +0.065 | 4.08 | 36,799 | 6/6 | **0.040** |
| idx5 | zma20 \| d30m | fclose | +0.030 | 3.94 | 52,570 | 6/6 | 0.056 |
| chain30m | gamma_flip_dist \| z60 | f30 | +0.134 | 4.01 | 730 (53 d) | 3/3 | 0.092 |
| chain1m | pcr_oi \| z20 | f30 | +0.072 | 3.69 | 2,870 (207 d) | 9/11 | 0.092 |
| idx5 | zma20 \| d1h | f120 | +0.046 | 3.62 | 31,542 | 6/6 | 0.092 |
| chain30m | tot_ce_oi \| sx1d | fD3 | −0.221 | −4.30 | 200 (22) | 4/4 | 0.092 |
| idx5 | er_today \| sx30m | f60 | +0.032 | 3.60 | 42,240 | 5/6 | 0.092 |
| idx5 | absret \| sx1d | f60 | +0.035 | 3.59 | 47,241 | 5/6 | 0.092 |
| chain30m | pcr_vol30 \| sx30m | fD3 | +0.228 | 4.27 | 204 (22) | 4/4 | 0.092 |
| idx5 | zma20 \| d30m | f120 | +0.033 | 3.57 | 36,799 | 6/6 | 0.092 |

Only the first four pass q < 0.05, and only zma20|dopen (f60) is close to Bonferroni (4.55 vs 4.33).

**What the survivors are:**
- **zma20|dopen** equals (P − P_open)/sd20, i.e. intraday momentum. The raw return since the open gives the same numbers: f60 +0.050 (t 4.30), close +0.067 (t 3.47).
  - By year (f60): 2021 +0.045, 2022 +0.046, 2023 +0.051, 2024 +0.101, 2025 +0.039, 2026 +0.013.
  - Pre-Nov 2024 +0.059 (t 4.21); post +0.035 (t 1.87).
  - By index: NIFTY +0.059 (t 4.47), SENSEX +0.057 (4.34), MIDCPNIFTY +0.065 (4.27), BANKNIFTY +0.026 (2.06).
  - By slot it is concentrated at 11:45–13:45 (IC +0.061 to +0.081, 5–6 of 6 years each). 10:15–11:15 is near 0.
- **chain30m dex_norm|lvl → D+3** does not replicate:
  - chain1m, same feature and horizon: IC −0.035 (t −0.38).
  - Earlier `real_eval` (14:45 real labels): weekly lane −0.051, monthly lane −0.034.
  - With 98 days and 22 weekly clusters it is a single regime (Nov 2025–Mar 2026). Treated as a false positive.
- **The PCR group** (pcr_oi level/z20/z60, gex_net_norm and gamma_flip_dist z20 — all functions of how put-heavy the chain is) is the most consistent directional near-miss:
  - A high PCR is followed by the index **rising** over the next 30–60 min.
  - chain1m pcr_oi|z20 f30: +0.072 (t 3.69, 9/11 months). By index: SENSEX t 3.4, NIFTY 1.7.
  - chain30m: NIFTY z20 +0.096 (t 2.3); BANKNIFTY ≈0; MIDCPNIFTY negative.
  - It is a level/relative-level effect, not a change effect: pcr_oi d30m +0.046 (t 2.4), d1h +0.030, dopen +0.033.

### 2.5 Option-target tests (family O): the expiry-cycle artefact

106 tests pass BH q < 0.05 (`master.csv`, `o_dte.json`). Re-ranking within DTE buckets (index × time × DTE) removes almost all of them:

| Feature \| representation → target | Pooled IC (t) | Within-DTE IC (t) |
|---|---|---|
| tot_ce_oi \| lvl → coin30 | −0.154 (−8.0) | −0.019 (−1.0) |
| tot_ce_oi \| z20 → coin30 | −0.153 (−8.1) | −0.007 (−0.4) |
| gex_abs_log \| lvl → coin30 | −0.143 (−7.3) | +0.006 (+0.3) |
| atm_ce_oi \| lvl → coin30 | −0.119 (−5.9) | +0.036 (+1.8) |
| iv_term \| z60 → coin30 | +0.137 (+5.9) | +0.054 (+2.6) |
| iv_atm \| d3d → coin30 | −0.113 (−4.6) | −0.036 (−1.6) |
| DTE itself → coin30 | +0.146 (+7.9) | – |
| largest within-DTE residuals: iv_atm \| lvl (30m) → coin60; dex_norm \| dopen → coin30; put_wall_dist \| dopen → coin120 | −0.146 / +0.092 / −0.141 | −0.121 (−3.9) / +0.081 (+3.8) / −0.118 (−3.5) |

No within-DTE residual reaches the 4.49 Bonferroni bar, and these are post-hoc retests. The "IV level → buying options pays less" residual (30-min monthly chains) is the volatility-risk premium the earlier study already found as VIX → coin.

**Long-history survivors on the simulated D+3 coin** (daily family O; 360 tests):

| Feature \| representation | IC vs coin | t (clustered) | t_yr | Years | Post-Nov24 IC (t) | Partial IC (controls: same-day move, ΔVIX, \|move\|) |
|---|---|---|---|---|---|---|
| fii_put_net \| d1d | −0.090 | −4.76 | −4.82 | 8/8 | −0.071 (−2.19) | −0.097 (−5.03) |
| client_put_net \| d1d | +0.092 | +4.53 | +4.74 | 8/8 | +0.079 (+2.24) | +0.099 (+4.88) |
| client_put_net \| acc | +0.069 | +4.00 | +4.74 | 8/8 | +0.054 (+1.85) | +0.068 (+3.79) |

No index-move or simulated-dir test passes BH in `daily`. The best dir test is abs_ret1d|lvl, IC +0.047, t 2.63, t_yr 4.01.

## 3. Contemporaneous (explanatory, NOT a signal)

Δfeature over the same window as the index move (IC, t):

| Feature (Δ) | chain1m 30m | chain1m 1d | chain30m 1d | Comment |
|---|---|---|---|---|
| oi_tilt (matched ΔPE−ΔCE) | **+0.563 (21.2)** | **+0.779 (19.4)** | +0.729 (8.9) | puts are written / added on up moves |
| tot_ce_oi (matched) | −0.478 (−19.3) | −0.331 (−6.9) | −0.437 (−4.2) | calls are covered or unwound on up moves |
| tot_pe_oi (matched) | +0.439 (17.5) | +0.298 (5.8) | +0.380 (4.5) | |
| atm_ce_oi / atm_pe_oi | −0.484 / +0.464 | −0.534 / +0.482 | −0.345 / +0.320 | |
| pcr_oi (unmatched band) | +0.184 (6.2) | +0.299 (3.8) | −0.363 (−3.6) | sign flips on monthly chains: band-composition artefact; use oi_tilt |
| pcr_vol (cumulative) / pcr_vol30 | −0.660 / −0.516 | −0.480 / −0.345 | −0.271 / −0.142 | call volume dominates on up moves |
| iv_atm | −0.192 (−5.6) | −0.151 (−2.2) | −0.582 (−7.2) | spot up, IV down |
| iv_skew (0.98S−1.02S) | +0.286 (10.3) | +0.259 (3.7) | +0.291 (5.1) | fixed-moneyness/stale-quote effect |
| iv_term | +0.026 (0.9) | −0.013 (−0.2) | +0.137 (1.1) | none |
| dex_norm / gex_net_norm | +0.626 / +0.132 | +0.529 / −0.165 | +0.855 / +0.508 | DEX is mechanical in spot |
| call_wall / put_wall / max pain / flip distance | −0.72 / −0.67 / −0.76 / −0.07 | −0.16 / −0.06 / −0.56 / +0.12 | −0.54 / −0.57 / −0.80 / −0.47 | distance = K/S−1, mechanical |

**Long history:**

| Feature (Δ1d vs that day's move; POI vs its own session) | IC (t) | Years | Δ3d vs 3-day move |
|---|---|---|---|
| VIX (daily 14:45) | −0.459 (−18.6) | 10/10 | −0.447 |
| VIX (idx5: 30m / 1d / 3d) | −0.460 (−36.2) / −0.487 (−16.6) / −0.457 (−12.6) | 6/6 | |
| intraday RV | −0.285 (−11.6) | 6/6 | −0.374 |
| FII futures long ratio | +0.456 (19.2) | 8/8 | +0.310 |
| client futures long ratio | −0.315 (−12.0) | 8/8 | −0.244 |
| FII option direction (call net − put net) | +0.466 (21.7) | 8/8 | +0.362 |
| client option direction | −0.598 (−30.1) | 8/8 | −0.451 |
| FII put net / client put net | −0.306 / +0.459 | 8/8 | −0.245 / +0.362 |
| FII futures long / short contracts | +0.346 / −0.385 | 8/8 | |
| total index-futures OI | −0.122 (−4.3) | 4/8 | −0.125 |
| pro futures ratio | −0.109 (−4.5) | 7/8 | |

**Interpretation:**
- **Confirmation features:**
  - The OI tilt and the CE/PE OI changes (writers sell the side the market moves away from).
  - FII futures/option positioning (FII trend-follow and clients fade).
  - VIX/IV (down on up moves).
- These co-move with price as strongly as anything in finance. That is why changes "look" informative on a chart.
- They do not predict the next move (§2). The lead-lag section below shows they lag it.

## 4. Lead-lag

corr(ΔF_t, r_{t+k}) per day, averaged over days, with t over days. **k > 0 means the feature change LEADS the index.** No overnight pairs are used.

**India VIX vs index, 5-minute bars, 2021–2026** (d = 1,034–1,414 days):

| Index | k=−3 | k=−2 | k=−1 | k=0 | k=+1 | k=+2 | k=+3 |
|---|---|---|---|---|---|---|---|
| NIFTY | +0.006 (1.8) | +0.025 (7.5) | −0.011 (−2.9) | **−0.413 (−52.3)** | −0.005 (−1.6) | +0.017 (5.2) | −0.007 (−2.0) |
| BANKNIFTY | +0.008 (2.4) | +0.019 (5.8) | −0.015 (−3.9) | −0.358 (−49.5) | −0.001 (−0.2) | +0.013 (3.9) | −0.006 (−1.8) |
| SENSEX | +0.005 (1.4) | +0.025 (7.4) | −0.007 (−1.9) | −0.404 (−51.0) | −0.014 (−4.1) | +0.018 (5.6) | −0.006 (−1.9) |
| MIDCPNIFTY | +0.006 (1.5) | +0.010 (2.4) | −0.010 (−2.2) | −0.310 (−41.1) | −0.010 (−2.3) | +0.016 (3.9) | −0.009 (−2.2) |

Pre and post Nov 2024 are the same: NIFTY k=0 −0.416 / −0.405, k=+2 +0.014 / +0.023.

**Reading:**
- VIX is contemporaneous.
- The k=±2 terms (≈ +0.02) are a 10-minute VIX overshoot/reversal: statistically detectable but worth R² ≈ 0.0003.
- Granger OOS (6 lags of r vs + 6 lags of ΔVIX, monthly expanding refit from 2022), next 5-min bar: R² gain +0.0001 / +0.0001 / +0.0003 / +0.0005 for NIFTY / BANKNIFTY / SENSEX / MIDCPNIFTY, DM t 0.94 / 0.71 / 1.75 / 1.38.
- Next 30 min: gains −0.0002..+0.0001.
- Directional hit rate 49.8–50.6%.

**Chain features vs index, 5-minute bars** (chain1m; OI changes strike-matched over 5 min):

| ΔFeature | Index | k=−4 | k=−3 | k=−2 | k=−1 | k=0 | k=+1 | k=+2 | k=+3 | k=+4 |
|---|---|---|---|---|---|---|---|---|---|---|
| OI tilt (ΔPE−ΔCE) | NIFTY (95 d) | +0.095 | +0.153 | +0.292 | **+0.470 (29.0)** | +0.164 | −0.022 (−1.7) | +0.004 | −0.001 | −0.020 |
| OI tilt | SENSEX (222 d) | +0.081 | +0.147 | +0.267 | **+0.490 (43.1)** | +0.142 | −0.022 (−2.4) | +0.003 | −0.013 | −0.029 (−3.5) |
| matched log PCR | NIFTY / SENSEX | +0.089 / +0.070 | +0.145 / +0.135 | +0.288 / +0.258 | +0.468 / +0.486 | +0.165 / +0.144 | −0.025 / −0.018 | +0.007 / +0.002 | −0.005 / −0.019 | −0.022 / −0.031 |
| total CE OI | NIFTY / SENSEX | −0.078 / −0.063 | −0.123 / −0.099 | −0.220 / −0.186 | −0.344 / −0.304 | −0.141 / −0.092 | +0.009 / +0.007 | +0.002 / +0.016 | −0.010 / +0.009 | +0.005 / +0.021 |
| total PE OI | NIFTY / SENSEX | +0.050 / +0.033 | +0.095 / +0.082 | +0.195 / +0.146 | +0.309 / +0.285 | +0.095 / +0.084 | −0.024 / −0.010 | +0.005 / +0.015 | −0.015 / −0.006 | −0.024 / −0.010 |
| ATM CE OI | NIFTY / SENSEX | −0.083 / −0.071 | −0.133 / −0.116 | −0.226 / −0.215 | −0.388 / −0.382 | −0.120 / −0.078 | +0.019 / +0.021 | +0.015 / +0.014 | −0.004 / +0.016 | +0.001 / +0.029 |
| ATM IV | NIFTY / SENSEX | −0.009 / +0.005 | +0.015 / +0.010 | +0.021 / +0.021 | +0.027 / +0.033 | −0.194 / −0.290 | +0.006 / +0.016 | +0.015 / +0.027 (3.3) | −0.001 / −0.007 | −0.008 / +0.009 |
| IV skew | NIFTY / SENSEX | +0.014 / +0.001 | +0.000 / +0.010 | +0.026 / +0.019 | +0.075 / +0.059 | +0.091 / +0.242 | +0.002 / **−0.073 (−8.6)** | −0.000 / −0.012 | +0.014 / −0.010 | −0.021 / −0.008 |
| GEX net | NIFTY / SENSEX | −0.086 / −0.070 | −0.108 / −0.096 | −0.229 / −0.224 | −0.370 / −0.417 | +0.701 / +0.688 | −0.020 / −0.034 | −0.032 / −0.027 | −0.010 / +0.013 | −0.006 / +0.003 |
| PCR volume (30m) | NIFTY / SENSEX | −0.109 / −0.111 | −0.108 / −0.138 | −0.138 / −0.174 | −0.267 / −0.291 | −0.376 / −0.289 | +0.028 / +0.022 | +0.022 / +0.005 | −0.004 / +0.003 | +0.008 / +0.004 |

**30-minute bars** (chain30m, pooled within-day pairs, day-clustered t, all 3 indices, 204 index-days):

| ΔFeature | k=−3 | k=−2 | k=−1 | k=0 | k=+1 | k=+2 | k=+3 |
|---|---|---|---|---|---|---|---|
| OI tilt (matched) | −0.036 | +0.068 (2.7) | **+0.336 (7.9)** | +0.426 (8.2) | +0.035 (1.9) | −0.043 | +0.027 |
| total CE OI | +0.002 | −0.057 | −0.252 (−6.2) | −0.292 (−7.6) | −0.017 | +0.021 | −0.045 |
| total PE OI | −0.039 | +0.017 | +0.185 (4.9) | +0.267 (5.5) | +0.022 | −0.047 | −0.018 |
| ATM IV | +0.027 | +0.032 | −0.008 | −0.511 (−8.3) | −0.009 | +0.073 (2.4) | +0.022 |
| GEX net | +0.015 | −0.099 | −0.227 | +0.594 | −0.004 | −0.013 | −0.052 |
| IV skew | −0.009 | −0.001 | −0.022 | +0.222 | −0.001 | −0.046 | +0.070 |

**Live 1-minute chain** (Sep 2026, 12–14 days):
- Raw PCR, NIFTY: k=−5..−2 +0.17 to +0.24 (t 6–10); k=−1 +0.073; k=+1 +0.031 (1.7); k=+2 +0.011. Exchange OI prints are batched about every 3 minutes, so the lag spreads over 2–6 minutes.
- BANKNIFTY PCR: k=+1 +0.031.
- VIX: k=0 −0.433 / −0.353; k=+1 −0.024 / −0.026.
- IV skew: k=+1 −0.076 (−3.8) NIFTY, −0.097 (−6.9) BANKNIFTY.

**Granger OOS on the chain features** (22 features incl. matched OI × NIFTY/SENSEX × next 5 min / next 30 min = 88 tests; expanding window, monthly refit, first 3 months training only):
- Positive OOS gain in 18 of 88 tests.
- Best DM t +2.28 (SENSEX matched ATM PE OI, 30 min; gain 0.3% of variance); worst −3.09. The median gain is negative: adding lagged feature changes **hurts** out of sample.
- Hit rates: full model 49.6–50.9% vs restricted 49.4–51.3%.

**Daily participant OI vs NIFTY close-to-close return** (lags in days; per-year correlations, t over 8 years):

| ΔPOI | k=−1 (index leads) | k=0 | k=+1 | k=+2 | k=+3 |
|---|---|---|---|---|---|
| FII futures long ratio | −0.039 | **+0.449 (10.5)** | −0.000 | +0.065 (2.4) | −0.008 |
| client futures long ratio | +0.030 | −0.351 (−7.1) | −0.024 | −0.044 | +0.021 |
| FII put net | +0.118 (3.8) | −0.283 (−6.6) | −0.065 (−1.7) | −0.005 | +0.056 (2.5) |
| client put net | −0.194 (−8.2) | +0.436 (23.9) | +0.043 | +0.025 | −0.062 (−2.7) |
| FII call net | −0.098 (−3.7) | +0.269 (7.4) | −0.015 | +0.008 | +0.026 |
| total index-futures OI | +0.019 | −0.085 (−2.5) | −0.019 | −0.049 | −0.005 |

**Which feature changes lead the index, and by how much:**
- **None in a usable way.**
- Option OI, PCR, OI tilt, GEX/DEX and wall/max-pain changes **lag** the index by one 5-min bar at peak, with a tail to about 20 minutes. On 30-min bars the lagged term is as large as the same-bar term.
- Participant positioning is same-day. Clients add puts the day *after* a fall (k=−1, −0.19).
- The only k>0 cells above |t| 3.5 are artefacts:
  - VIX k=+2 (+0.017): VIX noise reversal.
  - IV-skew k=+1 (−0.07 to −0.10) on SENSEX and live BANKNIFTY, but **+0.002 on NIFTY 1-min**. Skew jumps when spot moves before OTM quotes update, then reverts. The Granger test controlling for own lags shows no value (SENSEX skew gain −0.29% of variance, DM t −1.93).
  - Matched OI tilt/PCR k=+4 (−0.03, SENSEX only).
- About 1,970 lead-lag tests make |t| ≈ 4.4 the Bonferroni bar.

## 5. Model and rule tests for the survivors

### 5.1 Intraday momentum (zma20|dopen), pre-registered on 2021–2024

**Rule:**
- Side = sign(z-move since the open).
- "Strong" = |z| ≥ the 2021–24 67th percentile at that time of day.
- Exit after 60 minutes (slots 10:15–14:15) or at the close (slots 10:15–14:45).

**(A) Index only, signed index move per trade** (basis points; t clustered by day):

| Period | Exit | All slots: n, bp (t), hit | Strong: n, bp (t), hit |
|---|---|---|---|
| 2021–24 (in-sample) | 60 min | 31,754, +1.31 (3.71), 52.0% | 10,485, +3.02 (4.26), 54.1% |
| 2021–24 (in-sample) | close | 35,282, +2.52 (2.37), 52.0% | 11,650, +5.40 (2.66), 54.1% |
| **2025-01 → 2026-10 (OOS)** | 60 min | 15,547, **+0.19 (0.46)**, 51.5% | 5,439, **+0.21 (0.29)**, 51.7% |
| **OOS** | close | 17,275, +1.27 (1.01), 53.0% | 6,035, +2.91 (1.37), 54.3% |

OOS by index (strong, close): NIFTY +1.4 bp (0.6), BANKNIFTY +0.6 (0.3), SENSEX +1.1 (0.5), MIDCPNIFTY +8.0 (2.5).

**(B) REAL option premiums, ATM front contract.**

Costs per round trip are a fraction of premium:
- Full bid-ask spread from live ATM data: NIFTY 0.23%, BANKNIFTY 0.29%. Assumed: SENSEX 0.35%, MIDCPNIFTY 0.50%.
- STT 0.1% on the sell side.
- Exchange 2 × 0.035% plus GST.
- Stamp.
- ₹20/order brokerage plus GST, per lot.

The median total is **1.08%** (1-min weekly) and **0.73%** (30-min monthly). Entry is one minute (1-min data) or one bar open (30-min data) after the decision.

| Data | Exit | Selection | n | Signal side y | Coin | Always-CE | Always-PE | Excess vs coin (t) | ₹/lot total |
|---|---|---|---|---|---|---|---|---|---|
| chain1m (NIFTY+SENSEX weekly) | 60 min | all | 2,852 | −0.63% (−1.09) | −1.44% | −1.29% | −1.60% | +0.81 pp (1.56) | −1.53 L |
| chain1m | 60 min | strong | 1,029 | −1.79% (−1.76) | −1.42% | −1.10% | −1.74% | −0.37 pp (−0.40) | −1.72 L |
| chain1m | close | all | 3,169 | −0.54% (−0.31) | −2.77% | −2.37% | −3.17% | +2.23 pp (1.46) | +0.33 L |
| chain1m | close | strong | 1,148 | −1.04% (−0.38) | −3.39% | −4.45% | −2.34% | +2.35 pp (0.95) | −1.19 L |
| chain30m (NIFTY/BANKNIFTY/MIDCP monthly) | 60 min | all | 1,836 | −0.16% (−0.25) | −0.70% | −1.35% | −0.04% | +0.54 pp (1.04) | −2.64 L |
| chain30m | 60 min | strong | 832 | +0.40% (0.43) | −0.58% | −1.17% | −0.00% | +0.99 pp (1.20) | −0.60 L |
| chain30m | close | all | 2,040 | +2.20% (0.89) | −1.01% | −2.80% | +0.79% | +3.20 pp (1.69) | +3.53 L |
| chain30m | close | strong | 921 | +5.44% (1.36) | −0.40% | −3.85% | +3.06% | +5.84 pp (1.87) | +5.52 L |

**Verdict:**
- Eight configurations were tried, and the best excess t is 1.87.
- The signal side is negative after costs on the weekly chains. The positive 30-min-monthly "to close" rows ride a falling market (always-PE +3.1%), and MIDCPNIFTY carries most of it (+11.6 pp, n=175).
- The gross index edge out of sample (+0.2 to +2.9 bp) is a fraction of the option round trip. **Do not trade it. At most, log it as a context feature.**

### 5.2 14:45 lane: participant put-net change as a gate (walk-forward by year, 2020–2026)

**Gate construction**, re-estimated each year on data before that year:
- score = sign_i × z(fii_put_net Δ1d) + sign_j × z(client_put_net Δ1d), with signs from training ICs vs coin. They came out −1 / +1 in every year.
- Skip when the score is below the training 33rd percentile. The take rate is 59–73% per year.
- It is applied on top of the earlier study's strategies (`preds.json`). Values are mean y per trade, with t:

| Strategy | Sample | No gate | Gate | Skipped days only | Skip Friday | Gate + skip Friday |
|---|---|---|---|---|---|---|
| coin flip | sim 2020+ | −0.51% (−1.78), n 5,091 | **+0.16% (0.46)**, 3,343 | −1.78% (−3.47), 1,748 | +0.10% | +0.49% (1.36) |
| coin flip | sim post-Nov24 | −0.86% (−2.01) | −0.79% (−1.57) | −1.02% (−1.26) | −0.32% | −0.48% |
| coin flip | real weekly 2025-26 | −3.15% (n 309) | −2.14% (206) | | −2.37% | −2.41% |
| coin flip | real monthly | −0.46% (427) | −0.26% (277) | | −0.37% | −0.57% |
| always-CE | sim 2020+ | +1.14% | −0.21% | +3.71% (2.43) | +1.58% | +0.17% |
| always-CE | real weekly / monthly | −3.45% / −4.92% | −8.76% / −7.46% | | | |
| always-PE | sim 2020+ | −2.16% | +0.52% | −7.27% (−4.34) | −1.39% | +0.81% |
| always-PE | real weekly / monthly | −2.86% / +4.01% | +4.49% / +6.94% | | | |
| trend5 | sim 2020+ | +1.04% | +2.13% (1.89) | −1.05% | +1.00% | +1.75% |
| trend5 | sim post-Nov24 | +0.68% | +2.53% (1.49) | −3.24% | | |
| ridge live λ0.07 | sim 2020+ / post | −0.71% / −1.73% | −1.08% / −0.70% | | | |
| GBM new d2 | sim 2020+ / post | −0.08% / −1.35% | −1.49% / −3.44% | | | |
| ridge new λ0.07 | sim 2020+ / post | −0.70% / −0.21% | −1.40% / −2.05% | | | |

**Mechanism:**
- The skipped days, where FII net put longs rose and client net puts fell, are followed by "calls win, puts lose a lot". On skipped days, always-CE earned +3.7% and always-PE −7.3%.
- The gate is therefore partly a hidden CE-vs-PE tilt.
- |3-day move| / implied: 0.806 skipped vs 0.821 kept. 3-day VIX change: +0.7% vs −0.1%.

**Verdict:**
- The gate helps the coin flip and put-heavy rules.
- It hurts always-CE and 7 of the 8 walk-forward models (only ridge live+ID improves).
- After Nov 2024 the coin improvement is only +0.07 pp.
- Not adopted. Skip-Friday from the earlier study remains the simpler gate.

### 5.3 Other candidates (no rule test, because they did not survive or did not replicate)

- **chain30m DEX level → D+3:** fails replication (§2.4). The earlier real-label walk-forward with OI features was already negative (−5% to −8%/trade).
- **PCR level / z20 → next 30–60 min (contrarian), q ≈ 0.09:**
  - Same sign in SENSEX weekly (t 3.4) and NIFTY monthly 30-min (t 2.3).
  - Absent in BANKNIFTY and reversed in MIDCPNIFTY.
  - With costs of ~1% of premium against IC 0.07, it is unlikely to pay. Worth logging and forward-testing only.

## 6. Caveats

- **Sample size of real chain data:**
  - 1-min chains: NIFTY 95 days (Apr–Aug 2025), SENSEX 222 days (Apr 2025–Feb 2026).
  - Full 30-min chains: about 98 / 65 / 41 days (Nov 2025–Mar 2026).
  - Live chain: 12–14 days.
  - D+1/D+3 tests on chains rest on 22–48 weekly clusters: IC standard errors ≈ ±0.1–0.15. A real |IC| of 0.05 is undetectable there.
  - No chain feature has a 1-year history, so `pct1y` exists only for VIX, RV, |move| and participant OI.
- **Multiple testing:** about 15,500 tests in total. FDR is applied within the discovery families. The pre/post, per-index and within-DTE views are post-hoc diagnostics, not new discoveries.
- **Persistence bias:** level and relative-level features in short samples share slow regimes with the index. The 15–16% |t|>2 rate for levels is probably partly this, not signal. Clustering by day or week does not fully fix it.
- **Front-series rolls:**
  - z-scores use the rolling front series. Changes are same-expiry, so expiry rolls do not create fake Δ.
  - OI levels and changes are dominated by the expiry cycle (OI builds about tenfold over a weekly's life). Hence the DTE-control retests.
  - The 30-min front is "nearest monthly with a full chain", sometimes up to 89 DTE.
- **OI timing:** exchange OI updates in batches (about 3 min). The lead-lag lag (price → OI) is partly reporting delay, but it holds at 30-min bars too, where reporting delay is irrelevant.
- **Unmatched band totals:** PCR from fixed-moneyness bands changes mechanically as spot moves (the sign flips between datasets in §3). Use strike-matched changes (oi_tilt, matched PCR).
- **Costs:**
  - The SENSEX and MIDCPNIFTY spreads are assumptions.
  - The brokerage share depends on lot value: current lots were used (65/30/20/120). The 2025 NIFTY lot was 75, which lowers its share slightly.
  - Entry is delayed one minute (or one 30-min bar open) after the decision.
- **The simulated D+3 labels (daily coin/dir)** inherit the simulator's missing skew dynamics (see the earlier study, §2).

## 7. Recommendations

1. **Answer to the question: changes in option-chain features are confirmation of price, not a lead.**
   - Do not add Δ-OI, ΔPCR, ΔGEX/DEX, Δwall, Δmax-pain or OI-buildup classes as directional inputs at any horizon from 30 minutes to 3 days.
   - They are 5–20 minutes *behind* the index and add nothing out of sample.
2. **Keep levels where they already earned their place:** VIX/ATM-IV level and |1-day move| (unsigned stress → index up), plus the weekday/DTE gates. Their relative or Δ versions are weaker than the raw level.
3. **Use the OI tilt as a contemporaneous *filter*, not a trigger.** "Price up and OI tilt up (puts written)" is how a genuine up-move looks.
   - If a trade's premise is "the market is going up", the absence of that confirmation is informative after the fact. It cannot be traded ahead of time.
   - Any such filter must be tested with the 5–20-minute lag built in.
4. **Intraday momentum** (move since the open, vol-scaled, midday slots) is the one statistically robust change-type effect (6/6 years).
   - It has decayed in 2025–26 and does not survive option costs.
   - If an intraday lane is ever built, use it as the side tie-breaker for a decision already taken for other reasons, not as an entry signal.
5. **Do not adopt the participant put-net gate.** It is a disguised CE/PE tilt and is unstable after Nov 2024. Skip-Friday stays the recommended gate.
6. **Forward-log, do not trade:**
   - PCR z-score vs the next 30–60 min (contrarian; SENSEX/NIFTY only).
   - ATM-IV level vs the intraday coin flip (within DTE, t −3.9 on 30-min chains).
   - Re-test once there are ≥12 months of full 1-min chains for all four indices.
7. **Data, to make any chain study decisive:**
   - Store full-chain OI snapshots every 5 minutes for all 4 indices and all near expiries. Today `chain_snapshots` has only NIFTY and BANKNIFTY, 25 strikes, one expiry, about 1 month.
   - Store strike-matched OI changes directly.
   - Store bid/ask for SENSEX and MIDCPNIFTY, so costs can be measured rather than assumed.

## Scripts and outputs

All are in `/private/tmp/claude-501/-Users-ramachandran-MACD-mini-ADV/77c677ac-a2ad-4b4d-977f-6fcea088fa80/scratchpad/levels_changes/` (index_study is mounted read-only at `/w`).

| Script | Role → output (`out/`) |
|---|---|
| `run_tb.sh`, `run.sh` | Docker runners: tradebot-backend image with TradeBot data read-only and no network; engine image with the DB opened `mode=ro` |
| `chainlib.py` | Vectorised chain features (IV, skew, GEX/DEX, flip, walls, max pain, OI totals) and the strike-matched OI change |
| `build_chain1m.py` | 1-min contracts → 5-min chain features, matched OI changes (5m/30m/1h/open/1d/5d), ATM premiums → `chain1m.parquet` |
| `build_chain30m.py` | 30-min option_candles → `chain30m.parquet` |
| `dump_live.py`, `build_live.py` | `chain_snapshots` (read-only) → `live_chain.csv`, `live1m.parquet`, `live_spread.json` (ATM spreads) |
| `targets.py` | Fyers 5-min index + VIX grid, forward moves → `idx5.parquet` |
| `stats.py` | Clustered rank IC, BH, representation builder (`add_reps`) |
| `ic_chain.py` | chain1m/chain30m ICs (predictive, option targets, contemporaneous, per index) → `ic_chain.json`, `chain_panel_*.parquet` |
| `ic_idx5.py` | long-history intraday ICs (all/pre/post/per index) → `ic_idx5.json`, `idx5_panel.parquet` |
| `ic_daily.py` | 14:45 multi-day ICs incl. participant OI → `ic_daily.json`, `daily_panel.parquet` |
| `master.py`, `repclass.py` | Master table and FDR → `master.csv` (all 13,358 rows), `master_tables.md` |
| `o_dte.py`, `checks.py` | Within-DTE retests, momentum/raw-return check, POI partial ICs → `o_dte.json` |
| `leadlag.py` | Cross-correlations, Granger OOS, daily POI lags → `leadlag.json` (`leadlag_show.txt` is the printed view) |
| `rules_intraday.py` | Momentum rule: index-only and real premiums with costs → `rules_intraday.json` |
| `rules_gate.py` | 14:45 participant-OI gate, walk-forward, sim + real labels → `rules_gate.json` |
