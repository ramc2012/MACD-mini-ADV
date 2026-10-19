# Side selection (CE vs PE): which signals pick the right side, judged as an option buyer

Study date 4 Oct 2026. Analysis only. Nothing under the project was changed except this file and
`reports/Side selection study.md`. Every SQLite file was opened read-only (`mode=ro`) from throwaway containers with no
network. Code and outputs: `/private/tmp/claude-501/-Users-ramachandran-MACD-mini-ADV/77c677ac-a2ad-4b4d-977f-6fcea088fa80/scratchpad/side_selection/` (section 10).

**The question.** The user's principle: "In options gains come from move. In September falling market gave gains via
put. Our duty is to select the side correctly. In options below 50% win rate can also give massive gains. We need to
be cautious in rejecting a theory." The previous study (`supertrend_pivot_standalone.md`) benchmarked each policy
against random entries *with the policy's own CE/PE mix*. That tests timing only and gives no credit for choosing the
side. It also read "fails FDR on 19 days" as a rejection, when it only means "not proven". This study isolates the
side decision and reports power.

## 0. Bottom line

1. **Side value is measured by buying BOTH legs.** At every decision the ATM CE and the ATM PE of the same strike and
   expiry are bought at the same minute and exited by the same rule. Side value = P&L(chosen leg) − P&L(other leg),
   ₹ per ₹1 L ticket, net of costs. Timing, volatility and theta cancel; only the side remains. A coin flip has side
   value 0 by construction. The equal-capital straddle comparison is exactly half of it: P&L(chosen, ₹1 L) −
   P&L(straddle, ₹50 k per leg) = side value / 2.
2. **The CE-vs-PE payoff is nearly symmetric, so the hit rate does matter for the side decision, but magnitude
   matters more.** Being wrong costs about as much as being right gains: side-value skew is −0.5 to +0.6 and the
   payoff ratio 0.9–1.1 for most methods; the CE−PE spread itself is mildly negatively skewed (−1.8 to −0.1: falls pay
   puts more). The option buyer's asymmetry (bounded loss, open gain) lives in each leg,
   not in the choice between them. The right single number is **capture** = Σ side value / Σ |CE−PE| (the share of the
   hindsight-oracle prize collected). A method with hit < 50% is positive if it is right on the bigger moves (payoff
   > 1); several are (e.g. VIX-median rule on TradeBot indices, hit 0.518, payoff 1.28).
3. **What picks the side better than a coin flip (positive & significant on real premiums, replicated):**
   - **The index-spot Supertrend flip, at the flip.** 15-min ST(14,2) flip on NIFTY/SENSEX, real 1-min premiums
     Apr 2025 – Feb 2026 (141–143 sessions): **+₹5.7 k per ₹1 L at +30 min [+1.0, +10.9], +₹15.3 k to D+1, +₹21.1 k
     to D+3 [+10.9, +34.2], +₹10.5 k with lane exits**; hit 0.54–0.58; capture 13–20% of the oracle; positive in both
     CE- and PE-favouring months (+₹21.3 k / +₹21.0 k at D+3). Live indices Aug–Oct 2026: +₹6.7 k at +30 min
     [+1.1, +12.9]. Simulated 2021–26 (1,229 sessions): +₹4.2 k at D+3 [+0.0, +8.0]. Spot move to the close after the
     flip is positive in every year 2021–2026 (+0.6 to +8.9 bps; pooled +3.6 bps, t +3.6, BH pass). 5-min and 30-min
     flips agree (ix 30-min flip +₹9.3 k at +30 min; TradeBot index 30-min flip +₹2.9 k). **On stock options the same
     flips have no side value** (TradeBot +₹0.2 k, live −₹1.0 k at +30 min).
   - **Pivot location for the next 30 minutes** (spot above P → CE, else PE): +₹2.6 k (NIFTY/SENSEX 1-min), +₹1.3 k
     (TradeBot indices), +₹0.4 k (TradeBot stocks) per ₹1 L, all significant over 204–214 sessions, positive in both
     month types. Not in the live window (indices −₹2.9 k), gone by the close, and negative at D+3 on 2025–26 indices.
     Small and short.
4. **Positive but not yet proven (underpowered) — do not reject:**
   - **Slow trend side selectors on indices, judged on 10 years.** Series-to-date return sign (SER) +₹4.8 k per ₹1 L
     on 3-session holds 2016–26 [−0.0, +9.6], +₹9.1 k 2021–26 [+0.2, +17.3]; 20-day return sign (T20) +₹4.3 k
     [−1.7, +10.4]. Capture is only 4–7% of the oracle; hit 0.53–0.54. **Sessions needed for 80% power: about 2,500
     (SER, 2021–26) to 10,000 (T20, 2016–26) index-sessions** — they cannot be proven by live trading in any
     reasonable time.
   - **Stress → CE tilt** (VIX in the top third of its year, or a large |1-day move|): +₹6–17 k per ₹1 L at 3
     sessions in the long simulation, spot D+3 move positive in 9 of 10 (VIX) and 10 of 11 (|move|) years. On real
     2025–26 premiums it is "significant" (+₹30–42 k) but that is one episode (May 2025, 21 of 34 active sessions).
     It **failed in September 2026** (falling market with rising VIX: −₹90 k on its one live D+3 day, spot move to D+1
     −101 bps on the active days).
   - **The MACD lane's own side, at its own exits** (40% stop / trail / +100% / 4 sessions): +₹11.2 k (NIFTY/SENSEX),
     +₹1.1 k (TradeBot indices), +₹1.9 k (TradeBot stocks), +₹11.6 k (live stocks, significant) per ₹1 L. Not proven on
     2025–26 TradeBot (needs ~2,500 sessions for stocks). At a fixed 4-session hold it is −₹16.5 k on TradeBot indices.
5. **No evidence (or negative):** daily Supertrend ST(10,3) (long history +₹0.9 k, 2021–26 −₹4.2 k), 5-day trend,
   ER-gated trend, spot 30-min MACD sign, the premium-MACD state at fixed times (negative at D+3 on 2025–26: TradeBot
   indices −₹13.8 k, stocks −₹4.9 k), the index-lane ridge model (champion analogue, −₹0.7 k over 2,152 sessions), the
   live champion (+₹41 k on 39 live trades but −₹29.9 k beyond its own PE tilt), and the VIX-median rule over the long
   history (+₹1.2 k).
6. **September 2026 is the user's example, and the slow selectors did catch it.** In the live window (182 of 207
   underlying-months PE-dominant) 20-day and series trend were on the dominant side 76–81% of days, locked on within
   1 day, and captured 44–53% of the "hold the dominant side all month" prize; daily ST captured 35%. Side value at D+3:
   T20 +₹111.7 k on indices [+0.7, +223.0], SER +₹49.7 k [+17.7, +72.3], daily ST +₹15.6 k on stocks [+1.6, +31.3].
   **But beyond their own average PE tilt they add ~0** (SER stocks +₹0.3 k, DST stocks +₹1.4 k), i.e. one falling
   month cannot separate a regime-follower from "always PE" (+₹44–120 k). Only multi-regime samples can.
7. **The asymmetry that matters is regime asymmetry.** Over 2016–26 the 20-day trend captures +32% (+29% in 2021–26) of the prize in
   CE-favouring months (locked from day 1) and −12 to −16% in PE-favouring months (median lock day 5–7): falls are sharp
   and rebound fast, so they are late. (Figures for T20; DST is +18% / −16%.) SER is the most balanced (+21% / +5% simulated 2016–26; +21% / +12% 2021–26). On
   2025–26 real premiums (8 CE / 8 PE NIFTY/SENSEX months, choppy) DST, T20, SER and ERG lost at D+3 on all three real
   panels.
8. **Multiple testing.** 3,160 option side-value tests: 437 nominal p < 0.05 (305 positive, 132 negative; 158 expected),
   17 BH passes, all in the 30-session live window (regime effects, plus one negative). Beyond-tilt tests: 0 BH passes.
   992 spot-skill tests: 105 nominal, 18 BH passes (index 15-min ST flip to the close 2021–26; index 15-min flip to D+3
   on 2025–26; the rest live). Headline classification is power-aware (section 6), not BH.

## 1. Data

| panel | what | underlyings | sessions (decision days) | exits available | premiums |
|---|---|---|---|---|---|
| `ix` | TradeBot `index_analytics_data` 1-min NIFTY (weekly+monthly to 28 Aug 2025) and SENSEX (to 26 Feb 2026) | 2 | 214 (21 Apr 2025 – 26 Feb 2026) | +30 min, close, D+1, D+3, lane | real |
| `tbI` | TradeBot `option_candles` 30-min, index underlyings (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50), monthly | 5 | 208 (2 May 2025 – 27 Feb 2026) | same (+30 min = next bar) | real |
| `tbS` | same, stock options | 206 (205 with option bars) | 204 | same | real |
| `live` (`liveI`, `liveS`) | project `historical.sqlite3` 1-min spot + every captured option contract | 214 (4 indices: NIFTY, BANKNIFTY, SENSEX, MIDCPNIFTY; 210 stocks, 208 with option bars) | 30 (20 Aug – 1 Oct 2026); 27 with D+3 | same | real |
| `fy` | Fyers 5-min index spot 2021–26 + India VIX; Black-Scholes/VIX simulated premiums (`index_study` calibration) | 4 (NIFTY, BANKNIFTY, SENSEX from May 2023, MIDCPNIFTY from Jul 2022) | 1,392 (1 Feb 2021 – 1 Oct 2026) | **D+3 only** (multi-day; the simulator's calendar-time theta is not valid intraday) | simulated |
| `lsim` | `index_study` 14:45 panel + validated simulated labels y = clip(mean of D+1..D+3 close returns) | 4 | 2,645 (Jan 2016 – Sep 2026) | 3-session label | simulated |

The spot-only directional-skill panels use the same rows (spot moves need no option pricing): Fyers 5-min
2021–26 (+30 min, close, D+1, D+3), the 14:45 index panel 2016–26 (D+1, D+3), TradeBot 30-min spot 2025–26 and live
1-min spot.

Daily-level features (5/20-day returns, series return, efficiency ratio, daily Supertrend, floor pivots, |1-day
move| percentile) come from Fyers daily bars (2015 →) for NIFTY, BANKNIFTY, SENSEX and MIDCPNIFTY, and from each
dataset's own spot bars aggregated to days for everything else. Live stocks therefore have only ~13 sessions of
history before 20 Aug: 20-day trend starts 31 Aug, the stress rule (needs 60 days) is unavailable for live stocks.
India VIX (Fyers 5-min and daily) is the market-wide VIX for every underlying.

## 2. Method

### 2.1 Decision sets
- **Common fixed grid** (removes timing selection): every underlying, every session, at **10:15, 12:15 and 14:45**
  (decision on bars that ended by then; entry at the open of the bar that starts then). Reported pooled ("grid") and per
  time (`g60`, `g180`, `g330` in `cells.csv`).
- **Each method's own decision times:**
  - `ev_macd` — the MACD lane: 30-min premium MACD(12,26) first intrabar zero-cross of the day's universe ATM contract,
    re-checked 5 minutes later (live and `ix`); closed-bar cross on 30-min TradeBot bars. Side = the crossing contract's
    side (CE or PE). `systems.macd_lane_specs`, unchanged.
  - `ev_st5`, `ev_st15`, `ev_st30` — spot Supertrend flips: ST(10,3) on 5-min, ST(14,2) on 15-min, ST(10,3) on 30-min
    bars, 09:20–14:45; side = new direction.
  - `ev_piv` — first 5-min close (30-min on TradeBot) after 09:30 above TC → CE, below BC → PE (one per side per day).
- At every decision **all** method states are recorded, so combinations ("event side and daily ST agree") are free.

### 2.2 Paired legs and exits
- **Contract:** the strike nearest spot that both the CE and the PE of the same expiry printed in the last 15 minutes
  (1-min data) / the decision bar (30-min), |K/S − 1| ≤ 3%; nearest expiry after the last day of the hold (or the one
  after). Black-Scholes panel: the index step strike nearest spot.
- **Exits** (identical for both legs):
  - `h30` exit 30 min later (30-min data: the next bar);
  - `cls` 15:15 the same day (a 14:45 decision on 30-min data exits at the 15:15 bar's open);
  - `d1` 15:15 next session; `d3` 15:15 of D+3 (4-session hold counting the entry day);
  - `lane` the d3 contract and cap plus the smart-lane overlay: 40% hard stop, trail 30% off the peak once +40%
    (breakeven floor), +100% target;
  - `y3` (`lsim` only) the index-study label (mean of the D+1, D+2, D+3 close returns, clipped to [−1, +2]).
- **Fills:** `spc` level model (open of the bar that starts at the decision; stops/targets at the level crossed inside a
  bar, at the open if gapped; +5 bps buy, −5 bps sell).
- **Costs and units:** each leg is a ₹1 L notional ticket (no lot rounding, no liquidity cap, so both legs are
  comparable). Charges per order: brokerage ₹20, STT 0.15% of sell premium, exchange 0.03503% NSE / 0.0325% BSE, SEBI
  ₹10/cr, stamp 0.003% on buys, GST 18% on brokerage + exchange + SEBI (≈ ₹250–280 per round trip). "k" in tables =
  ₹ thousand per ₹1 L ticket; +₹10 k = +10% of premium.

### 2.3 Methods (side selectors) and benchmarks

| code | rule (state at the decision; completed bars / sessions only) |
|---|---|
| ST5 / ST15 / ST30 | spot Supertrend direction: ST(10,3) 5-min / ST(14,2) 15-min / ST(10,3) 30-min |
| DST | daily Supertrend(10,3) direction of sessions before D |
| PIVTC / PIVP | floor pivots from D−1: spot > TC → CE, < BC → PE, else none / spot > P → CE else PE |
| T5 / T20 | sign of spot ÷ close 5 / 20 sessions ago − 1 |
| SER | sign of spot ÷ close on the last expiry day before D − 1 (series-to-date) |
| ERG | 10-session efficiency ratio ≥ 0.35 → follow the 10-session direction, else none |
| VIX2 | India VIX above its trailing-250-session median → CE, else PE |
| VIXHI | VIX percentile ≥ 2/3 → CE, else none (the "high VIX → CE" tilt) |
| STRESS | \|spot ÷ previous close − 1\| ≥ 70th percentile of the trailing 250 \|daily returns\| → CE, else none |
| MACDspot | sign of the spot 30-min MACD(12,26) line |
| MACDx / MACDr | premium 30-min MACD line ÷ premium of the ATM CE and PE: the only positive one / the higher one |
| MODEL / MODELID | index-lane walk-forward ridge (`index_study` `ridge_live_l0.07` = live champion analogue; `ridge_live_id_l0.07` + intraday features), 14:45 only, indices |
| CHAMP | live champion `index-only-l100` picks (`agent_index_trades`, latest run per day), live indices, 14:45 |
| A&B | combination: side when both agree, else none (DST&ST15, DST&ST30, DST&MACDr, T5&DST, DST&PIVP, T20&DST; EV&DST, EV&T20, EV&T5 on events) |
| ALLCE / ALLPE | benchmarks; coin flip = 0 by construction; oracle = mean \|CE − PE\| (reported as "capture") |

### 2.4 Statistics
- Per cell (panel × decision set × exit × method): n, sessions, coverage, CE share, mean side value, median, hit rate,
  average gain when right / loss when wrong, payoff ratio, skew, top-5% and top-10% trades' share of the total,
  capture of the oracle, chosen-leg absolute P&L, both legs' means on the same rows.
- **CI:** moving-block bootstrap over sessions (2,000 draws; block 1 session for +30 min / close, 2 for D+1, 5 for
  D+3 / lane / y3 because consecutive multi-day holds overlap). Per-trade mean = Σ over resampled sessions ÷ count.
- **Power:** SE from the bootstrap; sessions needed for 80% power at two-sided 5% = N × (2.80 × SE / |mean|)². A
  skew-aware check resamples blocks at that size (400 draws) and counts how often the mean clears 1.96 SE: 0.78–0.83
  for every cell checked, so the normal approximation is adequate.
- **Beyond own tilt:** mean side value − (2·CE share − 1) × mean(CE − PE over all decisions of the set), with a joint
  block bootstrap. This removes what any constant side with the same CE/PE mix would have earned in that sample; for a
  CE-only rule it is the lift over always-CE. In a one-regime sample (September 2026) it is the only way to separate a
  side signal from "it was mostly PE".
- **Month types:** each underlying-month is CE- or PE-favouring by the sign of Σ (CE − PE) over its grid decisions
  (hindsight, for the breakdown only). Every cell reports its mean in each.
- **Classification:** CI above 0 → *positive & significant*; CI below 0 → *negative*; mean > ₹500 per ₹1 L (0.5% of
  premium) with CI spanning 0 → *positive, not yet proven* (with sessions needed); mean ≤ ₹500 → *no evidence*.
- BH-FDR (5%) over all 3,160 non-benchmark option tests, the 3,160 beyond-tilt tests and the 992 spot tests, as
  context.

### 2.5 Validation of the simulated side value (`valid.py`)
864 matched decisions (NIFTY/SENSEX, same date and grid time), 4-session hold, Black-Scholes/VIX (`fy`) vs real 1-min
premiums (`ix`): CE corr 0.966, PE corr 0.985, **CE−PE corr 0.982, sign agreement 97.1%**, straddle corr 0.956;
CE−PE mean −5.8% simulated vs −11.8% real, i.e. **the simulator overstates CE against PE by about 6 pp per
4-session hold** (skew dynamics), constant across grid times (+5.0, +6.2, +6.7 pp). The index study found +2.8 pp on its
3-day mean label. Simulated side values of CE-leaning rules are therefore flattered by about (2·CE share − 1) × ₹6 k
(`fy`) / × ₹2.8 k (`lsim`); the beyond-tilt figures are immune.

## 3. Headline cells

Selected cells (full set of 3,704 in `res/cells.csv`). "k" = ₹ thousand per ₹1 L ticket per trade. CI = 95%
moving-block bootstrap over sessions. Capture = Σ side value ÷ Σ |CE − PE|. CE-month / PE-month = mean side value in
underlying-months where CE / PE won overall. Sessions needed = for 80% power at the observed mean and variance.

| method | set | panel | exit | n (sessions) | CE share | side value mean [95% CI] | hit | payoff | capture of oracle | CE-month / PE-month mean | beyond own tilt [CI] | sessions needed (80%) | class |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| EV | ev_st15 | ix | h30 | 286 (143) | 0.50 | +5.7k [+1.0k, +10.9k] | 0.577 | 1.11 | +0.205 | +5.2k / +6.7k | +5.7k [+0.7k, +10.7k] | 221 | positive & significant |
| EV | ev_st15 | ix | cls | 286 (143) | 0.50 | +8.2k [-1.3k, +18.9k] | 0.542 | 1.10 | +0.133 | +4.5k / +10.5k | +8.2k [-1.5k, +18.7k] | 442 | positive, not yet proven |
| EV | ev_st15 | ix | d1 | 285 (142) | 0.50 | +15.3k [+4.7k, +26.4k] | 0.568 | 1.05 | +0.160 | +20.8k / +9.7k | +15.3k [+4.6k, +27.1k] | 150 | positive & significant |
| EV | ev_st15 | ix | d3 | 281 (141) | 0.50 | +21.1k [+10.9k, +34.2k] | 0.584 | 1.00 | +0.170 | +21.3k / +21.0k | +21.1k [+11.3k, +34.0k] | 86 | positive & significant |
| EV | ev_st15 | ix | lane | 281 (141) | 0.50 | +10.5k [+3.7k, +19.8k] | 0.544 | 1.00 | +0.134 | +14.4k / +7.3k | +10.5k [+3.6k, +19.5k] | 174 | positive & significant |
| EV | ev_st15 | liveI | h30 | 113 (29) | 0.48 | +6.7k [+1.1k, +12.9k] | 0.575 | 1.61 | +0.379 | +10.3k / +5.1k | +6.5k [+0.6k, +12.7k] | 47 | positive & significant |
| EV | ev_st15 | liveI | cls | 113 (29) | 0.48 | +5.0k [-9.0k, +24.0k] | 0.496 | 1.27 | +0.112 | - / +6.6k | +4.3k [-9.0k, +25.1k] | 716 | positive, not yet proven |
| EV | ev_st15 | liveI | d3 | 77 (26) | 0.49 | -0.5k [-18.1k, +10.5k] | 0.468 | 1.13 | -0.004 | - / +0.1k | -2.1k [-20.0k, +10.1k] | 36,262 | no evidence |
| EV | ev_st15 | liveS | cls | 5,818 (30) | 0.50 | +2.6k [-0.4k, +5.9k] | 0.511 | 1.12 | +0.078 | +2.5k / +2.6k | +2.7k [-0.3k, +6.1k] | 88 | positive, not yet proven |
| EV | ev_st15 | liveS | lane | 4,986 (27) | 0.50 | +2.7k [-1.2k, +7.1k] | 0.512 | 0.98 | +0.037 | +2.5k / +2.7k | +2.7k [-1.1k, +7.3k] | 130 | positive, not yet proven |
| EV | ev_st15 | fy | d3 | 4,625 (1229) | 0.50 | +4.2k [+0.0k, +8.0k] | 0.518 | 0.99 | +0.031 | +6.3k / +2.0k | +4.2k [-0.0k, +7.9k] | 2,282 | positive & significant |
| EV | ev_st5 | ix | h30 | 513 (187) | 0.49 | +1.7k [-1.9k, +5.1k] | 0.499 | 1.14 | +0.063 | -1.5k / +6.7k | +1.7k [-1.6k, +5.1k] | 1,577 | positive, not yet proven |
| EV | ev_st5 | ix | cls | 513 (187) | 0.49 | +3.4k [-2.0k, +9.2k] | 0.503 | 1.11 | +0.057 | +2.6k / +3.8k | +3.3k [-2.0k, +9.3k] | 1,097 | positive, not yet proven |
| EV | ev_st5 | ix | d3 | 497 (183) | 0.49 | +10.6k [+2.9k, +19.9k] | 0.519 | 1.11 | +0.089 | +7.2k / +13.4k | +10.5k [+3.2k, +20.8k] | 239 | positive & significant |
| EV | ev_st5 | liveI | h30 | 163 (30) | 0.49 | +4.9k [+0.1k, +9.6k] | 0.589 | 1.35 | +0.324 | +5.6k / +4.7k | +4.9k [-0.2k, +9.2k] | 56 | positive & significant |
| EV | ev_st5 | liveS | h30 | 9,332 (30) | 0.51 | +1.3k [-0.2k, +2.5k] | 0.527 | 1.05 | +0.083 | +1.5k / +1.2k | +1.3k [-0.1k, +2.6k] | 69 | positive, not yet proven |
| EV | ev_st5 | fy | d3 | 7,755 (1357) | 0.50 | +2.0k [-1.0k, +4.6k] | 0.505 | 1.01 | +0.014 | +3.1k / +0.8k | +2.0k [-0.8k, +4.5k] | 5,439 | positive, not yet proven |
| EV | ev_st30 | ix | h30 | 114 (79) | 0.49 | +9.3k [+3.0k, +15.5k] | 0.588 | 1.65 | +0.403 | +10.1k / +8.0k | +9.4k [+2.8k, +15.6k] | 74 | positive & significant |
| EV | ev_st30 | tbI | h30 | 256 (121) | 0.50 | +2.9k [+0.1k, +5.8k] | 0.547 | 1.29 | +0.219 | +3.8k / +1.8k | +2.9k [+0.2k, +6.1k] | 238 | positive & significant |
| EV | ev_st30 | tbS | h30 | 7,429 (204) | 0.50 | +0.2k [-0.5k, +0.9k] | 0.495 | 1.05 | +0.016 | +0.3k / +0.1k | +0.2k [-0.5k, +0.9k] | 3,988 | no evidence (positive but < Rs 500) |
| EV | ev_st30 | liveS | h30 | 1,950 (30) | 0.48 | -1.0k [-2.0k, -0.0k] | 0.467 | 0.99 | -0.069 | -1.1k / -0.9k | -1.0k [-2.0k, +0.1k] | 69 | negative |
| EV | ev_st30 | fy | d3 | 1,647 (801) | 0.50 | -0.6k [-9.9k, +7.4k] | 0.503 | 0.98 | -0.005 | +2.0k / -3.2k | -0.6k [-9.4k, +8.2k] | >99,999 | no evidence |
| EV | ev_macd | ix | cls | 151 (94) | 0.48 | -4.6k [-18.2k, +9.3k] | 0.457 | 0.99 | -0.090 | -0.1k / -7.2k | -4.9k [-18.7k, +8.0k] | 1,716 | no evidence |
| EV | ev_macd | ix | d3 | 153 (96) | 0.48 | +6.2k [-32.2k, +50.5k] | 0.542 | 0.93 | +0.048 | +23.2k / -7.4k | +5.6k [-31.8k, +47.7k] | 8,782 | positive, not yet proven |
| EV | ev_macd | ix | lane | 153 (96) | 0.48 | +11.2k [-4.3k, +30.5k] | 0.542 | 1.02 | +0.139 | +9.8k / +12.3k | +10.7k [-4.9k, +29.7k] | 475 | positive, not yet proven |
| EV | ev_macd | tbI | cls | 183 (96) | 0.54 | +5.6k [-0.2k, +11.0k] | 0.612 | 1.01 | +0.229 | +11.0k / +3.2k | +6.1k [+0.6k, +11.4k] | 184 | positive, not yet proven |
| EV | ev_macd | tbI | d1 | 190 (100) | 0.54 | +15.2k [+1.6k, +29.7k] | 0.637 | 1.00 | +0.275 | +9.9k / +20.0k | +16.4k [+2.9k, +29.9k] | 165 | positive & significant |
| EV | ev_macd | tbI | d3 | 190 (100) | 0.54 | -16.5k [-38.7k, +3.9k] | 0.489 | 0.73 | -0.175 | +0.5k / -37.1k | -15.1k [-36.0k, +3.5k] | 354 | no evidence |
| EV | ev_macd | tbS | cls | 5,993 (200) | 0.49 | +1.7k [-0.2k, +3.6k] | 0.499 | 1.14 | +0.066 | +1.9k / +1.7k | +1.7k [-0.2k, +3.5k] | 496 | positive, not yet proven |
| EV | ev_macd | tbS | d3 | 6,474 (200) | 0.49 | +0.5k [-6.6k, +8.4k] | 0.497 | 1.02 | +0.006 | -1.0k / +1.9k | +0.5k [-6.7k, +8.4k] | >99,999 | no evidence (positive but < Rs 500) |
| EV | ev_macd | tbS | lane | 6,474 (200) | 0.49 | +1.9k [-2.9k, +6.7k] | 0.499 | 1.03 | +0.029 | -0.3k / +4.3k | +2.0k [-2.6k, +7.0k] | 2,480 | positive, not yet proven |
| EV | ev_macd | liveI | cls | 28 (15) | 0.57 | +2.0k [-28.5k, +40.0k] | 0.357 | 1.96 | +0.043 | - / +2.0k | +7.3k [-24.7k, +47.2k] | 10,255 | positive, not yet proven |
| EV | ev_macd | liveS | cls | 2,186 (27) | 0.44 | +6.1k [-1.5k, +14.6k] | 0.504 | 1.40 | +0.173 | +3.9k / +6.6k | +4.8k [-2.0k, +12.5k] | 97 | positive, not yet proven |
| EV | ev_macd | liveS | d3 | 1,980 (24) | 0.45 | +2.0k [-16.9k, +8.4k] | 0.558 | 0.82 | +0.019 | -4.0k / +3.2k | -4.2k [-24.8k, +5.1k] | 1,982 | positive, not yet proven |
| EV | ev_macd | liveS | lane | 1,980 (24) | 0.45 | +11.6k [+1.3k, +16.5k] | 0.567 | 1.00 | +0.154 | +10.5k / +11.9k | +8.2k [-2.9k, +13.6k] | 21 | positive & significant |
| EV | ev_piv | ix | h30 | 406 (215) | 0.51 | +1.2k [-2.8k, +5.5k] | 0.530 | 0.96 | +0.041 | +0.5k / +2.2k | +1.2k [-3.1k, +5.2k] | 5,525 | positive, not yet proven |
| EV | ev_piv | tbI | h30 | 908 (208) | 0.50 | +0.7k [-1.0k, +2.4k] | 0.517 | 1.03 | +0.049 | +0.3k / +1.1k | +0.7k [-1.0k, +2.4k] | 2,839 | positive, not yet proven |
| EV | ev_piv | tbS | h30 | 30,528 (204) | 0.49 | +0.7k [+0.1k, +1.2k] | 0.514 | 1.04 | +0.048 | +0.7k / +0.7k | +0.7k [+0.1k, +1.2k] | 286 | positive & significant |
| EV | ev_piv | liveI | h30 | 124 (30) | 0.41 | +8.4k [+3.7k, +13.9k] | 0.677 | 1.52 | +0.522 | +9.4k / +8.1k | +7.9k [+3.8k, +12.8k] | 23 | positive & significant |
| EV | ev_piv | liveS | h30 | 7,214 (30) | 0.42 | +0.5k [-0.7k, +1.6k] | 0.504 | 1.04 | +0.028 | +0.9k / +0.3k | +0.4k [-0.7k, +1.6k] | 333 | no evidence (positive but < Rs 500) |
| EV | ev_piv | fy | d3 | 6,184 (1393) | 0.54 | +2.6k [-3.1k, +8.1k] | 0.509 | 1.00 | +0.019 | +7.4k / -2.6k | +2.6k [-3.2k, +8.2k] | 13,327 | positive, not yet proven |
| PIVP | grid | ix | h30 | 900 (214) | 0.49 | +2.6k [+0.2k, +4.8k] | 0.547 | 1.05 | +0.120 | +3.2k / +1.6k | +2.6k [+0.3k, +5.0k] | 361 | positive & significant |
| PIVP | grid | tbI | h30 | 2,176 (208) | 0.50 | +1.3k [+0.2k, +2.4k] | 0.560 | 0.97 | +0.107 | +1.4k / +1.1k | +1.3k [+0.2k, +2.4k] | 327 | positive & significant |
| PIVP | grid | tbS | h30 | 69,863 (204) | 0.48 | +0.4k [+0.1k, +0.8k] | 0.510 | 1.03 | +0.036 | +0.3k / +0.5k | +0.4k [+0.1k, +0.8k] | 267 | positive & significant |
| PIVP | grid | liveI | h30 | 300 (30) | 0.38 | -2.9k [-5.4k, -0.4k] | 0.397 | 0.96 | -0.227 | -1.7k / -3.3k | -3.2k [-5.5k, -1.0k] | 46 | negative |
| PIVP | grid | liveS | h30 | 16,509 (30) | 0.40 | -0.1k [-1.0k, +0.8k] | 0.487 | 1.03 | -0.011 | -0.5k / +0.0k | -0.4k [-1.0k, +0.4k] | 2,629 | no evidence |
| PIVP | grid | ix | cls | 900 (214) | 0.49 | +2.2k [-4.3k, +8.6k] | 0.538 | 0.95 | +0.048 | +2.2k / +2.2k | +2.1k [-4.5k, +8.4k] | 3,915 | positive, not yet proven |
| PIVP | grid | tbS | cls | 69,863 (204) | 0.48 | +0.9k [-0.1k, +2.0k] | 0.508 | 1.04 | +0.039 | +0.4k / +1.3k | +0.9k [-0.1k, +2.0k] | 550 | positive, not yet proven |
| ST5 | grid | ix | cls | 900 (214) | 0.49 | +3.9k [-1.7k, +9.7k] | 0.559 | 0.94 | +0.087 | +7.4k / +1.8k | +3.9k [-2.0k, +9.8k] | 934 | positive, not yet proven |
| ST5 | grid | liveS | cls | 16,509 (30) | 0.48 | +1.7k [-0.7k, +4.7k] | 0.495 | 1.16 | +0.066 | +0.8k / +1.9k | +1.5k [-0.9k, +4.0k] | 153 | positive, not yet proven |
| ST5 | grid | fy | d3 | 13,937 (1392) | 0.53 | +2.8k [-2.8k, +7.8k] | 0.514 | 0.98 | +0.020 | +6.6k / -1.3k | +2.7k [-2.5k, +7.6k] | 10,119 | positive, not yet proven |
| ST15 | grid | ix | cls | 900 (214) | 0.48 | +2.1k [-3.8k, +8.2k] | 0.543 | 0.92 | +0.047 | +1.1k / +2.7k | +2.1k [-4.0k, +8.0k] | 3,393 | positive, not yet proven |
| ST15 | grid | ix | lane | 867 (211) | 0.48 | +10.3k [+1.2k, +19.0k] | 0.534 | 1.01 | +0.132 | +12.2k / +8.8k | +9.9k [+1.1k, +18.4k] | 321 | positive & significant |
| ST15 | grid | liveS | lane | 14,566 (27) | 0.45 | +4.9k [+1.3k, +6.6k] | 0.523 | 1.00 | +0.068 | +3.1k / +5.3k | +2.4k [-1.1k, +4.9k] | 16 | positive & significant |
| ST15 | grid | fy | d3 | 13,935 (1392) | 0.55 | +3.2k [-3.2k, +9.0k] | 0.514 | 0.99 | +0.023 | +10.6k / -5.0k | +3.0k [-3.0k, +8.8k] | 10,403 | positive, not yet proven |
| DST | grid | ix | d3 | 867 (211) | 0.73 | -22.4k [-55.3k, +10.8k] | 0.474 | 0.77 | -0.183 | +18.0k / -53.6k | -17.5k [-47.3k, +10.7k] | 893 | no evidence |
| DST | grid | tbI | d3 | 2,043 (208) | 0.70 | -8.3k [-23.2k, +9.2k] | 0.455 | 0.98 | -0.099 | +2.9k / -23.1k | -7.1k [-24.2k, +12.8k] | 1,711 | no evidence |
| DST | grid | tbS | d3 | 68,312 (201) | 0.51 | -2.1k [-6.7k, +2.5k] | 0.497 | 0.96 | -0.026 | -0.7k / -3.5k | -2.1k [-6.7k, +2.4k] | 1,842 | no evidence |
| DST | grid | liveI | d3 | 222 (27) | 0.44 | +76.5k [-41.9k, +208.4k] | 0.514 | 3.08 | +0.530 | - / +77.8k | +62.5k [-50.6k, +178.6k] | 179 | positive, not yet proven |
| DST | grid | liveS | d3 | 14,566 (27) | 0.34 | +15.6k [+1.6k, +31.3k] | 0.562 | 1.07 | +0.158 | -17.2k / +22.6k | +1.4k [-6.8k, +8.2k] | 52 | positive & significant |
| DST | grid | liveS | cls | 16,509 (30) | 0.33 | +2.7k [+0.5k, +5.3k] | 0.534 | 1.07 | +0.105 | -1.6k / +3.8k | +0.5k [-0.3k, +1.4k] | 48 | positive & significant |
| DST | grid | fy | d3 | 13,910 (1392) | 0.57 | -4.2k [-16.6k, +7.5k] | 0.491 | 0.98 | -0.031 | +3.5k / -12.5k | -4.4k [-16.7k, +7.5k] | 24,648 | no evidence |
| DST | grid | lsim | y3 | 7,034 (2645) | 0.60 | +0.9k [-5.2k, +6.7k] | 0.513 | 0.97 | +0.010 | +6.2k / -5.7k | +0.2k [-6.2k, +5.9k] | >99,999 | positive, not yet proven |
| T20 | grid | ix | d3 | 867 (211) | 0.70 | -9.6k [-37.6k, +19.5k] | 0.510 | 0.82 | -0.079 | +17.2k / -30.4k | -5.4k [-31.8k, +21.7k] | 3,682 | no evidence |
| T20 | grid | tbI | d3 | 2,043 (208) | 0.66 | -10.0k [-25.0k, +7.9k] | 0.464 | 0.91 | -0.120 | +6.1k / -31.3k | -9.0k [-24.9k, +9.8k] | 1,132 | no evidence |
| T20 | grid | tbS | d3 | 68,255 (201) | 0.51 | -4.6k [-9.5k, +0.1k] | 0.493 | 0.92 | -0.057 | -2.3k / -6.9k | -4.6k [-9.4k, +0.0k] | 416 | no evidence |
| T20 | grid | liveI | d3 | 222 (27) | 0.18 | +111.7k [+0.7k, +223.0k] | 0.689 | 3.52 | +0.773 | - / +114.8k | +35.9k [-17.6k, +80.3k] | 63 | positive & significant |
| T20 | grid | liveS | d3 | 11,067 (20) | 0.25 | +24.4k [-2.9k, +49.0k] | 0.595 | 1.07 | +0.223 | -18.9k / +31.2k | +2.3k [-13.1k, +13.0k] | 47 | positive, not yet proven |
| T20 | grid | fy | d3 | 13,880 (1392) | 0.61 | +1.8k [-10.4k, +13.0k] | 0.514 | 0.97 | +0.013 | +13.7k / -11.1k | +1.4k [-11.0k, +13.2k] | >99,999 | positive, not yet proven |
| T20 | grid | lsim | y3 | 7,034 (2645) | 0.62 | +4.3k [-1.7k, +10.4k] | 0.527 | 0.98 | +0.046 | +11.1k / -4.2k | +3.4k [-2.7k, +8.8k] | 10,390 | positive, not yet proven |
| SER | grid | ix | d3 | 867 (211) | 0.49 | -8.4k [-29.6k, +12.8k] | 0.487 | 0.92 | -0.069 | -9.7k / -7.4k | -8.7k [-31.0k, +12.4k] | 2,893 | no evidence |
| SER | grid | tbI | d3 | 2,043 (208) | 0.59 | -3.3k [-17.3k, +11.1k] | 0.511 | 0.89 | -0.039 | +6.3k / -15.8k | -2.7k [-16.6k, +11.6k] | 8,014 | no evidence |
| SER | grid | tbS | d3 | 59,304 (201) | 0.58 | -2.8k [-6.6k, +0.9k] | 0.506 | 0.91 | -0.034 | +6.5k / -11.6k | -2.8k [-6.3k, +0.2k] | 704 | no evidence |
| SER | grid | liveI | d3 | 222 (27) | 0.32 | +49.7k [+17.7k, +72.3k] | 0.680 | 0.96 | +0.344 | - / +50.3k | +6.4k [-24.3k, +32.1k] | 17 | positive & significant |
| SER | grid | liveS | d3 | 8,075 (23) | 0.19 | +27.6k [-3.3k, +52.5k] | 0.604 | 1.11 | +0.257 | -28.1k / +37.0k | +0.3k [-12.8k, +5.7k] | 48 | positive, not yet proven |
| SER | grid | fy | d3 | 13,919 (1392) | 0.57 | +9.1k [+0.2k, +17.3k] | 0.540 | 0.97 | +0.067 | +9.7k / +8.5k | +8.9k [+0.4k, +17.4k] | 2,531 | positive & significant |
| SER | grid | lsim | y3 | 7,027 (2645) | 0.57 | +4.8k [-0.0k, +9.6k] | 0.529 | 0.99 | +0.051 | +7.2k / +1.7k | +4.2k [-0.4k, +8.9k] | 5,315 | positive, not yet proven |
| T5 | grid | ix | d3 | 867 (211) | 0.52 | +0.8k [-21.3k, +24.2k] | 0.516 | 0.95 | +0.007 | -8.4k / +8.0k | +1.3k [-23.5k, +24.1k] | >99,999 | positive, not yet proven |
| T5 | grid | tbI | d3 | 2,043 (208) | 0.53 | -3.9k [-17.6k, +10.1k] | 0.506 | 0.89 | -0.046 | -0.3k / -8.6k | -3.7k [-17.6k, +8.6k] | 5,339 | no evidence |
| T5 | grid | tbS | d3 | 68,223 (201) | 0.49 | -4.0k [-8.4k, +0.1k] | 0.490 | 0.94 | -0.049 | -3.6k / -4.4k | -4.0k [-8.1k, -0.0k] | 458 | no evidence |
| T5 | grid | liveI | d3 | 222 (27) | 0.26 | -19.9k [-117.8k, +42.4k] | 0.518 | 0.71 | -0.138 | - / -19.9k | -78.4k [-221.4k, +8.8k] | 937 | no evidence |
| T5 | grid | fy | d3 | 13,925 (1392) | 0.56 | +4.3k [-6.2k, +14.4k] | 0.526 | 0.96 | +0.031 | +5.8k / +2.6k | +4.1k [-6.1k, +13.8k] | 16,271 | positive, not yet proven |
| T5 | grid | lsim | y3 | 7,034 (2645) | 0.56 | +1.2k [-4.0k, +6.4k] | 0.514 | 0.97 | +0.012 | +2.5k / -0.5k | +0.7k [-4.3k, +5.7k] | >99,999 | positive, not yet proven |
| VIX2 | grid | ix | d3 | 867 (211) | 0.31 | +23.2k [-7.7k, +54.2k] | 0.547 | 1.22 | +0.189 | -11.5k / +50.0k | +19.0k [-1.9k, +43.1k] | 764 | positive, not yet proven |
| VIX2 | grid | tbI | d3 | 2,043 (208) | 0.25 | +13.1k [-5.4k, +34.4k] | 0.518 | 1.28 | +0.157 | -10.4k / +44.2k | +11.6k [+0.6k, +27.2k] | 999 | positive, not yet proven |
| VIX2 | grid | tbS | d3 | 68,312 (201) | 0.16 | +5.9k [-5.5k, +18.0k] | 0.512 | 1.10 | +0.071 | -24.4k / +34.9k | +5.9k [-0.7k, +13.9k] | 1,696 | positive, not yet proven |
| VIX2 | grid | liveI | d3 | 222 (27) | 0.24 | +99.2k [+39.7k, +208.3k] | 0.685 | 2.48 | +0.686 | - / +101.9k | +37.4k [+24.4k, +90.8k] | 44 | positive & significant |
| VIX2 | grid | liveS | d3 | 14,566 (27) | 0.22 | +41.1k [+28.6k, +71.2k] | 0.629 | 1.43 | +0.415 | -10.6k / +52.0k | +16.2k [+11.8k, +39.4k] | 15 | positive & significant |
| VIX2 | grid | fy | d3 | 13,934 (1392) | 0.46 | +6.6k [-6.6k, +20.3k] | 0.509 | 1.06 | +0.048 | -0.8k / +14.6k | +6.7k [-7.1k, +19.9k] | 12,418 | positive, not yet proven |
| VIX2 | grid | lsim | y3 | 7,034 (2645) | 0.48 | +1.2k [-5.5k, +7.6k] | 0.500 | 1.03 | +0.013 | -2.6k / +6.0k | +1.4k [-5.1k, +8.0k] | >99,999 | positive, not yet proven |
| VIXHI | grid | ix | d3 | 210 (45) | 1.00 | +28.1k [-5.6k, +72.2k] | 0.638 | 1.07 | +0.308 | +33.7k / +18.7k | +38.9k [+2.1k, +92.4k] | 163 | positive, not yet proven |
| VIXHI | grid | tbI | d3 | 326 (41) | 1.00 | +41.7k [+25.0k, +75.9k] | 0.709 | 1.42 | +0.550 | +43.2k / +36.0k | +44.8k [+20.8k, +87.4k] | 34 | positive & significant |
| VIXHI | grid | tbS | d3 | 6,940 (36) | 1.00 | +29.9k [+7.2k, +46.7k] | 0.607 | 1.58 | +0.420 | +63.6k / -3.0k | +29.8k [+8.0k, +59.7k] | 33 | positive & significant |
| VIXHI | grid | fy | d3 | 4,314 (444) | 1.00 | +17.3k [-5.9k, +38.3k] | 0.566 | 1.00 | +0.131 | +65.9k / -36.2k | +15.7k [-3.9k, +35.9k] | 1,512 | positive, not yet proven |
| VIXHI | grid | lsim | y3 | 2,212 (800) | 1.00 | +6.1k [-5.7k, +17.3k] | 0.550 | 0.93 | +0.066 | +37.1k / -26.6k | +2.5k [-6.9k, +11.8k] | 5,827 | positive, not yet proven |
| STRESS | grid | ix | d3 | 130 (56) | 1.00 | +36.0k [-0.9k, +68.8k] | 0.677 | 1.02 | +0.365 | +65.1k / -3.8k | +46.8k [+14.1k, +79.1k] | 109 | positive, not yet proven |
| STRESS | grid | tbI | d3 | 281 (89) | 1.00 | +21.0k [-7.4k, +53.5k] | 0.651 | 0.91 | +0.259 | +53.6k / -20.2k | +24.1k [+5.5k, +47.1k] | 387 | positive, not yet proven |
| STRESS | grid | tbS | d3 | 10,290 (160) | 1.00 | +0.4k [-15.2k, +12.5k] | 0.512 | 0.96 | +0.004 | +41.9k / -42.0k | +0.3k [-7.0k, +6.5k] | >99,999 | no evidence (positive but < Rs 500) |
| STRESS | grid | liveI | d3 | 42 (11) | 1.00 | -93.0k [-161.8k, +33.5k] | 0.286 | 0.24 | -0.826 | - / -93.0k | +27.2k [+2.6k, +121.8k] | 33 | no evidence |
| STRESS | grid | fy | d3 | 2,955 (713) | 1.00 | +12.5k [-8.7k, +33.3k] | 0.566 | 0.92 | +0.091 | +70.2k / -40.0k | +10.8k [-2.9k, +24.8k] | 4,018 | positive, not yet proven |
| STRESS | grid | lsim | y3 | 2,003 (1081) | 1.00 | +7.7k [-1.8k, +17.9k] | 0.563 | 0.91 | +0.080 | +43.4k / -28.0k | +4.1k [-2.3k, +10.4k] | 3,662 | positive, not yet proven |
| MACDr | grid | ix | cls | 856 (211) | 0.51 | -3.7k [-10.3k, +2.7k] | 0.487 | 0.89 | -0.084 | -2.8k / -4.3k | -3.7k [-10.3k, +2.9k] | 1,345 | no evidence |
| MACDr | grid | ix | d3 | 824 (208) | 0.51 | -16.1k [-40.4k, +9.0k] | 0.456 | 0.92 | -0.129 | -5.2k / -24.5k | -15.8k [-40.0k, +8.2k] | 995 | no evidence |
| MACDr | grid | tbI | d3 | 1,855 (208) | 0.51 | -13.8k [-25.7k, -1.9k] | 0.464 | 0.83 | -0.162 | -6.5k / -23.4k | -13.7k [-26.9k, -2.8k] | 334 | negative |
| MACDr | grid | tbS | d3 | 61,815 (201) | 0.49 | -4.9k [-8.8k, -1.0k] | 0.489 | 0.93 | -0.057 | -4.5k / -5.2k | -4.9k [-8.8k, -1.0k] | 259 | negative |
| MACDr | grid | liveS | d3 | 12,188 (25) | 0.40 | -6.0k [-25.6k, +6.5k] | 0.520 | 0.82 | -0.057 | -19.8k / -3.3k | -14.9k [-38.0k, -3.3k] | 386 | no evidence |
| MODEL | grid | ix | d3 | 292 (210) | 0.42 | +5.5k [-13.6k, +26.5k] | 0.507 | 1.06 | +0.045 | -0.5k / +10.2k | +3.7k [-16.1k, +24.5k] | 6,109 | positive, not yet proven |
| MODEL | grid | tbI | d3 | 522 (207) | 0.43 | +3.6k [-10.0k, +18.7k] | 0.513 | 1.03 | +0.044 | +1.8k / +6.3k | +3.2k [-9.1k, +17.0k] | 6,047 | positive, not yet proven |
| MODEL | grid | liveI | d3 | 74 (26) | 0.53 | -37.4k [-131.6k, +61.9k] | 0.541 | 0.50 | -0.258 | - / -38.6k | -30.9k [-121.3k, +60.4k] | 405 | no evidence |
| MODEL | grid | fy | d3 | 4,581 (1392) | 0.48 | -0.1k [-8.8k, +9.4k] | 0.506 | 0.98 | -0.001 | +4.2k / -4.8k | -0.0k [-8.9k, +8.9k] | >99,999 | no evidence |
| MODEL | grid | lsim | y3 | 6,073 (2152) | 0.50 | -0.7k [-5.5k, +4.4k] | 0.510 | 0.95 | -0.007 | +0.8k / -2.6k | -0.7k [-5.6k, +4.7k] | >99,999 | no evidence |
| CHAMP | grid | liveI | cls | 50 (17) | 0.16 | -1.1k [-7.1k, +3.9k] | 0.480 | 0.87 | -0.107 | - / -1.1k | -6.8k [-16.9k, +2.7k] | 921 | no evidence |
| CHAMP | grid | liveI | d3 | 39 (14) | 0.21 | +41.0k [-37.4k, +87.1k] | 0.462 | 1.89 | +0.236 | - / +41.0k | -29.9k [-85.4k, +18.6k] | 66 | positive, not yet proven |
| CHAMP | grid | liveI | lane | 39 (14) | 0.21 | +20.4k [-13.5k, +39.5k] | 0.462 | 1.82 | +0.267 | - / +20.4k | -6.4k [-31.8k, +21.2k] | 49 | positive, not yet proven |
| DST&ST15 | grid | ix | d3 | 418 (148) | 0.72 | -25.7k [-60.0k, +9.8k] | 0.471 | 0.71 | -0.222 | +14.0k / -63.1k | -21.0k [-54.7k, +9.6k] | 556 | no evidence |
| DST&ST15 | grid | liveS | d3 | 7,444 (27) | 0.29 | +16.1k [+2.6k, +25.0k] | 0.585 | 0.99 | +0.168 | -23.4k / +24.2k | -2.3k [-11.2k, +3.9k] | 25 | positive & significant |
| DST&ST15 | grid | liveS | lane | 7,444 (27) | 0.29 | +11.2k [+2.4k, +15.9k] | 0.567 | 1.01 | +0.159 | -6.0k / +15.2k | +1.3k [-5.5k, +3.4k] | 21 | positive & significant |
| DST&ST15 | grid | fy | d3 | 6,961 (1279) | 0.61 | -1.0k [-15.7k, +13.1k] | 0.505 | 0.97 | -0.007 | +13.5k / -18.5k | -1.3k [-15.5k, +12.0k] | >99,999 | no evidence |
| EV&DST | ev_macd | tbS | cls | 2,922 (200) | 0.49 | +1.2k [-1.2k, +3.5k] | 0.489 | 1.14 | +0.046 | +1.5k / +1.0k | +1.2k [-1.2k, +3.5k] | 1,629 | positive, not yet proven |
| EV&DST | ev_macd | liveS | d3 | 1,025 (24) | 0.30 | +21.7k [+5.8k, +30.9k] | 0.624 | 0.92 | +0.210 | -27.8k / +29.4k | -1.3k [-16.4k, +10.3k] | 17 | positive & significant |

Activity of the CE-only stress rules (sessions with the rule on, by month): TradeBot 2025–26 `VIXHI` May 2025 21,
Jun 3, Jan 2026 3, Feb 7 (one episode dominates); NIFTY/SENSEX 1-min `VIXHI` Apr 2025 8, May 21, Jun 3, Jan 2026 3,
Feb 6; live `VIXHI` Sep 2026 3, Oct 1; live `STRESS` (indices) Aug 1, Sep 9, Oct 1. Fyers 2021–26: `VIXHI` active in
Feb–Jul 2022, Dec 2023 – Dec 2024, Jan–Feb and Apr–Jun 2025, Mar–Jun 2026 (444 sessions).

## 4. Side value tables

Panels: `ix` NIFTY/SENSEX 1-min real; `tbI` / `tbS` TradeBot 30-min real index / stock options; `liveI` / `liveS` live
Aug–Oct 2026 indices / stocks; `fy` Fyers 2021–26 simulated (D+3 only); `lsim` 2016–26 simulated 3-session label.
Grid = 10:15, 12:15, 14:45 pooled. Class codes: **S+** positive & significant, P positive not yet proven, 0 no
evidence, 0+ positive below ₹500, **N** negative. "BEYOND the method's own tilt" = side value minus what a constant
side with the same CE/PE mix earned on the same decisions (always-CE / always-PE rows are 0 there by construction).

### 4.1 Fixed grid (10:15, 12:15, 14:45 pooled)

#### Side value per trade, grid 10:15/12:15/14:45 pooled, exit `cls` (Rs per Rs 1 L ticket, mean [95% day-block CI] class)

| method | ix | tbI | tbS | liveI | liveS |
|---|---|---|---|---|---|
| ST5 (Spot ST(10,3) 5-min) | +3.9k [-1.7k, +9.7k] P | - | - | +3.0k [-6.2k, +13.4k] P | +1.7k [-0.7k, +4.7k] P |
| ST15 (Spot ST(14,2) 15-min) | +2.1k [-3.8k, +8.2k] P | - | - | -0.7k [-10.0k, +10.1k] 0 | +1.8k [-1.0k, +5.3k] P |
| ST30 (Spot ST(10,3) 30-min) | -3.6k [-10.4k, +3.1k] 0 | -0.0k [-3.6k, +3.5k] 0 | +0.3k [-0.8k, +1.5k] 0+ | +0.0k [-10.4k, +10.8k] 0+ | +0.2k [-2.5k, +3.2k] 0+ |
| DST (Daily ST(10,3)) | +0.2k [-6.9k, +7.2k] 0+ | +0.9k [-3.4k, +4.9k] P | -0.1k [-0.9k, +0.8k] 0 | +3.1k [-9.1k, +15.3k] P | +2.7k [+0.5k, +5.3k] **S+** |
| PIVTC (Pivot CPR (>TC CE, <BC PE)) | +1.5k [-5.8k, +8.3k] P | +2.7k [-1.7k, +7.0k] P | +1.0k [-0.2k, +2.3k] P | -3.8k [-15.5k, +9.2k] 0 | +1.8k [-1.9k, +6.5k] P |
| PIVP (Pivot P (>P CE)) | +2.2k [-4.3k, +8.6k] P | +2.4k [-1.5k, +6.5k] P | +0.9k [-0.1k, +2.0k] P | -4.3k [-15.0k, +7.1k] 0 | +1.4k [-1.8k, +5.2k] P |
| T5 (5-day return sign) | -5.5k [-12.8k, +1.7k] 0 | -0.7k [-4.4k, +3.1k] 0 | -0.3k [-1.5k, +0.9k] 0 | +6.6k [-5.3k, +18.0k] P | -0.1k [-3.1k, +2.8k] 0 |
| T20 (20-day return sign) | -4.5k [-11.7k, +2.3k] 0 | -0.6k [-4.8k, +3.5k] 0 | -0.2k [-1.1k, +0.6k] 0 | +6.6k [-5.3k, +18.6k] P | +4.5k [+0.9k, +8.9k] **S+** |
| SER (Series return sign) | -2.8k [-9.6k, +4.0k] 0 | -1.1k [-4.9k, +2.5k] 0 | -0.5k [-1.4k, +0.3k] 0 | +4.5k [-6.5k, +15.2k] P | +3.6k [-0.4k, +8.4k] P |
| ERG (ER-gated 10-day trend) | -4.6k [-15.6k, +6.7k] 0 | +0.8k [-6.3k, +8.9k] P | -1.0k [-2.7k, +1.0k] 0 | +4.7k [-16.3k, +23.1k] P | +2.0k [-2.0k, +6.8k] P |
| VIX2 (VIX above 1-y median -> CE else PE) | +2.2k [-4.8k, +9.6k] P | +1.9k [-2.8k, +6.2k] P | +2.0k [-0.3k, +4.3k] P | +5.2k [-6.7k, +17.7k] P | +0.7k [-4.9k, +6.7k] P |
| VIXHI (VIX top tercile -> CE) | -0.6k [-14.4k, +12.8k] 0 | -0.5k [-11.5k, +9.6k] 0 | +1.1k [-6.1k, +7.5k] P | -8.2k [-39.8k, +21.9k] 0 | -12.3k [-34.3k, +2.9k] 0 |
| STRESS (Big |1-day move| -> CE) | -7.6k [-26.3k, +10.9k] 0 | -10.8k [-19.1k, -2.1k] **N** | -1.4k [-4.3k, +1.3k] 0 | -17.8k [-43.8k, +21.8k] 0 | - |
| MACDspot (Spot 30-min MACD sign) | -3.0k [-9.8k, +3.7k] 0 | +0.4k [-3.3k, +4.0k] 0+ | +0.0k [-1.1k, +1.2k] 0+ | +3.8k [-7.7k, +14.7k] P | +0.0k [-3.1k, +3.6k] 0+ |
| MACDx (Premium MACD: only one side > 0) | -3.9k [-12.8k, +5.1k] 0 | -0.2k [-4.1k, +3.9k] 0 | -0.3k [-1.7k, +1.2k] 0 | +6.6k [-10.8k, +23.1k] P | -0.6k [-4.5k, +3.8k] 0 |
| MACDr (Premium MACD: higher side) | -3.7k [-10.3k, +2.7k] 0 | -0.6k [-4.4k, +3.1k] 0 | -0.2k [-1.3k, +1.0k] 0 | +4.1k [-8.7k, +17.5k] P | -0.0k [-3.5k, +4.2k] 0 |
| MODEL (Index-lane ridge (champion analogue)) | -1.5k [-5.3k, +2.4k] 0 | +0.6k [-1.2k, +2.6k] P | - | -0.4k [-3.8k, +3.2k] 0 | - |
| MODELID (Index-lane ridge + intraday) | -0.0k [-4.2k, +4.0k] 0 | +0.7k [-1.1k, +2.5k] P | - | +1.5k [-1.7k, +5.0k] P | - |
| CHAMP (Live champion picks) | - | - | - | -1.1k [-7.1k, +3.9k] 0 | - |
| DST&ST15 (Daily ST + 15-min ST agree) | +2.5k [-8.5k, +12.3k] P | - | - | +2.4k [-17.4k, +20.1k] P | +4.4k [-0.3k, +9.7k] P |
| DST&ST30 (Daily ST + 30-min ST agree) | -3.2k [-14.3k, +6.5k] 0 | +0.9k [-4.3k, +5.8k] P | +0.2k [-1.1k, +1.6k] 0+ | +2.9k [-16.0k, +20.8k] P | +2.7k [-1.4k, +7.5k] P |
| DST&MACDr (Daily ST + premium MACD agree) | -3.3k [-14.3k, +7.3k] 0 | +0.2k [-6.1k, +6.5k] 0+ | -0.3k [-1.6k, +1.1k] 0 | +7.6k [-14.6k, +29.1k] P | +2.7k [-1.9k, +7.7k] P |
| T5&DST (5-day trend + daily ST agree) | -4.7k [-14.7k, +5.2k] 0 | +0.2k [-5.3k, +5.9k] 0+ | -0.3k [-1.7k, +1.1k] 0 | +7.5k [-11.4k, +23.5k] P | +2.1k [-1.3k, +5.6k] P |
| DST&PIVP (Daily ST + pivot agree) | +2.5k [-8.9k, +12.9k] P | +3.4k [-2.9k, +9.8k] P | +0.9k [-0.5k, +2.3k] P | -1.1k [-23.0k, +18.7k] 0 | +3.8k [-1.0k, +9.3k] P |
| T20&DST (20-day trend + daily ST agree) | -2.6k [-10.4k, +5.3k] 0 | +0.2k [-4.7k, +4.9k] 0+ | -0.2k [-1.2k, +0.8k] 0 | +6.2k [-9.6k, +20.7k] P | +5.1k [+1.1k, +9.8k] **S+** |
| ALLCE (Always CE) | -2.0k [-8.9k, +5.3k] 0 | -3.1k [-7.8k, +1.2k] 0 | -1.7k [-4.1k, +0.6k] 0 | -8.5k [-20.2k, +3.4k] 0 | -6.1k [-12.6k, -1.0k] **N** |
| ALLPE (Always PE) | +2.0k [-5.3k, +8.9k] P | +3.1k [-1.2k, +7.8k] P | +1.7k [-0.6k, +4.1k] P | +8.5k [-3.4k, +20.2k] P | +6.1k [+1.0k, +12.6k] **S+** |

#### Side value per trade, grid 10:15/12:15/14:45 pooled, exit `cls` — BEYOND the method's own CE/PE tilt (Rs per Rs 1 L ticket, mean [95% day-block CI] class)

| method | ix | tbI | tbS | liveI | liveS |
|---|---|---|---|---|---|
| ST5 (Spot ST(10,3) 5-min) | +3.9k [-2.0k, +9.8k] P | - | - | +1.9k [-6.6k, +10.4k] P | +1.5k [-0.9k, +4.0k] P |
| ST15 (Spot ST(14,2) 15-min) | +2.1k [-4.0k, +8.0k] P | - | - | -2.0k [-10.4k, +6.6k] 0 | +1.1k [-1.2k, +3.7k] P |
| ST30 (Spot ST(10,3) 30-min) | -3.5k [-10.3k, +3.1k] 0 | +0.1k [-3.5k, +4.0k] 0+ | +0.3k [-0.8k, +1.5k] 0+ | -2.3k [-10.8k, +5.6k] 0 | -0.9k [-2.9k, +1.3k] 0 |
| DST (Daily ST(10,3)) | +1.1k [-5.6k, +8.0k] P | +2.1k [-2.0k, +6.6k] P | -0.0k [-0.9k, +0.8k] 0 | +0.7k [-9.3k, +10.7k] P | +0.5k [-0.3k, +1.4k] P |
| PIVTC (Pivot CPR (>TC CE, <BC PE)) | +1.4k [-6.1k, +8.5k] P | +2.7k [-1.7k, +7.6k] P | +1.0k [-0.2k, +2.2k] P | -6.0k [-16.6k, +4.2k] 0 | +0.4k [-2.3k, +3.6k] 0+ |
| PIVP (Pivot P (>P CE)) | +2.1k [-4.5k, +8.4k] P | +2.4k [-1.6k, +6.9k] P | +0.9k [-0.1k, +2.0k] P | -6.3k [-15.9k, +2.4k] 0 | +0.1k [-2.0k, +2.7k] 0+ |
| T5 (5-day return sign) | -5.4k [-12.6k, +1.6k] 0 | -0.5k [-4.6k, +3.7k] 0 | -0.3k [-1.5k, +1.0k] 0 | +1.3k [-4.2k, +6.7k] P | -2.1k [-5.5k, +0.6k] 0 |
| T20 (20-day return sign) | -3.8k [-10.9k, +3.5k] 0 | +0.4k [-3.6k, +4.8k] 0+ | -0.2k [-1.0k, +0.6k] 0 | +0.4k [-3.4k, +4.3k] 0+ | +1.2k [-0.3k, +3.2k] P |
| SER (Series return sign) | -2.9k [-9.9k, +3.8k] 0 | -0.6k [-4.2k, +3.2k] 0 | -0.3k [-1.1k, +0.6k] 0 | +1.0k [-5.7k, +7.5k] P | +0.1k [-1.8k, +2.3k] 0+ |
| ERG (ER-gated 10-day trend) | -4.0k [-15.9k, +8.0k] 0 | +1.5k [-6.3k, +9.8k] P | -1.0k [-2.7k, +0.9k] 0 | -3.8k [-18.2k, +8.0k] 0 | -2.1k [-4.6k, +0.6k] 0 |
| VIX2 (VIX above 1-y median -> CE else PE) | +1.5k [-4.9k, +7.9k] P | +0.4k [-4.9k, +4.7k] 0+ | +0.9k [-1.2k, +2.7k] P | +2.3k [-11.7k, +16.1k] P | -1.7k [-8.3k, +3.8k] 0 |
| VIXHI (VIX top tercile -> CE) | +1.4k [-10.6k, +13.8k] P | +2.6k [-6.8k, +11.2k] P | +2.8k [-4.0k, +9.3k] P | +0.3k [-34.0k, +32.6k] 0+ | -6.1k [-30.8k, +10.1k] 0 |
| STRESS (Big |1-day move| -> CE) | -5.6k [-23.1k, +9.3k] 0 | -7.7k [-14.3k, -1.0k] **N** | +0.3k [-1.4k, +1.9k] 0+ | -9.3k [-29.7k, +26.0k] 0 | - |
| MACDspot (Spot 30-min MACD sign) | -2.8k [-9.5k, +3.9k] 0 | +0.5k [-3.2k, +4.3k] 0+ | -0.0k [-1.2k, +1.1k] 0 | +0.3k [-7.0k, +7.4k] 0+ | -1.6k [-4.0k, +1.2k] 0 |
| MACDx (Premium MACD: only one side > 0) | -3.9k [-13.4k, +5.3k] 0 | -0.2k [-4.2k, +4.1k] 0 | -0.4k [-1.7k, +1.1k] 0 | +0.9k [-9.6k, +11.5k] P | -2.6k [-6.1k, +1.0k] 0 |
| MACDr (Premium MACD: higher side) | -3.7k [-10.3k, +2.9k] 0 | -0.5k [-4.4k, +3.4k] 0 | -0.2k [-1.3k, +0.9k] 0 | +0.9k [-9.0k, +10.7k] P | -1.6k [-4.6k, +1.9k] 0 |
| MODEL (Index-lane ridge (champion analogue)) | -1.8k [-5.7k, +2.1k] 0 | +0.2k [-1.5k, +2.0k] 0+ | - | -1.5k [-5.1k, +2.1k] 0 | - |
| MODELID (Index-lane ridge + intraday) | -0.2k [-4.3k, +3.7k] 0 | +0.7k [-1.2k, +2.6k] P | - | +1.2k [-1.9k, +4.6k] P | - |
| CHAMP (Live champion picks) | - | - | - | -6.8k [-16.9k, +2.7k] 0 | - |
| DST&ST15 (Daily ST + 15-min ST agree) | +3.3k [-7.3k, +13.5k] P | - | - | -1.3k [-18.5k, +12.0k] 0 | +1.6k [-0.9k, +4.3k] P |
| DST&ST30 (Daily ST + 30-min ST agree) | -2.3k [-13.1k, +8.5k] 0 | +2.2k [-3.4k, +8.1k] P | +0.3k [-1.0k, +1.7k] 0+ | -1.5k [-15.2k, +12.3k] 0 | -0.4k [-2.3k, +1.8k] 0 |
| DST&MACDr (Daily ST + premium MACD agree) | -2.4k [-13.3k, +8.5k] 0 | +1.5k [-5.2k, +8.5k] P | -0.3k [-1.6k, +1.1k] 0 | +2.4k [-14.1k, +18.0k] P | -0.5k [-3.1k, +2.6k] 0 |
| T5&DST (5-day trend + daily ST agree) | -3.8k [-14.7k, +6.5k] 0 | +1.4k [-4.6k, +8.1k] P | -0.3k [-1.6k, +1.1k] 0 | +1.6k [-8.9k, +11.3k] P | -1.3k [-3.9k, +0.8k] 0 |
| DST&PIVP (Daily ST + pivot agree) | +3.4k [-8.4k, +14.2k] P | +4.6k [-2.2k, +12.4k] P | +0.8k [-0.5k, +2.2k] P | -5.6k [-23.1k, +9.0k] 0 | +0.6k [-1.7k, +3.2k] P |
| T20&DST (20-day trend + daily ST agree) | -1.6k [-9.6k, +6.4k] 0 | +1.5k [-3.2k, +6.8k] P | -0.2k [-1.2k, +0.8k] 0 | +0.7k [-7.6k, +8.9k] P | +1.4k [-0.2k, +3.5k] P |
| ALLCE (Always CE) | +0.0k [+0.0k, +0.0k] 0 | +0.0k [+0.0k, +0.0k] 0 | +0.0k [+0.0k, +0.0k] 0 | +0.0k [+0.0k, +0.0k] 0 | +0.0k [+0.0k, +0.0k] 0 |
| ALLPE (Always PE) | +0.0k [+0.0k, +0.0k] 0 | +0.0k [+0.0k, +0.0k] 0 | +0.0k [+0.0k, +0.0k] 0 | +0.0k [+0.0k, +0.0k] 0 | +0.0k [+0.0k, +0.0k] 0 |

#### Side value per trade, grid 10:15/12:15/14:45 pooled, exit `d3` (Rs per Rs 1 L ticket, mean [95% day-block CI] class)

| method | ix | tbI | tbS | liveI | liveS | fy | lsim |
|---|---|---|---|---|---|---|---|
| ST5 (Spot ST(10,3) 5-min) | -0.1k [-13.4k, +13.1k] 0 | - | - | -0.2k [-47.1k, +31.1k] 0 | -3.8k [-14.8k, +1.9k] 0 | +2.8k [-2.8k, +7.8k] P | - |
| ST15 (Spot ST(14,2) 15-min) | -2.3k [-17.7k, +14.0k] 0 | - | - | -7.9k [-56.5k, +19.4k] 0 | +0.9k [-8.8k, +6.8k] P | +3.2k [-3.2k, +9.0k] P | - |
| ST30 (Spot ST(10,3) 30-min) | -25.2k [-45.3k, -4.4k] **N** | -13.9k [-24.4k, -2.3k] **N** | -2.0k [-6.4k, +2.6k] 0 | -6.1k [-76.7k, +33.0k] 0 | -1.0k [-14.9k, +6.6k] 0 | +2.1k [-6.6k, +10.5k] P | - |
| DST (Daily ST(10,3)) | -22.4k [-55.3k, +10.8k] 0 | -8.3k [-23.2k, +9.2k] 0 | -2.1k [-6.7k, +2.5k] 0 | +76.5k [-41.9k, +208.4k] P | +15.6k [+1.6k, +31.3k] **S+** | -4.2k [-16.6k, +7.5k] 0 | +0.9k [-5.2k, +6.7k] P |
| PIVTC (Pivot CPR (>TC CE, <BC PE)) | -12.3k [-33.5k, +9.0k] 0 | -12.5k [-25.0k, -1.0k] **N** | -0.5k [-4.6k, +3.8k] 0 | -15.3k [-84.6k, +35.6k] 0 | +1.5k [-10.4k, +8.0k] P | +4.4k [-4.2k, +12.7k] P | +2.3k [-2.3k, +6.7k] P |
| PIVP (Pivot P (>P CE)) | -12.8k [-31.1k, +7.6k] 0 | -11.3k [-22.6k, -0.7k] **N** | -0.5k [-4.2k, +3.5k] 0 | -9.3k [-66.5k, +28.1k] 0 | +1.0k [-9.8k, +7.2k] P | +3.7k [-4.1k, +11.3k] P | +2.2k [-2.3k, +6.5k] P |
| T5 (5-day return sign) | +0.8k [-21.3k, +24.2k] P | -3.9k [-17.6k, +10.1k] 0 | -4.0k [-8.4k, +0.1k] 0 | -19.9k [-117.8k, +42.4k] 0 | -8.5k [-30.9k, +9.5k] 0 | +4.3k [-6.2k, +14.4k] P | +1.2k [-4.0k, +6.4k] P |
| T20 (20-day return sign) | -9.6k [-37.6k, +19.5k] 0 | -10.0k [-25.0k, +7.9k] 0 | -4.6k [-9.5k, +0.1k] 0 | +111.7k [+0.7k, +223.0k] **S+** | +24.4k [-2.9k, +49.0k] P | +1.8k [-10.4k, +13.0k] P | +4.3k [-1.7k, +10.4k] P |
| SER (Series return sign) | -8.4k [-29.6k, +12.8k] 0 | -3.3k [-17.3k, +11.1k] 0 | -2.8k [-6.6k, +0.9k] 0 | +49.7k [+17.7k, +72.3k] **S+** | +27.6k [-3.3k, +52.5k] P | +9.1k [+0.2k, +17.3k] **S+** | +4.8k [-0.0k, +9.6k] P |
| ERG (ER-gated 10-day trend) | -25.0k [-66.6k, +18.0k] 0 | -11.4k [-35.5k, +14.7k] 0 | -4.2k [-11.2k, +2.8k] 0 | +49.1k [-23.4k, +107.2k] P | +17.6k [-5.3k, +39.1k] P | -4.4k [-21.2k, +12.3k] 0 | +2.6k [-5.5k, +10.7k] P |
| VIX2 (VIX above 1-y median -> CE else PE) | +23.2k [-7.7k, +54.2k] P | +13.1k [-5.4k, +34.4k] P | +5.9k [-5.5k, +18.0k] P | +99.2k [+39.7k, +208.3k] **S+** | +41.1k [+28.6k, +71.2k] **S+** | +6.6k [-6.6k, +20.3k] P | +1.2k [-5.5k, +7.6k] P |
| VIXHI (VIX top tercile -> CE) | +28.1k [-5.6k, +72.2k] P | +41.7k [+25.0k, +75.9k] **S+** | +29.9k [+7.2k, +46.7k] **S+** | - | -90.1k [-, -]  | +17.3k [-5.9k, +38.3k] P | +6.1k [-5.7k, +17.3k] P |
| STRESS (Big |1-day move| -> CE) | +36.0k [-0.9k, +68.8k] P | +21.0k [-7.4k, +53.5k] P | +0.4k [-15.2k, +12.5k] 0+ | -93.0k [-161.8k, +33.5k] 0 | - | +12.5k [-8.7k, +33.3k] P | +7.7k [-1.8k, +17.9k] P |
| MACDspot (Spot 30-min MACD sign) | -12.5k [-35.7k, +10.8k] 0 | -13.6k [-25.9k, -2.4k] **N** | -4.1k [-8.3k, +0.3k] 0 | -16.2k [-97.1k, +29.5k] 0 | -4.7k [-21.5k, +5.2k] 0 | +4.9k [-4.5k, +13.9k] P | - |
| MACDx (Premium MACD: only one side > 0) | -10.8k [-40.1k, +23.3k] 0 | -13.7k [-28.1k, +0.4k] 0 | -5.7k [-10.7k, -0.4k] **N** | +25.5k [-69.0k, +46.7k] P | -4.5k [-27.5k, +7.6k] 0 | - | - |
| MACDr (Premium MACD: higher side) | -16.1k [-40.4k, +9.0k] 0 | -13.8k [-25.7k, -1.9k] **N** | -4.9k [-8.8k, -1.0k] **N** | -52.2k [-179.6k, +17.7k] 0 | -6.0k [-25.6k, +6.5k] 0 | - | - |
| MODEL (Index-lane ridge (champion analogue)) | +5.5k [-13.6k, +26.5k] P | +3.6k [-10.0k, +18.7k] P | - | -37.4k [-131.6k, +61.9k] 0 | - | -0.1k [-8.8k, +9.4k] 0 | -0.7k [-5.5k, +4.4k] 0 |
| MODELID (Index-lane ridge + intraday) | +4.5k [-18.3k, +29.0k] P | -3.4k [-17.4k, +10.2k] 0 | - | -47.7k [-161.6k, +47.9k] 0 | - | -3.7k [-12.3k, +5.4k] 0 | -2.2k [-8.1k, +3.8k] 0 |
| CHAMP (Live champion picks) | - | - | - | +41.0k [-37.4k, +87.1k] P | - | - | - |
| DST&ST15 (Daily ST + 15-min ST agree) | -25.7k [-60.0k, +9.8k] 0 | - | - | +69.9k [-44.8k, +168.9k] P | +16.1k [+2.6k, +25.0k] **S+** | -1.0k [-15.7k, +13.1k] 0 | - |
| DST&ST30 (Daily ST + 30-min ST agree) | -45.0k [-80.2k, -13.1k] **N** | -21.9k [-39.3k, -0.6k] **N** | -4.1k [-10.6k, +2.0k] 0 | +71.1k [-34.4k, +146.6k] P | +13.7k [-5.2k, +24.6k] P | -1.9k [-16.4k, +13.2k] 0 | - |
| DST&MACDr (Daily ST + premium MACD agree) | -38.3k [-81.6k, +4.7k] 0 | -21.1k [-39.5k, -1.3k] **N** | -6.8k [-13.0k, -1.3k] **N** | +47.1k [-92.4k, +114.9k] P | +11.1k [-7.6k, +21.4k] P | - | - |
| T5&DST (5-day trend + daily ST agree) | -18.6k [-54.2k, +20.1k] 0 | -11.4k [-28.6k, +7.7k] 0 | -5.6k [-11.1k, -0.5k] **N** | +50.3k [-40.7k, +92.4k] P | +6.0k [-12.1k, +18.9k] P | +0.1k [-14.5k, +14.0k] 0+ | +1.7k [-5.4k, +8.4k] P |
| DST&PIVP (Daily ST + pivot agree) | -35.7k [-74.0k, +2.6k] 0 | -20.2k [-39.5k, +0.3k] 0 | -2.6k [-8.8k, +3.3k] 0 | +75.4k [-51.7k, +181.9k] P | +15.7k [-1.1k, +25.9k] P | -0.4k [-15.7k, +14.1k] 0 | +3.0k [-4.4k, +10.3k] P |
| T20&DST (20-day trend + daily ST agree) | -19.2k [-51.1k, +13.8k] 0 | -11.4k [-28.9k, +9.2k] 0 | -4.3k [-9.9k, +1.1k] 0 | +126.6k [-23.8k, +266.8k] P | +29.4k [+0.1k, +55.5k] **S+** | -1.6k [-16.3k, +12.7k] 0 | +3.1k [-3.9k, +9.7k] P |
| ALLCE (Always CE) | -10.8k [-41.9k, +20.9k] 0 | -3.1k [-24.7k, +18.7k] 0 | +0.0k [-12.0k, +11.8k] 0+ | -120.2k [-231.8k, -21.3k] **N** | -44.1k [-77.3k, -10.7k] **N** | +1.6k [-12.1k, +16.5k] P | +3.7k [-3.2k, +10.8k] P |
| ALLPE (Always PE) | +10.8k [-20.9k, +41.9k] P | +3.1k [-18.7k, +24.7k] P | -0.0k [-11.8k, +12.0k] 0 | +120.2k [+21.3k, +231.8k] **S+** | +44.1k [+10.7k, +77.3k] **S+** | -1.6k [-16.5k, +12.1k] 0 | -3.7k [-10.8k, +3.2k] 0 |

#### Side value per trade, grid 10:15/12:15/14:45 pooled, exit `d3` — BEYOND the method's own CE/PE tilt (Rs per Rs 1 L ticket, mean [95% day-block CI] class)

| method | ix | tbI | tbS | liveI | liveS | fy | lsim |
|---|---|---|---|---|---|---|---|
| ST5 (Spot ST(10,3) 5-min) | -0.5k [-14.7k, +13.7k] 0 | - | - | -8.9k [-63.0k, +24.5k] 0 | -5.3k [-17.7k, +0.6k] 0 | +2.7k [-2.5k, +7.6k] P | - |
| ST15 (Spot ST(14,2) 15-min) | -2.7k [-18.3k, +15.1k] 0 | - | - | -18.7k [-74.4k, +12.2k] 0 | -3.8k [-16.7k, +3.1k] 0 | +3.0k [-3.0k, +8.8k] P | - |
| ST30 (Spot ST(10,3) 30-min) | -24.9k [-45.8k, -3.4k] **N** | -13.7k [-24.8k, -3.7k] **N** | -2.0k [-6.5k, +2.8k] 0 | -24.5k [-108.6k, +21.7k] 0 | -8.1k [-24.1k, -0.5k] **N** | +1.9k [-6.7k, +10.2k] P | - |
| DST (Daily ST(10,3)) | -17.5k [-47.3k, +10.7k] 0 | -7.1k [-24.2k, +12.8k] 0 | -2.1k [-6.7k, +2.4k] 0 | +62.5k [-50.6k, +178.6k] P | +1.4k [-6.8k, +8.2k] P | -4.4k [-16.7k, +7.5k] 0 | +0.2k [-6.2k, +5.9k] 0+ |
| PIVTC (Pivot CPR (>TC CE, <BC PE)) | -12.4k [-33.0k, +9.3k] 0 | -12.5k [-24.8k, -1.2k] **N** | -0.5k [-4.2k, +3.7k] 0 | -35.8k [-123.7k, +22.5k] 0 | -7.5k [-23.3k, -0.6k] **N** | +4.2k [-4.1k, +12.3k] P | +2.0k [-2.6k, +6.6k] P |
| PIVP (Pivot P (>P CE)) | -13.1k [-33.0k, +7.0k] 0 | -11.3k [-22.3k, -1.0k] **N** | -0.5k [-3.9k, +3.4k] 0 | -28.8k [-100.0k, +16.0k] 0 | -7.0k [-21.4k, -0.4k] **N** | +3.5k [-4.2k, +11.0k] P | +1.9k [-2.5k, +6.3k] P |
| T5 (5-day return sign) | +1.3k [-23.5k, +24.1k] P | -3.7k [-17.6k, +8.6k] 0 | -4.0k [-8.1k, -0.0k] **N** | -78.4k [-221.4k, +8.8k] 0 | -20.2k [-48.8k, -2.0k] **N** | +4.1k [-6.1k, +13.8k] P | +0.7k [-4.3k, +5.7k] P |
| T20 (20-day return sign) | -5.4k [-31.8k, +21.7k] 0 | -9.0k [-24.9k, +9.8k] 0 | -4.6k [-9.4k, +0.0k] 0 | +35.9k [-17.6k, +80.3k] P | +2.3k [-13.1k, +13.0k] P | +1.4k [-11.0k, +13.2k] P | +3.4k [-2.7k, +8.8k] P |
| SER (Series return sign) | -8.7k [-31.0k, +12.4k] 0 | -2.7k [-16.6k, +11.6k] 0 | -2.8k [-6.3k, +0.2k] 0 | +6.4k [-24.3k, +32.1k] P | +0.3k [-12.8k, +5.7k] 0+ | +8.9k [+0.4k, +17.4k] **S+** | +4.2k [-0.4k, +8.9k] P |
| ERG (ER-gated 10-day trend) | -22.2k [-67.1k, +15.6k] 0 | -10.6k [-35.9k, +16.4k] 0 | -4.2k [-11.9k, +3.0k] 0 | -71.2k [-166.0k, +25.2k] 0 | -11.9k [-25.2k, -0.6k] **N** | -4.8k [-19.8k, +10.3k] 0 | +1.5k [-6.7k, +9.4k] P |
| VIX2 (VIX above 1-y median -> CE else PE) | +19.0k [-1.9k, +43.1k] P | +11.6k [+0.6k, +27.2k] **S+** | +5.9k [-0.7k, +13.9k] P | +37.4k [+24.4k, +90.8k] **S+** | +16.2k [+11.8k, +39.4k] **S+** | +6.7k [-7.1k, +19.9k] P | +1.4k [-5.1k, +8.0k] P |
| VIXHI (VIX top tercile -> CE) | +38.9k [+2.1k, +92.4k] **S+** | +44.8k [+20.8k, +87.4k] **S+** | +29.8k [+8.0k, +59.7k] **S+** | - | -46.0k [-50.6k, +70.0k] 0 | +15.7k [-3.9k, +35.9k] P | +2.5k [-6.9k, +11.8k] P |
| STRESS (Big |1-day move| -> CE) | +46.8k [+14.1k, +79.1k] **S+** | +24.1k [+5.5k, +47.1k] **S+** | +0.3k [-7.0k, +6.5k] 0+ | +27.2k [+2.6k, +121.8k] **S+** | - | +10.8k [-2.9k, +24.8k] P | +4.1k [-2.3k, +10.4k] P |
| MACDspot (Spot 30-min MACD sign) | -11.6k [-35.1k, +11.4k] 0 | -13.4k [-26.6k, -3.5k] **N** | -4.1k [-8.1k, +0.0k] 0 | -47.6k [-149.3k, +12.9k] 0 | -14.0k [-33.9k, -4.4k] **N** | +4.7k [-4.4k, +13.7k] P | - |
| MACDx (Premium MACD: only one side > 0) | -10.7k [-41.0k, +21.0k] 0 | -13.6k [-28.8k, -1.2k] **N** | -5.7k [-10.4k, -0.6k] **N** | -45.0k [-168.1k, -5.8k] **N** | -16.6k [-42.1k, -5.6k] **N** | - | - |
| MACDr (Premium MACD: higher side) | -15.8k [-40.0k, +8.2k] 0 | -13.7k [-26.9k, -2.8k] **N** | -4.9k [-8.8k, -1.0k] **N** | -84.1k [-233.2k, -0.3k] **N** | -14.9k [-38.0k, -3.3k] **N** | - | - |
| MODEL (Index-lane ridge (champion analogue)) | +3.7k [-16.1k, +24.5k] P | +3.2k [-9.1k, +17.0k] P | - | -30.9k [-121.3k, +60.4k] 0 | - | -0.0k [-8.9k, +8.9k] 0 | -0.7k [-5.6k, +4.7k] 0 |
| MODELID (Index-lane ridge + intraday) | +3.3k [-19.1k, +26.2k] P | -3.4k [-17.1k, +10.7k] 0 | - | -41.2k [-148.0k, +48.8k] 0 | - | -3.7k [-12.4k, +5.7k] 0 | -2.2k [-8.2k, +3.8k] 0 |
| CHAMP (Live champion picks) | - | - | - | -29.9k [-85.4k, +18.6k] 0 | - | - | - |
| DST&ST15 (Daily ST + 15-min ST agree) | -21.0k [-54.7k, +9.6k] 0 | - | - | +44.5k [-60.5k, +118.7k] P | -2.3k [-11.2k, +3.9k] 0 | -1.3k [-15.5k, +12.0k] 0 | - |
| DST&ST30 (Daily ST + 30-min ST agree) | -40.1k [-76.3k, -9.9k] **N** | -20.4k [-40.8k, +1.5k] 0 | -4.1k [-10.8k, +2.5k] 0 | +38.3k [-58.2k, +99.8k] P | -6.3k [-18.0k, -3.4k] **N** | -2.2k [-16.7k, +11.6k] 0 | - |
| DST&MACDr (Daily ST + premium MACD agree) | -33.2k [-74.0k, +3.7k] 0 | -19.7k [-40.7k, +1.1k] 0 | -6.8k [-13.3k, -1.0k] **N** | -5.3k [-130.6k, +22.3k] 0 | -9.6k [-26.9k, -4.7k] **N** | - | - |
| T5&DST (5-day trend + daily ST agree) | -14.0k [-52.8k, +19.7k] 0 | -10.0k [-29.6k, +10.2k] 0 | -5.6k [-11.0k, -0.2k] **N** | -14.1k [-75.8k, +2.7k] 0 | -16.0k [-36.1k, -4.5k] **N** | -0.3k [-14.6k, +13.6k] 0 | +0.8k [-6.6k, +7.8k] P |
| DST&PIVP (Daily ST + pivot agree) | -31.1k [-66.6k, -0.9k] **N** | -18.9k [-39.5k, +2.4k] 0 | -2.6k [-8.8k, +3.2k] 0 | +37.8k [-78.4k, +106.9k] P | -5.3k [-15.6k, -1.4k] **N** | -0.8k [-15.3k, +13.0k] 0 | +2.0k [-5.7k, +9.4k] P |
| T20&DST (20-day trend + daily ST agree) | -13.7k [-44.2k, +15.6k] 0 | -10.0k [-28.5k, +11.4k] 0 | -4.3k [-9.7k, +1.1k] 0 | +66.2k [-46.9k, +151.3k] P | +4.5k [-12.6k, +15.7k] P | -1.9k [-15.9k, +12.0k] 0 | +2.2k [-5.1k, +8.5k] P |
| ALLCE (Always CE) | +0.0k [+0.0k, +0.0k] 0 | +0.0k [+0.0k, +0.0k] 0 | +0.0k [+0.0k, +0.0k] 0 | +0.0k [+0.0k, +0.0k] 0 | +0.0k [+0.0k, +0.0k] 0 | +0.0k [+0.0k, +0.0k] 0 | +0.0k [+0.0k, +0.0k] 0 |
| ALLPE (Always PE) | +0.0k [+0.0k, +0.0k] 0 | +0.0k [+0.0k, +0.0k] 0 | +0.0k [+0.0k, +0.0k] 0 | +0.0k [+0.0k, +0.0k] 0 | +0.0k [+0.0k, +0.0k] 0 | +0.0k [+0.0k, +0.0k] 0 | +0.0k [+0.0k, +0.0k] 0 |

#### Side value per trade, grid 10:15/12:15/14:45 pooled, exit `lane` (Rs per Rs 1 L ticket, mean [95% day-block CI] class)

| method | ix | tbI | tbS | liveI | liveS |
|---|---|---|---|---|---|
| ST5 (Spot ST(10,3) 5-min) | +0.4k [-7.1k, +8.7k] 0+ | - | - | +7.7k [-9.8k, +30.6k] P | +2.2k [-0.8k, +4.0k] P |
| ST15 (Spot ST(14,2) 15-min) | +10.3k [+1.2k, +19.0k] **S+** | - | - | +2.0k [-13.1k, +13.2k] P | +4.9k [+1.3k, +6.6k] **S+** |
| ST30 (Spot ST(10,3) 30-min) | -10.2k [-22.2k, +3.3k] 0 | -12.0k [-21.9k, -2.0k] **N** | -2.0k [-4.7k, +0.8k] 0 | +13.1k [-11.1k, +34.8k] P | +4.4k [-2.7k, +7.7k] P |
| DST (Daily ST(10,3)) | -14.6k [-30.2k, +0.7k] 0 | -10.8k [-22.0k, +1.5k] 0 | -1.8k [-4.6k, +1.2k] 0 | +5.7k [-40.0k, +36.8k] P | +6.6k [-2.0k, +12.4k] P |
| PIVTC (Pivot CPR (>TC CE, <BC PE)) | +1.9k [-8.8k, +13.1k] P | -5.4k [-14.5k, +3.0k] 0 | +0.6k [-1.8k, +3.1k] P | +5.5k [-15.1k, +33.0k] P | +6.6k [+0.2k, +10.0k] **S+** |
| PIVP (Pivot P (>P CE)) | +2.3k [-7.6k, +12.7k] P | -4.9k [-12.8k, +3.0k] 0 | +0.6k [-1.7k, +2.9k] P | +4.6k [-14.8k, +30.9k] P | +5.8k [+0.1k, +8.9k] **S+** |
| T5 (5-day return sign) | -8.7k [-22.8k, +5.7k] 0 | -10.0k [-21.5k, +1.1k] 0 | -3.3k [-6.0k, -0.5k] **N** | +10.3k [-23.5k, +45.7k] P | -1.3k [-11.4k, +8.2k] 0 |
| T20 (20-day return sign) | -9.0k [-24.8k, +6.3k] 0 | -11.6k [-22.9k, +0.5k] 0 | -2.9k [-5.7k, -0.0k] **N** | +38.9k [-2.4k, +71.1k] P | +12.1k [-4.9k, +22.9k] P |
| SER (Series return sign) | -4.9k [-18.9k, +8.4k] 0 | -8.0k [-20.3k, +3.8k] 0 | -1.3k [-3.9k, +1.4k] 0 | +33.1k [+11.8k, +59.0k] **S+** | +14.8k [-3.3k, +25.6k] P |
| ERG (ER-gated 10-day trend) | -14.7k [-30.6k, +10.0k] 0 | -12.4k [-31.8k, +6.5k] 0 | -3.1k [-8.2k, +2.1k] 0 | +33.1k [-13.4k, +82.2k] P | +11.3k [-3.4k, +23.6k] P |
| VIX2 (VIX above 1-y median -> CE else PE) | +8.4k [-7.9k, +24.4k] P | +3.9k [-9.7k, +18.3k] P | +2.1k [-5.7k, +9.8k] P | +35.1k [+19.7k, +67.5k] **S+** | +16.3k [+11.3k, +31.1k] **S+** |
| VIXHI (VIX top tercile -> CE) | -4.0k [-27.3k, +15.1k] 0 | +13.5k [-3.4k, +41.9k] P | +17.8k [+3.8k, +28.4k] **S+** | - | -61.3k [-, -]  |
| STRESS (Big |1-day move| -> CE) | +12.7k [-12.9k, +32.4k] P | +11.4k [-5.2k, +34.3k] P | +1.5k [-6.6k, +8.7k] P | -27.9k [-56.1k, +20.1k] 0 | - |
| MACDspot (Spot 30-min MACD sign) | -3.8k [-16.3k, +9.3k] 0 | -10.6k [-21.0k, -1.3k] **N** | -2.9k [-5.7k, -0.1k] **N** | +9.9k [-16.5k, +35.3k] P | +2.4k [-5.9k, +6.7k] P |
| MACDx (Premium MACD: only one side > 0) | -3.6k [-18.0k, +13.9k] 0 | -11.9k [-23.7k, -0.7k] **N** | -4.1k [-7.5k, -0.6k] **N** | +32.0k [-6.8k, +61.3k] P | +3.6k [-6.0k, +9.0k] P |
| MACDr (Premium MACD: higher side) | -3.6k [-15.5k, +10.2k] 0 | -10.6k [-20.2k, -1.3k] **N** | -3.1k [-5.9k, -0.3k] **N** | +8.3k [-23.4k, +37.6k] P | +2.9k [-4.7k, +7.6k] P |
| MODEL (Index-lane ridge (champion analogue)) | +4.8k [-6.2k, +16.8k] P | +4.6k [-6.2k, +15.8k] P | - | +7.4k [-20.2k, +52.6k] P | - |
| MODELID (Index-lane ridge + intraday) | +2.6k [-10.8k, +19.1k] P | -5.1k [-15.6k, +5.0k] 0 | - | +6.2k [-25.2k, +42.8k] P | - |
| CHAMP (Live champion picks) | - | - | - | +20.4k [-13.5k, +39.5k] P | - |
| DST&ST15 (Daily ST + 15-min ST agree) | -4.4k [-20.8k, +11.4k] 0 | - | - | +7.8k [-47.4k, +36.5k] P | +11.2k [+2.4k, +15.9k] **S+** |
| DST&ST30 (Daily ST + 30-min ST agree) | -23.4k [-42.9k, -4.5k] **N** | -22.5k [-35.6k, -6.7k] **N** | -3.7k [-7.7k, +0.1k] 0 | +19.0k [-38.6k, +49.5k] P | +10.4k [-4.0k, +17.9k] P |
| DST&MACDr (Daily ST + premium MACD agree) | -18.1k [-38.0k, +2.9k] 0 | -21.5k [-36.0k, -6.2k] **N** | -4.8k [-9.0k, -0.7k] **N** | +10.9k [-74.4k, +45.4k] P | +9.9k [-4.2k, +17.6k] P |
| T5&DST (5-day trend + daily ST agree) | -20.1k [-39.4k, +0.7k] 0 | -19.4k [-33.0k, -3.4k] **N** | -4.6k [-8.2k, -0.8k] **N** | +14.2k [-31.4k, +29.6k] P | +4.5k [-9.9k, +13.8k] P |
| DST&PIVP (Daily ST + pivot agree) | -12.5k [-28.5k, +3.2k] 0 | -16.2k [-27.9k, -2.3k] **N** | -1.2k [-4.9k, +2.4k] 0 | +11.6k [-45.6k, +39.9k] P | +11.8k [+0.5k, +17.3k] **S+** |
| T20&DST (20-day trend + daily ST agree) | -14.2k [-30.3k, +2.9k] 0 | -13.9k [-26.9k, +0.4k] 0 | -3.0k [-6.2k, +0.3k] 0 | +30.0k [-18.9k, +59.8k] P | +13.3k [-5.4k, +25.3k] P |
| ALLCE (Always CE) | -11.2k [-25.9k, +4.8k] 0 | -2.0k [-15.2k, +12.3k] 0 | +1.7k [-6.3k, +9.3k] P | -45.5k [-75.0k, -12.5k] **N** | -23.6k [-37.8k, -4.9k] **N** |
| ALLPE (Always PE) | +11.2k [-4.8k, +25.9k] P | +2.0k [-12.3k, +15.2k] P | -1.7k [-9.3k, +6.3k] 0 | +45.5k [+12.5k, +75.0k] **S+** | +23.6k [+4.9k, +37.8k] **S+** |

#### Side value per trade, grid 10:15/12:15/14:45 pooled, exit `h30` (Rs per Rs 1 L ticket, mean [95% day-block CI] class)

| method | ix | tbI | tbS | liveI | liveS |
|---|---|---|---|---|---|
| ST5 (Spot ST(10,3) 5-min) | +2.8k [+0.6k, +5.1k] **S+** | - | - | -1.1k [-4.6k, +2.3k] 0 | +0.0k [-0.7k, +0.9k] 0+ |
| ST15 (Spot ST(14,2) 15-min) | +2.1k [-0.2k, +4.4k] P | - | - | -1.1k [-3.9k, +1.4k] 0 | -0.1k [-0.9k, +0.7k] 0 |
| ST30 (Spot ST(10,3) 30-min) | +0.2k [-1.9k, +2.3k] 0+ | +0.7k [-0.4k, +1.9k] P | +0.1k [-0.2k, +0.4k] 0+ | -1.6k [-4.3k, +1.3k] 0 | -0.3k [-1.2k, +0.5k] 0 |
| DST (Daily ST(10,3)) | +1.7k [-0.5k, +4.1k] P | +0.7k [-0.5k, +1.8k] P | +0.1k [-0.1k, +0.4k] 0+ | +2.2k [-0.5k, +4.9k] P | +0.6k [-0.1k, +1.3k] P |
| PIVTC (Pivot CPR (>TC CE, <BC PE)) | +2.4k [-0.1k, +4.9k] P | +1.2k [+0.1k, +2.5k] **S+** | +0.4k [+0.1k, +0.8k] **S+** | -2.5k [-5.3k, +0.1k] 0 | -0.2k [-1.2k, +0.9k] 0 |
| PIVP (Pivot P (>P CE)) | +2.6k [+0.2k, +4.8k] **S+** | +1.3k [+0.2k, +2.4k] **S+** | +0.4k [+0.1k, +0.8k] **S+** | -2.9k [-5.4k, -0.4k] **N** | -0.1k [-1.0k, +0.8k] 0 |
| T5 (5-day return sign) | -1.9k [-4.3k, +0.4k] 0 | -0.7k [-1.6k, +0.4k] 0 | -0.1k [-0.4k, +0.2k] 0 | -0.3k [-3.3k, +2.9k] 0 | -0.1k [-1.1k, +0.8k] 0 |
| T20 (20-day return sign) | -0.2k [-2.5k, +2.2k] 0 | +0.2k [-1.0k, +1.3k] 0+ | +0.0k [-0.2k, +0.3k] 0+ | +1.2k [-1.8k, +4.4k] P | +0.9k [-0.3k, +2.0k] P |
| SER (Series return sign) | -1.0k [-3.3k, +1.4k] 0 | -0.0k [-0.9k, +0.8k] 0 | -0.1k [-0.4k, +0.1k] 0 | +0.7k [-1.6k, +3.0k] P | +0.4k [-0.7k, +1.5k] 0+ |
| ERG (ER-gated 10-day trend) | +3.6k [+0.1k, +7.0k] **S+** | +1.6k [+0.1k, +3.1k] **S+** | +0.3k [-0.1k, +0.7k] 0+ | -1.9k [-6.5k, +2.5k] 0 | +0.1k [-1.4k, +1.8k] 0+ |
| VIX2 (VIX above 1-y median -> CE else PE) | -0.1k [-2.4k, +2.2k] 0 | -0.1k [-1.4k, +1.1k] 0 | +0.2k [-0.5k, +0.8k] 0+ | +0.3k [-2.3k, +3.1k] 0+ | -0.5k [-2.0k, +1.0k] 0 |
| VIXHI (VIX top tercile -> CE) | -0.3k [-5.7k, +4.6k] 0 | +0.3k [-3.4k, +3.6k] 0+ | -0.1k [-2.7k, +2.1k] 0 | -4.4k [-8.3k, -0.8k] **N** | -3.6k [-6.7k, -1.4k] **N** |
| STRESS (Big |1-day move| -> CE) | -4.3k [-11.5k, +2.1k] 0 | -3.4k [-7.3k, +0.1k] 0 | -0.2k [-1.4k, +0.8k] 0 | +1.4k [-3.9k, +9.6k] P | - |
| MACDspot (Spot 30-min MACD sign) | -0.8k [-3.3k, +1.7k] 0 | +0.1k [-0.9k, +1.2k] 0+ | +0.0k [-0.3k, +0.3k] 0+ | -1.2k [-4.2k, +2.0k] 0 | -0.3k [-1.3k, +0.7k] 0 |
| MACDx (Premium MACD: only one side > 0) | -0.8k [-4.0k, +2.4k] 0 | +0.0k [-1.3k, +1.3k] 0+ | -0.1k [-0.6k, +0.3k] 0 | +0.9k [-3.2k, +4.5k] P | -0.5k [-1.7k, +0.8k] 0 |
| MACDr (Premium MACD: higher side) | -1.2k [-3.8k, +1.3k] 0 | -0.3k [-1.3k, +0.8k] 0 | -0.0k [-0.4k, +0.3k] 0 | +0.3k [-2.9k, +3.4k] 0+ | -0.4k [-1.5k, +0.7k] 0 |
| MODEL (Index-lane ridge (champion analogue)) | -1.5k [-5.3k, +2.4k] 0 | +0.6k [-1.2k, +2.6k] P | - | -0.4k [-3.8k, +3.2k] 0 | - |
| MODELID (Index-lane ridge + intraday) | -0.0k [-4.2k, +4.0k] 0 | +0.7k [-1.1k, +2.5k] P | - | +1.5k [-1.7k, +5.0k] P | - |
| CHAMP (Live champion picks) | - | - | - | -1.1k [-7.1k, +3.9k] 0 | - |
| DST&ST15 (Daily ST + 15-min ST agree) | +4.0k [+0.3k, +7.7k] **S+** | - | - | +1.0k [-2.7k, +4.3k] P | +0.4k [-0.8k, +1.8k] 0+ |
| DST&ST30 (Daily ST + 30-min ST agree) | +1.9k [-1.2k, +4.8k] P | +1.4k [-0.2k, +3.0k] P | +0.2k [-0.2k, +0.5k] 0+ | +0.5k [-3.8k, +4.3k] P | +0.2k [-1.0k, +1.5k] 0+ |
| DST&MACDr (Daily ST + premium MACD agree) | +0.7k [-2.8k, +4.0k] P | +0.2k [-1.3k, +1.7k] 0+ | +0.1k [-0.3k, +0.4k] 0+ | +2.0k [-3.0k, +6.5k] P | +0.2k [-1.1k, +1.6k] 0+ |
| T5&DST (5-day trend + daily ST agree) | -0.1k [-3.2k, +2.8k] 0 | +0.0k [-1.3k, +1.3k] 0+ | -0.0k [-0.3k, +0.3k] 0 | +1.5k [-2.3k, +4.8k] P | +0.4k [-0.8k, +1.5k] 0+ |
| DST&PIVP (Daily ST + pivot agree) | +4.5k [+0.7k, +7.8k] **S+** | +2.0k [+0.4k, +3.5k] **S+** | +0.5k [+0.1k, +0.9k] **S+** | -0.7k [-5.0k, +3.1k] 0 | +0.4k [-0.9k, +1.8k] 0+ |
| T20&DST (20-day trend + daily ST agree) | +0.9k [-1.7k, +3.5k] P | +0.5k [-0.7k, +1.8k] P | +0.1k [-0.2k, +0.4k] 0+ | +2.2k [-1.3k, +5.6k] P | +1.0k [-0.3k, +2.3k] P |
| ALLCE (Always CE) | +0.3k [-2.1k, +2.7k] 0+ | +0.1k [-1.2k, +1.4k] 0+ | -0.1k [-0.8k, +0.6k] 0 | -1.2k [-4.4k, +1.7k] 0 | -1.2k [-2.7k, +0.2k] 0 |
| ALLPE (Always PE) | -0.3k [-2.7k, +2.1k] 0 | -0.1k [-1.4k, +1.2k] 0 | +0.1k [-0.6k, +0.8k] 0+ | +1.2k [-1.7k, +4.4k] P | +1.2k [-0.2k, +2.7k] P |

#### Side value per trade, grid 10:15/12:15/14:45 pooled, exit `d1` (Rs per Rs 1 L ticket, mean [95% day-block CI] class)

| method | ix | tbI | tbS | liveI | liveS |
|---|---|---|---|---|---|
| ST5 (Spot ST(10,3) 5-min) | +2.0k [-10.9k, +13.3k] P | - | - | -4.5k [-22.6k, +13.1k] 0 | -3.1k [-8.5k, +1.0k] 0 |
| ST15 (Spot ST(14,2) 15-min) | +9.4k [-4.3k, +22.5k] P | - | - | -7.2k [-27.3k, +13.4k] 0 | -1.1k [-7.6k, +3.4k] 0 |
| ST30 (Spot ST(10,3) 30-min) | -12.7k [-28.5k, +2.2k] 0 | -6.9k [-15.6k, +2.0k] 0 | +0.2k [-2.6k, +3.0k] 0+ | +8.0k [-32.2k, +47.7k] P | +3.5k [-6.3k, +14.5k] P |
| DST (Daily ST(10,3)) | -10.1k [-27.0k, +6.1k] 0 | -1.3k [-11.2k, +9.5k] 0 | -0.9k [-3.2k, +1.3k] 0 | +36.7k [-3.8k, +82.9k] P | +8.4k [+1.0k, +16.5k] **S+** |
| PIVTC (Pivot CPR (>TC CE, <BC PE)) | +2.8k [-15.2k, +19.1k] P | -1.8k [-11.8k, +8.1k] 0 | +1.6k [-0.9k, +4.3k] P | -9.9k [-37.9k, +19.9k] 0 | +2.7k [-6.3k, +11.7k] P |
| PIVP (Pivot P (>P CE)) | +4.0k [-11.9k, +19.3k] P | -1.3k [-10.1k, +7.5k] 0 | +1.5k [-0.8k, +3.9k] P | -9.5k [-34.3k, +16.1k] 0 | +2.7k [-5.5k, +11.1k] P |
| T5 (5-day return sign) | -12.0k [-29.8k, +5.3k] 0 | -8.2k [-16.5k, +0.1k] 0 | -1.7k [-4.3k, +1.0k] 0 | +17.3k [-37.6k, +59.5k] P | +1.2k [-12.0k, +11.3k] P |
| T20 (20-day return sign) | -3.1k [-18.7k, +13.3k] 0 | -2.6k [-11.8k, +6.1k] 0 | -1.3k [-3.8k, +1.1k] 0 | +51.9k [+14.1k, +93.0k] **S+** | +15.4k [+1.4k, +30.5k] **S+** |
| SER (Series return sign) | -5.6k [-21.5k, +9.7k] 0 | -1.6k [-9.3k, +5.9k] 0 | -0.7k [-2.5k, +1.0k] 0 | +25.6k [+8.4k, +48.7k] **S+** | +12.4k [-0.3k, +30.3k] P |
| ERG (ER-gated 10-day trend) | -27.1k [-51.2k, -2.1k] **N** | -9.0k [-29.8k, +8.6k] 0 | -2.7k [-7.0k, +1.7k] 0 | +33.5k [+5.0k, +62.3k] **S+** | +9.9k [-1.9k, +22.0k] P |
| VIX2 (VIX above 1-y median -> CE else PE) | +12.9k [-4.6k, +30.7k] P | +9.2k [-2.0k, +21.5k] P | +4.8k [-0.9k, +10.9k] P | +25.3k [-6.1k, +74.1k] P | +5.1k [-8.4k, +23.0k] P |
| VIXHI (VIX top tercile -> CE) | +13.2k [-18.9k, +45.7k] P | +15.4k [-6.7k, +40.9k] P | +11.6k [-3.5k, +28.9k] P | -25.9k [-77.2k, -11.6k] **N** | -25.1k [-46.6k, -20.3k] **N** |
| STRESS (Big |1-day move| -> CE) | +31.2k [-13.2k, +73.0k] P | +7.4k [-12.7k, +27.4k] P | -1.1k [-8.6k, +5.8k] 0 | -12.9k [-22.2k, +11.8k] 0 | - |
| MACDspot (Spot 30-min MACD sign) | -8.5k [-24.5k, +6.8k] 0 | -3.2k [-12.1k, +6.1k] 0 | -0.6k [-3.3k, +2.2k] 0 | +16.8k [-23.6k, +54.9k] P | +3.7k [-7.5k, +15.4k] P |
| MACDx (Premium MACD: only one side > 0) | -8.2k [-31.2k, +13.3k] 0 | -4.2k [-13.9k, +5.4k] 0 | -1.4k [-4.7k, +1.9k] 0 | +38.1k [-14.0k, +87.9k] P | +5.3k [-8.9k, +20.6k] P |
| MACDr (Premium MACD: higher side) | -8.8k [-24.3k, +6.2k] 0 | -5.1k [-13.8k, +4.4k] 0 | -1.0k [-3.7k, +1.8k] 0 | +2.1k [-73.4k, +65.1k] P | +3.2k [-10.1k, +17.0k] P |
| MODEL (Index-lane ridge (champion analogue)) | +2.2k [-14.9k, +19.8k] P | -2.7k [-13.2k, +6.4k] 0 | - | -19.5k [-80.7k, +25.6k] 0 | - |
| MODELID (Index-lane ridge + intraday) | +1.5k [-15.7k, +17.9k] P | -2.5k [-12.1k, +6.9k] 0 | - | -15.4k [-67.3k, +21.3k] 0 | - |
| CHAMP (Live champion picks) | - | - | - | +36.8k [-16.4k, +105.0k] P | - |
| DST&ST15 (Daily ST + 15-min ST agree) | -0.7k [-17.9k, +17.3k] 0 | - | - | +29.2k [-9.6k, +65.4k] P | +7.1k [-0.0k, +15.4k] P |
| DST&ST30 (Daily ST + 30-min ST agree) | -21.8k [-41.2k, -1.6k] **N** | -8.2k [-20.3k, +5.1k] 0 | -0.8k [-4.2k, +2.6k] 0 | +41.9k [-7.5k, +93.2k] P | +11.0k [-1.9k, +26.2k] P |
| DST&MACDr (Daily ST + premium MACD agree) | -18.8k [-40.4k, +3.5k] 0 | -5.1k [-17.7k, +7.5k] 0 | -1.8k [-5.5k, +1.5k] 0 | +48.2k [-16.3k, +105.9k] P | +11.2k [-4.2k, +28.5k] P |
| T5&DST (5-day trend + daily ST agree) | -19.4k [-40.8k, +2.5k] 0 | -8.8k [-20.8k, +2.8k] 0 | -2.4k [-5.3k, +0.7k] 0 | +42.8k [+1.6k, +88.7k] **S+** | +7.9k [-5.8k, +20.7k] P |
| DST&PIVP (Daily ST + pivot agree) | -6.3k [-25.6k, +13.7k] 0 | -2.7k [-14.3k, +9.3k] 0 | +0.6k [-2.2k, +3.3k] P | +28.5k [-12.3k, +69.3k] P | +10.4k [+0.5k, +22.5k] **S+** |
| T20&DST (20-day trend + daily ST agree) | -7.9k [-25.1k, +9.9k] 0 | -2.4k [-12.8k, +7.9k] 0 | -1.4k [-4.1k, +1.3k] 0 | +57.2k [+6.4k, +109.8k] **S+** | +17.0k [+2.0k, +32.0k] **S+** |
| ALLCE (Always CE) | -4.5k [-21.9k, +12.6k] 0 | -5.0k [-16.7k, +6.5k] 0 | -1.6k [-7.9k, +4.3k] 0 | -55.0k [-94.6k, -17.3k] **N** | -23.0k [-42.0k, -6.5k] **N** |
| ALLPE (Always PE) | +4.5k [-12.6k, +21.9k] P | +5.0k [-6.5k, +16.7k] P | +1.6k [-4.3k, +7.9k] P | +55.0k [+17.3k, +94.6k] **S+** | +23.0k [+6.5k, +42.0k] **S+** |


### 4.2 Own decision times

`EV` = the event's own side (MACD cross side, Supertrend flip direction, pivot break side). `EV&X` = only events whose
side agrees with method X. Fyers (simulated) is D+3 only.



**ev_macd**

| method | exit | ix | tbI | tbS | liveI | liveS | fy (d3 only) |
|---|---|---|---|---|---|---|---|
| EV | h30 | -0.7k [-5.4k, +5.0k] 0 | +1.3k [-1.0k, +3.6k] P | +0.3k [-0.2k, +0.8k] 0+ | -1.2k [-5.7k, +3.5k] 0 | +0.7k [-0.3k, +1.7k] P | - |
| EV | cls | -4.6k [-18.2k, +9.3k] 0 | +5.6k [-0.2k, +11.0k] P | +1.7k [-0.2k, +3.6k] P | +2.0k [-28.5k, +40.0k] P | +6.1k [-1.5k, +14.6k] P | - |
| EV | d1 | +13.4k [-9.8k, +39.6k] P | +15.2k [+1.6k, +29.7k] **S+** | +2.8k [-0.8k, +6.4k] P | -4.6k [-36.8k, +22.0k] 0 | -0.3k [-10.4k, +8.2k] 0 | - |
| EV | d3 | +6.2k [-32.2k, +50.5k] P | -16.5k [-38.7k, +3.9k] 0 | +0.5k [-6.6k, +8.4k] 0+ | -44.8k [-143.2k, -14.5k] **N** | +2.0k [-16.9k, +8.4k] P | - |
| EV | lane | +11.2k [-4.3k, +30.5k] P | +1.1k [-15.4k, +14.6k] P | +1.9k [-2.9k, +6.7k] P | -6.7k [-55.9k, +18.2k] 0 | +11.6k [+1.3k, +16.5k] **S+** | - |
| EV&DST | h30 | +0.9k [-5.8k, +9.3k] P | -1.4k [-4.7k, +1.7k] 0 | +0.2k [-0.4k, +0.7k] 0+ | - | +1.0k [-0.7k, +2.7k] P | - |
| EV&DST | cls | -2.9k [-23.8k, +18.3k] 0 | +7.6k [-1.7k, +17.2k] P | +1.2k [-1.2k, +3.5k] P | - | +10.0k [-0.1k, +20.2k] P | - |
| EV&DST | d1 | +0.5k [-28.4k, +32.6k] P | +10.4k [-7.0k, +27.7k] P | +1.4k [-3.1k, +5.8k] P | - | +4.0k [-3.2k, +10.9k] P | - |
| EV&DST | d3 | -20.6k [-73.2k, +34.1k] 0 | -18.4k [-45.6k, +16.1k] 0 | -2.7k [-14.2k, +8.4k] 0 | - | +21.7k [+5.8k, +30.9k] **S+** | - |
| EV&DST | lane | -3.4k [-27.6k, +24.3k] 0 | -6.1k [-30.9k, +17.2k] 0 | -0.4k [-6.9k, +5.8k] 0 | - | +19.2k [+6.6k, +26.0k] **S+** | - |
| EV&T20 | h30 | -0.4k [-6.9k, +7.0k] 0 | +0.8k [-2.4k, +3.6k] P | +0.5k [-0.1k, +1.1k] P | - | +0.4k [-1.1k, +1.8k] 0+ | - |
| EV&T20 | cls | +1.1k [-17.9k, +19.5k] P | +7.7k [-1.9k, +16.8k] P | +1.9k [-0.5k, +4.4k] P | - | +12.8k [-0.1k, +25.7k] P | - |
| EV&T20 | d1 | +23.4k [-8.4k, +60.6k] P | +11.7k [-5.3k, +29.7k] P | +0.9k [-3.4k, +5.5k] P | - | +8.1k [-4.8k, +20.7k] P | - |
| EV&T20 | d3 | +11.1k [-35.6k, +63.3k] P | -30.8k [-62.3k, +4.3k] 0 | -4.8k [-15.9k, +6.2k] 0 | - | +22.8k [-4.1k, +35.8k] P | - |
| EV&T20 | lane | +16.8k [-9.9k, +45.3k] P | -10.8k [-30.6k, +7.4k] 0 | -1.1k [-7.3k, +5.2k] 0 | - | +22.3k [+3.9k, +34.2k] **S+** | - |
| EV&T5 | h30 | +2.4k [-3.9k, +9.1k] P | -0.6k [-4.0k, +2.1k] 0 | +0.3k [-0.3k, +0.9k] 0+ | +0.5k [-4.9k, +5.3k] 0+ | +1.3k [-0.2k, +2.7k] P | - |
| EV&T5 | cls | -6.8k [-24.1k, +11.4k] 0 | -1.2k [-9.7k, +6.7k] 0 | +1.7k [-0.6k, +3.9k] P | +13.7k [-24.0k, +54.5k] P | +7.4k [-1.1k, +16.3k] P | - |
| EV&T5 | d1 | +20.8k [-9.3k, +53.0k] P | +4.8k [-11.6k, +20.3k] P | +1.9k [-1.6k, +5.3k] P | +16.8k [-23.5k, +43.2k] P | +1.3k [-11.1k, +11.6k] P | - |
| EV&T5 | d3 | +27.7k [-9.5k, +70.8k] P | +1.8k [-25.7k, +20.7k] P | -1.6k [-8.7k, +6.6k] 0 | - | -3.4k [-38.1k, +15.0k] 0 | - |
| EV&T5 | lane | +15.7k [-6.0k, +41.7k] P | -5.1k [-24.5k, +8.3k] 0 | +0.2k [-3.8k, +4.4k] 0+ | - | +12.1k [+2.3k, +16.4k] **S+** | - |
| ALLCE | h30 | +4.9k [-0.4k, +10.1k] P | -1.0k [-3.3k, +1.2k] 0 | +0.1k [-0.4k, +0.7k] 0+ | -6.2k [-9.8k, -3.1k] **N** | -0.3k [-1.5k, +1.0k] 0 | - |
| ALLCE | cls | -7.9k [-22.3k, +6.2k] 0 | -6.6k [-15.4k, +0.9k] 0 | -1.7k [-4.6k, +1.0k] 0 | -37.5k [-65.0k, -14.2k] **N** | -10.2k [-19.9k, -0.9k] **N** | - |
| ALLCE | d1 | -18.7k [-49.3k, +8.4k] 0 | -14.1k [-29.3k, +2.1k] 0 | -1.6k [-8.2k, +4.4k] 0 | -53.0k [-74.7k, -31.9k] **N** | -24.7k [-38.3k, -11.2k] **N** | - |
| ALLCE | d3 | -16.8k [-67.9k, +33.4k] 0 | -16.9k [-57.5k, +20.6k] 0 | +0.2k [-13.6k, +12.8k] 0+ | -148.1k [-200.6k, -61.4k] **N** | -58.8k [-92.0k, -23.7k] **N** | - |
| ALLCE | lane | -16.1k [-40.1k, +11.0k] 0 | -18.7k [-37.9k, +2.7k] 0 | +1.7k [-6.5k, +10.0k] P | -81.9k [-113.5k, -58.9k] **N** | -32.2k [-44.6k, -11.3k] **N** | - |

**ev_st5**

| method | exit | ix | tbI | tbS | liveI | liveS | fy (d3 only) |
|---|---|---|---|---|---|---|---|
| EV | h30 | +1.7k [-1.9k, +5.1k] P | - | - | +4.9k [+0.1k, +9.6k] **S+** | +1.3k [-0.2k, +2.5k] P | - |
| EV | cls | +3.4k [-2.0k, +9.2k] P | - | - | +2.2k [-5.6k, +12.7k] P | +1.6k [-0.6k, +4.3k] P | - |
| EV | d1 | +1.1k [-5.7k, +8.3k] P | - | - | +0.5k [-18.3k, +14.1k] 0+ | -1.6k [-8.7k, +2.8k] 0 | - |
| EV | d3 | +10.6k [+2.9k, +19.9k] **S+** | - | - | -9.4k [-33.5k, +7.1k] 0 | -0.3k [-4.7k, +2.8k] 0 | +2.0k [-1.0k, +4.6k] P |
| EV | lane | +3.0k [-2.3k, +9.5k] P | - | - | -4.8k [-14.8k, +5.7k] 0 | +1.4k [-0.7k, +3.7k] P | - |
| EV&DST | h30 | +0.6k [-4.5k, +5.4k] P | - | - | +6.2k [+0.6k, +12.6k] **S+** | +1.7k [+0.1k, +3.3k] **S+** | - |
| EV&DST | cls | -1.7k [-14.8k, +12.3k] 0 | - | - | +4.6k [-21.4k, +28.5k] P | +4.3k [-0.1k, +9.4k] P | - |
| EV&DST | d1 | -15.9k [-39.3k, +5.4k] 0 | - | - | +31.4k [-12.2k, +79.9k] P | +6.9k [+0.9k, +11.0k] **S+** | - |
| EV&DST | d3 | -14.1k [-49.0k, +21.2k] 0 | - | - | +43.5k [-51.1k, +132.0k] P | +13.5k [+1.9k, +22.8k] **S+** | -1.2k [-15.1k, +12.1k] 0 |
| EV&DST | lane | -16.2k [-33.7k, -0.4k] **N** | - | - | -9.6k [-54.3k, +16.9k] 0 | +8.0k [-1.0k, +13.6k] P | - |
| EV&T20 | h30 | +1.6k [-3.3k, +6.8k] P | - | - | +6.1k [+0.2k, +12.0k] **S+** | +2.3k [+0.4k, +4.7k] **S+** | - |
| EV&T20 | cls | -3.5k [-15.8k, +9.2k] 0 | - | - | +12.5k [-14.1k, +36.6k] P | +6.0k [-1.2k, +14.5k] P | - |
| EV&T20 | d1 | -9.9k [-31.8k, +11.5k] 0 | - | - | +53.9k [+11.8k, +99.8k] **S+** | +14.1k [+1.0k, +24.9k] **S+** | - |
| EV&T20 | d3 | -6.3k [-31.0k, +20.5k] 0 | - | - | +87.8k [+1.8k, +166.8k] **S+** | +20.0k [-6.6k, +39.1k] P | +1.0k [-13.0k, +13.1k] P |
| EV&T20 | lane | -10.6k [-27.1k, +6.5k] 0 | - | - | +34.5k [-5.2k, +71.1k] P | +11.2k [-7.8k, +21.7k] P | - |
| EV&T5 | h30 | +0.9k [-4.5k, +6.2k] P | - | - | +5.9k [+0.9k, +11.5k] **S+** | +1.7k [-0.2k, +3.6k] P | - |
| EV&T5 | cls | -2.5k [-14.7k, +10.4k] 0 | - | - | +10.1k [-14.4k, +32.4k] P | +1.3k [-4.2k, +7.3k] P | - |
| EV&T5 | d1 | -9.9k [-31.6k, +10.7k] 0 | - | - | +29.7k [-8.0k, +60.0k] P | +3.1k [-7.3k, +9.4k] P | - |
| EV&T5 | d3 | +1.6k [-23.5k, +25.4k] P | - | - | -24.5k [-108.5k, +48.0k] 0 | -6.7k [-30.0k, +10.1k] 0 | +6.5k [-4.2k, +17.9k] P |
| EV&T5 | lane | -7.1k [-23.2k, +9.1k] 0 | - | - | +8.2k [-24.2k, +45.1k] P | +2.2k [-8.0k, +10.7k] P | - |
| ALLCE | h30 | -0.7k [-4.6k, +3.5k] 0 | - | - | -1.3k [-5.5k, +2.5k] 0 | -1.4k [-3.1k, +0.1k] 0 | - |
| ALLCE | cls | -3.1k [-14.4k, +8.5k] 0 | - | - | -11.6k [-29.7k, +7.8k] 0 | -7.5k [-16.0k, -0.4k] **N** | - |
| ALLCE | d1 | +0.5k [-19.9k, +21.0k] 0+ | - | - | -55.6k [-100.8k, -16.0k] **N** | -24.1k [-44.1k, -5.9k] **N** | - |
| ALLCE | d3 | -3.8k [-37.6k, +30.9k] 0 | - | - | -106.6k [-187.2k, -22.2k] **N** | -43.6k [-72.8k, -12.6k] **N** | +1.1k [-13.9k, +16.6k] P |
| ALLCE | lane | -8.6k [-25.4k, +7.4k] 0 | - | - | -48.4k [-77.2k, -17.1k] **N** | -23.5k [-37.1k, -4.7k] **N** | - |

**ev_st15**

| method | exit | ix | tbI | tbS | liveI | liveS | fy (d3 only) |
|---|---|---|---|---|---|---|---|
| EV | h30 | +5.7k [+1.0k, +10.9k] **S+** | - | - | +6.7k [+1.1k, +12.9k] **S+** | +0.6k [-0.4k, +1.5k] P | - |
| EV | cls | +8.2k [-1.3k, +18.9k] P | - | - | +5.0k [-9.0k, +24.0k] P | +2.6k [-0.4k, +5.9k] P | - |
| EV | d1 | +15.3k [+4.7k, +26.4k] **S+** | - | - | -14.0k [-40.9k, +3.7k] 0 | -2.9k [-10.5k, +3.8k] 0 | - |
| EV | d3 | +21.1k [+10.9k, +34.2k] **S+** | - | - | -0.5k [-18.1k, +10.5k] 0 | -1.3k [-9.1k, +6.3k] 0 | +4.2k [+0.0k, +8.0k] **S+** |
| EV | lane | +10.5k [+3.7k, +19.8k] **S+** | - | - | -5.2k [-19.2k, +6.6k] 0 | +2.7k [-1.2k, +7.1k] P | - |
| EV&DST | h30 | +5.1k [-4.1k, +14.1k] P | - | - | +12.4k [+1.1k, +24.5k] **S+** | +1.3k [+0.1k, +2.6k] **S+** | - |
| EV&DST | cls | +6.4k [-11.5k, +25.7k] P | - | - | +12.0k [-25.8k, +51.3k] P | +6.9k [+1.1k, +13.6k] **S+** | - |
| EV&DST | d1 | +10.3k [-11.9k, +34.1k] P | - | - | +23.5k [-32.4k, +74.6k] P | +6.7k [+0.9k, +11.1k] **S+** | - |
| EV&DST | d3 | +11.1k [-33.3k, +53.0k] P | - | - | +72.2k [-89.7k, +191.8k] P | +14.5k [+3.4k, +23.8k] **S+** | +2.7k [-11.6k, +16.2k] P |
| EV&DST | lane | -3.2k [-19.8k, +18.9k] 0 | - | - | -6.8k [-74.1k, +18.1k] 0 | +10.3k [+2.3k, +15.9k] **S+** | - |
| EV&T20 | h30 | +4.2k [-5.1k, +13.7k] P | - | - | +10.7k [-0.9k, +23.1k] P | +0.8k [-0.9k, +2.5k] P | - |
| EV&T20 | cls | -2.1k [-19.2k, +17.3k] 0 | - | - | +20.0k [-17.5k, +56.5k] P | +8.6k [+0.3k, +17.4k] **S+** | - |
| EV&T20 | d1 | +8.1k [-15.9k, +31.9k] P | - | - | +50.4k [+7.5k, +94.1k] **S+** | +12.8k [+4.3k, +20.4k] **S+** | - |
| EV&T20 | d3 | +12.7k [-20.1k, +49.6k] P | - | - | +125.5k [+14.5k, +219.6k] **S+** | +23.5k [+0.8k, +42.1k] **S+** | +4.9k [-9.4k, +17.0k] P |
| EV&T20 | lane | -8.2k [-22.5k, +12.4k] 0 | - | - | +35.7k [+3.2k, +54.6k] **S+** | +15.0k [+1.1k, +23.6k] **S+** | - |
| EV&T5 | h30 | +3.4k [-4.8k, +11.7k] P | - | - | +9.4k [-1.1k, +21.1k] P | +0.1k [-1.3k, +1.5k] 0+ | - |
| EV&T5 | cls | +1.8k [-15.2k, +21.1k] P | - | - | +16.3k [-16.0k, +50.8k] P | +2.2k [-3.4k, +7.9k] P | - |
| EV&T5 | d1 | +9.1k [-11.1k, +30.1k] P | - | - | +18.6k [-27.8k, +46.1k] P | +0.3k [-13.4k, +9.1k] 0+ | - |
| EV&T5 | d3 | +20.0k [-17.3k, +55.9k] P | - | - | -8.3k [-81.3k, +25.8k] 0 | -6.1k [-30.0k, +11.3k] 0 | +6.5k [-5.1k, +17.1k] P |
| EV&T5 | lane | -4.0k [-21.3k, +17.3k] 0 | - | - | +6.6k [-24.1k, +19.3k] P | +3.2k [-5.6k, +11.1k] P | - |
| ALLCE | h30 | -3.1k [-9.0k, +2.8k] 0 | - | - | -3.8k [-11.9k, +3.1k] 0 | -0.3k [-1.6k, +1.0k] 0 | - |
| ALLCE | cls | -8.1k [-21.8k, +6.4k] 0 | - | - | -15.9k [-39.8k, +9.1k] 0 | -7.9k [-17.2k, -0.4k] **N** | - |
| ALLCE | d1 | -8.6k [-30.1k, +13.3k] 0 | - | - | -63.6k [-111.2k, -15.5k] **N** | -24.8k [-40.2k, -8.7k] **N** | - |
| ALLCE | d3 | -4.7k [-44.6k, +34.7k] 0 | - | - | -120.8k [-223.1k, -20.2k] **N** | -44.9k [-73.8k, -15.9k] **N** | +2.2k [-13.5k, +18.5k] P |
| ALLCE | lane | -11.3k [-25.9k, +7.8k] 0 | - | - | -45.5k [-71.5k, -8.3k] **N** | -24.6k [-37.9k, -6.8k] **N** | - |

**ev_st30**

| method | exit | ix | tbI | tbS | liveI | liveS | fy (d3 only) |
|---|---|---|---|---|---|---|---|
| EV | h30 | +9.3k [+3.0k, +15.5k] **S+** | +2.9k [+0.1k, +5.8k] **S+** | +0.2k [-0.5k, +0.9k] 0+ | +3.0k [-1.1k, +6.6k] P | -1.0k [-2.0k, -0.0k] **N** | - |
| EV | cls | +12.6k [-4.0k, +30.6k] P | +4.5k [-4.3k, +13.2k] P | +1.4k [-0.7k, +3.6k] P | +2.6k [-22.5k, +35.8k] P | +4.0k [-1.4k, +10.4k] P | - |
| EV | d1 | +17.7k [-4.1k, +45.3k] P | -4.3k [-26.2k, +13.1k] 0 | +2.9k [-1.4k, +6.7k] P | -52.1k [-120.7k, -1.1k] **N** | -3.0k [-13.1k, +7.3k] 0 | - |
| EV | d3 | -10.2k [-29.8k, +12.1k] 0 | -0.4k [-17.8k, +16.8k] 0 | +2.2k [-3.7k, +8.5k] P | -64.3k [-130.7k, +17.6k] 0 | +2.7k [-10.0k, +14.3k] P | -0.6k [-9.9k, +7.4k] 0 |
| EV | lane | +1.0k [-12.8k, +20.9k] P | +4.8k [-6.2k, +16.1k] P | +2.8k [-1.4k, +7.4k] P | -8.6k [-36.4k, +9.6k] 0 | +6.0k [-1.2k, +11.7k] P | - |
| EV&DST | h30 | +14.3k [+4.0k, +24.6k] **S+** | +4.9k [+0.6k, +9.7k] **S+** | +0.3k [-0.7k, +1.3k] 0+ | - | -1.0k [-2.6k, +0.3k] 0 | - |
| EV&DST | cls | +24.2k [-6.0k, +55.0k] P | +9.7k [-1.2k, +20.8k] P | +1.6k [-1.0k, +4.3k] P | - | +8.1k [-0.3k, +17.9k] P | - |
| EV&DST | d1 | +22.8k [-8.6k, +51.4k] P | +8.9k [-4.8k, +22.8k] P | +2.0k [-2.7k, +6.6k] P | - | +5.4k [-0.6k, +12.0k] P | - |
| EV&DST | d3 | +2.1k [-33.1k, +43.9k] P | -0.4k [-27.9k, +33.2k] 0 | +0.0k [-10.5k, +10.2k] 0+ | - | +21.3k [+5.6k, +37.3k] **S+** | +2.1k [-16.6k, +17.6k] P |
| EV&DST | lane | +4.6k [-17.1k, +28.6k] P | +2.7k [-11.7k, +22.7k] P | +0.2k [-5.7k, +6.5k] 0+ | - | +12.1k [+2.1k, +19.1k] **S+** | - |
| EV&T20 | h30 | +13.5k [+3.5k, +23.7k] **S+** | +3.7k [-0.1k, +7.8k] P | +0.6k [-0.2k, +1.5k] P | +1.7k [-2.2k, +5.6k] P | -1.7k [-4.0k, +0.1k] 0 | - |
| EV&T20 | cls | +13.9k [-13.6k, +42.7k] P | +4.5k [-7.3k, +15.4k] P | +2.1k [-0.5k, +4.7k] P | +24.5k [-10.9k, +60.8k] P | +12.3k [+0.4k, +24.4k] **S+** | - |
| EV&T20 | d1 | +27.4k [-9.6k, +63.7k] P | -7.0k [-38.8k, +17.0k] 0 | +2.5k [-2.0k, +7.3k] P | - | +8.4k [-0.5k, +18.9k] P | - |
| EV&T20 | d3 | +3.7k [-26.0k, +39.3k] P | -2.7k [-28.9k, +31.7k] 0 | -1.3k [-11.2k, +7.9k] 0 | - | +22.9k [-2.7k, +50.5k] P | +0.3k [-19.6k, +16.0k] 0+ |
| EV&T20 | lane | +3.5k [-17.8k, +29.4k] P | +0.1k [-11.8k, +17.6k] 0+ | +0.7k [-5.0k, +6.7k] P | - | +12.5k [-2.3k, +26.2k] P | - |
| EV&T5 | h30 | +8.1k [-3.4k, +19.4k] P | +2.3k [-1.2k, +5.9k] P | +0.1k [-0.9k, +1.0k] 0+ | +2.8k [-0.7k, +6.5k] P | -2.1k [-3.9k, -0.5k] **N** | - |
| EV&T5 | cls | +12.2k [-18.9k, +44.2k] P | +6.0k [-8.5k, +20.2k] P | +0.6k [-2.2k, +3.3k] P | +24.7k [-8.1k, +59.4k] P | +2.8k [-4.1k, +9.0k] P | - |
| EV&T5 | d1 | +4.4k [-30.7k, +39.2k] P | -6.8k [-49.0k, +21.4k] 0 | +0.6k [-3.9k, +4.8k] P | - | -10.0k [-28.8k, +1.7k] 0 | - |
| EV&T5 | d3 | -3.5k [-45.4k, +35.1k] 0 | +6.4k [-16.3k, +22.6k] P | -1.4k [-7.7k, +5.6k] 0 | - | -11.6k [-43.2k, +6.0k] 0 | +8.0k [-6.6k, +24.5k] P |
| EV&T5 | lane | -11.4k [-32.6k, +7.8k] 0 | +6.2k [-9.0k, +18.6k] P | -1.0k [-5.6k, +3.9k] 0 | - | -0.3k [-9.4k, +6.0k] 0 | - |
| ALLCE | h30 | +6.5k [-0.0k, +13.3k] P | -0.7k [-3.7k, +2.3k] 0 | +0.6k [-0.2k, +1.4k] P | -0.0k [-5.2k, +4.8k] 0 | +0.2k [-1.2k, +1.8k] 0+ | - |
| ALLCE | cls | +1.7k [-15.9k, +20.6k] P | -5.0k [-14.5k, +3.5k] 0 | -1.7k [-4.8k, +1.6k] 0 | -28.1k [-51.0k, -6.7k] **N** | -8.1k [-16.6k, -0.5k] **N** | - |
| ALLCE | d1 | +0.7k [-30.1k, +28.8k] P | -13.0k [-37.5k, +8.3k] 0 | -1.7k [-8.6k, +5.1k] 0 | -73.4k [-136.0k, -8.1k] **N** | -20.6k [-35.4k, -4.9k] **N** | - |
| ALLCE | d3 | +10.6k [-33.7k, +55.5k] P | +10.6k [-15.6k, +38.1k] P | +5.1k [-7.3k, +17.1k] P | -163.8k [-238.4k, -21.0k] **N** | -42.4k [-72.0k, -12.7k] **N** | +4.2k [-10.9k, +21.9k] P |
| ALLCE | lane | +4.9k [-18.1k, +25.6k] P | +1.7k [-12.8k, +19.4k] P | +3.3k [-4.7k, +11.3k] P | -63.9k [-77.4k, -29.8k] **N** | -22.0k [-35.3k, -4.3k] **N** | - |

**ev_piv**

| method | exit | ix | tbI | tbS | liveI | liveS | fy (d3 only) |
|---|---|---|---|---|---|---|---|
| EV | h30 | +1.2k [-2.8k, +5.5k] P | +0.7k [-1.0k, +2.4k] P | +0.7k [+0.1k, +1.2k] **S+** | +8.4k [+3.7k, +13.9k] **S+** | +0.5k [-0.7k, +1.6k] 0+ | - |
| EV | cls | -0.0k [-7.6k, +8.9k] 0 | +1.7k [-3.1k, +6.8k] P | +1.0k [-0.3k, +2.3k] P | +0.3k [-16.6k, +21.0k] 0+ | +1.5k [-2.8k, +6.5k] P | - |
| EV | d1 | +2.1k [-10.7k, +15.0k] P | -0.3k [-7.9k, +7.9k] 0 | +1.1k [-1.1k, +3.1k] P | -5.0k [-30.9k, +27.2k] 0 | +1.9k [-4.6k, +8.8k] P | - |
| EV | d3 | -7.3k [-21.2k, +7.4k] 0 | -7.9k [-17.2k, +1.2k] 0 | -0.5k [-3.4k, +2.5k] 0 | -5.3k [-49.5k, +15.2k] 0 | -0.1k [-8.3k, +5.0k] 0 | +2.6k [-3.1k, +8.1k] P |
| EV | lane | +1.9k [-7.2k, +12.4k] P | -2.7k [-9.8k, +4.4k] 0 | +0.4k [-1.5k, +2.4k] 0+ | -1.1k [-18.6k, +9.0k] 0 | +4.0k [+0.0k, +6.1k] **S+** | - |
| EV&DST | h30 | +0.4k [-6.5k, +7.7k] 0+ | +1.2k [-1.3k, +3.5k] P | +0.6k [-0.1k, +1.2k] P | +10.9k [+3.5k, +20.1k] **S+** | +0.9k [-0.4k, +2.2k] P | - |
| EV&DST | cls | -3.6k [-20.3k, +13.0k] 0 | +4.7k [-4.0k, +13.8k] P | +1.1k [-0.5k, +2.8k] P | +6.5k [-29.9k, +43.8k] P | +4.9k [-2.3k, +13.3k] P | - |
| EV&DST | d1 | -13.4k [-35.9k, +8.5k] 0 | +1.8k [-9.0k, +13.4k] P | +0.5k [-2.2k, +3.1k] 0+ | +32.4k [-19.9k, +93.4k] P | +10.3k [+0.5k, +22.2k] **S+** | - |
| EV&DST | d3 | -28.7k [-61.0k, +5.4k] 0 | -14.4k [-31.9k, +7.0k] 0 | -2.7k [-8.4k, +2.7k] 0 | +73.6k [-56.6k, +186.7k] P | +13.4k [-0.5k, +23.4k] P | -0.8k [-15.1k, +13.6k] 0 |
| EV&DST | lane | -14.9k [-33.1k, +2.9k] 0 | -9.4k [-21.1k, +5.6k] 0 | -1.1k [-4.7k, +2.3k] 0 | +5.1k [-61.3k, +39.8k] P | +8.9k [-0.3k, +14.1k] P | - |
| EV&T20 | h30 | -0.0k [-6.7k, +6.8k] 0 | +1.3k [-1.0k, +3.7k] P | +0.8k [+0.1k, +1.4k] **S+** | +8.8k [+2.3k, +16.8k] **S+** | +0.6k [-1.2k, +2.3k] P | - |
| EV&T20 | cls | -8.5k [-23.4k, +7.1k] 0 | +3.6k [-3.1k, +10.7k] P | +1.0k [-0.6k, +2.7k] P | +8.8k [-22.3k, +39.7k] P | +6.1k [-3.6k, +17.0k] P | - |
| EV&T20 | d1 | -6.9k [-27.6k, +16.5k] 0 | +0.1k [-10.4k, +10.9k] 0+ | -0.1k [-3.0k, +2.8k] 0 | +42.1k [-2.6k, +92.1k] P | +14.7k [-1.0k, +32.6k] P | - |
| EV&T20 | d3 | -15.5k [-41.2k, +12.3k] 0 | -13.9k [-31.4k, +6.2k] 0 | -4.3k [-10.3k, +1.7k] 0 | +89.4k [-8.1k, +180.6k] P | +16.9k [-9.0k, +33.8k] P | +3.0k [-10.7k, +15.6k] P |
| EV&T20 | lane | -6.6k [-23.3k, +12.1k] 0 | -10.0k [-21.9k, +3.8k] 0 | -1.9k [-5.3k, +1.6k] 0 | +34.0k [-11.3k, +64.2k] P | +11.5k [-3.6k, +19.9k] P | - |
| EV&T5 | h30 | -1.4k [-7.8k, +4.8k] 0 | +0.5k [-1.8k, +2.8k] P | +0.6k [-0.1k, +1.3k] P | +8.5k [+2.3k, +16.1k] **S+** | +0.7k [-0.8k, +2.0k] P | - |
| EV&T5 | cls | -8.7k [-22.9k, +6.2k] 0 | +0.7k [-7.4k, +9.5k] P | +0.1k [-2.1k, +2.3k] 0+ | +9.1k [-20.7k, +38.1k] P | +0.5k [-6.2k, +7.6k] P | - |
| EV&T5 | d1 | -11.9k [-33.6k, +9.3k] 0 | -8.3k [-19.9k, +3.6k] 0 | -0.9k [-4.3k, +2.6k] 0 | +17.0k [-40.7k, +73.7k] P | +2.6k [-12.1k, +14.6k] P | - |
| EV&T5 | d3 | -12.2k [-38.7k, +13.8k] 0 | -13.0k [-26.8k, +1.6k] 0 | -4.0k [-8.8k, +0.9k] 0 | -19.4k [-131.6k, +39.0k] 0 | -8.7k [-33.7k, +9.4k] 0 | +4.7k [-6.7k, +15.7k] P |
| EV&T5 | lane | -8.7k [-26.1k, +9.4k] 0 | -13.6k [-26.1k, -1.2k] **N** | -2.8k [-5.9k, +0.4k] 0 | +8.2k [-30.7k, +37.9k] P | +1.8k [-7.8k, +9.0k] P | - |
| ALLCE | h30 | -0.1k [-5.6k, +5.0k] 0 | +0.3k [-1.8k, +2.4k] 0+ | +0.0k [-0.9k, +1.0k] 0+ | -2.7k [-9.1k, +2.3k] 0 | -0.5k [-2.8k, +1.6k] 0 | - |
| ALLCE | cls | -3.9k [-16.7k, +8.7k] 0 | -4.3k [-12.1k, +3.3k] 0 | -2.4k [-5.9k, +1.1k] 0 | -13.6k [-36.7k, +7.3k] 0 | -7.3k [-17.6k, +1.4k] 0 | - |
| ALLCE | d1 | -10.0k [-30.5k, +9.9k] 0 | -2.0k [-14.7k, +10.5k] 0 | -2.3k [-9.3k, +4.2k] 0 | -58.3k [-106.6k, -16.6k] **N** | -23.9k [-44.6k, -5.1k] **N** | - |
| ALLCE | d3 | -15.4k [-45.6k, +15.6k] 0 | +1.8k [-18.1k, +23.1k] P | +0.1k [-12.1k, +12.3k] 0+ | -120.8k [-226.3k, -23.0k] **N** | -44.3k [-76.6k, -12.0k] **N** | -0.2k [-14.7k, +15.4k] 0 |
| ALLCE | lane | -15.0k [-31.1k, +1.9k] 0 | -0.8k [-13.6k, +13.9k] 0 | +1.5k [-6.6k, +9.5k] P | -50.1k [-77.0k, -16.8k] **N** | -21.8k [-35.7k, -5.0k] **N** | - |


### 4.3 Payoff profile

Avg right / avg wrong = mean side value when the chosen side was better / worse. Top-5% / top-10% = sum of the best 5% /
10% of trades divided by the total (n/a when the total ≤ 0; > 1 means the remaining trades net negative).


| panel | exit | method | n | CE share | mean | median | hit | avg right | avg wrong | payoff | skew | top-5% sum / total | top-10% sum / total | capture of oracle | CE-month mean (hit) | PE-month mean (hit) | beyond tilt |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ix | cls | ST5 | 900 | 0.49 | +3.9k | +4.7k | 0.559 | +43.9k | +46.7k | 0.94 | -0.1 | 1.94 | 3.08 | +0.087 | +7.4k (0.58) | +1.8k (0.54) | +3.9k |
| ix | cls | ST15 | 900 | 0.48 | +2.1k | +4.2k | 0.543 | +43.5k | +47.0k | 0.92 | +0.1 | 3.57 | 5.63 | +0.047 | +1.1k (0.55) | +2.7k (0.54) | +2.1k |
| ix | cls | DST | 900 | 0.73 | +0.2k | -0.0k | 0.500 | +45.3k | +44.9k | 1.01 | +0.5 | 37.78 | 57.77 | +0.005 | +4.7k (0.51) | -2.5k (0.49) | +1.1k |
| ix | cls | PIVP | 900 | 0.49 | +2.2k | +3.9k | 0.538 | +44.0k | +46.4k | 0.95 | +0.1 | 3.54 | 5.54 | +0.048 | +2.2k (0.53) | +2.2k (0.54) | +2.1k |
| ix | cls | T5 | 900 | 0.52 | -5.5k | -2.0k | 0.480 | +41.3k | +48.7k | 0.85 | -0.1 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.122 | -8.6k (0.44) | -3.6k (0.50) | -5.4k |
| ix | cls | T20 | 900 | 0.70 | -4.5k | -2.6k | 0.467 | +43.5k | +46.5k | 0.93 | -0.1 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.101 | -1.2k (0.47) | -6.6k (0.46) | -3.8k |
| ix | cls | SER | 900 | 0.49 | -2.8k | -0.9k | 0.490 | +43.1k | +47.0k | 0.92 | -0.0 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.063 | -1.3k (0.51) | -3.8k (0.48) | -2.9k |
| ix | cls | VIX2 | 900 | 0.32 | +2.2k | +1.3k | 0.512 | +46.2k | +43.9k | 1.05 | +0.1 | 3.67 | 5.77 | +0.050 | +0.3k (0.48) | +3.4k (0.53) | +1.5k |
| ix | cls | VIXHI | 224 | 1.00 | -0.6k | +1.0k | 0.509 | +38.0k | +40.6k | 0.94 | +0.1 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.015 | +9.3k (0.51) | -4.1k (0.51) | +1.4k |
| ix | cls | STRESS | 142 | 1.00 | -7.6k | +0.1k | 0.500 | +35.6k | +50.9k | 0.70 | -0.5 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.176 | +8.3k (0.63) | -14.5k (0.44) | -5.6k |
| ix | cls | MACDr | 856 | 0.51 | -3.7k | -1.0k | 0.487 | +41.9k | +47.1k | 0.89 | -0.3 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.084 | -2.8k (0.50) | -4.3k (0.48) | -3.7k |
| ix | cls | MODEL | 299 | 0.42 | -1.5k | -1.1k | 0.472 | +21.7k | +22.3k | 0.98 | +0.3 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.069 | -3.9k (0.43) | -0.0k (0.50) | -1.8k |
| ix | cls | ALLCE | 900 | 1.00 | -2.0k | -0.2k | 0.496 | +43.5k | +46.7k | 0.93 | -0.1 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.044 | +5.0k (0.53) | -6.3k (0.47) | +0.0k |
| ix | d3 | ST5 | 867 | 0.48 | -0.1k | -3.6k | 0.495 | +123.8k | +121.6k | 1.02 | -0.0 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.001 | +4.8k (0.52) | -4.0k (0.47) | -0.5k |
| ix | d3 | ST15 | 867 | 0.48 | -2.3k | -0.7k | 0.498 | +120.8k | +124.6k | 0.97 | +0.0 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.019 | -3.0k (0.51) | -1.8k (0.49) | -2.7k |
| ix | d3 | DST | 867 | 0.73 | -22.4k | -15.5k | 0.474 | +105.8k | +137.9k | 0.77 | -0.1 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.183 | +18.0k (0.60) | -53.6k (0.38) | -17.5k |
| ix | d3 | PIVP | 867 | 0.48 | -12.8k | -15.9k | 0.463 | +118.8k | +126.0k | 0.94 | +0.1 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.104 | -7.5k (0.50) | -16.8k (0.44) | -13.1k |
| ix | d3 | T5 | 867 | 0.52 | +0.8k | +5.9k | 0.516 | +119.8k | +125.8k | 0.95 | +0.2 | 21.52 | 34.70 | +0.007 | -8.4k (0.52) | +8.0k (0.51) | +1.3k |
| ix | d3 | T20 | 867 | 0.70 | -9.6k | +5.1k | 0.510 | +110.9k | +135.0k | 0.82 | +0.0 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.079 | +17.2k (0.61) | -30.4k (0.43) | -5.4k |
| ix | d3 | SER | 867 | 0.49 | -8.4k | -8.9k | 0.487 | +117.4k | +127.7k | 0.92 | +0.0 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.069 | -9.7k (0.48) | -7.4k (0.49) | -8.7k |
| ix | d3 | VIX2 | 867 | 0.31 | +23.2k | +18.1k | 0.547 | +133.4k | +109.8k | 1.22 | +0.4 | 0.82 | 1.40 | +0.189 | -11.5k (0.50) | +50.0k (0.58) | +19.0k |
| ix | d3 | VIXHI | 210 | 1.00 | +28.1k | +28.0k | 0.638 | +93.7k | +87.5k | 1.07 | +0.3 | 0.48 | 0.89 | +0.308 | +33.7k (0.70) | +18.7k (0.54) | +38.9k |
| ix | d3 | STRESS | 130 | 1.00 | +36.0k | +44.2k | 0.677 | +99.5k | +97.1k | 1.02 | -0.7 | 0.38 | 0.69 | +0.365 | +65.1k (0.80) | -3.8k (0.51) | +46.8k |
| ix | d3 | MACDr | 824 | 0.51 | -16.1k | -20.6k | 0.456 | +119.0k | +129.6k | 0.92 | +0.1 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.129 | -5.2k (0.52) | -24.5k (0.41) | -15.8k |
| ix | d3 | MODEL | 292 | 0.42 | +5.5k | +5.4k | 0.507 | +125.6k | +118.0k | 1.06 | -0.2 | 3.05 | 5.00 | +0.045 | -0.5k (0.52) | +10.2k (0.50) | +3.7k |
| ix | d3 | ALLCE | 867 | 1.00 | -10.8k | +6.5k | 0.517 | +108.3k | +138.1k | 0.78 | -0.5 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.088 | +39.5k (0.66) | -49.7k (0.41) | +0.0k |
| tbI | cls | DST | 2176 | 0.69 | +0.9k | -0.4k | 0.496 | +27.1k | +24.8k | 1.09 | +0.6 | 5.97 | 8.71 | +0.035 | +0.9k (0.52) | +0.9k (0.48) | +2.1k |
| tbI | cls | PIVP | 2176 | 0.50 | +2.4k | +2.0k | 0.536 | +26.5k | +25.3k | 1.05 | +1.0 | 2.36 | 3.35 | +0.094 | +2.7k (0.54) | +2.3k (0.54) | +2.4k |
| tbI | cls | T5 | 2176 | 0.52 | -0.7k | -0.3k | 0.497 | +25.4k | +26.5k | 0.96 | +0.5 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.026 | -1.8k (0.49) | -0.1k (0.50) | -0.5k |
| tbI | cls | T20 | 2176 | 0.66 | -0.6k | -0.6k | 0.491 | +25.8k | +26.0k | 0.99 | +0.1 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.022 | +0.7k (0.51) | -1.1k (0.48) | +0.4k |
| tbI | cls | SER | 2176 | 0.59 | -1.1k | +1.8k | 0.530 | +23.4k | +28.8k | 0.81 | -0.4 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.043 | -0.9k (0.53) | -1.2k (0.53) | -0.6k |
| tbI | cls | VIX2 | 2176 | 0.26 | +1.9k | +0.1k | 0.501 | +27.8k | +24.1k | 1.15 | -0.0 | 2.79 | 4.19 | +0.073 | -0.8k (0.49) | +3.2k (0.51) | +0.4k |
| tbI | cls | VIXHI | 364 | 1.00 | -0.5k | +1.6k | 0.533 | +25.3k | +29.9k | 0.85 | -1.4 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.018 | +1.6k (0.55) | -3.1k (0.52) | +2.6k |
| tbI | cls | STRESS | 316 | 1.00 | -10.8k | -2.9k | 0.437 | +17.7k | +33.0k | 0.54 | -1.5 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.411 | -6.5k (0.46) | -13.3k (0.42) | -7.7k |
| tbI | cls | MACDr | 1976 | 0.51 | -0.6k | +0.9k | 0.518 | +24.7k | +27.8k | 0.89 | -0.3 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.023 | -1.5k (0.52) | -0.2k (0.52) | -0.5k |
| tbI | cls | MODEL | 538 | 0.43 | +0.6k | +0.6k | 0.519 | +11.9k | +11.5k | 1.03 | +0.3 | 3.55 | 5.32 | +0.054 | -2.0k (0.48) | +1.5k (0.53) | +0.2k |
| tbI | cls | ALLCE | 2176 | 1.00 | -3.1k | +0.9k | 0.518 | +22.1k | +30.1k | 0.73 | -1.8 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.119 | +3.2k (0.57) | -6.0k (0.49) | +0.0k |
| tbI | d3 | DST | 2043 | 0.70 | -8.3k | -14.0k | 0.455 | +82.9k | +84.5k | 0.98 | +0.5 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.099 | +2.9k (0.51) | -23.1k (0.38) | -7.1k |
| tbI | d3 | PIVP | 2043 | 0.50 | -11.3k | -8.2k | 0.470 | +77.2k | +89.7k | 0.86 | -0.4 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.135 | -5.8k (0.50) | -18.5k (0.43) | -11.3k |
| tbI | d3 | T5 | 2043 | 0.53 | -3.9k | +1.8k | 0.506 | +78.9k | +88.8k | 0.89 | -0.4 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.046 | -0.3k (0.53) | -8.6k (0.47) | -3.7k |
| tbI | d3 | T20 | 2043 | 0.66 | -10.0k | -10.4k | 0.464 | +79.5k | +87.5k | 0.91 | +0.1 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.120 | +6.1k (0.53) | -31.3k (0.37) | -9.0k |
| tbI | d3 | SER | 2043 | 0.59 | -3.3k | +3.3k | 0.511 | +78.8k | +89.0k | 0.89 | -0.0 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.039 | +6.3k (0.55) | -15.8k (0.45) | -2.7k |
| tbI | d3 | VIX2 | 2043 | 0.25 | +13.1k | +4.7k | 0.518 | +93.5k | +73.3k | 1.28 | +0.7 | 1.10 | 1.76 | +0.157 | -10.4k (0.43) | +44.2k (0.64) | +11.6k |
| tbI | d3 | VIXHI | 326 | 1.00 | +41.7k | +41.6k | 0.709 | +82.9k | +58.5k | 1.42 | -0.1 | 0.25 | 0.46 | +0.550 | +43.2k (0.72) | +36.0k (0.68) | +44.8k |
| tbI | d3 | STRESS | 281 | 1.00 | +21.0k | +26.0k | 0.651 | +78.3k | +86.0k | 0.91 | -0.7 | 0.50 | 0.90 | +0.259 | +53.6k (0.80) | -20.2k (0.47) | +24.1k |
| tbI | d3 | MACDr | 1855 | 0.51 | -13.8k | -10.5k | 0.464 | +77.0k | +92.4k | 0.83 | -0.5 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.162 | -6.5k (0.51) | -23.4k (0.41) | -13.7k |
| tbI | d3 | MODEL | 522 | 0.43 | +3.6k | +3.9k | 0.513 | +83.6k | +80.8k | 1.03 | +0.3 | 3.47 | 5.51 | +0.044 | +1.8k (0.51) | +6.3k (0.52) | +3.2k |
| tbI | d3 | ALLCE | 2043 | 1.00 | -3.1k | +11.6k | 0.547 | +73.7k | +95.9k | 0.77 | -1.0 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.037 | +34.0k (0.69) | -52.0k (0.36) | +0.0k |
| tbS | cls | DST | 69863 | 0.51 | -0.1k | -0.0k | 0.499 | +23.8k | +23.8k | 1.00 | -1.6 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.003 | -0.4k (0.50) | +0.1k (0.50) | -0.0k |
| tbS | cls | PIVP | 69863 | 0.48 | +0.9k | +0.4k | 0.508 | +24.3k | +23.3k | 1.04 | -1.5 | 5.05 | 7.49 | +0.039 | +0.4k (0.50) | +1.3k (0.51) | +0.9k |
| tbS | cls | T5 | 69770 | 0.49 | -0.3k | -0.3k | 0.492 | +23.9k | +23.7k | 1.01 | -1.0 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.012 | -0.7k (0.49) | +0.0k (0.50) | -0.3k |
| tbS | cls | T20 | 69807 | 0.50 | -0.2k | -0.3k | 0.494 | +23.8k | +23.7k | 1.00 | -1.4 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.010 | -0.6k (0.49) | +0.0k (0.49) | -0.2k |
| tbS | cls | SER | 60683 | 0.58 | -0.5k | +0.3k | 0.506 | +23.2k | +24.9k | 0.93 | -2.0 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.022 | +0.1k (0.51) | -0.9k (0.50) | -0.3k |
| tbS | cls | VIX2 | 69863 | 0.17 | +2.0k | +0.9k | 0.520 | +24.8k | +22.6k | 1.10 | -1.1 | 2.37 | 3.53 | +0.086 | -2.7k (0.48) | +5.0k (0.55) | +0.9k |
| tbS | cls | VIXHI | 7310 | 1.00 | +1.1k | +1.1k | 0.528 | +22.0k | +22.3k | 0.98 | -0.7 | 3.79 | 5.79 | +0.048 | +7.3k (0.60) | -3.9k (0.47) | +2.8k |
| tbS | cls | STRESS | 10517 | 1.00 | -1.4k | +0.3k | 0.506 | +21.6k | +24.9k | 0.87 | -2.9 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.060 | +5.8k (0.57) | -6.3k (0.46) | +0.3k |
| tbS | cls | MACDr | 63362 | 0.49 | -0.2k | -0.0k | 0.499 | +24.3k | +24.5k | 0.99 | -1.4 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.008 | -0.5k (0.50) | +0.0k (0.50) | -0.2k |
| tbS | cls | ALLCE | 69863 | 1.00 | -1.7k | -0.3k | 0.494 | +22.3k | +25.2k | 0.88 | -0.1 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.073 | +5.7k (0.57) | -6.4k (0.45) | +0.0k |
| tbS | d3 | DST | 68312 | 0.51 | -2.1k | -0.6k | 0.497 | +80.6k | +83.9k | 0.96 | +3.0 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.026 | -0.7k (0.50) | -3.5k (0.49) | -2.1k |
| tbS | d3 | PIVP | 68312 | 0.48 | -0.5k | -0.4k | 0.498 | +82.1k | +82.4k | 1.00 | -1.4 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.006 | -1.7k (0.50) | +0.6k (0.50) | -0.5k |
| tbS | d3 | T5 | 68223 | 0.49 | -4.0k | -2.2k | 0.490 | +79.8k | +84.6k | 0.94 | +0.9 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.049 | -3.6k (0.50) | -4.4k (0.48) | -4.0k |
| tbS | d3 | T20 | 68255 | 0.51 | -4.6k | -1.5k | 0.493 | +78.7k | +85.7k | 0.92 | -3.5 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.057 | -2.3k (0.50) | -6.9k (0.48) | -4.6k |
| tbS | d3 | SER | 59304 | 0.58 | -2.8k | +1.3k | 0.506 | +79.7k | +87.4k | 0.91 | -4.0 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.034 | +6.5k (0.55) | -11.6k (0.47) | -2.8k |
| tbS | d3 | VIX2 | 68312 | 0.16 | +5.9k | +2.6k | 0.512 | +86.0k | +78.3k | 1.10 | +3.4 | 2.44 | 3.79 | +0.071 | -24.4k (0.41) | +34.9k (0.61) | +5.9k |
| tbS | d3 | VIXHI | 6940 | 1.00 | +29.9k | +19.2k | 0.607 | +83.3k | +52.6k | 1.58 | +0.6 | 0.45 | 0.74 | +0.420 | +63.6k (0.75) | -3.0k (0.47) | +29.8k |
| tbS | d3 | STRESS | 10290 | 1.00 | +0.4k | +2.7k | 0.512 | +83.3k | +86.6k | 0.96 | +0.2 | 36.53 | 56.54 | +0.004 | +41.9k (0.65) | -42.0k (0.37) | +0.3k |
| tbS | d3 | MACDr | 61815 | 0.49 | -4.9k | -2.4k | 0.489 | +81.8k | +87.8k | 0.93 | -3.4 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.057 | -4.5k (0.50) | -5.2k (0.48) | -4.9k |
| tbS | d3 | ALLCE | 68312 | 1.00 | +0.0k | +2.0k | 0.509 | +80.8k | +83.7k | 0.97 | -3.3 | 314.18 | 498.65 | +0.000 | +42.0k (0.66) | -40.1k (0.37) | +0.0k |
| liveS | cls | ST5 | 16509 | 0.48 | +1.7k | -0.2k | 0.495 | +27.5k | +23.6k | 1.16 | +0.6 | 3.18 | 4.72 | +0.066 | +0.8k (0.49) | +1.9k (0.50) | +1.5k |
| liveS | cls | ST15 | 16509 | 0.44 | +1.8k | -0.2k | 0.495 | +27.6k | +23.5k | 1.17 | +0.5 | 2.94 | 4.38 | +0.072 | -0.0k (0.48) | +2.3k (0.50) | +1.1k |
| liveS | cls | DST | 16509 | 0.33 | +2.7k | +1.6k | 0.534 | +26.4k | +24.6k | 1.07 | +0.3 | 1.97 | 2.92 | +0.105 | -1.6k (0.48) | +3.8k (0.55) | +0.5k |
| liveS | cls | PIVP | 16509 | 0.40 | +1.4k | -0.4k | 0.491 | +27.4k | +23.7k | 1.15 | +0.5 | 3.87 | 5.73 | +0.054 | -0.2k (0.48) | +1.8k (0.49) | +0.1k |
| liveS | cls | T5 | 16488 | 0.34 | -0.1k | +0.1k | 0.503 | +25.2k | +25.8k | 0.98 | +0.1 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.005 | -1.7k (0.48) | +0.3k (0.51) | -2.1k |
| liveS | cls | T20 | 13007 | 0.23 | +4.5k | +2.8k | 0.552 | +29.5k | +26.3k | 1.12 | +0.7 | 1.29 | 1.94 | +0.161 | -3.7k (0.46) | +5.6k (0.57) | +1.2k |
| liveS | cls | SER | 9731 | 0.22 | +3.6k | +2.1k | 0.539 | +28.4k | +25.5k | 1.11 | +0.6 | 1.55 | 2.35 | +0.132 | -1.9k (0.48) | +4.6k (0.55) | +0.1k |
| liveS | cls | VIX2 | 16509 | 0.30 | +0.7k | +1.2k | 0.524 | +25.0k | +26.1k | 0.96 | -0.6 | 6.68 | 9.92 | +0.029 | -2.3k (0.48) | +1.5k (0.54) | -1.7k |
| liveS | cls | VIXHI | 1690 | 1.00 | -12.3k | -2.5k | 0.456 | +22.5k | +41.4k | 0.54 | -2.0 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.374 | +8.9k (0.62) | -15.5k (0.43) | -6.1k |
| liveS | cls | MACDr | 13783 | 0.37 | -0.0k | -0.5k | 0.490 | +27.4k | +26.5k | 1.04 | +0.3 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.001 | -1.8k (0.47) | +0.4k (0.49) | -1.6k |
| liveS | cls | ALLCE | 16509 | 1.00 | -6.1k | -3.8k | 0.418 | +23.2k | +27.2k | 0.85 | -1.0 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.239 | +5.7k (0.55) | -9.1k (0.39) | +0.0k |
| liveS | d3 | ST5 | 14566 | 0.48 | -3.8k | +0.8k | 0.503 | +94.5k | +103.4k | 0.91 | -1.9 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.039 | -3.1k (0.50) | -4.0k (0.50) | -5.3k |
| liveS | d3 | ST15 | 14566 | 0.45 | +0.9k | +6.2k | 0.525 | +95.1k | +103.2k | 0.92 | -1.1 | 17.66 | 27.57 | +0.009 | -5.9k (0.50) | +2.3k (0.53) | -3.8k |
| liveS | d3 | DST | 14566 | 0.34 | +15.6k | +15.8k | 0.562 | +101.9k | +95.2k | 1.07 | +1.8 | 1.13 | 1.72 | +0.158 | -17.2k (0.45) | +22.6k (0.59) | +1.4k |
| liveS | d3 | PIVP | 14566 | 0.41 | +1.0k | +8.2k | 0.531 | +94.0k | +104.6k | 0.90 | -2.3 | 15.34 | 24.10 | +0.010 | -9.8k (0.48) | +3.2k (0.54) | -7.0k |
| liveS | d3 | T5 | 14545 | 0.37 | -8.5k | +1.5k | 0.505 | +89.5k | +108.7k | 0.82 | -1.7 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.086 | -14.9k (0.44) | -7.2k (0.52) | -20.2k |
| liveS | d3 | T20 | 11067 | 0.25 | +24.4k | +26.4k | 0.595 | +112.4k | +105.1k | 1.07 | -1.3 | 0.80 | 1.22 | +0.223 | -18.9k (0.43) | +31.2k (0.62) | +2.3k |
| liveS | d3 | SER | 8075 | 0.19 | +27.6k | +28.3k | 0.604 | +111.7k | +100.5k | 1.11 | -0.1 | 0.72 | 1.10 | +0.257 | -28.1k (0.42) | +37.0k (0.63) | +0.3k |
| liveS | d3 | VIX2 | 14566 | 0.22 | +41.1k | +31.2k | 0.629 | +111.3k | +78.0k | 1.43 | +3.1 | 0.50 | 0.76 | +0.415 | -10.6k (0.47) | +52.0k (0.66) | +16.2k |
| liveS | d3 | VIXHI | 564 | 1.00 | -90.1k | -82.1k | 0.156 | +42.3k | +114.6k | 0.37 | -1.8 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.872 | -72.7k (0.21) | -92.5k (0.15) | -46.0k |
| liveS | d3 | MACDr | 12188 | 0.40 | -6.0k | +5.7k | 0.520 | +96.3k | +117.0k | 0.82 | -2.7 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.057 | -19.8k (0.44) | -3.3k (0.54) | -14.9k |
| liveS | d3 | ALLCE | 14566 | 1.00 | -44.1k | -37.3k | 0.342 | +80.3k | +108.6k | 0.74 | -3.1 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.446 | +30.9k (0.58) | -59.9k (0.29) | +0.0k |
| liveI | d3 | ST5 | 222 | 0.46 | -0.2k | +17.9k | 0.563 | +128.1k | +165.6k | 0.77 | -1.1 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.001 | - | +0.3k (0.57) | -8.9k |
| liveI | d3 | ST15 | 222 | 0.45 | -7.9k | +2.2k | 0.509 | +134.2k | +155.2k | 0.86 | -1.2 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.055 | - | -7.5k (0.52) | -18.7k |
| liveI | d3 | DST | 222 | 0.44 | +76.5k | +2.4k | 0.514 | +215.2k | +69.8k | 3.08 | +2.4 | 0.58 | 0.85 | +0.530 | - | +77.8k (0.51) | +62.5k |
| liveI | d3 | PIVP | 222 | 0.42 | -9.3k | +14.2k | 0.563 | +120.0k | +176.0k | 0.68 | -1.2 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.064 | - | -9.0k (0.57) | -28.8k |
| liveI | d3 | T5 | 222 | 0.26 | -19.9k | +4.9k | 0.518 | +120.2k | +170.5k | 0.71 | -2.3 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.138 | - | -19.9k (0.52) | -78.4k |
| liveI | d3 | T20 | 222 | 0.18 | +111.7k | +44.4k | 0.689 | +185.9k | +52.7k | 3.52 | +2.5 | 0.40 | 0.59 | +0.773 | - | +114.8k (0.70) | +35.9k |
| liveI | d3 | SER | 222 | 0.32 | +49.7k | +32.8k | 0.680 | +142.7k | +148.2k | 0.96 | -0.7 | 0.66 | 0.98 | +0.344 | - | +50.3k (0.68) | +6.4k |
| liveI | d3 | VIX2 | 222 | 0.24 | +99.2k | +43.0k | 0.685 | +177.9k | +71.9k | 2.48 | +2.4 | 0.45 | 0.66 | +0.686 | - | +101.9k (0.69) | +37.4k |
| liveI | d3 | STRESS | 42 | 1.00 | -93.0k | -72.2k | 0.286 | +34.2k | +143.9k | 0.24 | -0.7 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.826 | - | -93.0k (0.29) | +27.2k |
| liveI | d3 | MACDr | 158 | 0.37 | -52.2k | +2.8k | 0.506 | +121.9k | +230.6k | 0.53 | -2.0 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.297 | - | -52.2k (0.51) | -84.1k |
| liveI | d3 | MODEL | 74 | 0.53 | -37.4k | +16.4k | 0.541 | +99.6k | +198.7k | 0.50 | -2.4 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.258 | - | -38.6k (0.53) | -30.9k |
| liveI | d3 | ALLCE | 222 | 1.00 | -120.2k | -57.5k | 0.306 | +39.6k | +190.8k | 0.21 | -2.6 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.832 | - | -123.5k (0.30) | +0.0k |
| fy | d3 | ST5 | 13937 | 0.53 | +2.8k | +5.2k | 0.514 | +135.2k | +137.2k | 0.98 | -0.1 | 7.03 | 11.48 | +0.020 | +6.6k (0.52) | -1.3k (0.50) | +2.7k |
| fy | d3 | ST15 | 13935 | 0.55 | +3.2k | +5.0k | 0.514 | +135.5k | +136.9k | 0.99 | -0.0 | 6.25 | 10.17 | +0.023 | +10.6k (0.53) | -5.0k (0.50) | +3.0k |
| fy | d3 | DST | 13910 | 0.57 | -4.2k | -3.9k | 0.491 | +134.4k | +137.7k | 0.98 | +0.0 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.031 | +3.5k (0.52) | -12.5k (0.46) | -4.4k |
| fy | d3 | PIVP | 13934 | 0.55 | +3.7k | +6.2k | 0.518 | +135.1k | +137.3k | 0.98 | -0.0 | 5.39 | 8.79 | +0.027 | +9.1k (0.53) | -2.2k (0.51) | +3.5k |
| fy | d3 | T5 | 13925 | 0.56 | +4.3k | +9.8k | 0.526 | +133.5k | +139.2k | 0.96 | -0.1 | 4.64 | 7.55 | +0.031 | +5.8k (0.54) | +2.6k (0.51) | +4.1k |
| fy | d3 | T20 | 13880 | 0.61 | +1.8k | +5.4k | 0.514 | +134.2k | +138.3k | 0.97 | -0.0 | 11.04 | 18.05 | +0.013 | +13.7k (0.56) | -11.1k (0.47) | +1.4k |
| fy | d3 | SER | 13919 | 0.57 | +9.1k | +15.3k | 0.540 | +134.5k | +138.1k | 0.97 | -0.1 | 2.19 | 3.58 | +0.067 | +9.7k (0.54) | +8.5k (0.54) | +8.9k |
| fy | d3 | VIX2 | 13934 | 0.46 | +6.6k | +3.7k | 0.509 | +140.2k | +132.0k | 1.06 | +0.2 | 3.18 | 5.14 | +0.048 | -0.8k (0.49) | +14.6k (0.53) | +6.7k |
| fy | d3 | VIXHI | 4314 | 1.00 | +17.3k | +28.4k | 0.566 | +131.9k | +132.2k | 1.00 | -0.2 | 1.06 | 1.77 | +0.131 | +65.9k (0.68) | -36.2k (0.44) | +15.7k |
| fy | d3 | STRESS | 2955 | 1.00 | +12.5k | +23.4k | 0.566 | +131.3k | +142.8k | 0.92 | -0.2 | 1.52 | 2.49 | +0.091 | +70.2k (0.69) | -40.0k (0.45) | +10.8k |
| fy | d3 | MODEL | 4581 | 0.48 | -0.1k | +2.8k | 0.506 | +132.5k | +135.9k | 0.98 | -0.1 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.001 | +4.2k (0.51) | -4.8k (0.50) | -0.0k |
| fy | d3 | ALLCE | 13937 | 1.00 | +1.6k | +17.1k | 0.542 | +127.2k | +146.7k | 0.87 | -0.5 | 10.83 | 18.14 | +0.012 | +53.0k (0.66) | -54.5k (0.41) | +0.0k |
| lsim | y3 | DST | 7034 | 0.60 | +0.9k | +4.1k | 0.513 | +92.3k | +95.5k | 0.97 | -0.1 | 13.88 | 23.40 | +0.010 | +6.2k (0.54) | -5.7k (0.48) | +0.2k |
| lsim | y3 | PIVP | 7034 | 0.54 | +2.2k | +4.3k | 0.516 | +93.1k | +94.7k | 0.98 | +0.0 | 6.08 | 10.29 | +0.023 | +3.8k (0.53) | +0.1k (0.50) | +1.9k |
| lsim | y3 | T5 | 7034 | 0.56 | +1.2k | +3.7k | 0.514 | +92.5k | +95.4k | 0.97 | -0.0 | 11.09 | 18.73 | +0.012 | +2.5k (0.53) | -0.5k (0.49) | +0.7k |
| lsim | y3 | T20 | 7034 | 0.62 | +4.3k | +7.6k | 0.527 | +93.2k | +94.7k | 0.98 | -0.1 | 3.02 | 5.12 | +0.046 | +11.1k (0.56) | -4.2k (0.48) | +3.4k |
| lsim | y3 | SER | 7027 | 0.57 | +4.8k | +7.7k | 0.529 | +93.3k | +94.5k | 0.99 | -0.0 | 2.74 | 4.67 | +0.051 | +7.2k (0.55) | +1.7k (0.50) | +4.2k |
| lsim | y3 | VIX2 | 7034 | 0.48 | +1.2k | +0.0k | 0.500 | +95.1k | +92.7k | 1.03 | +0.1 | 10.87 | 18.43 | +0.013 | -2.6k (0.48) | +6.0k (0.52) | +1.4k |
| lsim | y3 | VIXHI | 2212 | 1.00 | +6.1k | +12.8k | 0.550 | +90.7k | +97.1k | 0.93 | -0.2 | 2.01 | 3.45 | +0.066 | +37.1k (0.65) | -26.6k (0.45) | +2.5k |
| lsim | y3 | STRESS | 2003 | 1.00 | +7.7k | +17.2k | 0.563 | +92.5k | +101.5k | 0.91 | -0.2 | 1.61 | 2.80 | +0.080 | +43.4k (0.68) | -28.0k (0.44) | +4.1k |
| lsim | y3 | MODEL | 6073 | 0.50 | -0.7k | +3.4k | 0.510 | +93.3k | +98.7k | 0.95 | -0.1 | n/a (total ≤ 0) | n/a (total ≤ 0) | -0.007 | +0.8k (0.52) | -2.6k (0.50) | -0.7k |
| lsim | y3 | ALLCE | 7034 | 1.00 | +3.7k | +10.7k | 0.545 | +89.5k | +99.2k | 0.90 | -0.2 | 3.38 | 5.72 | +0.039 | +34.8k (0.65) | -35.4k (0.41) | +0.0k |


### 4.4 Absolute P&L of the chosen leg

Chosen mean / win = the chosen leg's own net P&L per ₹1 L and win rate; the CE and PE columns are both legs on the same
rows; straddle = their average (equal-capital straddle); last column = chosen − straddle = side value ÷ 2.
Column header order: panel | exit | method | n | chosen mean | win | CE leg | PE leg | straddle | chosen − straddle.


| panel | exit | method | n | chosen mean | win | always-CE same rows | always-PE same rows | straddle (avg of legs) | chosen − straddle (= SV/2) |
|---|---|---|---|---|---|---|---|---|---|
| ix | cls | ST15 | 900 | +0.4k | 0.47 | -1.6k | +0.4k | -0.6k | +1.1k |
| ix | cls | DST | 900 | -0.5k | 0.43 | -1.6k | +0.4k | -0.6k | +0.1k |
| ix | cls | PIVP | 900 | +0.5k | 0.47 | -1.6k | +0.4k | -0.6k | +1.1k |
| ix | cls | T20 | 900 | -2.9k | 0.41 | -1.6k | +0.4k | -0.6k | -2.3k |
| ix | cls | SER | 900 | -2.0k | 0.42 | -1.6k | +0.4k | -0.6k | -1.4k |
| ix | cls | VIX2 | 900 | +0.5k | 0.44 | -1.6k | +0.4k | -0.6k | +1.1k |
| ix | cls | VIXHI | 224 | -1.5k | 0.43 | -1.5k | -0.9k | -1.2k | -0.3k |
| ix | cls | MACDr | 856 | -2.5k | 0.42 | -1.5k | +0.2k | -0.6k | -1.9k |
| ix | cls | MODEL | 299 | -1.1k | 0.46 | -0.3k | -0.3k | -0.3k | -0.8k |
| ix | cls | DST&PIVP | 436 | +1.6k | 0.47 | +0.6k | -0.0k | +0.3k | +1.2k |
| ix | cls | ALLCE | 900 | -1.6k | 0.43 | -1.6k | +0.4k | -0.6k | -1.0k |
| ix | cls | ALLPE | 900 | +0.4k | 0.43 | -1.6k | +0.4k | -0.6k | +1.0k |
| ix | d3 | ST15 | 867 | -9.4k | 0.33 | -13.6k | -2.8k | -8.2k | -1.2k |
| ix | d3 | DST | 867 | -19.4k | 0.30 | -13.6k | -2.8k | -8.2k | -11.2k |
| ix | d3 | PIVP | 867 | -14.6k | 0.30 | -13.6k | -2.8k | -8.2k | -6.4k |
| ix | d3 | T20 | 867 | -13.1k | 0.33 | -13.6k | -2.8k | -8.2k | -4.8k |
| ix | d3 | SER | 867 | -12.4k | 0.31 | -13.6k | -2.8k | -8.2k | -4.2k |
| ix | d3 | VIX2 | 867 | +3.4k | 0.37 | -13.6k | -2.8k | -8.2k | +11.6k |
| ix | d3 | VIXHI | 210 | -3.9k | 0.37 | -3.9k | -32.0k | -18.0k | +14.1k |
| ix | d3 | MACDr | 824 | -15.6k | 0.29 | -13.7k | -1.4k | -7.6k | -8.1k |
| ix | d3 | MODEL | 292 | -4.8k | 0.35 | -13.1k | -1.9k | -7.5k | +2.7k |
| ix | d3 | DST&PIVP | 427 | -27.5k | 0.24 | -23.1k | +3.7k | -9.7k | -17.9k |
| ix | d3 | ALLCE | 867 | -13.6k | 0.34 | -13.6k | -2.8k | -8.2k | -5.4k |
| ix | d3 | ALLPE | 867 | -2.8k | 0.35 | -13.6k | -2.8k | -8.2k | +5.4k |
| ix | lane | ST15 | 867 | +4.7k | 0.47 | -6.0k | +5.2k | -0.4k | +5.2k |
| ix | lane | DST | 867 | -7.7k | 0.38 | -6.0k | +5.2k | -0.4k | -7.3k |
| ix | lane | PIVP | 867 | +0.7k | 0.45 | -6.0k | +5.2k | -0.4k | +1.1k |
| ix | lane | T20 | 867 | -4.9k | 0.40 | -6.0k | +5.2k | -0.4k | -4.5k |
| ix | lane | SER | 867 | -2.9k | 0.42 | -6.0k | +5.2k | -0.4k | -2.5k |
| ix | lane | VIX2 | 867 | +3.8k | 0.46 | -6.0k | +5.2k | -0.4k | +4.2k |
| ix | lane | VIXHI | 210 | -7.4k | 0.41 | -7.4k | -3.5k | -5.5k | -2.0k |
| ix | lane | MACDr | 824 | -2.0k | 0.42 | -6.0k | +5.6k | -0.2k | -1.8k |
| ix | lane | MODEL | 292 | +2.5k | 0.45 | -4.2k | +4.4k | +0.1k | +2.4k |
| ix | lane | DST&PIVP | 427 | -6.7k | 0.41 | -7.5k | +6.5k | -0.5k | -6.2k |
| ix | lane | ALLCE | 867 | -6.0k | 0.39 | -6.0k | +5.2k | -0.4k | -5.6k |
| ix | lane | ALLPE | 867 | +5.2k | 0.47 | -6.0k | +5.2k | -0.4k | +5.6k |
| tbI | cls | DST | 2176 | -0.1k | 0.44 | -2.1k | +1.0k | -0.5k | +0.5k |
| tbI | cls | PIVP | 2176 | +0.7k | 0.47 | -2.1k | +1.0k | -0.5k | +1.2k |
| tbI | cls | T20 | 2176 | -0.8k | 0.42 | -2.1k | +1.0k | -0.5k | -0.3k |
| tbI | cls | SER | 2176 | -1.1k | 0.46 | -2.1k | +1.0k | -0.5k | -0.6k |
| tbI | cls | VIX2 | 2176 | +0.4k | 0.44 | -2.1k | +1.0k | -0.5k | +0.9k |
| tbI | cls | VIXHI | 364 | -1.4k | 0.47 | -1.4k | -0.9k | -1.2k | -0.2k |
| tbI | cls | MACDr | 1976 | -0.7k | 0.45 | -2.1k | +1.2k | -0.5k | -0.3k |
| tbI | cls | MODEL | 538 | -0.1k | 0.44 | -0.9k | +0.1k | -0.4k | +0.3k |
| tbI | cls | DST&PIVP | 1067 | +1.4k | 0.47 | -2.3k | +1.8k | -0.3k | +1.7k |
| tbI | cls | ALLCE | 2176 | -2.1k | 0.44 | -2.1k | +1.0k | -0.5k | -1.5k |
| tbI | cls | ALLPE | 2176 | +1.0k | 0.43 | -2.1k | +1.0k | -0.5k | +1.5k |
| tbI | d3 | DST | 2043 | -8.8k | 0.34 | -6.2k | -3.1k | -4.6k | -4.2k |
| tbI | d3 | PIVP | 2043 | -10.3k | 0.36 | -6.2k | -3.1k | -4.6k | -5.6k |
| tbI | d3 | T20 | 2043 | -9.6k | 0.34 | -6.2k | -3.1k | -4.6k | -5.0k |
| tbI | d3 | SER | 2043 | -6.2k | 0.39 | -6.2k | -3.1k | -4.6k | -1.6k |
| tbI | d3 | VIX2 | 2043 | +2.0k | 0.40 | -6.2k | -3.1k | -4.6k | +6.6k |
| tbI | d3 | VIXHI | 326 | +16.4k | 0.58 | +16.4k | -25.3k | -4.4k | +20.8k |
| tbI | d3 | MACDr | 1855 | -11.5k | 0.35 | -7.2k | -2.0k | -4.6k | -6.9k |
| tbI | d3 | MODEL | 522 | -2.1k | 0.40 | -5.0k | -2.8k | -3.9k | +1.8k |
| tbI | d3 | DST&PIVP | 993 | -16.0k | 0.29 | -9.3k | -2.5k | -5.9k | -10.1k |
| tbI | d3 | ALLCE | 2043 | -6.2k | 0.42 | -6.2k | -3.1k | -4.6k | -1.5k |
| tbI | d3 | ALLPE | 2043 | -3.1k | 0.36 | -6.2k | -3.1k | -4.6k | +1.5k |
| tbI | lane | DST | 2043 | -7.7k | 0.38 | -3.3k | -1.3k | -2.3k | -5.4k |
| tbI | lane | PIVP | 2043 | -4.7k | 0.40 | -3.3k | -1.3k | -2.3k | -2.4k |
| tbI | lane | T20 | 2043 | -8.1k | 0.38 | -3.3k | -1.3k | -2.3k | -5.8k |
| tbI | lane | SER | 2043 | -6.3k | 0.40 | -3.3k | -1.3k | -2.3k | -4.0k |
| tbI | lane | VIX2 | 2043 | -0.4k | 0.43 | -3.3k | -1.3k | -2.3k | +1.9k |
| tbI | lane | VIXHI | 326 | +7.5k | 0.47 | +7.5k | -6.0k | +0.8k | +6.8k |
| tbI | lane | MACDr | 1855 | -7.6k | 0.39 | -3.9k | -0.8k | -2.4k | -5.3k |
| tbI | lane | MODEL | 522 | -0.6k | 0.44 | -2.6k | -3.1k | -2.8k | +2.3k |
| tbI | lane | DST&PIVP | 993 | -11.0k | 0.35 | -6.1k | +0.2k | -2.9k | -8.1k |
| tbI | lane | ALLCE | 2043 | -3.3k | 0.43 | -3.3k | -1.3k | -2.3k | -1.0k |
| tbI | lane | ALLPE | 2043 | -1.3k | 0.42 | -3.3k | -1.3k | -2.3k | +1.0k |
| tbS | cls | DST | 69863 | -0.5k | 0.44 | -1.4k | +0.4k | -0.5k | -0.0k |
| tbS | cls | PIVP | 69863 | -0.0k | 0.45 | -1.4k | +0.4k | -0.5k | +0.5k |
| tbS | cls | T20 | 69807 | -0.6k | 0.44 | -1.4k | +0.4k | -0.5k | -0.1k |
| tbS | cls | SER | 60683 | -0.7k | 0.45 | -1.4k | +0.5k | -0.4k | -0.3k |
| tbS | cls | VIX2 | 69863 | +0.5k | 0.46 | -1.4k | +0.4k | -0.5k | +1.0k |
| tbS | cls | VIXHI | 7310 | -0.3k | 0.46 | -0.3k | -1.4k | -0.8k | +0.5k |
| tbS | cls | MACDr | 63362 | -0.6k | 0.44 | -1.4k | +0.5k | -0.5k | -0.1k |
| tbS | cls | DST&PIVP | 34588 | -0.1k | 0.45 | -1.4k | +0.4k | -0.5k | +0.4k |
| tbS | cls | ALLCE | 69863 | -1.4k | 0.43 | -1.4k | +0.4k | -0.5k | -0.9k |
| tbS | cls | ALLPE | 69863 | +0.4k | 0.45 | -1.4k | +0.4k | -0.5k | +0.9k |
| tbS | d3 | DST | 68312 | -3.9k | 0.38 | -2.8k | -2.9k | -2.9k | -1.1k |
| tbS | d3 | PIVP | 68312 | -3.1k | 0.38 | -2.8k | -2.9k | -2.9k | -0.2k |
| tbS | d3 | T20 | 68255 | -5.2k | 0.37 | -2.8k | -2.9k | -2.9k | -2.3k |
| tbS | d3 | SER | 59304 | -3.9k | 0.38 | -3.3k | -1.6k | -2.4k | -1.4k |
| tbS | d3 | VIX2 | 68312 | +0.1k | 0.40 | -2.8k | -2.9k | -2.9k | +2.9k |
| tbS | d3 | VIXHI | 6940 | +12.1k | 0.48 | +12.1k | -17.8k | -2.9k | +14.9k |
| tbS | d3 | MACDr | 61815 | -5.1k | 0.37 | -3.2k | -2.2k | -2.7k | -2.4k |
| tbS | d3 | DST&PIVP | 33852 | -3.9k | 0.38 | -1.9k | -3.3k | -2.6k | -1.3k |
| tbS | d3 | ALLCE | 68312 | -2.8k | 0.39 | -2.8k | -2.9k | -2.9k | +0.0k |
| tbS | d3 | ALLPE | 68312 | -2.9k | 0.38 | -2.8k | -2.9k | -2.9k | -0.0k |
| tbS | lane | DST | 68312 | -3.4k | 0.41 | -1.7k | -3.4k | -2.6k | -0.9k |
| tbS | lane | PIVP | 68312 | -2.3k | 0.42 | -1.7k | -3.4k | -2.6k | +0.3k |
| tbS | lane | T20 | 68255 | -4.0k | 0.40 | -1.7k | -3.4k | -2.6k | -1.4k |
| tbS | lane | SER | 59304 | -3.1k | 0.41 | -1.6k | -3.3k | -2.5k | -0.7k |
| tbS | lane | VIX2 | 68312 | -1.5k | 0.43 | -1.7k | -3.4k | -2.6k | +1.0k |
| tbS | lane | VIXHI | 6940 | +5.5k | 0.46 | +5.5k | -12.3k | -3.4k | +8.9k |
| tbS | lane | MACDr | 61815 | -3.9k | 0.40 | -1.9k | -2.8k | -2.4k | -1.5k |
| tbS | lane | DST&PIVP | 33852 | -3.2k | 0.41 | -1.1k | -4.0k | -2.6k | -0.6k |
| tbS | lane | ALLCE | 68312 | -1.7k | 0.42 | -1.7k | -3.4k | -2.6k | +0.9k |
| tbS | lane | ALLPE | 68312 | -3.4k | 0.41 | -1.7k | -3.4k | -2.6k | -0.9k |
| liveI | cls | ST15 | 300 | -0.9k | 0.37 | -4.8k | +3.7k | -0.6k | -0.3k |
| liveI | cls | DST | 300 | +1.0k | 0.39 | -4.8k | +3.7k | -0.6k | +1.6k |
| liveI | cls | PIVP | 300 | -2.7k | 0.35 | -4.8k | +3.7k | -0.6k | -2.1k |
| liveI | cls | T20 | 300 | +2.7k | 0.45 | -4.8k | +3.7k | -0.6k | +3.3k |
| liveI | cls | SER | 300 | +1.7k | 0.43 | -4.8k | +3.7k | -0.6k | +2.2k |
| liveI | cls | VIX2 | 300 | +2.0k | 0.46 | -4.8k | +3.7k | -0.6k | +2.6k |
| liveI | cls | VIXHI | 36 | -9.1k | 0.36 | -9.1k | -0.9k | -5.0k | -4.1k |
| liveI | cls | MACDr | 199 | +1.2k | 0.42 | -5.5k | +3.9k | -0.8k | +2.0k |
| liveI | cls | MODEL | 99 | -1.8k | 0.36 | -1.9k | -1.3k | -1.6k | -0.2k |
| liveI | cls | DST&PIVP | 149 | -0.5k | 0.38 | -4.5k | +4.7k | +0.1k | -0.6k |
| liveI | cls | ALLCE | 300 | -4.8k | 0.30 | -4.8k | +3.7k | -0.6k | -4.2k |
| liveI | cls | ALLPE | 300 | +3.7k | 0.50 | -4.8k | +3.7k | -0.6k | +4.2k |
| liveI | d3 | ST15 | 222 | +19.2k | 0.39 | -36.9k | +83.3k | +23.2k | -3.9k |
| liveI | d3 | DST | 222 | +61.5k | 0.42 | -36.9k | +83.3k | +23.2k | +38.3k |
| liveI | d3 | PIVP | 222 | +18.5k | 0.43 | -36.9k | +83.3k | +23.2k | -4.7k |
| liveI | d3 | T20 | 222 | +79.0k | 0.59 | -36.9k | +83.3k | +23.2k | +55.8k |
| liveI | d3 | SER | 222 | +48.0k | 0.56 | -36.9k | +83.3k | +23.2k | +24.8k |
| liveI | d3 | VIX2 | 222 | +72.8k | 0.56 | -36.9k | +83.3k | +23.2k | +49.6k |
| liveI | d3 | MACDr | 158 | +8.7k | 0.44 | -41.6k | +111.2k | +34.8k | -26.1k |
| liveI | d3 | MODEL | 74 | +4.6k | 0.41 | -36.2k | +82.7k | +23.3k | -18.7k |
| liveI | d3 | DST&PIVP | 99 | +56.3k | 0.51 | -39.0k | +76.2k | +18.6k | +37.7k |
| liveI | d3 | ALLCE | 222 | -36.9k | 0.14 | -36.9k | +83.3k | +23.2k | -60.1k |
| liveI | d3 | ALLPE | 222 | +83.3k | 0.62 | -36.9k | +83.3k | +23.2k | +60.1k |
| liveI | lane | ST15 | 222 | -1.6k | 0.37 | -25.4k | +20.1k | -2.6k | +1.0k |
| liveI | lane | DST | 222 | +0.2k | 0.41 | -25.4k | +20.1k | -2.6k | +2.8k |
| liveI | lane | PIVP | 222 | -0.3k | 0.39 | -25.4k | +20.1k | -2.6k | +2.3k |
| liveI | lane | T20 | 222 | +16.8k | 0.54 | -25.4k | +20.1k | -2.6k | +19.4k |
| liveI | lane | SER | 222 | +13.9k | 0.54 | -25.4k | +20.1k | -2.6k | +16.6k |
| liveI | lane | VIX2 | 222 | +14.9k | 0.51 | -25.4k | +20.1k | -2.6k | +17.5k |
| liveI | lane | MACDr | 158 | +4.2k | 0.44 | -29.5k | +29.6k | +0.1k | +4.1k |
| liveI | lane | MODEL | 74 | +1.9k | 0.41 | -25.8k | +22.2k | -1.8k | +3.7k |
| liveI | lane | DST&PIVP | 99 | +3.5k | 0.42 | -24.3k | +19.7k | -2.3k | +5.8k |
| liveI | lane | ALLCE | 222 | -25.4k | 0.19 | -25.4k | +20.1k | -2.6k | -22.8k |
| liveI | lane | ALLPE | 222 | +20.1k | 0.57 | -25.4k | +20.1k | -2.6k | +22.8k |
| liveS | cls | ST15 | 16509 | +0.5k | 0.43 | -3.4k | +2.7k | -0.4k | +0.9k |
| liveS | cls | DST | 16509 | +1.0k | 0.47 | -3.4k | +2.7k | -0.4k | +1.3k |
| liveS | cls | PIVP | 16509 | +0.3k | 0.43 | -3.4k | +2.7k | -0.4k | +0.7k |
| liveS | cls | T20 | 13007 | +2.1k | 0.49 | -4.1k | +3.7k | -0.2k | +2.3k |
| liveS | cls | SER | 9731 | +1.6k | 0.48 | -3.9k | +3.6k | -0.2k | +1.8k |
| liveS | cls | VIX2 | 16509 | -0.0k | 0.46 | -3.4k | +2.7k | -0.4k | +0.4k |
| liveS | cls | VIXHI | 1690 | -5.3k | 0.42 | -5.3k | +6.9k | +0.8k | -6.1k |
| liveS | cls | MACDr | 13783 | -0.3k | 0.43 | -3.7k | +3.1k | -0.3k | -0.0k |
| liveS | cls | DST&PIVP | 8892 | +1.7k | 0.47 | -3.4k | +3.1k | -0.2k | +1.9k |
| liveS | cls | ALLCE | 16509 | -3.4k | 0.36 | -3.4k | +2.7k | -0.4k | -3.1k |
| liveS | cls | ALLPE | 16509 | +2.7k | 0.51 | -3.4k | +2.7k | -0.4k | +3.1k |
| liveS | d3 | ST15 | 14566 | +3.5k | 0.43 | -19.0k | +25.1k | +3.1k | +0.4k |
| liveS | d3 | DST | 14566 | +10.9k | 0.46 | -19.0k | +25.1k | +3.1k | +7.8k |
| liveS | d3 | PIVP | 14566 | +3.5k | 0.43 | -19.0k | +25.1k | +3.1k | +0.5k |
| liveS | d3 | T20 | 11067 | +16.7k | 0.49 | -21.6k | +30.6k | +4.5k | +12.2k |
| liveS | d3 | SER | 8075 | +18.8k | 0.51 | -22.0k | +32.0k | +5.0k | +13.8k |
| liveS | d3 | VIX2 | 14566 | +23.6k | 0.52 | -19.0k | +25.1k | +3.1k | +20.6k |
| liveS | d3 | VIXHI | 564 | -31.1k | 0.16 | -31.1k | +59.0k | +14.0k | -45.1k |
| liveS | d3 | MACDr | 12188 | +2.1k | 0.43 | -21.2k | +31.3k | +5.1k | -3.0k |
| liveS | d3 | DST&PIVP | 7686 | +10.3k | 0.49 | -18.9k | +23.6k | +2.4k | +7.9k |
| liveS | d3 | ALLCE | 14566 | -19.0k | 0.25 | -19.0k | +25.1k | +3.1k | -22.0k |
| liveS | d3 | ALLPE | 14566 | +25.1k | 0.55 | -19.0k | +25.1k | +3.1k | +22.0k |
| liveS | lane | ST15 | 14566 | +2.1k | 0.46 | -12.2k | +11.4k | -0.4k | +2.4k |
| liveS | lane | DST | 14566 | +2.9k | 0.47 | -12.2k | +11.4k | -0.4k | +3.3k |
| liveS | lane | PIVP | 14566 | +2.6k | 0.47 | -12.2k | +11.4k | -0.4k | +2.9k |
| liveS | lane | T20 | 11067 | +6.4k | 0.51 | -13.1k | +13.8k | +0.3k | +6.0k |
| liveS | lane | SER | 8075 | +8.0k | 0.53 | -14.6k | +15.8k | +0.6k | +7.4k |
| liveS | lane | VIX2 | 14566 | +7.8k | 0.51 | -12.2k | +11.4k | -0.4k | +8.1k |
| liveS | lane | VIXHI | 564 | -25.0k | 0.19 | -25.0k | +36.3k | +5.6k | -30.7k |
| liveS | lane | MACDr | 12188 | +2.5k | 0.47 | -13.3k | +15.3k | +1.0k | +1.5k |
| liveS | lane | DST&PIVP | 7686 | +5.4k | 0.51 | -12.9k | +12.0k | -0.4k | +5.9k |
| liveS | lane | ALLCE | 14566 | -12.2k | 0.31 | -12.2k | +11.4k | -0.4k | -11.8k |
| liveS | lane | ALLPE | 14566 | +11.4k | 0.55 | -12.2k | +11.4k | -0.4k | +11.8k |
| ix | cls | ev_st15 EV | 286 | +3.7k | 0.45 | -4.5k | +3.6k | -0.4k | +4.1k |
| ix | d3 | ev_st15 EV | 281 | +3.2k | 0.36 | -9.7k | -5.0k | -7.3k | +10.6k |
| ix | lane | ev_st15 EV | 281 | +5.1k | 0.47 | -5.8k | +5.5k | -0.2k | +5.2k |
| ix | cls | ev_macd EV | 151 | -5.1k | 0.38 | -6.7k | +1.1k | -2.8k | -2.3k |
| ix | d3 | ev_macd EV | 153 | -2.9k | 0.37 | -14.4k | +2.4k | -6.0k | +3.1k |
| ix | lane | ev_macd EV | 153 | +6.9k | 0.52 | -6.7k | +9.3k | +1.3k | +5.6k |
| tbI | cls | ev_macd EV | 183 | +1.7k | 0.51 | -4.4k | +2.2k | -1.1k | +2.8k |
| tbI | d3 | ev_macd EV | 190 | -10.4k | 0.36 | -10.6k | +6.3k | -2.1k | -8.3k |
| tbI | lane | ev_macd EV | 190 | -1.3k | 0.44 | -11.3k | +7.5k | -1.9k | +0.5k |
| tbS | cls | ev_macd EV | 5993 | +0.0k | 0.44 | -1.7k | -0.0k | -0.8k | +0.9k |
| tbS | d3 | ev_macd EV | 6474 | -2.5k | 0.38 | -2.6k | -2.8k | -2.7k | +0.2k |
| tbS | lane | ev_macd EV | 6474 | -1.3k | 0.43 | -1.4k | -3.1k | -2.3k | +1.0k |
| liveS | cls | ev_macd EV | 2186 | +2.8k | 0.45 | -5.3k | +4.9k | -0.2k | +3.1k |
| liveS | d3 | ev_macd EV | 1980 | +6.9k | 0.47 | -23.5k | +35.3k | +5.9k | +1.0k |
| liveS | lane | ev_macd EV | 1980 | +7.3k | 0.51 | -14.6k | +17.5k | +1.5k | +5.8k |
| liveI | cls | ev_st15 EV | 113 | +5.1k | 0.40 | -5.3k | +10.5k | +2.6k | +2.5k |
| liveI | d3 | ev_st15 EV | 77 | +21.7k | 0.36 | -38.4k | +82.4k | +22.0k | -0.3k |
| liveI | lane | ev_st15 EV | 77 | -6.1k | 0.34 | -26.3k | +19.2k | -3.6k | -2.6k |
| liveS | cls | ev_st15 EV | 5818 | +1.2k | 0.45 | -4.1k | +3.8k | -0.1k | +1.3k |
| liveS | d3 | ev_st15 EV | 4986 | +1.8k | 0.42 | -20.0k | +24.9k | +2.4k | -0.7k |
| liveS | lane | ev_st15 EV | 4986 | +1.6k | 0.46 | -12.1k | +12.5k | +0.2k | +1.3k |
| tbI | cls | ev_st30 EV | 256 | +1.3k | 0.47 | -3.4k | +1.6k | -0.9k | +2.2k |
| tbI | d3 | ev_st30 EV | 236 | -6.1k | 0.41 | -0.6k | -11.2k | -5.9k | -0.2k |
| tbI | lane | ev_st30 EV | 236 | -1.0k | 0.43 | -2.5k | -4.2k | -3.4k | +2.4k |
| tbS | cls | ev_st30 EV | 7429 | +0.0k | 0.44 | -1.5k | +0.2k | -0.6k | +0.7k |
| tbS | d3 | ev_st30 EV | 7254 | -1.8k | 0.39 | -0.4k | -5.4k | -2.9k | +1.1k |
| tbS | lane | ev_st30 EV | 7254 | -0.7k | 0.43 | -0.4k | -3.7k | -2.1k | +1.4k |


## 5. Regime level, directional skill, power, multiple testing

### 5.1 Regime lock-on

Per underlying-month (and per expiry series), 14:45 decisions with the multi-day exit (`d3` real / Fyers, `y3`
14:45 panel): the dominant side is the sign of Σ (CE − PE). "On dominant side" = share of the period's decision days the
method chose it (abstentions count as not on it). Lock day = first day after which the method is on the dominant side
on that day and on ≥ 80% of the remaining days. Capture vs dominant = Σ s·(CE − PE) ÷ |Σ (CE − PE)| (holding the
winning side all period = +1.00). PREV = last period's dominant side (outcomes closed by the period start); MTD =
sign of the move since the period's first decision. Series rows: header counts are underlying-series; weekly series on
indices have 3–5 decision days, so lock days are short by construction.

#### 5.1a By calendar month (14:45 decisions, multi-day exit)


**lsim** (y3): CE-dominant underlying-months 192, PE-dominant 153

| method | on dominant side (all / CE months / PE months) | abstain | median lock day (CE / PE months) | share of months locked | capture vs holding the dominant side all month (all / CE / PE) | capture vs daily oracle |
|---|---|---|---|---|---|---|
| DST | 0.68 / 0.74 / 0.60 | 0.00 | 1 / 4 | 0.83 | +0.03 / +0.18 / -0.16 | +0.010 |
| T5 | 0.66 / 0.70 / 0.61 | 0.00 | 8 / 12 | 0.79 | +0.04 / +0.07 / -0.01 | +0.013 |
| T20 | 0.72 / 0.80 / 0.61 | 0.00 | 1 / 7 | 0.93 | +0.12 / +0.32 / -0.12 | +0.046 |
| SER | 0.64 / 0.69 / 0.58 | 0.00 | 9 / 12 | 0.74 | +0.14 / +0.21 / +0.05 | +0.051 |
| ERG | 0.36 / 0.42 / 0.29 | 0.56 | 13 / 16 | 0.46 | +0.03 / +0.11 / -0.06 | +0.013 |
| VIX2 | 0.46 / 0.45 / 0.49 | 0.00 | 1 / 1 | 0.47 | +0.04 / -0.08 / +0.17 | +0.013 |
| PIVP | 0.59 / 0.62 / 0.55 | 0.00 | 16 / 18 | 0.66 | +0.06 / +0.11 / +0.01 | +0.024 |
| MODEL | 0.58 / 0.57 / 0.58 | 0.00 | 14 / 15 | 0.72 | -0.02 / +0.02 / -0.07 | -0.007 |
| PREV | 0.55 / 0.56 / 0.53 | 0.00 | 1 / 1 | 0.55 | +0.02 / -0.03 / +0.08 | +0.007 |
| MTD | 0.72 / 0.76 / 0.68 | 0.05 | 3 / 4 | 0.92 | +0.11 / +0.25 / -0.06 | +0.042 |
| ALLCE | 0.56 / 1.00 / 0.00 | 0.00 | 1 / - | 0.56 | +0.11 / +1.00 / -1.00 | +0.039 |
| ALLPE | 0.44 / 0.00 / 1.00 | 0.00 | - / 1 | 0.44 | -0.11 / -1.00 / +1.00 | -0.039 |

**fy** (d3): CE-dominant underlying-months 120, PE-dominant 108

| method | on dominant side (all / CE months / PE months) | abstain | median lock day (CE / PE months) | share of months locked | capture vs holding the dominant side all month (all / CE / PE) | capture vs daily oracle |
|---|---|---|---|---|---|---|
| DST | 0.67 / 0.72 / 0.61 | 0.00 | 1 / 4 | 0.84 | -0.06 / +0.06 / -0.19 | -0.023 |
| T5 | 0.65 / 0.70 / 0.60 | 0.00 | 8 / 10 | 0.79 | +0.07 / +0.14 / -0.01 | +0.026 |
| T20 | 0.70 / 0.79 / 0.61 | 0.00 | 1 / 5 | 0.90 | +0.07 / +0.29 / -0.16 | +0.028 |
| SER | 0.62 / 0.67 / 0.56 | 0.00 | 9 / 13 | 0.71 | +0.17 / +0.21 / +0.12 | +0.064 |
| ERG | 0.35 / 0.42 / 0.28 | 0.56 | 13 / 16 | 0.46 | -0.02 / +0.11 / -0.15 | -0.006 |
| VIX2 | 0.49 / 0.46 / 0.54 | 0.00 | 1 / 1 | 0.49 | +0.14 / +0.01 / +0.28 | +0.054 |
| PIVP | 0.58 / 0.62 / 0.54 | 0.00 | 16 / 18 | 0.64 | +0.02 / +0.12 / -0.08 | +0.009 |
| ST30 | 0.58 / 0.62 / 0.53 | 0.00 | 16 / 17 | 0.64 | -0.03 / +0.09 / -0.16 | -0.010 |
| MACDspot | 0.62 / 0.66 / 0.57 | 0.00 | 10 / 16 | 0.68 | +0.06 / +0.17 / -0.05 | +0.025 |
| MODEL | 0.60 / 0.58 / 0.62 | 0.00 | 14 / 11 | 0.75 | -0.00 / +0.09 / -0.10 | -0.001 |
| PREV | 0.56 / 0.56 / 0.56 | 0.00 | 1 / 1 | 0.56 | -0.01 / -0.12 / +0.11 | -0.004 |
| MTD | 0.73 / 0.79 / 0.68 | 0.00 | 2 / 3 | 0.90 | +0.07 / +0.29 / -0.17 | +0.028 |
| ALLCE | 0.53 / 1.00 / 0.00 | 0.00 | 1 / - | 0.53 | +0.05 / +1.00 / -1.00 | +0.018 |
| ALLPE | 0.47 / 0.00 / 1.00 | 0.00 | - / 1 | 0.47 | -0.05 / -1.00 / +1.00 | -0.018 |

**ix** (d3): CE-dominant underlying-months 8, PE-dominant 8

| method | on dominant side (all / CE months / PE months) | abstain | median lock day (CE / PE months) | share of months locked | capture vs holding the dominant side all month (all / CE / PE) | capture vs daily oracle |
|---|---|---|---|---|---|---|
| DST | 0.61 / 0.83 / 0.39 | 0.00 | 1 / 20 | 0.75 | -0.48 / +0.49 / -1.09 | -0.169 |
| T5 | 0.65 / 0.71 / 0.58 | 0.00 | 7 / 14 | 0.94 | +0.06 / +0.03 / +0.08 | +0.022 |
| T20 | 0.62 / 0.81 / 0.44 | 0.00 | 1 / 14 | 0.75 | -0.07 / +0.56 / -0.46 | -0.024 |
| SER | 0.61 / 0.62 / 0.60 | 0.00 | 15 / 13 | 0.81 | -0.12 / -0.18 / -0.09 | -0.043 |
| ERG | 0.28 / 0.37 / 0.19 | 0.60 | 7 / 16 | 0.44 | -0.24 / -0.07 / -0.34 | -0.083 |
| VIX2 | 0.61 / 0.47 / 0.75 | 0.00 | 1 / 1 | 0.50 | +0.47 / -0.38 / +0.99 | +0.164 |
| PIVP | 0.54 / 0.53 / 0.55 | 0.00 | 17 / 15 | 0.50 | -0.31 / -0.16 / -0.41 | -0.111 |
| ST30 | 0.54 / 0.57 / 0.52 | 0.00 | 1 / 15 | 0.31 | -0.79 / -0.73 / -0.82 | -0.277 |
| MACDspot | 0.62 / 0.66 / 0.57 | 0.00 | 6 / 15 | 0.62 | -0.31 / -0.11 / -0.43 | -0.109 |
| MODEL | 0.59 / 0.54 / 0.64 | 0.00 | 9 / 18 | 0.62 | +0.13 / -0.01 / +0.21 | +0.045 |
| PREV | 0.57 / 0.67 / 0.50 | 0.00 | 1 / 1 | 0.57 | +0.01 / +0.20 / -0.10 | +0.003 |
| MTD | 0.71 / 0.80 / 0.63 | 0.00 | 1 / 1 | 0.75 | +0.34 / +0.30 / +0.36 | +0.120 |
| ALLCE | 0.50 / 1.00 / 0.00 | 0.00 | 1 / - | 0.50 | -0.23 / +1.00 / -1.00 | -0.081 |
| ALLPE | 0.50 / 0.00 / 1.00 | 0.00 | - / 1 | 0.50 | +0.23 / -1.00 / +1.00 | +0.081 |

**tbI** (d3): CE-dominant underlying-months 20, PE-dominant 17

| method | on dominant side (all / CE months / PE months) | abstain | median lock day (CE / PE months) | share of months locked | capture vs holding the dominant side all month (all / CE / PE) | capture vs daily oracle |
|---|---|---|---|---|---|---|
| DST | 0.62 / 0.79 / 0.42 | 0.00 | 1 / 6 | 0.70 | -0.18 / +0.17 / -0.44 | -0.082 |
| T5 | 0.62 / 0.63 / 0.60 | 0.00 | 8 / 12 | 0.76 | -0.12 / -0.03 / -0.19 | -0.056 |
| T20 | 0.66 / 0.80 / 0.50 | 0.00 | 1 / 10 | 0.92 | -0.23 / +0.19 / -0.54 | -0.104 |
| SER | 0.60 / 0.64 / 0.54 | 0.00 | 5 / 4 | 0.65 | -0.11 / +0.09 / -0.26 | -0.051 |
| ERG | 0.24 / 0.28 / 0.19 | 0.68 | 11 / 12 | 0.33 | -0.02 / -0.02 / -0.03 | -0.011 |
| VIX2 | 0.58 / 0.37 / 0.83 | 0.00 | 1 / 1 | 0.49 | +0.30 / -0.24 / +0.71 | +0.140 |
| PIVP | 0.56 / 0.55 / 0.57 | 0.00 | 12 / 16 | 0.65 | -0.36 / -0.27 / -0.43 | -0.168 |
| ST30 | 0.56 / 0.58 / 0.53 | 0.00 | 10 / 15 | 0.65 | -0.44 / -0.29 / -0.55 | -0.202 |
| MACDspot | 0.57 / 0.58 / 0.57 | 0.00 | 10 / 15 | 0.68 | -0.46 / -0.28 / -0.60 | -0.214 |
| MODEL | 0.56 / 0.48 / 0.66 | 0.01 | 17 / 18 | 0.81 | +0.05 / -0.08 / +0.15 | +0.020 |
| PREV | 0.53 / 0.50 / 0.56 | 0.00 | 1 / 1 | 0.53 | +0.15 / +0.03 / +0.24 | +0.072 |
| MTD | 0.75 / 0.72 / 0.77 | 0.00 | 2 / 2 | 0.89 | +0.25 / +0.14 / +0.33 | +0.117 |
| ALLCE | 0.54 / 1.00 / 0.00 | 0.00 | 1 / - | 0.54 | -0.14 / +1.00 / -1.00 | -0.065 |
| ALLPE | 0.46 / 0.00 / 1.00 | 0.00 | - / 1 | 0.46 | +0.14 / -1.00 / +1.00 | +0.065 |

**tbS** (d3): CE-dominant underlying-months 692, PE-dominant 726

| method | on dominant side (all / CE months / PE months) | abstain | median lock day (CE / PE months) | share of months locked | capture vs holding the dominant side all month (all / CE / PE) | capture vs daily oracle |
|---|---|---|---|---|---|---|
| DST | 0.60 / 0.61 / 0.60 | 0.00 | 1 / 1 | 0.74 | -0.02 / +0.05 / -0.08 | -0.009 |
| T5 | 0.59 / 0.57 / 0.60 | 0.00 | 8 / 9 | 0.70 | -0.14 / -0.17 / -0.11 | -0.064 |
| T20 | 0.62 / 0.62 / 0.62 | 0.00 | 3 / 5 | 0.84 | -0.10 / -0.02 / -0.17 | -0.045 |
| SER | 0.59 / 0.66 / 0.53 | 0.09 | 2 / 7 | 0.75 | -0.08 / +0.12 / -0.27 | -0.039 |
| ERG | 0.25 / 0.25 / 0.25 | 0.64 | 11 / 12 | 0.36 | -0.04 / -0.04 / -0.04 | -0.019 |
| VIX2 | 0.50 / 0.15 / 0.84 | 0.00 | 8 / 1 | 0.47 | +0.12 / -0.73 / +0.90 | +0.054 |
| PIVP | 0.54 / 0.52 / 0.57 | 0.00 | 12 / 12 | 0.61 | -0.04 / -0.09 / +0.02 | -0.017 |
| ST30 | 0.56 / 0.55 / 0.56 | 0.00 | 10 / 10 | 0.64 | -0.06 / -0.05 / -0.06 | -0.027 |
| MACDspot | 0.57 / 0.55 / 0.58 | 0.00 | 10 / 10 | 0.64 | -0.11 / -0.14 / -0.09 | -0.051 |
| PREV | 0.49 / 0.51 / 0.48 | 0.00 | 1 / 1 | 0.49 | -0.01 / +0.02 / -0.03 | -0.002 |
| MTD | 0.68 / 0.70 / 0.66 | 0.00 | 2 / 3 | 0.84 | -0.04 / +0.04 / -0.11 | -0.017 |
| ALLCE | 0.49 / 1.00 / 0.00 | 0.00 | 1 / - | 0.49 | -0.04 / +1.00 / -1.00 | -0.018 |
| ALLPE | 0.51 / 0.00 / 1.00 | 0.00 | - / 1 | 0.51 | +0.04 / -1.00 / +1.00 | +0.018 |

**live** (d3): CE-dominant underlying-months 25, PE-dominant 182

| method | on dominant side (all / CE months / PE months) | abstain | median lock day (CE / PE months) | share of months locked | capture vs holding the dominant side all month (all / CE / PE) | capture vs daily oracle |
|---|---|---|---|---|---|---|
| DST | 0.67 / 0.45 / 0.70 | 0.00 | 8 / 1 | 0.78 | +0.35 / -0.54 / +0.41 | +0.195 |
| T5 | 0.68 / 0.55 / 0.70 | 0.00 | 9 / 11 | 0.91 | -0.16 / -0.45 / -0.15 | -0.090 |
| T20 | 0.76 / 0.48 / 0.80 | 0.01 | 9 / 1 | 0.93 | +0.44 / -0.52 / +0.49 | +0.241 |
| SER | 0.81 / 0.37 / 0.87 | 0.00 | 10 / 1 | 0.93 | +0.53 / -0.76 / +0.61 | +0.294 |
| ERG | 0.33 / 0.19 / 0.35 | 0.62 | 13 / 12 | 0.36 | +0.16 / -0.37 / +0.19 | +0.088 |
| VIX2 | 0.65 / 0.30 / 0.70 | 0.00 | 19 / 13 | 0.30 | +0.87 / +0.32 / +0.90 | +0.480 |
| PIVP | 0.60 / 0.51 / 0.61 | 0.00 | 10 / 15 | 0.84 | -0.07 / -0.44 / -0.05 | -0.040 |
| ST30 | 0.60 / 0.53 / 0.61 | 0.00 | 10 / 15 | 0.86 | -0.02 / -0.40 / -0.00 | -0.013 |
| MACDspot | 0.63 / 0.51 / 0.64 | 0.00 | 11 / 15 | 0.89 | -0.11 / -0.71 / -0.08 | -0.062 |
| MODEL | 0.43 / - / 0.43 | 0.00 | - / 16 | 1.00 | -0.31 / - / -0.31 | -0.266 |
| PREV | 0.53 / 0.38 / 0.55 | 0.00 | 1 / 1 | 0.53 | +0.04 / -0.32 / +0.06 | +0.022 |
| MTD | 0.74 / 0.57 / 0.76 | 0.00 | 5 / 1 | 0.93 | +0.31 / -0.71 / +0.38 | +0.173 |
| ALLCE | 0.12 / 1.00 / 0.00 | 0.00 | 1 / - | 0.12 | -0.89 / +1.00 / -1.00 | -0.490 |
| ALLPE | 0.88 / 0.00 / 1.00 | 0.00 | - / 1 | 0.88 | +0.89 / -1.00 / +1.00 | +0.490 |

#### 5.1b By expiry series (14:45 decisions, multi-day exit)


**lsim** (y3): CE-dominant underlying-series 656, PE-dominant 550

| method | on dominant side (all / CE series / PE series) | abstain | median lock day (CE / PE series) | share of series locked | capture vs holding the dominant side all series (all / CE / PE) | capture vs daily oracle |
|---|---|---|---|---|---|---|
| DST | 0.53 / 0.61 / 0.44 | 0.00 | 1 / 1 | 0.57 | +0.02 / +0.14 / -0.13 | +0.011 |
| T5 | 0.61 / 0.66 / 0.56 | 0.00 | 1 / 2 | 0.77 | +0.02 / +0.09 / -0.06 | +0.013 |
| T20 | 0.58 / 0.68 / 0.46 | 0.00 | 1 / 1 | 0.64 | +0.07 / +0.24 / -0.12 | +0.046 |
| SER | 0.64 / 0.69 / 0.59 | 0.00 | 1 / 1 | 0.76 | +0.08 / +0.17 / -0.01 | +0.052 |
| ERG | 0.39 / 0.49 / 0.29 | 0.40 | 2 / 3 | 0.49 | +0.03 / +0.16 / -0.12 | +0.017 |
| VIX2 | 0.48 / 0.46 / 0.50 | 0.00 | 1 / 1 | 0.47 | +0.02 / -0.06 / +0.11 | +0.013 |
| PIVP | 0.62 / 0.65 / 0.58 | 0.00 | 2 / 3 | 0.73 | +0.04 / +0.06 / +0.01 | +0.024 |
| MODEL | 0.53 / 0.54 / 0.53 | 0.00 | 2 / 2 | 0.59 | -0.01 / -0.03 / +0.01 | -0.006 |
| PREV | 0.48 / 0.54 / 0.42 | 0.00 | 1 / 1 | 0.48 | -0.06 / -0.00 / -0.13 | -0.038 |
| MTD | 0.54 / 0.58 / 0.50 | 0.20 | 2 / 2 | 0.77 | +0.02 / +0.11 / -0.08 | +0.013 |
| ALLCE | 0.54 / 1.00 / 0.00 | 0.00 | 1 / - | 0.54 | +0.06 / +1.00 / -1.00 | +0.039 |
| ALLPE | 0.46 / 0.00 / 1.00 | 0.00 | - / 1 | 0.46 | -0.06 / -1.00 / +1.00 | -0.039 |

**fy** (d3): CE-dominant underlying-series 451, PE-dominant 386

| method | on dominant side (all / CE series / PE series) | abstain | median lock day (CE / PE series) | share of series locked | capture vs holding the dominant side all series (all / CE / PE) | capture vs daily oracle |
|---|---|---|---|---|---|---|
| DST | 0.50 / 0.56 / 0.44 | 0.00 | 1 / 1 | 0.54 | -0.04 / +0.06 / -0.13 | -0.025 |
| T5 | 0.59 / 0.64 / 0.54 | 0.00 | 1 / 2 | 0.73 | +0.04 / +0.14 / -0.07 | +0.026 |
| T20 | 0.55 / 0.64 / 0.45 | 0.00 | 1 / 1 | 0.63 | +0.04 / +0.23 / -0.16 | +0.027 |
| SER | 0.60 / 0.65 / 0.55 | 0.00 | 1 / 2 | 0.78 | +0.09 / +0.21 / -0.04 | +0.062 |
| ERG | 0.37 / 0.46 / 0.26 | 0.40 | 1 / 3 | 0.45 | -0.01 / +0.16 / -0.19 | -0.008 |
| VIX2 | 0.50 / 0.46 / 0.55 | 0.00 | 1 / 1 | 0.50 | +0.08 / -0.05 / +0.21 | +0.054 |
| PIVP | 0.60 / 0.63 / 0.56 | 0.00 | 2 / 3 | 0.74 | +0.01 / +0.10 / -0.08 | +0.008 |
| ST30 | 0.59 / 0.62 / 0.54 | 0.00 | 3 / 3 | 0.72 | -0.02 / +0.07 / -0.11 | -0.012 |
| MACDspot | 0.62 / 0.66 / 0.57 | 0.00 | 1 / 3 | 0.77 | +0.04 / +0.16 / -0.10 | +0.025 |
| MODEL | 0.54 / 0.52 / 0.55 | 0.00 | 2 / 2 | 0.63 | -0.00 / +0.00 / -0.01 | -0.001 |
| PREV | 0.48 / 0.54 / 0.42 | 0.00 | 1 / 1 | 0.48 | -0.06 / +0.06 / -0.19 | -0.043 |
| MTD | 0.61 / 0.64 / 0.58 | 0.00 | 1 / 2 | 0.74 | +0.05 / +0.14 / -0.05 | +0.033 |
| ALLCE | 0.54 / 1.00 / 0.00 | 0.00 | 1 / - | 0.54 | +0.03 / +1.00 / -1.00 | +0.019 |
| ALLPE | 0.46 / 0.00 / 1.00 | 0.00 | - / 1 | 0.46 | -0.03 / -1.00 / +1.00 | -0.019 |

**ix** (d3): CE-dominant underlying-series 28, PE-dominant 32

| method | on dominant side (all / CE series / PE series) | abstain | median lock day (CE / PE series) | share of series locked | capture vs holding the dominant side all series (all / CE / PE) | capture vs daily oracle |
|---|---|---|---|---|---|---|
| DST | 0.47 / 0.72 / 0.25 | 0.00 | 1 / 1 | 0.50 | -0.24 / +0.12 / -0.52 | -0.158 |
| T5 | 0.60 / 0.64 / 0.56 | 0.00 | 1 / 1 | 0.75 | +0.02 / -0.01 / +0.05 | +0.015 |
| T20 | 0.55 / 0.76 / 0.36 | 0.00 | 1 / 1 | 0.58 | -0.02 / +0.39 / -0.33 | -0.011 |
| SER | 0.62 / 0.62 / 0.62 | 0.00 | 2 / 2 | 0.77 | -0.08 / -0.12 / -0.04 | -0.052 |
| ERG | 0.36 / 0.49 / 0.25 | 0.43 | 2 / 4 | 0.41 | -0.17 / -0.03 / -0.25 | -0.119 |
| VIX2 | 0.53 / 0.33 / 0.71 | 0.00 | 1 / 1 | 0.52 | +0.28 / -0.50 / +0.87 | +0.181 |
| PIVP | 0.55 / 0.53 / 0.57 | 0.00 | 3 / 3 | 0.68 | -0.18 / -0.17 / -0.19 | -0.120 |
| ST30 | 0.53 / 0.54 / 0.51 | 0.00 | 2 / 3 | 0.57 | -0.42 / -0.35 / -0.47 | -0.273 |
| MACDspot | 0.60 / 0.63 / 0.57 | 0.00 | 2 / 3 | 0.73 | -0.19 / +0.07 / -0.39 | -0.127 |
| MODEL | 0.58 / 0.50 / 0.64 | 0.00 | 4 / 1 | 0.68 | +0.08 / -0.03 / +0.17 | +0.054 |
| PREV | 0.53 / 0.52 / 0.53 | 0.00 | 1 / 1 | 0.53 | -0.03 / +0.03 / -0.07 | -0.020 |
| MTD | 0.59 / 0.55 / 0.63 | 0.00 | 3 / 1 | 0.73 | -0.05 / -0.03 / -0.07 | -0.034 |
| ALLCE | 0.47 / 1.00 / 0.00 | 0.00 | 1 / - | 0.47 | -0.13 / +1.00 / -1.00 | -0.084 |
| ALLPE | 0.53 / 0.00 / 1.00 | 0.00 | - / 1 | 0.53 | +0.13 / -1.00 / +1.00 | +0.084 |

**tbI** (d3): CE-dominant underlying-series 22, PE-dominant 19

| method | on dominant side (all / CE series / PE series) | abstain | median lock day (CE / PE series) | share of series locked | capture vs holding the dominant side all series (all / CE / PE) | capture vs daily oracle |
|---|---|---|---|---|---|---|
| DST | 0.51 / 0.64 / 0.36 | 0.00 | 1 / 7 | 0.66 | -0.22 / +0.10 / -0.48 | -0.099 |
| T5 | 0.59 / 0.59 / 0.59 | 0.00 | 7 / 16 | 0.83 | -0.11 / -0.02 / -0.18 | -0.050 |
| T20 | 0.62 / 0.75 / 0.46 | 0.00 | 1 / 15 | 0.93 | -0.22 / +0.29 / -0.65 | -0.099 |
| SER | 0.60 / 0.68 / 0.51 | 0.00 | 2 / 12 | 0.90 | -0.04 / +0.23 / -0.28 | -0.020 |
| ERG | 0.22 / 0.26 / 0.18 | 0.63 | 12 / 13 | 0.38 | -0.05 / -0.05 / -0.04 | -0.021 |
| VIX2 | 0.57 / 0.34 / 0.83 | 0.00 | 4 / 1 | 0.46 | +0.23 / -0.40 / +0.76 | +0.104 |
| PIVP | 0.57 / 0.57 / 0.56 | 0.00 | 12 / 14 | 0.71 | -0.32 / -0.13 / -0.49 | -0.148 |
| ST30 | 0.55 / 0.59 / 0.51 | 0.00 | 8 / 14 | 0.71 | -0.40 / -0.09 / -0.66 | -0.182 |
| MACDspot | 0.58 / 0.60 / 0.57 | 0.00 | 8 / 15 | 0.78 | -0.42 / -0.10 / -0.69 | -0.194 |
| MODEL | 0.59 / 0.55 / 0.64 | 0.01 | 16 / 15 | 0.77 | +0.10 / +0.10 / +0.11 | +0.047 |
| PREV | 0.69 / 0.85 / 0.47 | 0.00 | 1 / 1 | 0.69 | +0.30 / +0.59 / +0.00 | +0.139 |
| MTD | 0.62 / 0.67 / 0.56 | 0.00 | 3 / 8 | 0.88 | -0.22 / +0.07 / -0.47 | -0.102 |
| ALLCE | 0.54 / 1.00 / 0.00 | 0.00 | 1 / - | 0.54 | -0.08 / +1.00 / -1.00 | -0.038 |
| ALLPE | 0.46 / 0.00 / 1.00 | 0.00 | - / 1 | 0.46 | +0.08 / -1.00 / +1.00 | +0.038 |

**tbS** (d3): CE-dominant underlying-series 850, PE-dominant 842

| method | on dominant side (all / CE series / PE series) | abstain | median lock day (CE / PE series) | share of series locked | capture vs holding the dominant side all series (all / CE / PE) | capture vs daily oracle |
|---|---|---|---|---|---|---|
| DST | 0.52 / 0.51 / 0.53 | 0.00 | 1 / 1 | 0.61 | -0.05 / -0.06 / -0.04 | -0.026 |
| T5 | 0.53 / 0.50 / 0.55 | 0.00 | 7 / 10 | 0.67 | -0.09 / -0.13 / -0.06 | -0.049 |
| T20 | 0.53 / 0.52 / 0.54 | 0.00 | 1 / 4 | 0.72 | -0.10 / -0.09 / -0.12 | -0.055 |
| SER | 0.60 / 0.68 / 0.52 | 0.02 | 3 / 7 | 0.85 | -0.04 / +0.16 / -0.23 | -0.023 |
| ERG | 0.21 / 0.21 / 0.21 | 0.61 | 10 / 11 | 0.26 | -0.03 / -0.06 / -0.01 | -0.018 |
| VIX2 | 0.56 / 0.24 / 0.89 | 0.00 | 1 / 1 | 0.58 | +0.11 / -0.59 / +0.82 | +0.058 |
| PIVP | 0.53 / 0.51 / 0.55 | 0.00 | 9 / 12 | 0.62 | -0.02 / -0.10 / +0.05 | -0.013 |
| ST30 | 0.53 / 0.52 / 0.53 | 0.00 | 8 / 11 | 0.64 | -0.05 / -0.09 / -0.01 | -0.026 |
| MACDspot | 0.53 / 0.51 / 0.55 | 0.00 | 9 / 12 | 0.63 | -0.10 / -0.15 / -0.04 | -0.050 |
| PREV | 0.51 / 0.54 / 0.48 | 0.00 | 1 / 1 | 0.51 | +0.01 / +0.06 / -0.04 | +0.005 |
| MTD | 0.59 / 0.61 / 0.57 | 0.00 | 3 / 5 | 0.83 | -0.09 / -0.01 / -0.16 | -0.045 |
| ALLCE | 0.50 / 1.00 / 0.00 | 0.00 | 1 / - | 0.50 | +0.01 / +1.00 / -1.00 | +0.005 |
| ALLPE | 0.50 / 0.00 / 1.00 | 0.00 | - / 1 | 0.50 | -0.01 / -1.00 / +1.00 | -0.005 |

**live** (d3): CE-dominant underlying-series 27, PE-dominant 186

| method | on dominant side (all / CE series / PE series) | abstain | median lock day (CE / PE series) | share of series locked | capture vs holding the dominant side all series (all / CE / PE) | capture vs daily oracle |
|---|---|---|---|---|---|---|
| DST | 0.67 / 0.51 / 0.69 | 0.00 | 10 / 1 | 0.81 | +0.32 / -0.67 / +0.38 | +0.162 |
| T5 | 0.65 / 0.52 / 0.67 | 0.00 | 16 / 14 | 0.90 | -0.17 / -0.59 / -0.15 | -0.089 |
| T20 | 0.61 / 0.44 / 0.64 | 0.21 | 10 / 7 | 0.93 | +0.40 / -0.51 / +0.45 | +0.203 |
| SER | 0.69 / 0.39 / 0.73 | 0.14 | 9 / 5 | 0.94 | +0.51 / -1.09 / +0.61 | +0.263 |
| ERG | 0.32 / 0.19 / 0.33 | 0.63 | 20 / 17 | 0.33 | +0.16 / -0.20 / +0.18 | +0.080 |
| VIX2 | 0.71 / 0.28 / 0.78 | 0.00 | 25 / 1 | 0.46 | +0.87 / -0.02 / +0.92 | +0.442 |
| PIVP | 0.59 / 0.50 / 0.60 | 0.00 | 16 / 21 | 0.80 | -0.06 / -0.62 / -0.03 | -0.031 |
| ST30 | 0.58 / 0.52 / 0.59 | 0.00 | 16 / 21 | 0.82 | -0.02 / -0.35 / +0.00 | -0.010 |
| MACDspot | 0.61 / 0.49 / 0.62 | 0.00 | 16 / 21 | 0.85 | -0.11 / -0.90 / -0.06 | -0.054 |
| MODEL | 0.57 / 1.00 / 0.46 | 0.00 | 1 / 16 | 1.00 | -0.34 / +1.00 / -0.37 | -0.289 |
| PREV | 0.33 / 0.00 / 0.50 | 0.00 | - / 1 | 0.33 | -0.88 / -1.00 / -0.87 | -0.840 |
| MTD | 0.73 / 0.63 / 0.74 | 0.00 | 5 / 2 | 0.92 | +0.31 / -0.80 / +0.38 | +0.159 |
| ALLCE | 0.13 / 1.00 / 0.00 | 0.00 | 1 / - | 0.13 | -0.89 / +1.00 / -1.00 | -0.452 |
| ALLPE | 0.87 / 0.00 / 1.00 | 0.00 | - / 1 | 0.87 | +0.89 / -1.00 / +1.00 | +0.452 |

### 5.2 Underlying-level directional skill (no option pricing)

Signed spot move in the chosen direction after the decision, bps, [95% block CI], h = hit rate, a = magnitude-weighted
accuracy Σ s·m ÷ Σ |m|. `lsim` = 14:45 2016–26 (D+1, D+3 only); `fy` = Fyers 5-min 2021–26.



**lsim**

| set | method | +30 min | to close | D+1 | D+3 |
|---|---|---|---|---|---|
| grid | DST | - | - | +1.6 [-2.1, +5.4] h0.513 a+0.020 | +1.9 [-7.9, +11.9] h0.510 a+0.013 |
| grid | PIVP | - | - | +1.2 [-2.4, +4.9] h0.516 a+0.016 | -0.3 [-7.6, +6.8] h0.513 a-0.002 |
| grid | T5 | - | - | +1.0 [-2.8, +5.2] h0.509 a+0.013 | +1.1 [-7.5, +10.6] h0.518 a+0.008 |
| grid | T20 | - | - | +3.2 [-0.5, +7.1] h0.530 a+0.041 | +6.1 [-3.9, +16.6] h0.528 a+0.043 |
| grid | SER | - | - | +3.5 [-0.0, +6.9] h0.525 a+0.044 | +5.5 [-2.7, +13.6] h0.532 a+0.039 |
| grid | ERG | - | - | +1.4 [-4.6, +7.6] h0.522 a+0.018 | +6.3 [-8.4, +21.3] h0.515 a+0.045 |
| grid | VIX2 | - | - | +0.6 [-3.5, +4.7] h0.501 a+0.008 | +2.7 [-8.4, +13.5] h0.499 a+0.019 |
| grid | VIXHI | - | - | +6.1 [-3.7, +15.3] h0.546 a+0.060 | +20.8 [-2.9, +45.3] h0.557 a+0.120 |
| grid | STRESS | - | - | +8.4 [-0.9, +17.4] h0.566 a+0.090 | +18.6 [-1.7, +41.5] h0.572 a+0.114 |
| grid | MODEL | - | - | -0.9 [-5.2, +3.6] h0.519 a-0.011 | -7.6 [-15.7, +1.1] h0.500 a-0.054 |
| grid | ALLCE | - | - | +5.0 [+0.6, +9.4] h0.541 a+0.063 | +14.6 [+3.7, +26.8] h0.558 a+0.104 |
| g330 | DST | - | - | +1.6 [-2.1, +5.4] h0.513 a+0.020 | +1.9 [-7.9, +11.9] h0.510 a+0.013 |
| g330 | PIVP | - | - | +1.2 [-2.4, +4.9] h0.516 a+0.016 | -0.3 [-7.6, +6.8] h0.513 a-0.002 |
| g330 | T5 | - | - | +1.0 [-2.8, +5.2] h0.509 a+0.013 | +1.1 [-7.5, +10.6] h0.518 a+0.008 |
| g330 | T20 | - | - | +3.2 [-0.5, +7.1] h0.530 a+0.041 | +6.1 [-3.9, +16.6] h0.528 a+0.043 |
| g330 | SER | - | - | +3.5 [-0.0, +6.9] h0.525 a+0.044 | +5.5 [-2.7, +13.6] h0.532 a+0.039 |
| g330 | ERG | - | - | +1.4 [-4.6, +7.6] h0.522 a+0.018 | +6.3 [-8.4, +21.3] h0.515 a+0.045 |
| g330 | VIX2 | - | - | +0.6 [-3.5, +4.7] h0.501 a+0.008 | +2.7 [-8.4, +13.5] h0.499 a+0.019 |
| g330 | VIXHI | - | - | +6.1 [-3.7, +15.3] h0.546 a+0.060 | +20.8 [-2.9, +45.3] h0.557 a+0.120 |
| g330 | STRESS | - | - | +8.4 [-0.9, +17.4] h0.566 a+0.090 | +18.6 [-1.7, +41.5] h0.572 a+0.114 |
| g330 | MODEL | - | - | -0.9 [-5.2, +3.6] h0.519 a-0.011 | -7.6 [-15.7, +1.1] h0.500 a-0.054 |
| g330 | ALLCE | - | - | +5.0 [+0.6, +9.4] h0.541 a+0.063 | +14.6 [+3.7, +26.8] h0.558 a+0.104 |

**fy**

| set | method | +30 min | to close | D+1 | D+3 |
|---|---|---|---|---|---|
| grid | ST5 | +0.4 [-0.1, +0.8] h0.512 a+0.025 | +0.3 [-1.1, +1.7] h0.513 a+0.009 | +1.8 [-1.7, +4.9] h0.515 a+0.022 | +1.8 [-3.5, +5.5] h0.511 a+0.013 |
| grid | ST15 | +0.1 [-0.4, +0.6] h0.507 a+0.007 | -0.3 [-1.9, +1.3] h0.510 a-0.008 | +1.9 [-1.9, +5.7] h0.517 a+0.024 | +1.7 [-4.6, +6.7] h0.511 a+0.013 |
| grid | ST30 | +0.0 [-0.5, +0.5] h0.508 a+0.000 | -2.1 [-3.8, -0.5] h0.489 a-0.061 | -0.7 [-5.7, +4.2] h0.500 a-0.008 | +1.2 [-6.9, +8.4] h0.507 a+0.009 |
| grid | DST | +0.3 [-0.2, +0.8] h0.509 a+0.021 | -0.7 [-2.6, +1.0] h0.501 a-0.021 | -1.1 [-5.9, +3.9] h0.497 a-0.014 | -3.1 [-14.1, +8.3] h0.494 a-0.023 |
| grid | PIVP | -0.1 [-0.7, +0.4] h0.509 a-0.009 | -1.2 [-3.0, +0.5] h0.504 a-0.036 | +2.0 [-3.0, +6.5] h0.516 a+0.025 | +2.4 [-4.9, +9.0] h0.515 a+0.018 |
| grid | T5 | +0.1 [-0.5, +0.7] h0.510 a+0.005 | -1.2 [-3.0, +0.6] h0.496 a-0.033 | -0.8 [-5.5, +3.8] h0.502 a-0.010 | +3.8 [-6.6, +13.7] h0.527 a+0.028 |
| grid | T20 | +0.1 [-0.4, +0.6] h0.508 a+0.009 | -0.8 [-2.7, +1.0] h0.501 a-0.024 | +0.6 [-4.6, +5.7] h0.512 a+0.007 | +2.8 [-9.2, +13.4] h0.518 a+0.021 |
| grid | SER | +0.1 [-0.5, +0.5] h0.505 a+0.004 | -1.1 [-2.7, +0.5] h0.501 a-0.032 | +3.2 [-1.4, +7.6] h0.520 a+0.039 | +8.2 [-0.7, +16.3] h0.539 a+0.062 |
| grid | ERG | +0.3 [-0.5, +1.1] h0.520 a+0.019 | -2.9 [-5.7, -0.1] h0.495 a-0.083 | -6.2 [-12.9, +0.7] h0.500 a-0.079 | -5.0 [-19.5, +10.0] h0.500 a-0.040 |
| grid | VIX2 | -0.4 [-1.0, +0.2] h0.497 a-0.027 | -0.1 [-2.1, +1.9] h0.500 a-0.002 | +2.7 [-2.9, +8.5] h0.494 a+0.034 | +6.5 [-6.9, +19.6] h0.504 a+0.049 |
| grid | VIXHI | -0.2 [-1.5, +1.1] h0.507 a-0.011 | +2.7 [-2.1, +7.1] h0.520 a+0.063 | +13.1 [+0.2, +24.7] h0.536 a+0.130 | +33.5 [+4.9, +59.0] h0.570 a+0.213 |
| grid | STRESS | +0.7 [-0.8, +2.0] h0.511 a+0.038 | +3.9 [-0.7, +8.5] h0.545 a+0.099 | +13.7 [+0.3, +24.2] h0.553 a+0.139 | +27.3 [+5.9, +50.8] h0.579 a+0.183 |
| grid | MACDspot | -0.1 [-0.7, +0.4] h0.505 a-0.010 | -2.3 [-4.0, -0.6] h0.486 a-0.066 | -0.4 [-5.5, +4.3] h0.503 a-0.005 | +3.8 [-4.8, +12.0] h0.519 a+0.029 |
| grid | MODEL | +0.2 [-0.8, +1.2] h0.510 a+0.010 | -0.0 [-1.1, +1.1] h0.512 a-0.001 | +1.5 [-3.6, +6.1] h0.521 a+0.020 | -2.9 [-11.7, +6.3] h0.505 a-0.022 |
| grid | ALLCE | +0.0 [-0.6, +0.6] h0.512 a+0.002 | +0.7 [-1.4, +2.8] h0.514 a+0.019 | +5.0 [-0.7, +10.6] h0.531 a+0.061 | +13.5 [-0.1, +27.5] h0.551 a+0.102 |
| g330 | ST5 | +0.5 [-0.5, +1.4] h0.511 a+0.027 | +0.5 [-0.5, +1.6] h0.506 a+0.028 | -0.1 [-4.5, +3.8] h0.496 a-0.002 | -0.6 [-8.1, +5.9] h0.500 a-0.005 |
| g330 | ST15 | +0.3 [-0.8, +1.3] h0.503 a+0.015 | +0.4 [-0.8, +1.6] h0.503 a+0.023 | +0.8 [-3.6, +4.9] h0.506 a+0.011 | +0.8 [-6.8, +7.9] h0.504 a+0.006 |
| g330 | ST30 | -0.3 [-1.3, +0.7] h0.500 a-0.017 | -1.1 [-2.2, +0.0] h0.480 a-0.059 | -0.5 [-5.2, +4.0] h0.499 a-0.006 | -1.6 [-9.9, +6.5] h0.496 a-0.012 |
| g330 | DST | +1.0 [-0.1, +1.9] h0.519 a+0.056 | +0.4 [-0.7, +1.5] h0.505 a+0.021 | +0.1 [-4.3, +4.6] h0.509 a+0.001 | -1.8 [-12.8, +8.6] h0.498 a-0.014 |
| g330 | PIVP | -0.1 [-1.1, +1.0] h0.507 a-0.005 | -0.6 [-1.7, +0.6] h0.495 a-0.030 | +1.3 [-3.5, +5.6] h0.512 a+0.017 | +0.4 [-7.5, +7.8] h0.507 a+0.003 |
| g330 | T5 | +0.5 [-0.6, +1.6] h0.511 a+0.032 | -0.7 [-1.9, +0.4] h0.490 a-0.038 | -0.3 [-5.2, +4.1] h0.503 a-0.004 | +3.4 [-6.7, +12.9] h0.521 a+0.026 |
| g330 | T20 | +0.8 [-0.2, +1.8] h0.516 a+0.045 | +0.1 [-1.0, +1.2] h0.507 a+0.005 | +1.5 [-3.4, +6.3] h0.521 a+0.020 | +4.0 [-7.6, +14.0] h0.522 a+0.031 |
| g330 | SER | +0.2 [-0.7, +1.2] h0.505 a+0.013 | -0.6 [-1.7, +0.4] h0.493 a-0.033 | +3.5 [-0.8, +7.6] h0.519 a+0.047 | +8.3 [-1.0, +16.4] h0.538 a+0.064 |
| g330 | ERG | +1.2 [-0.5, +2.8] h0.534 a+0.071 | -0.5 [-2.4, +1.2] h0.506 a-0.028 | -4.1 [-11.3, +2.2] h0.508 a-0.057 | -2.7 [-16.9, +12.7] h0.502 a-0.022 |
| g330 | VIX2 | -1.9 [-3.0, -0.7] h0.485 a-0.108 | -1.2 [-2.5, +0.0] h0.498 a-0.065 | +2.2 [-3.0, +7.7] h0.498 a+0.030 | +6.9 [-5.8, +19.2] h0.505 a+0.053 |
| g330 | VIXHI | -0.8 [-3.3, +1.5] h0.502 a-0.042 | +0.9 [-1.7, +3.5] h0.540 a+0.041 | +11.1 [-0.9, +22.9] h0.542 a+0.120 | +30.1 [+1.7, +54.0] h0.565 a+0.197 |
| g330 | STRESS | +0.9 [-1.5, +3.1] h0.521 a+0.043 | +2.7 [+0.3, +5.3] h0.560 a+0.128 | +11.5 [+1.1, +21.1] h0.554 a+0.133 | +20.3 [+0.0, +40.7] h0.579 a+0.144 |
| g330 | MACDspot | -0.4 [-1.5, +0.6] h0.493 a-0.023 | -1.5 [-2.7, -0.4] h0.474 a-0.080 | -0.2 [-5.1, +4.3] h0.503 a-0.002 | +2.4 [-6.4, +10.8] h0.512 a+0.019 |
| g330 | MODEL | +0.2 [-0.8, +1.2] h0.510 a+0.010 | -0.0 [-1.1, +1.1] h0.512 a-0.001 | +1.5 [-3.6, +6.1] h0.521 a+0.020 | -2.9 [-11.7, +6.3] h0.505 a-0.022 |
| g330 | ALLCE | +0.7 [-0.5, +1.8] h0.519 a+0.038 | +1.0 [-0.3, +2.3] h0.528 a+0.052 | +5.2 [+0.1, +10.0] h0.537 a+0.070 | +13.8 [+1.0, +27.7] h0.555 a+0.106 |
| ev_st5 | EV | +0.5 [-0.2, +1.2] h0.498 a+0.030 | +2.0 [+0.8, +3.4] h0.505 a+0.046 | +2.3 [+0.2, +4.3] h0.504 a+0.027 | +2.0 [-1.0, +4.5] h0.503 a+0.015 |
| ev_st15 | EV | +0.8 [-0.1, +1.7] h0.512 a+0.047 | +3.6 [+1.5, +5.5] h0.527 a+0.080 | +4.6 [+1.5, +7.5] h0.519 a+0.052 | +3.6 [-0.7, +7.2] h0.511 a+0.027 |
| ev_st30 | EV | +0.8 [-0.8, +2.3] h0.531 a+0.047 | +2.9 [-1.5, +7.3] h0.548 a+0.064 | +3.1 [-4.4, +10.7] h0.526 a+0.035 | -2.6 [-11.8, +5.2] h0.500 a-0.019 |
| ev_piv | EV | -0.7 [-1.6, +0.2] h0.493 a-0.035 | -1.4 [-3.4, +0.6] h0.501 a-0.026 | +0.6 [-3.2, +4.3] h0.511 a+0.007 | +1.3 [-4.1, +6.3] h0.507 a+0.010 |

**ix**

| set | method | +30 min | to close | D+1 | D+3 |
|---|---|---|---|---|---|
| grid | ST5 | +1.0 [-0.0, +2.1] h0.549 a+0.094 | +1.1 [-1.8, +4.2] h0.543 a+0.047 | +1.5 [-5.7, +8.9] h0.521 a+0.027 | -1.2 [-8.6, +7.8] h0.494 a-0.014 |
| grid | ST15 | +0.8 [-0.2, +2.0] h0.549 a+0.078 | +0.5 [-2.6, +3.7] h0.540 a+0.021 | +3.8 [-2.9, +10.3] h0.532 a+0.070 | -4.6 [-13.9, +5.8] h0.494 a-0.053 |
| grid | ST30 | +0.3 [-0.8, +1.3] h0.526 a+0.023 | -2.6 [-5.7, +0.9] h0.489 a-0.107 | -11.0 [-20.6, -2.0] h0.441 a-0.200 | -16.8 [-28.0, -2.6] h0.454 a-0.193 |
| grid | DST | +1.0 [-0.1, +2.2] h0.526 a+0.094 | -1.0 [-4.9, +3.0] h0.488 a-0.044 | -3.3 [-12.0, +6.4] h0.436 a-0.059 | -10.6 [-29.1, +11.8] h0.439 a-0.122 |
| grid | PIVP | +1.0 [-0.2, +2.1] h0.565 a+0.090 | +0.5 [-2.7, +3.8] h0.536 a+0.020 | -0.8 [-10.2, +7.9] h0.509 a-0.015 | -12.9 [-27.1, +0.2] h0.454 a-0.148 |
| grid | T5 | -0.7 [-1.7, +0.3] h0.495 a-0.063 | -2.6 [-6.2, +1.1] h0.464 a-0.110 | -7.5 [-17.9, +1.8] h0.448 a-0.136 | -1.9 [-18.0, +13.3] h0.509 a-0.022 |
| grid | T20 | +0.1 [-1.0, +1.3] h0.495 a+0.012 | -1.9 [-5.8, +2.1] h0.468 a-0.081 | -2.9 [-12.1, +5.9] h0.443 a-0.053 | -5.1 [-21.3, +16.0] h0.484 a-0.058 |
| grid | SER | -0.2 [-1.1, +0.8] h0.517 a-0.014 | -1.8 [-4.9, +1.1] h0.491 a-0.076 | -4.8 [-13.4, +3.9] h0.473 a-0.087 | -5.3 [-17.9, +9.5] h0.495 a-0.061 |
| grid | ERG | +1.9 [+0.2, +3.4] h0.559 a+0.170 | -4.6 [-10.5, +1.3] h0.463 a-0.195 | -16.6 [-32.1, -2.1] h0.359 a-0.298 | -16.2 [-46.2, +16.7] h0.422 a-0.185 |
| grid | VIX2 | -0.2 [-1.3, +0.9] h0.488 a-0.022 | +0.9 [-3.1, +4.7] h0.524 a+0.040 | +3.9 [-5.5, +13.1] h0.514 a+0.071 | +3.9 [-14.6, +26.2] h0.516 a+0.045 |
| grid | VIXHI | -0.1 [-3.9, +3.2] h0.520 a-0.009 | -1.4 [-15.4, +11.8] h0.508 a-0.043 | +12.7 [-14.0, +39.7] h0.500 a+0.171 | +30.7 [+1.5, +75.5] h0.599 a+0.324 |
| grid | STRESS | -2.8 [-7.2, +1.1] h0.471 a-0.186 | -4.7 [-16.0, +5.4] h0.508 a-0.177 | +27.1 [+0.7, +56.4] h0.567 a+0.389 | +32.4 [+6.5, +75.9] h0.626 a+0.366 |
| grid | MACDspot | -0.6 [-1.7, +0.5] h0.498 a-0.058 | -3.0 [-6.3, +0.5] h0.476 a-0.124 | -7.8 [-18.0, +1.2] h0.461 a-0.143 | -5.0 [-22.0, +7.0] h0.479 a-0.058 |
| grid | MACDr | -1.0 [-2.3, +0.2] h0.492 a-0.095 | -3.1 [-6.2, +0.1] h0.462 a-0.128 | -8.9 [-18.6, +0.6] h0.461 a-0.161 | -10.8 [-28.6, +5.5] h0.460 a-0.128 |
| grid | MODEL | -0.5 [-2.6, +1.6] h0.486 a-0.039 | -0.5 [-2.9, +1.8] h0.516 a-0.040 | -0.9 [-9.8, +7.9] h0.491 a-0.017 | -2.0 [-15.5, +14.2] h0.505 a-0.023 |
| grid | ALLCE | +0.1 [-1.1, +1.3] h0.523 a+0.007 | -2.0 [-5.9, +1.9] h0.485 a-0.082 | -0.3 [-9.3, +9.8] h0.481 a-0.005 | +0.7 [-15.8, +25.3] h0.505 a+0.009 |
| g330 | ST5 | +0.2 [-2.0, +2.2] h0.495 a+0.015 | -0.2 [-2.7, +2.2] h0.512 a-0.016 | -5.1 [-12.7, +4.1] h0.472 a-0.099 | +5.3 [-7.1, +19.7] h0.519 a+0.063 |
| g330 | ST15 | +0.8 [-1.4, +3.0] h0.523 a+0.066 | +0.1 [-2.6, +2.7] h0.535 a+0.006 | +1.9 [-7.4, +10.7] h0.514 a+0.036 | +6.2 [-8.7, +17.7] h0.523 a+0.072 |
| g330 | ST30 | +0.1 [-2.2, +2.2] h0.532 a+0.006 | -0.7 [-3.2, +1.8] h0.493 a-0.056 | -11.7 [-21.3, -3.4] h0.428 a-0.228 | -22.5 [-36.5, -6.7] h0.426 a-0.264 |
| g330 | DST | +2.7 [+0.5, +4.9] h0.565 a+0.216 | +0.6 [-2.2, +3.2] h0.507 a+0.047 | -1.5 [-9.5, +7.4] h0.475 a-0.030 | -8.7 [-26.8, +13.1] h0.449 a-0.102 |
| g330 | PIVP | +1.0 [-1.3, +3.1] h0.549 a+0.080 | +0.9 [-1.6, +3.4] h0.537 a+0.070 | -1.5 [-11.3, +6.7] h0.486 a-0.030 | -13.7 [-33.0, -0.0] h0.433 a-0.161 |
| g330 | T5 | +0.2 [-1.8, +2.3] h0.509 a+0.017 | -1.2 [-3.7, +1.5] h0.470 a-0.091 | -6.6 [-16.5, +2.2] h0.447 a-0.128 | +0.1 [-17.0, +14.1] h0.509 a+0.001 |
| g330 | T20 | +0.7 [-1.4, +2.9] h0.514 a+0.060 | +0.1 [-2.5, +2.6] h0.488 a+0.008 | -0.8 [-9.4, +7.7] h0.493 a-0.016 | -1.3 [-18.5, +18.5] h0.486 a-0.016 |
| g330 | SER | -0.3 [-2.2, +1.5] h0.493 a-0.027 | -1.1 [-3.1, +1.2] h0.463 a-0.083 | -5.3 [-14.1, +3.2] h0.454 a-0.104 | -2.7 [-17.3, +11.2] h0.493 a-0.032 |
| g330 | ERG | +1.5 [-2.1, +5.2] h0.518 a+0.108 | -0.9 [-5.4, +3.8] h0.482 a-0.062 | -13.8 [-27.6, +1.2] h0.390 a-0.263 | -15.8 [-45.8, +14.8] h0.433 a-0.185 |
| g330 | VIX2 | +0.6 [-1.6, +2.8] h0.514 a+0.049 | -0.1 [-2.7, +2.4] h0.512 a-0.006 | +2.9 [-5.3, +11.6] h0.505 a+0.056 | +1.6 [-16.9, +22.8] h0.500 a+0.019 |
| g330 | VIXHI | +3.0 [-3.0, +8.7] h0.585 a+0.209 | -1.2 [-10.2, +7.3] h0.488 a-0.058 | +17.7 [-6.6, +46.9] h0.561 a+0.259 | +28.2 [+2.0, +79.4] h0.610 a+0.284 |
| g330 | STRESS | -1.8 [-7.8, +4.2] h0.529 a-0.112 | -2.6 [-10.1, +4.3] h0.540 a-0.155 | +22.8 [+0.4, +49.8] h0.609 a+0.385 | +27.6 [+1.3, +61.7] h0.609 a+0.351 |
| g330 | MACDspot | -0.5 [-2.8, +1.5] h0.488 a-0.042 | -1.3 [-3.8, +1.1] h0.454 a-0.098 | -6.6 [-16.3, +2.2] h0.458 a-0.129 | -6.3 [-25.0, +6.8] h0.470 a-0.074 |
| g330 | MACDr | -1.2 [-3.5, +1.1] h0.488 a-0.100 | -1.4 [-4.3, +1.4] h0.450 a-0.104 | -7.0 [-17.5, +2.7] h0.457 a-0.135 | -16.6 [-35.4, -0.6] h0.426 a-0.199 |
| g330 | MODEL | -0.5 [-2.6, +1.6] h0.486 a-0.039 | -0.5 [-2.9, +1.8] h0.516 a-0.040 | -0.9 [-9.8, +7.9] h0.491 a-0.017 | -2.0 [-15.5, +14.2] h0.505 a-0.023 |
| g330 | ALLCE | +0.2 [-2.1, +2.4] h0.528 a+0.018 | -0.7 [-3.4, +1.8] h0.493 a-0.053 | +1.1 [-7.4, +10.2] h0.512 a+0.021 | +2.2 [-13.5, +25.2] h0.519 a+0.026 |
| ev_macd | EV | +0.2 [-2.1, +3.0] h0.497 a+0.015 | -2.6 [-10.0, +3.6] h0.497 a-0.092 | +6.0 [-6.6, +20.0] h0.516 a+0.123 | +6.3 [-15.8, +30.9] h0.547 a+0.077 |
| ev_st5 | EV | +0.6 [-1.0, +2.2] h0.489 a+0.045 | +1.4 [-1.2, +4.3] h0.518 a+0.046 | +0.3 [-2.9, +4.1] h0.515 a+0.005 | +5.4 [+1.2, +11.4] h0.521 a+0.062 |
| ev_st15 | EV | +2.7 [+0.4, +5.2] h0.542 a+0.202 | +4.8 [+0.6, +9.8] h0.554 a+0.153 | +8.7 [+2.9, +15.7] h0.564 a+0.152 | +16.1 [+8.2, +25.4] h0.571 a+0.180 |
| ev_st30 | EV | +3.3 [+0.1, +6.2] h0.579 a+0.273 | +8.0 [-0.5, +16.6] h0.598 a+0.271 | +8.2 [-3.9, +21.4] h0.573 a+0.150 | -8.6 [-29.7, +9.2] h0.494 a-0.093 |
| ev_piv | EV | +0.3 [-1.7, +2.3] h0.510 a+0.020 | -1.1 [-4.8, +3.0] h0.498 a-0.028 | -2.0 [-9.9, +5.5] h0.505 a-0.034 | -7.3 [-18.3, +1.8] h0.451 a-0.082 |

**tbI**

| set | method | +30 min | to close | D+1 | D+3 |
|---|---|---|---|---|---|
| grid | ST30 | +0.4 [-0.6, +1.4] h0.526 a+0.028 | -1.1 [-4.4, +2.1] h0.505 a-0.038 | -7.9 [-17.3, +1.4] h0.479 a-0.117 | -14.8 [-27.3, +0.7] h0.464 a-0.138 |
| grid | DST | +0.8 [-0.2, +1.8] h0.523 a+0.066 | -1.5 [-5.7, +2.3] h0.479 a-0.052 | -5.8 [-15.7, +4.8] h0.454 a-0.085 | -13.9 [-31.7, +10.7] h0.440 a-0.129 |
| grid | PIVP | +1.0 [+0.0, +1.9] h0.553 a+0.079 | +1.3 [-2.2, +4.7] h0.532 a+0.044 | -0.1 [-9.5, +8.2] h0.515 a-0.002 | -11.1 [-25.0, +1.3] h0.484 a-0.103 |
| grid | T5 | -0.8 [-1.8, +0.2] h0.487 a-0.064 | -2.2 [-6.0, +1.6] h0.483 a-0.076 | -10.0 [-18.7, -0.3] h0.454 a-0.148 | -9.5 [-23.7, +7.6] h0.479 a-0.088 |
| grid | T20 | +0.2 [-0.9, +1.1] h0.501 a+0.013 | -2.3 [-6.3, +1.4] h0.474 a-0.077 | -5.5 [-14.8, +4.8] h0.469 a-0.081 | -12.1 [-29.8, +11.8] h0.471 a-0.113 |
| grid | SER | +0.1 [-0.8, +0.9] h0.510 a+0.006 | -2.1 [-5.2, +1.0] h0.511 a-0.071 | -5.1 [-14.2, +4.1] h0.495 a-0.075 | -11.3 [-27.8, +7.7] h0.487 a-0.105 |
| grid | ERG | +1.4 [+0.1, +2.6] h0.536 a+0.119 | -4.2 [-9.5, +1.3] h0.486 a-0.144 | -14.6 [-30.3, -0.0] h0.441 a-0.214 | -29.4 [-59.3, +2.0] h0.395 a-0.285 |
| grid | VIX2 | -0.2 [-1.3, +1.0] h0.488 a-0.012 | +1.9 [-2.4, +6.1] h0.524 a+0.066 | +4.9 [-5.6, +17.2] h0.509 a+0.073 | +4.2 [-18.5, +29.7] h0.499 a+0.039 |
| grid | VIXHI | +0.6 [-3.6, +4.4] h0.520 a+0.034 | +0.2 [-18.4, +17.6] h0.530 a+0.005 | +20.8 [-16.4, +64.6] h0.552 a+0.212 | +57.4 [+28.8, +135.3] h0.661 a+0.428 |
| grid | STRESS | -2.0 [-6.1, +1.5] h0.466 a-0.126 | -6.2 [-15.5, +3.2] h0.456 a-0.202 | +10.9 [-12.5, +37.4] h0.559 a+0.141 | +32.9 [+8.5, +74.2] h0.653 a+0.305 |
| grid | MACDspot | -0.4 [-1.4, +0.7] h0.501 a-0.028 | -1.7 [-4.7, +1.2] h0.496 a-0.058 | -7.0 [-16.4, +2.0] h0.476 a-0.103 | -13.8 [-27.7, -0.1] h0.460 a-0.128 |
| grid | MACDr | -0.5 [-1.5, +0.6] h0.500 a-0.037 | -2.8 [-6.1, +0.4] h0.497 a-0.097 | -8.6 [-18.0, +0.6] h0.456 a-0.131 | -15.6 [-27.7, -2.2] h0.448 a-0.153 |
| grid | MODEL | -0.3 [-2.4, +1.9] h0.498 a-0.018 | +0.6 [-1.9, +3.0] h0.522 a+0.038 | -0.2 [-10.1, +8.3] h0.514 a-0.004 | +1.5 [-12.7, +17.8] h0.525 a+0.015 |
| grid | ALLCE | +0.6 [-0.7, +1.9] h0.526 a+0.048 | -1.6 [-6.4, +3.0] h0.494 a-0.053 | +1.9 [-9.1, +14.2] h0.514 a+0.028 | +7.2 [-12.9, +32.6] h0.541 a+0.067 |
| g330 | ST30 | +0.6 [-1.4, +2.5] h0.533 a+0.043 | +0.5 [-1.8, +2.6] h0.514 a+0.028 | -7.9 [-16.7, +1.6] h0.479 a-0.124 | -18.7 [-32.3, -3.6] h0.456 a-0.176 |
| g330 | DST | +1.0 [-1.2, +3.0] h0.541 a+0.065 | -0.2 [-3.0, +2.2] h0.508 a-0.010 | -4.4 [-13.4, +5.4] h0.468 a-0.069 | -12.6 [-29.8, +11.3] h0.445 a-0.118 |
| g330 | PIVP | +1.5 [-0.6, +3.5] h0.546 a+0.098 | +1.9 [-0.5, +4.0] h0.547 a+0.115 | -1.0 [-10.3, +7.5] h0.508 a-0.016 | -12.6 [-28.1, +0.5] h0.471 a-0.118 |
| g330 | T5 | -0.8 [-2.9, +1.1] h0.478 a-0.055 | -1.1 [-3.3, +1.0] h0.496 a-0.067 | -9.4 [-17.8, -1.2] h0.447 a-0.148 | -8.2 [-22.9, +8.6] h0.474 a-0.078 |
| g330 | T20 | +0.4 [-1.8, +2.5] h0.512 a+0.028 | -0.3 [-2.9, +2.0] h0.499 a-0.020 | -3.5 [-12.7, +5.5] h0.490 a-0.055 | -10.3 [-27.5, +12.6] h0.481 a-0.097 |
| g330 | SER | +1.4 [-0.4, +3.2] h0.541 a+0.092 | +1.0 [-1.1, +3.0] h0.544 a+0.060 | -3.4 [-11.7, +5.7] h0.509 a-0.054 | -11.8 [-28.1, +6.4] h0.487 a-0.111 |
| g330 | ERG | +1.2 [-1.8, +4.4] h0.541 a+0.082 | +0.5 [-3.2, +4.2] h0.546 a+0.032 | -9.8 [-25.5, +5.8] h0.468 a-0.151 | -26.0 [-57.3, +4.3] h0.412 a-0.257 |
| g330 | VIX2 | +0.3 [-2.1, +2.7] h0.512 a+0.021 | +1.1 [-1.9, +3.8] h0.524 a+0.069 | +3.9 [-6.3, +14.7] h0.499 a+0.061 | +2.1 [-18.8, +26.6] h0.475 a+0.020 |
| g330 | VIXHI | +4.7 [-4.0, +13.1] h0.641 a+0.224 | +4.1 [-7.9, +15.3] h0.600 a+0.153 | +32.2 [+2.3, +70.9] h0.606 a+0.356 | +60.8 [+21.7, +148.7] h0.647 a+0.435 |
| g330 | STRESS | -2.8 [-9.1, +3.5] h0.471 a-0.152 | -3.8 [-10.9, +3.2] h0.482 a-0.189 | +16.0 [-0.4, +36.3] h0.607 a+0.238 | +35.3 [+11.6, +68.4] h0.665 a+0.333 |
| g330 | MACDspot | +0.1 [-1.8, +2.0] h0.505 a+0.007 | -0.1 [-2.4, +2.0] h0.506 a-0.009 | -6.3 [-15.3, +3.3] h0.482 a-0.099 | -16.9 [-31.9, -2.5] h0.453 a-0.158 |
| g330 | MACDr | +0.2 [-1.8, +2.1] h0.505 a+0.012 | +0.1 [-2.3, +2.3] h0.509 a+0.006 | -5.5 [-15.5, +3.8] h0.472 a-0.089 | -17.7 [-31.1, -2.9] h0.434 a-0.176 |
| g330 | MODEL | -0.3 [-2.4, +1.9] h0.498 a-0.018 | +0.6 [-1.9, +3.0] h0.522 a+0.038 | -0.2 [-10.1, +8.3] h0.514 a-0.004 | +1.5 [-12.7, +17.8] h0.525 a+0.015 |
| g330 | ALLCE | +1.4 [-1.0, +3.8] h0.553 a+0.096 | +0.2 [-2.7, +3.0] h0.519 a+0.011 | +3.7 [-6.7, +14.9] h0.536 a+0.058 | +9.0 [-10.7, +32.9] h0.555 a+0.084 |
| ev_macd | EV | +2.3 [-0.1, +4.6] h0.508 a+0.164 | +9.7 [+2.6, +17.0] h0.645 a+0.309 | +11.6 [-2.4, +24.1] h0.645 a+0.183 | -20.8 [-51.1, +5.1] h0.482 a-0.188 |
| ev_st30 | EV | +3.9 [+1.0, +6.9] h0.567 a+0.273 | +10.1 [+2.0, +18.7] h0.584 a+0.272 | +7.2 [-7.9, +21.2] h0.558 a+0.100 | -2.3 [-25.1, +16.6] h0.538 a-0.020 |
| ev_piv | EV | +0.2 [-1.2, +1.7] h0.500 a+0.016 | +1.0 [-3.4, +5.5] h0.517 a+0.024 | +0.4 [-8.0, +7.7] h0.524 a+0.006 | -7.4 [-18.4, +3.5] h0.486 a-0.068 |

**tbS**

| set | method | +30 min | to close | D+1 | D+3 |
|---|---|---|---|---|---|
| grid | ST30 | +0.0 [-0.6, +0.6] h0.493 a+0.000 | -0.8 [-3.1, +1.5] h0.488 a-0.013 | -1.9 [-8.5, +4.6] h0.490 a-0.013 | -6.5 [-15.2, +4.4] h0.487 a-0.028 |
| grid | DST | +0.1 [-0.4, +0.7] h0.495 a+0.005 | -2.1 [-4.6, +0.0] h0.483 a-0.035 | -1.9 [-8.0, +4.0] h0.494 a-0.014 | -0.7 [-13.4, +14.3] h0.497 a-0.003 |
| grid | PIVP | +0.4 [-0.3, +1.1] h0.495 a+0.014 | +0.8 [-1.7, +3.2] h0.492 a+0.013 | +1.6 [-5.2, +7.3] h0.498 a+0.011 | -2.7 [-10.7, +5.7] h0.494 a-0.012 |
| grid | T5 | -0.3 [-1.0, +0.3] h0.487 a-0.013 | -2.0 [-4.6, +0.4] h0.479 a-0.033 | -3.8 [-10.2, +2.6] h0.483 a-0.027 | -7.2 [-19.8, +6.5] h0.484 a-0.031 |
| grid | T20 | -0.0 [-0.6, +0.6] h0.492 a-0.001 | -2.2 [-4.7, +0.0] h0.480 a-0.035 | -2.1 [-8.7, +5.1] h0.490 a-0.015 | -4.4 [-17.7, +12.8] h0.491 a-0.019 |
| grid | SER | +0.0 [-0.6, +0.6] h0.493 a+0.001 | -1.2 [-3.5, +0.8] h0.485 a-0.021 | +0.7 [-4.6, +5.9] h0.493 a+0.005 | +0.7 [-10.6, +13.0] h0.495 a+0.003 |
| grid | ERG | +0.7 [-0.2, +1.7] h0.498 a+0.027 | -3.1 [-7.0, +0.9] h0.467 a-0.050 | -5.2 [-16.4, +5.5] h0.481 a-0.036 | -10.4 [-33.8, +12.9] h0.483 a-0.044 |
| grid | VIX2 | -0.1 [-1.5, +1.3] h0.499 a-0.004 | +2.6 [-2.7, +8.1] h0.520 a+0.042 | +8.9 [-4.2, +23.5] h0.525 a+0.063 | +12.3 [-15.1, +45.1] h0.518 a+0.053 |
| grid | VIXHI | +0.8 [-3.8, +5.4] h0.500 a+0.025 | -0.8 [-23.0, +19.8] h0.508 a-0.010 | +30.5 [-13.2, +80.1] h0.538 a+0.176 | +88.7 [+50.8, +186.8] h0.582 a+0.327 |
| grid | STRESS | -0.0 [-2.7, +2.7] h0.491 a-0.000 | -1.3 [-8.8, +6.4] h0.483 a-0.020 | +1.5 [-15.9, +21.2] h0.495 a+0.010 | +6.0 [-27.1, +44.7] h0.493 a+0.025 |
| grid | MACDspot | -0.1 [-0.7, +0.6] h0.493 a-0.003 | -1.4 [-3.9, +1.0] h0.484 a-0.024 | -2.6 [-9.7, +4.5] h0.487 a-0.019 | -8.5 [-18.6, +1.8] h0.483 a-0.037 |
| grid | MACDr | -0.0 [-0.7, +0.6] h0.495 a-0.001 | -1.2 [-3.6, +1.0] h0.486 a-0.023 | -3.6 [-9.7, +1.9] h0.482 a-0.029 | -11.5 [-20.4, -3.6] h0.480 a-0.056 |
| grid | ALLCE | +0.6 [-0.8, +2.1] h0.491 a+0.024 | -2.4 [-8.3, +3.4] h0.479 a-0.039 | +1.8 [-12.2, +16.7] h0.486 a+0.013 | +8.2 [-18.6, +41.2] h0.497 a+0.035 |
| g330 | ST30 | +1.2 [-0.2, +2.4] h0.508 a+0.043 | +0.2 [-1.4, +1.6] h0.491 a+0.006 | -1.1 [-7.7, +5.2] h0.492 a-0.009 | -6.8 [-16.8, +4.2] h0.486 a-0.030 |
| g330 | DST | -0.0 [-1.2, +0.9] h0.498 a-0.001 | -1.4 [-2.8, -0.2] h0.481 a-0.042 | -1.2 [-6.6, +4.0] h0.496 a-0.009 | +0.1 [-12.4, +14.6] h0.499 a+0.000 |
| g330 | PIVP | +1.4 [+0.1, +2.7] h0.507 a+0.051 | +0.8 [-0.7, +2.3] h0.493 a+0.024 | +0.7 [-5.8, +6.7] h0.495 a+0.006 | -4.3 [-13.0, +5.6] h0.490 a-0.019 |
| g330 | T5 | +0.1 [-1.1, +1.4] h0.495 a+0.005 | -0.9 [-2.3, +0.6] h0.482 a-0.027 | -3.3 [-9.0, +2.7] h0.484 a-0.026 | -7.0 [-18.7, +7.2] h0.484 a-0.031 |
| g330 | T20 | -0.1 [-1.5, +1.1] h0.495 a-0.003 | -1.0 [-2.6, +0.3] h0.481 a-0.031 | -1.3 [-7.3, +5.2] h0.490 a-0.010 | -3.7 [-16.3, +13.1] h0.491 a-0.016 |
| g330 | SER | +1.0 [-0.2, +2.2] h0.509 a+0.037 | +0.0 [-1.4, +1.3] h0.491 a+0.001 | +1.9 [-3.0, +6.8] h0.494 a+0.015 | +1.6 [-9.2, +13.9] h0.497 a+0.007 |
| g330 | ERG | +0.8 [-1.3, +2.9] h0.503 a+0.028 | -1.4 [-4.1, +1.1] h0.472 a-0.043 | -4.1 [-14.8, +5.8] h0.483 a-0.031 | -10.3 [-33.8, +12.3] h0.484 a-0.045 |
| g330 | VIX2 | -0.3 [-3.0, +2.6] h0.491 a-0.009 | +0.8 [-2.6, +4.2] h0.507 a+0.024 | +7.0 [-5.2, +20.0] h0.523 a+0.054 | +9.6 [-16.7, +41.3] h0.515 a+0.043 |
| g330 | VIXHI | +4.2 [-6.8, +14.3] h0.560 a+0.120 | +3.0 [-12.3, +16.1] h0.533 a+0.068 | +43.0 [+6.8, +85.3] h0.570 a+0.267 | +91.2 [+49.8, +192.7] h0.587 a+0.340 |
| g330 | STRESS | +0.0 [-5.3, +5.4] h0.508 a+0.000 | -0.5 [-6.0, +5.0] h0.486 a-0.013 | +4.7 [-11.2, +22.4] h0.508 a+0.034 | +11.3 [-20.3, +49.8] h0.501 a+0.049 |
| g330 | MACDspot | +0.9 [-0.6, +2.3] h0.505 a+0.032 | -0.2 [-1.9, +1.3] h0.487 a-0.008 | -1.8 [-8.4, +4.7] h0.489 a-0.014 | -8.5 [-18.8, +2.3] h0.482 a-0.038 |
| g330 | MACDr | +0.9 [-0.7, +2.4] h0.507 a+0.037 | +0.4 [-1.3, +1.9] h0.493 a+0.012 | -2.2 [-8.3, +3.4] h0.488 a-0.019 | -11.5 [-23.4, -2.0] h0.479 a-0.057 |
| g330 | ALLCE | +2.3 [-0.7, +5.1] h0.527 a+0.084 | +0.4 [-3.3, +3.7] h0.494 a+0.013 | +4.6 [-7.8, +18.3] h0.494 a+0.036 | +11.0 [-14.2, +43.1] h0.502 a+0.049 |
| ev_macd | EV | +1.8 [+0.8, +2.8] h0.506 a+0.068 | +6.8 [+2.3, +11.0] h0.520 a+0.111 | +9.9 [+2.4, +17.9] h0.515 a+0.076 | +2.6 [-15.9, +18.9] h0.504 a+0.012 |
| ev_st30 | EV | +0.3 [-1.3, +1.8] h0.487 a+0.008 | +6.5 [+0.9, +12.4] h0.503 a+0.081 | +10.2 [+1.5, +19.4] h0.514 a+0.067 | +9.0 [-7.0, +24.9] h0.515 a+0.037 |
| ev_piv | EV | +0.3 [-0.9, +1.4] h0.487 a+0.008 | +0.2 [-2.9, +3.4] h0.489 a+0.003 | +0.6 [-5.3, +5.5] h0.497 a+0.004 | -2.8 [-10.1, +4.5] h0.494 a-0.012 |

**liveI**

| set | method | +30 min | to close | D+1 | D+3 |
|---|---|---|---|---|---|
| grid | ST5 | -0.2 [-2.7, +2.4] h0.501 a-0.022 | +2.4 [-5.7, +11.8] h0.510 a+0.079 | -0.1 [-9.8, +10.8] h0.536 a-0.001 | +22.8 [-2.1, +41.4] h0.611 a+0.218 |
| grid | ST15 | -1.1 [-3.3, +1.1] h0.470 a-0.110 | -1.2 [-8.9, +8.3] h0.451 a-0.039 | -2.4 [-14.6, +12.0] h0.493 a-0.043 | +16.0 [-13.2, +31.4] h0.564 a+0.153 |
| grid | ST30 | -1.3 [-3.2, +0.8] h0.479 a-0.128 | -7.2 [-14.7, +0.8] h0.408 a-0.243 | +2.0 [-18.2, +23.5] h0.510 a+0.036 | +16.5 [-29.9, +43.0] h0.630 a+0.157 |
| grid | DST | +1.3 [-0.9, +3.4] h0.501 a+0.124 | +10.1 [+0.2, +20.5] h0.611 a+0.338 | +22.0 [+1.5, +44.1] h0.569 a+0.390 | +40.9 [-26.2, +102.5] h0.539 a+0.390 |
| grid | PIVP | -2.5 [-4.5, -0.6] h0.431 a-0.243 | -10.2 [-18.5, -1.4] h0.366 a-0.342 | -8.3 [-23.3, +10.0] h0.466 a-0.147 | +6.4 [-33.7, +30.4] h0.586 a+0.061 |
| grid | T5 | -0.2 [-2.6, +2.1] h0.485 a-0.018 | +2.7 [-8.0, +13.3] h0.487 a+0.091 | +11.0 [-14.9, +33.5] h0.586 a+0.196 | +23.0 [-25.6, +62.0] h0.621 a+0.219 |
| grid | T20 | +1.0 [-1.3, +3.6] h0.515 a+0.098 | +6.3 [-4.1, +17.7] h0.558 a+0.210 | +31.9 [+11.3, +52.5] h0.682 a+0.566 | +75.0 [+12.6, +135.8] h0.737 a+0.716 |
| grid | SER | +0.3 [-1.5, +2.3] h0.501 a+0.033 | +0.3 [-9.0, +10.4] h0.476 a+0.011 | +14.1 [+1.0, +31.2] h0.592 a+0.250 | +49.5 [+15.1, +74.6] h0.690 a+0.473 |
| grid | ERG | -0.8 [-4.7, +3.3] h0.500 a-0.061 | +6.0 [-9.1, +21.7] h0.542 a+0.192 | +23.5 [+0.3, +43.1] h0.652 a+0.465 | +54.6 [+0.2, +106.4] h0.694 a+0.639 |
| grid | VIX2 | +0.4 [-1.8, +2.8] h0.561 a+0.040 | -3.0 [-13.8, +8.3] h0.468 a-0.100 | +6.0 [-14.1, +32.5] h0.548 a+0.107 | +65.1 [+42.5, +116.7] h0.705 a+0.622 |
| grid | VIXHI | -3.1 [-3.7, -1.4] h0.583 a-0.165 | -9.5 [-20.3, +4.0] h0.444 a-0.320 | - | - |
| grid | STRESS | +0.6 [-4.7, +8.2] h0.508 a+0.054 | -11.7 [-32.0, +16.8] h0.410 a-0.321 | -24.0 [-40.2, +0.6] h0.310 a-0.482 | -98.7 [-149.1, +12.7] h0.245 a-0.818 |
| grid | MACDspot | -1.4 [-3.5, +1.1] h0.454 a-0.130 | -4.7 [-13.0, +4.5] h0.406 a-0.157 | +8.1 [-13.1, +29.5] h0.554 a+0.144 | +11.7 [-39.3, +40.9] h0.599 a+0.112 |
| grid | MACDr | -0.4 [-3.5, +2.2] h0.487 a-0.038 | -5.5 [-17.8, +6.3] h0.397 a-0.169 | +3.7 [-37.8, +39.8] h0.563 a+0.053 | -2.6 [-82.6, +28.4] h0.559 a-0.020 |
| grid | MODEL | -1.0 [-5.0, +2.7] h0.452 a-0.118 | +0.5 [-6.4, +7.7] h0.478 a+0.026 | -2.4 [-28.8, +16.8] h0.568 a-0.046 | +11.1 [-34.5, +61.7] h0.612 a+0.108 |
| grid | ALLCE | -1.2 [-3.8, +1.1] h0.442 a-0.114 | -5.4 [-17.2, +5.0] h0.459 a-0.182 | -30.3 [-51.6, -8.5] h0.347 a-0.537 | -81.9 [-140.7, -29.0] h0.251 a-0.781 |
| g330 | ST5 | +2.6 [-0.4, +5.6] h0.539 a+0.299 | +3.2 [-2.9, +8.9] h0.548 a+0.182 | -6.6 [-31.2, +9.9] h0.559 a-0.126 | +3.3 [-46.6, +45.2] h0.573 a+0.032 |
| g330 | ST15 | -0.8 [-4.2, +2.5] h0.417 a-0.088 | -0.5 [-6.2, +5.0] h0.443 a-0.031 | -12.9 [-38.1, +6.8] h0.514 a-0.244 | -7.9 [-44.7, +24.1] h0.515 a-0.077 |
| g330 | ST30 | -0.1 [-4.0, +3.4] h0.478 a-0.016 | -5.0 [-11.3, +1.2] h0.365 a-0.283 | -1.9 [-30.2, +19.9] h0.486 a-0.035 | +11.9 [-37.1, +40.6] h0.660 a+0.115 |
| g330 | DST | +0.9 [-2.8, +4.4] h0.487 a+0.106 | +8.7 [+2.5, +15.0] h0.635 a+0.490 | +22.9 [+3.7, +44.2] h0.532 a+0.435 | +44.1 [-19.7, +109.8] h0.524 a+0.428 |
| g330 | PIVP | -1.1 [-5.0, +2.5] h0.435 a-0.131 | -6.3 [-13.1, +0.1] h0.339 a-0.356 | -14.8 [-37.7, +1.8] h0.441 a-0.281 | -5.7 [-47.6, +25.8] h0.602 a-0.056 |
| g330 | T5 | +1.6 [-2.1, +5.1] h0.539 a+0.189 | -0.7 [-7.9, +6.3] h0.461 a-0.039 | +8.6 [-19.0, +32.4] h0.586 a+0.163 | +18.7 [-30.9, +60.0] h0.602 a+0.182 |
| g330 | T20 | +2.2 [-1.8, +5.6] h0.548 a+0.253 | +2.8 [-4.8, +9.9] h0.539 a+0.160 | +33.9 [+15.5, +53.9] h0.667 a+0.644 | +78.5 [+20.9, +139.2] h0.728 a+0.761 |
| g330 | SER | +1.7 [-1.2, +4.6] h0.513 a+0.197 | -0.3 [-7.0, +6.2] h0.470 a-0.015 | +17.3 [+4.6, +32.7] h0.622 a+0.328 | +51.8 [+18.9, +79.7] h0.699 a+0.502 |
| g330 | ERG | +2.8 [-4.9, +9.4] h0.632 a+0.247 | +5.5 [-2.6, +14.2] h0.561 a+0.322 | +26.5 [+4.5, +45.8] h0.698 a+0.580 | +63.4 [+7.5, +118.0] h0.708 a+0.688 |
| g330 | VIX2 | +2.5 [-1.2, +6.3] h0.635 a+0.292 | -4.9 [-11.9, +2.2] h0.452 a-0.276 | +11.7 [-10.2, +37.6] h0.577 a+0.222 | +61.9 [+39.9, +111.0] h0.728 a+0.600 |
| g330 | MACDspot | -1.1 [-4.9, +2.4] h0.426 a-0.132 | -5.5 [-12.3, +1.0] h0.330 a-0.312 | +2.8 [-27.6, +27.3] h0.541 a+0.053 | +8.3 [-44.0, +40.1] h0.641 a+0.081 |
| g330 | MACDr | -1.0 [-5.1, +3.2] h0.424 a-0.121 | -6.8 [-15.9, +0.2] h0.288 a-0.399 | +2.5 [-40.3, +38.3] h0.556 a+0.038 | -8.1 [-85.3, +30.0] h0.593 a-0.064 |
| g330 | MODEL | -1.0 [-5.0, +2.7] h0.452 a-0.118 | +0.5 [-6.4, +7.7] h0.478 a+0.026 | -2.4 [-28.8, +16.8] h0.568 a-0.046 | +11.1 [-34.5, +61.7] h0.612 a+0.108 |
| g330 | ALLCE | -1.9 [-5.4, +1.9] h0.417 a-0.217 | +0.7 [-6.5, +8.2] h0.513 a+0.039 | -27.0 [-48.5, -6.5] h0.369 a-0.513 | -79.6 [-140.3, -28.3] h0.272 a-0.772 |
| ev_macd | EV | -0.6 [-5.6, +4.9] h0.400 a-0.051 | -4.7 [-35.8, +29.7] h0.367 a-0.087 | - | - |
| ev_st5 | EV | +1.7 [-2.1, +5.4] h0.543 a+0.143 | +0.8 [-5.0, +10.6] h0.495 a+0.022 | +1.8 [-5.8, +7.9] h0.502 a+0.033 | -2.4 [-19.5, +5.8] h0.489 a-0.024 |
| ev_st15 | EV | +3.9 [+0.7, +7.5] h0.602 a+0.310 | +3.3 [-6.7, +16.5] h0.504 a+0.085 | -2.4 [-13.1, +9.0] h0.512 a-0.040 | +7.3 [-16.9, +19.2] h0.504 a+0.068 |
| ev_st30 | EV | +3.2 [-0.5, +6.7] h0.581 a+0.381 | +2.5 [-16.9, +27.6] h0.512 a+0.076 | -20.8 [-50.1, +2.4] h0.405 a-0.310 | +9.5 [-44.7, +30.9] h0.568 a+0.087 |
| ev_piv | EV | +4.2 [+1.5, +7.5] h0.636 a+0.344 | -8.0 [-18.5, +4.0] h0.437 a-0.204 | -5.7 [-22.0, +16.2] h0.500 a-0.092 | +8.4 [-23.1, +24.1] h0.540 a+0.076 |

**liveS**

| set | method | +30 min | to close | D+1 | D+3 |
|---|---|---|---|---|---|
| grid | ST5 | +0.5 [-1.1, +2.1] h0.501 a+0.019 | +2.4 [-2.8, +8.6] h0.501 a+0.035 | -0.6 [-6.9, +5.2] h0.493 a-0.004 | +0.6 [-14.2, +6.8] h0.504 a+0.003 |
| grid | ST15 | +0.1 [-1.5, +1.8] h0.500 a+0.004 | +2.2 [-3.6, +9.0] h0.500 a+0.033 | +2.5 [-6.5, +10.9] h0.502 a+0.018 | +13.4 [-3.9, +20.5] h0.523 a+0.059 |
| grid | ST30 | -1.0 [-2.9, +1.0] h0.484 a-0.035 | -1.4 [-7.2, +4.8] h0.483 a-0.021 | +4.9 [-10.9, +21.4] h0.506 a+0.036 | +6.5 [-25.5, +17.8] h0.512 a+0.029 |
| grid | DST | +0.8 [-0.5, +2.2] h0.508 a+0.029 | +3.4 [-2.0, +8.9] h0.518 a+0.051 | +10.9 [-3.7, +23.5] h0.532 a+0.079 | +25.7 [-7.9, +51.5] h0.550 a+0.113 |
| grid | PIVP | -0.7 [-2.5, +1.2] h0.490 a-0.027 | +0.3 [-6.2, +7.5] h0.490 a+0.005 | +6.8 [-7.5, +23.5] h0.507 a+0.049 | +12.7 [-15.0, +24.2] h0.530 a+0.056 |
| grid | T5 | -0.7 [-2.9, +1.6] h0.489 a-0.027 | -1.2 [-7.6, +5.9] h0.491 a-0.018 | +1.5 [-21.4, +19.0] h0.502 a+0.011 | -12.6 [-48.7, +13.9] h0.496 a-0.055 |
| grid | T20 | +0.8 [-1.7, +3.1] h0.510 a+0.027 | +5.3 [-2.7, +13.3] h0.522 a+0.076 | +19.0 [-6.6, +43.9] h0.554 a+0.135 | +41.6 [-25.5, +88.6] h0.578 a+0.177 |
| grid | SER | -0.6 [-3.2, +1.9] h0.498 a-0.022 | +1.4 [-6.5, +11.2] h0.505 a+0.020 | +12.6 [-12.6, +41.5] h0.538 a+0.088 | +53.0 [-18.8, +99.3] h0.593 a+0.225 |
| grid | ERG | -0.7 [-4.0, +3.1] h0.499 a-0.024 | +0.3 [-10.1, +13.3] h0.502 a+0.004 | +8.4 [-18.8, +32.8] h0.529 a+0.062 | +24.4 [-39.4, +74.4] h0.560 a+0.111 |
| grid | VIX2 | -2.2 [-5.1, +1.0] h0.484 a-0.079 | -9.6 [-21.5, +2.6] h0.468 a-0.143 | -4.3 [-33.3, +27.1] h0.517 a-0.031 | +63.6 [+49.1, +122.2] h0.611 a+0.280 |
| grid | VIXHI | -9.2 [-10.8, -7.2] h0.385 a-0.254 | -26.7 [-35.8, -8.6] h0.378 a-0.347 | -102.6 [-145.4, -52.5] h0.284 a-0.589 | - |
| grid | MACDspot | -1.4 [-3.5, +0.9] h0.485 a-0.050 | -1.5 [-8.1, +5.8] h0.483 a-0.023 | +4.8 [-16.7, +22.4] h0.502 a+0.035 | -0.3 [-38.3, +14.0] h0.509 a-0.001 |
| grid | MACDr | -1.3 [-3.4, +0.7] h0.481 a-0.048 | -1.0 [-7.1, +5.6] h0.485 a-0.015 | +6.2 [-15.7, +28.1] h0.510 a+0.044 | +3.5 [-30.1, +18.8] h0.518 a+0.015 |
| grid | ALLCE | -0.7 [-4.2, +2.6] h0.460 a-0.027 | -4.3 [-17.6, +8.2] h0.462 a-0.064 | -31.3 [-58.8, -0.4] h0.407 a-0.228 | -81.0 [-139.6, -6.2] h0.360 a-0.357 |
| g330 | ST5 | +1.7 [-1.0, +4.3] h0.509 a+0.050 | +2.5 [-1.4, +6.9] h0.503 a+0.058 | -6.4 [-21.3, +2.8] h0.480 a-0.050 | -9.5 [-28.8, +0.5] h0.487 a-0.043 |
| g330 | ST15 | +0.8 [-2.4, +4.0] h0.502 a+0.024 | +1.0 [-3.1, +5.5] h0.489 a+0.023 | -4.8 [-14.9, +2.6] h0.486 a-0.037 | +4.2 [-14.2, +14.6] h0.509 a+0.019 |
| g330 | ST30 | -0.2 [-3.0, +2.7] h0.493 a-0.005 | +0.0 [-4.3, +4.5] h0.478 a+0.001 | +4.7 [-9.1, +20.1] h0.504 a+0.036 | +8.3 [-23.2, +19.1] h0.519 a+0.038 |
| g330 | DST | +1.0 [-1.2, +2.9] h0.518 a+0.032 | +0.2 [-2.8, +3.0] h0.503 a+0.006 | +9.2 [-4.1, +20.7] h0.522 a+0.071 | +25.4 [-8.3, +50.8] h0.551 a+0.114 |
| g330 | PIVP | -0.6 [-3.4, +2.4] h0.488 a-0.018 | +0.0 [-3.9, +4.6] h0.483 a+0.001 | +3.3 [-9.6, +16.4] h0.497 a+0.025 | +6.2 [-21.8, +18.2] h0.525 a+0.028 |
| g330 | T5 | -1.4 [-4.8, +2.1] h0.488 a-0.043 | -1.7 [-5.8, +2.3] h0.478 a-0.039 | +1.8 [-20.3, +19.2] h0.505 a+0.014 | -10.8 [-46.3, +15.2] h0.502 a-0.049 |
| g330 | T20 | +0.8 [-4.1, +4.8] h0.521 a+0.024 | +1.5 [-3.5, +6.8] h0.501 a+0.032 | +17.0 [-4.5, +38.0] h0.542 a+0.128 | +40.3 [-24.5, +85.1] h0.579 a+0.175 |
| g330 | SER | +0.0 [-4.3, +4.5] h0.513 a+0.001 | -0.5 [-5.6, +5.5] h0.490 a-0.010 | +13.5 [-12.1, +39.4] h0.536 a+0.099 | +55.8 [-19.6, +100.9] h0.597 a+0.244 |
| g330 | ERG | +0.7 [-4.5, +6.1] h0.525 a+0.023 | +0.3 [-5.5, +7.0] h0.502 a+0.007 | +10.3 [-15.2, +35.0] h0.527 a+0.082 | +31.1 [-32.5, +79.4] h0.573 a+0.143 |
| g330 | VIX2 | -2.7 [-8.5, +2.9] h0.490 a-0.084 | -8.7 [-15.4, -1.9] h0.432 a-0.202 | +4.1 [-23.0, +33.1] h0.540 a+0.031 | +61.3 [+40.1, +118.1] h0.608 a+0.277 |
| g330 | VIXHI | -0.8 [-14.1, +19.9] h0.474 a-0.019 | -5.4 [-31.2, +19.9] h0.467 a-0.104 | -97.4 [-124.7, -57.0] h0.289 a-0.583 | - |
| g330 | MACDspot | -2.1 [-5.2, +0.9] h0.482 a-0.065 | -1.4 [-5.8, +3.4] h0.475 a-0.032 | +4.7 [-13.7, +21.7] h0.499 a+0.036 | +3.0 [-34.0, +17.4] h0.519 a+0.014 |
| g330 | MACDr | -1.8 [-4.8, +1.1] h0.483 a-0.056 | -1.0 [-5.1, +3.5] h0.479 a-0.023 | +7.2 [-12.7, +26.8] h0.506 a+0.054 | +8.0 [-26.2, +24.7] h0.529 a+0.036 |
| g330 | ALLCE | -1.4 [-7.1, +5.0] h0.440 a-0.043 | +1.5 [-6.0, +9.0] h0.498 a+0.035 | -28.3 [-54.9, +1.1] h0.415 a-0.218 | -76.7 [-134.3, -2.7] h0.364 a-0.346 |
| ev_macd | EV | +1.9 [-0.4, +4.2] h0.492 a+0.063 | +9.6 [-1.6, +20.4] h0.502 a+0.119 | +10.9 [-7.2, +32.1] h0.510 a+0.075 | +35.1 [-6.9, +50.7] h0.562 a+0.144 |
| ev_st5 | EV | +0.7 [-2.0, +3.0] h0.482 a+0.022 | +1.7 [-1.9, +6.4] h0.501 a+0.022 | +0.6 [-6.1, +6.6] h0.494 a+0.004 | +0.9 [-7.9, +4.8] h0.500 a+0.004 |
| ev_st15 | EV | +1.2 [-0.5, +3.0] h0.493 a+0.040 | +5.0 [-1.4, +13.3] h0.510 a+0.061 | +1.1 [-8.7, +13.5] h0.494 a+0.008 | +4.6 [-11.1, +15.3] h0.507 a+0.020 |
| ev_st30 | EV | -1.2 [-3.1, +0.9] h0.457 a-0.039 | +6.6 [-4.7, +16.9] h0.495 a+0.079 | +3.0 [-11.5, +24.9] h0.499 a+0.020 | +23.3 [-5.0, +38.1] h0.534 a+0.100 |
| ev_piv | EV | +0.4 [-1.9, +2.4] h0.486 a+0.010 | -0.5 [-8.8, +7.7] h0.482 a-0.005 | +4.0 [-7.0, +17.2] h0.502 a+0.027 | +9.4 [-9.2, +16.4] h0.519 a+0.040 |

#### Spot skill by year (long histories; mean bps (t))


**lsim g330 md3**

| method | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| DST | +23.0 (+1.4) | +8.5 (+0.8) | +11.9 (+0.8) | -17.2 (-1.1) | +42.3 (+1.0) | +6.6 (+0.4) | -15.3 (-1.0) | +27.4 (+2.3) | -27.0 (-2.4) | -4.4 (-0.4) | -9.7 (-0.6) |
| T5 | -13.9 (-0.9) | +1.5 (+0.2) | -3.5 (-0.3) | -24.4 (-1.5) | +31.2 (+0.8) | +6.9 (+0.5) | +24.8 (+1.6) | +7.8 (+0.7) | -17.7 (-1.7) | -1.0 (-0.1) | +8.6 (+0.7) |
| T20 | +1.1 (+0.1) | +5.9 (+0.5) | +20.0 (+1.4) | -9.0 (-0.6) | +48.1 (+1.2) | +6.1 (+0.3) | -15.8 (-0.9) | +22.9 (+1.9) | -10.9 (-0.9) | +7.9 (+0.6) | +0.1 (+0.0) |
| SER | +12.6 (+0.9) | -0.8 (-0.1) | +6.1 (+0.5) | -29.5 (-1.8) | +9.7 (+0.3) | -6.9 (-0.5) | +36.9 (+2.7) | +13.2 (+1.5) | -5.4 (-0.6) | +4.7 (+0.6) | +16.5 (+1.3) |
| VIX2 | -5.2 (-0.3) | -26.4 (-2.3) | -7.6 (-0.5) | +19.2 (+1.1) | -22.0 (-0.5) | +28.7 (+1.3) | -14.9 (-0.8) | -8.6 (-0.6) | +21.7 (+1.5) | +8.3 (+0.6) | +15.6 (+0.8) |
| VIXHI | +0.4 (+0.0) | +65.1 (+3.5) | +5.6 (+0.2) | +44.4 (+1.4) | -39.5 (-0.4) | - | +15.1 (+0.4) | +49.0 (+1.7) | +30.9 (+2.0) | +62.3 (+1.9) | +5.5 (+0.1) |
| STRESS | +10.3 (+0.3) | +31.2 (+2.1) | +8.1 (+0.4) | +34.8 (+1.2) | +2.3 (+0.0) | +72.6 (+2.3) | +17.0 (+0.6) | +33.2 (+2.1) | +17.4 (+0.9) | +40.9 (+1.6) | -20.7 (-0.6) |
| MODEL | - | - | +3.4 (+0.4) | -5.3 (-0.4) | -52.7 (-1.8) | -28.5 (-1.8) | +0.1 (+0.0) | +13.0 (+1.2) | -10.4 (-1.2) | +3.0 (+0.3) | -14.3 (-1.1) |
| ALLCE | +4.9 (+0.2) | +36.4 (+3.3) | +7.6 (+0.5) | +20.0 (+1.2) | +13.7 (+0.3) | +25.9 (+1.2) | +9.3 (+0.5) | +31.5 (+2.3) | +16.2 (+1.1) | +13.9 (+1.0) | -17.1 (-0.9) |

**lsim g330 md1**

| method | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| DST | +8.2 (+1.2) | +2.7 (+0.7) | +5.3 (+1.0) | -7.1 (-1.1) | +15.7 (+1.1) | +1.4 (+0.2) | -2.6 (-0.4) | +11.0 (+2.5) | -8.7 (-1.9) | -0.8 (-0.2) | -0.2 (-0.0) |
| T5 | -1.9 (-0.3) | +3.8 (+1.0) | +1.2 (+0.2) | -2.6 (-0.5) | +14.7 (+1.0) | +6.0 (+0.8) | +13.5 (+1.9) | +2.0 (+0.5) | -11.8 (-2.0) | -5.3 (-1.1) | +5.9 (+0.9) |
| T20 | +3.2 (+0.5) | +3.3 (+0.9) | +7.0 (+1.3) | -1.8 (-0.3) | +21.1 (+1.5) | +2.5 (+0.3) | -2.0 (-0.3) | +9.2 (+2.0) | -6.6 (-1.1) | +4.0 (+0.9) | +2.1 (+0.3) |
| SER | +7.1 (+1.1) | -0.8 (-0.2) | +5.1 (+1.2) | -4.2 (-0.8) | -3.7 (-0.3) | +0.2 (+0.0) | +17.1 (+2.3) | +7.3 (+1.9) | -1.0 (-0.2) | +1.2 (+0.3) | +8.9 (+1.6) |
| VIX2 | -0.1 (-0.0) | -8.3 (-1.8) | -2.6 (-0.5) | +3.6 (+0.5) | -9.8 (-0.7) | +12.1 (+1.5) | -6.8 (-0.8) | -2.5 (-0.5) | +3.6 (+0.6) | +6.6 (+1.1) | +3.6 (+0.4) |
| VIXHI | -4.8 (-0.2) | +10.1 (+0.8) | +2.1 (+0.3) | +12.1 (+1.0) | -16.7 (-0.5) | - | +7.9 (+0.5) | +19.2 (+1.0) | +9.5 (+1.2) | +21.8 (+1.8) | +1.8 (+0.1) |
| STRESS | -6.3 (-0.4) | +15.8 (+2.1) | -2.6 (-0.3) | +15.4 (+1.0) | +10.5 (+0.4) | +31.0 (+2.1) | +20.9 (+1.4) | +17.2 (+2.1) | -0.4 (-0.0) | +18.3 (+1.8) | -4.3 (-0.3) |
| MODEL | - | - | -2.3 (-0.5) | +0.1 (+0.0) | -23.8 (-1.9) | +4.9 (+0.8) | +9.7 (+1.5) | +8.5 (+2.2) | -3.4 (-0.6) | +0.1 (+0.0) | -6.6 (-0.9) |
| ALLCE | +1.0 (+0.1) | +12.1 (+3.0) | +2.9 (+0.6) | +6.1 (+0.9) | +4.6 (+0.3) | +7.9 (+1.0) | +3.2 (+0.4) | +12.1 (+2.4) | +4.8 (+0.9) | +5.3 (+1.0) | -5.8 (-0.7) |

**fy grid mcl**

| method | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| ST5 | -2.2 (-1.0) | +0.4 (+0.2) | +0.1 (+0.1) | -0.0 (-0.0) | +0.9 (+0.7) | +2.7 (+1.3) |
| ST15 | -0.6 (-0.2) | -2.0 (-0.8) | -0.7 (-0.5) | -0.9 (-0.5) | +0.4 (+0.3) | +2.4 (+1.1) |
| ST30 | -2.8 (-1.0) | -1.6 (-0.6) | -1.9 (-1.1) | -3.5 (-2.0) | -0.9 (-0.5) | -2.3 (-0.9) |
| DST | -1.1 (-0.4) | +0.4 (+0.2) | +1.4 (+0.7) | -2.0 (-1.1) | -1.4 (-0.8) | -1.9 (-0.7) |
| T5 | -1.5 (-0.5) | -0.2 (-0.1) | +0.4 (+0.3) | -1.2 (-0.6) | -2.3 (-1.2) | -2.6 (-1.1) |
| T20 | -0.0 (-0.0) | -0.8 (-0.3) | +1.5 (+0.8) | -1.6 (-0.9) | -1.6 (-0.9) | -2.7 (-1.1) |
| SER | -1.9 (-0.6) | -1.6 (-0.6) | +0.8 (+0.5) | -1.8 (-1.0) | -1.8 (-1.2) | -0.8 (-0.3) |
| VIX2 | -0.4 (-0.1) | -2.5 (-0.8) | -1.5 (-0.8) | -0.5 (-0.2) | +3.3 (+1.6) | +1.0 (+0.3) |
| VIXHI | -24.4 (-1.3) | -1.2 (-0.2) | -2.4 (-0.1) | +2.9 (+0.9) | +5.3 (+1.0) | +5.8 (+1.1) |
| STRESS | +5.6 (+0.6) | +1.9 (+0.3) | +2.4 (+0.6) | +2.4 (+0.5) | +7.9 (+1.8) | +4.3 (+0.8) |
| MACDspot | -1.7 (-0.7) | -3.9 (-1.4) | -1.3 (-0.8) | -3.4 (-1.5) | -1.9 (-1.1) | -1.5 (-0.6) |
| MODEL | +3.9 (+2.1) | +0.8 (+0.4) | -0.5 (-0.5) | -0.9 (-0.8) | -1.9 (-1.7) | +1.2 (+0.9) |
| ALLCE | +0.4 (+0.1) | -0.3 (-0.1) | +2.3 (+1.2) | +0.7 (+0.3) | -0.1 (-0.0) | +0.8 (+0.3) |

**fy grid md1**

| method | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| ST5 | +5.5 (+1.2) | +2.5 (+0.6) | +4.4 (+1.5) | -5.2 (-1.1) | +3.5 (+1.0) | +0.9 (+0.2) |
| ST15 | +6.5 (+1.2) | +4.7 (+0.9) | +4.3 (+1.3) | -3.6 (-0.8) | +3.9 (+1.1) | -3.9 (-0.9) |
| ST30 | -2.1 (-0.3) | +6.7 (+1.1) | +1.1 (+0.3) | -3.5 (-0.6) | -0.1 (-0.0) | -7.3 (-1.0) |
| DST | +3.6 (+0.5) | -2.7 (-0.4) | +10.0 (+2.0) | -10.8 (-2.1) | -2.9 (-0.5) | -3.1 (-0.4) |
| T5 | +3.9 (+0.6) | +10.5 (+1.5) | +2.3 (+0.5) | -10.4 (-1.7) | -5.1 (-1.0) | -3.8 (-0.6) |
| T20 | +4.9 (+0.7) | -3.8 (-0.5) | +9.2 (+1.9) | -6.5 (-1.1) | +1.8 (+0.3) | -1.8 (-0.2) |
| SER | +0.3 (+0.0) | +13.3 (+2.0) | +7.2 (+1.7) | -1.9 (-0.4) | +0.9 (+0.2) | +1.5 (+0.2) |
| VIX2 | +11.6 (+1.4) | -7.5 (-0.9) | -3.4 (-0.7) | +4.7 (+0.7) | +6.9 (+1.0) | +6.4 (+0.8) |
| VIXHI | +33.1 (+0.9) | +8.8 (+0.6) | +11.2 (+0.5) | +13.1 (+1.5) | +22.8 (+1.6) | +9.3 (+0.5) |
| STRESS | +45.8 (+2.7) | +10.6 (+0.7) | +17.6 (+1.5) | +3.3 (+0.3) | +23.6 (+1.9) | +4.7 (+0.3) |
| MACDspot | -5.9 (-0.8) | +5.7 (+0.8) | +2.2 (+0.5) | -4.0 (-0.7) | +0.9 (+0.2) | -2.9 (-0.4) |
| MODEL | +9.1 (+1.4) | +8.0 (+1.2) | +8.6 (+2.2) | -3.9 (-0.7) | -0.3 (-0.1) | -7.2 (-1.0) |
| ALLCE | +7.8 (+1.0) | +3.7 (+0.4) | +10.9 (+2.0) | +5.9 (+0.9) | +4.4 (+0.8) | -4.3 (-0.5) |

**fy ev_st15 mcl**

| method | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| EV | +8.9 (+2.3) | +4.0 (+1.5) | +0.6 (+0.4) | +3.3 (+1.5) | +1.6 (+0.9) | +5.1 (+1.7) |

**fy ev_st5 mcl**

| method | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| EV | +2.9 (+1.5) | +2.6 (+1.4) | +0.6 (+0.5) | +3.4 (+1.6) | +0.9 (+0.7) | +2.1 (+1.2) |

**fy ev_st15 md3**

| method | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| EV | +8.6 (+1.6) | -1.4 (-0.3) | -2.4 (-0.7) | +1.5 (+0.4) | +4.9 (+1.3) | +14.3 (+2.5) |


### 5.3 Power and multiple testing

The complete per-cell power table (every grid and own-event cell at close, D+3, y3 and lane: sessions, mean, SE, t,
power now, sessions needed, bootstrap power at that size, class, beyond-tilt) is in `res/tables.md` (§ "Statistical
power", 520 rows) and every statistic of all 3,704 cells is in `res/cells.csv`. Section 3 above carries the headline
cells. Rules of thumb from it:

- intraday (+30 min / close) index side value of +₹2–6 k per ₹1 L needs 100–1,000 sessions; the 15-min flip at +30 min
  needs 221 (have 143 on NIFTY/SENSEX, 29 live) and at D+3 86;
- slow multi-day selectors (+₹1–9 k on a ±₹90–135 k prize) need 2,500 to > 100,000 index-sessions;
- the 27–30 session live window detects only effects of roughly ±₹15–40 k per trade at D+3, i.e. the size of the
  regime itself (always-PE +₹44 k stocks / +₹120 k indices).

#### Classification counts and BH-FDR


| panel | positive & significant | positive, not yet proven | no evidence | no evidence (positive but < Rs 500) | negative |
|---|---|---|---|---|---|
| ix | 18 | 195 | 310 | 12 | 25 |
| tbI | 17 | 132 | 227 | 28 | 56 |
| tbS | 18 | 94 | 219 | 68 | 41 |
| live | 90 | 281 | 134 | 36 | 6 |
| liveI | 55 | 296 | 136 | 24 | 8 |
| liveS | 86 | 266 | 111 | 38 | 7 |
| fy | 3 | 56 | 31 | 6 | 0 |
| lsim | 0 | 26 | 4 | 0 | 0 |

Option side-value tests: 3160; nominal p<0.05: 437 (positive 305, negative 132); expected by chance 158; BH 5% passes: 17
- BH pass: live grid d1 VIXHI mean -25.1k [-47.2k, -20.1k] p 4.1e-06
- BH pass: live grid d3 VIX2 mean +42.0k [+28.9k, +73.0k] p 1.9e-04
- BH pass: live g60 d3 VIX2 mean +41.2k [+30.1k, +72.0k] p 8.4e-05
- BH pass: live g180 d3 DST&ST15 mean +20.1k [+8.5k, +28.2k] p 7.4e-05
- BH pass: live g180 lane DST&ST15 mean +13.3k [+6.6k, +17.2k] p 4.4e-07
- BH pass: live g330 lane VIX2 mean +18.7k [+12.5k, +32.7k] p 2.3e-04
- BH pass: liveI g180 d3 SER mean +43.6k [+17.6k, +60.4k] p 8.9e-05
- BH pass: liveS grid d1 VIXHI mean -25.1k [-46.6k, -20.3k] p 2.2e-06
- BH pass: liveS grid d3 VIX2 mean +41.1k [+28.6k, +71.2k] p 1.4e-04
- BH pass: liveS g60 d3 VIX2 mean +40.4k [+30.0k, +69.7k] p 5.9e-05
- BH pass: liveS g180 d3 VIX2 mean +40.8k [+28.8k, +71.8k] p 2.4e-04
- BH pass: liveS g180 d3 DST&ST15 mean +19.2k [+8.8k, +26.0k] p 1.5e-05
- BH pass: liveS g330 d3 VIX2 mean +42.2k [+27.1k, +71.9k] p 2.6e-04
- BH pass: liveS grid lane ST15 mean +4.9k [+1.3k, +6.6k] p 2.6e-04
- BH pass: liveS g180 lane DST&ST15 mean +13.3k [+6.7k, +16.8k] p 1.3e-07
- BH pass: liveS g330 lane VIX2 mean +18.3k [+12.3k, +32.1k] p 2.5e-04
- BH pass: liveS ev_macd lane EV&DST mean +19.2k [+6.6k, +26.0k] p 1.7e-04

Beyond-tilt tests: 3160; nominal p<0.05: 249; BH passes: 0

Spot directional-skill tests: 992; nominal p<0.05: 105; BH passes: 18
- BH pass (spot): fy ev_st15 mcl EV +3.60 bps t +3.60 hit 0.527
- BH pass (spot): ix ev_st15 md3 EV +16.07 bps t +3.68 hit 0.571
- BH pass (spot): tbS ev_macd m30 EV +1.80 bps t +3.47 hit 0.506
- BH pass (spot): live grid md3 VIX2 +63.62 bps t +3.37 hit 0.613
- BH pass (spot): live grid m30 VIXHI -9.09 bps t -10.28 hit 0.389
- BH pass (spot): live grid mcl VIXHI -26.41 bps t -4.01 hit 0.380
- BH pass (spot): live grid md1 VIXHI -101.27 bps t -3.59 hit 0.284
- BH pass (spot): live g330 md1 VIXHI -96.25 bps t -5.57 hit 0.287
- BH pass (spot): liveI grid md3 VIX2 +65.13 bps t +3.42 hit 0.705
- BH pass (spot): liveI grid m30 VIXHI -3.07 bps t -5.44 hit 0.583
- BH pass (spot): liveI g330 md1 T20 +33.88 bps t +3.56 hit 0.667
- BH pass (spot): liveI g330 md3 SER +51.82 bps t +3.34 hit 0.699
- BH pass (spot): liveI g330 md3 VIX2 +61.90 bps t +3.43 hit 0.728
- BH pass (spot): liveS grid md3 VIX2 +63.59 bps t +3.34 hit 0.611
- BH pass (spot): liveS grid m30 VIXHI -9.20 bps t -10.17 hit 0.385
- BH pass (spot): liveS grid mcl VIXHI -26.73 bps t -3.95 hit 0.378
- BH pass (spot): liveS grid md1 VIXHI -102.62 bps t -3.63 hit 0.284
- BH pass (spot): liveS g330 md1 VIXHI -97.37 bps t -5.66 hit 0.289


## 6. Verdict per method (power-aware)

Class codes in the tables: **S+** positive & significant, **P** positive not yet proven, **0** no evidence,
**0+** positive but below ₹500 per ₹1 L, **N** negative. "Sessions needed" = for 80% power at the observed mean and
variance.

| method | best evidence (panel, exit) | side value per ₹1 L [95% CI] | hit / payoff / capture | CE-month / PE-month | sessions: have / need | class | comment |
|---|---|---|---|---|---|---|---|
| Index 15-min ST(14,2) flip, at the flip | `ix` (real), D+3 | +21.1 k [+10.9, +34.2] | 0.584 / 1.00 / 0.170 | +21.3 k / +21.0 k | 141 / 86 | **positive & significant** | Also +30 min +5.7 k, D+1 +15.3 k, lane +10.5 k (all S+); live indices +30 min +6.7 k (S+); sim 2021–26 D+3 +4.2 k [+0.0, +8.0]; spot to the close positive every year 2021–26. Index only. |
| Index 5-min ST(10,3) flip | `ix`, D+3 | +10.6 k [+2.9, +19.9] | 0.519 / 1.11 / 0.089 | +7.2 k / +13.4 k | 183 / 239 | **positive & significant** (D+3) / P (intraday) | live indices +30 min +4.9 k (S+); sim 2021–26 +2.0 k (P, needs 5,439) |
| Index 30-min ST(10,3) flip | `ix`, +30 min | +9.3 k [+3.0, +15.5] | 0.588 / 1.65 / 0.403 | +10.1 k / +8.0 k | 79 / 74 | **positive & significant** (short) | TradeBot indices +2.9 k (S+); D+3 ~0 |
| Stock Supertrend flips | `tbS` / `liveS`, +30 min | +0.2 k / −1.0 k | – | – | 204 / 3,988 | **no evidence** (live: negative) | flips on stocks carry no side information |
| Pivot location P (state) | `ix`/`tbI`/`tbS`, +30 min | +2.6 k / +1.3 k / +0.4 k (all S+) | 0.51–0.56 / ~1.0 / 0.04–0.12 | both positive | 204–214 / 267–361 | **positive & significant for 30 min** | Not live (indices −2.9 k, N). Gone by the close; D+3 negative on 2025–26 indices (tbI −11.3 k, N). Stocks: ₹0.4 k is immaterial. |
| Series-to-date return (SER) | `fy` sim 2021–26, D+3; `lsim` 2016–26 | +9.1 k [+0.2, +17.3]; +4.8 k [−0.0, +9.6] | 0.540 / 0.97 / 0.067; 0.529 / 0.99 / 0.051 | +9.7 k / +8.5 k; +7.2 k / +1.7 k | 1,392 / 2,531; 2,645 / 5,315 | **positive, not yet proven** (fy CI just above 0) | The only slow rule positive in both month types over the long history. Real 2025–26: −8.4 k (ix), −3.3 k (tbI), −2.8 k (tbS). Live: +49.7 k indices (S+) but +6.4 k beyond tilt. |
| 20-day return (T20) | `lsim`, y3 | +4.3 k [−1.7, +10.4] | 0.527 / 0.98 / 0.046 | +11.1 k / −4.2 k | 2,645 / 10,390 | **positive, not yet proven** | Regime-asymmetric: wins in CE months, loses in PE months. Real 2025–26 −4.6 to −10.0 k. Live +111.7 k indices (S+), +35.9 k beyond tilt (n.s.). |
| Daily ST(10,3) (DST) | `lsim` / `fy` | +0.9 k / −4.2 k | 0.513 / 0.97 / 0.010 | +6.2 k / −5.7 k | 2,645 / >99,999 | **no evidence** | Live stocks +15.6 k (S+) is the PE tilt: +1.4 k beyond it. |
| 5-day return (T5), ER-gated (ERG), spot MACD | `lsim` / `fy` | +1.2 k / +4.3 k; ERG +2.6 k / −4.4 k; MACDspot fy +4.9 k | ~0.51 | mixed | – / >10,000 | **no evidence** | T5 negative on TradeBot stocks at D+3 beyond tilt (−4.0 k, N). |
| VIX high → CE (VIXHI) | `lsim` / `fy` | +6.1 k [−5.7, +17.3] / +17.3 k [−5.9, +38.3] | 0.55–0.57 / 0.93–1.00 | +37 k / −27 k (lsim) | 800 / 5,827; 444 / 1,512 | **positive, not yet proven** | Real 2025–26 S+ (+30–42 k) is one episode (May 2025). Failed Sep 2026. Spot D+3 positive 9 of 10 years. |
| Big \|1-day move\| → CE (STRESS) | `lsim` / `fy` | +7.7 k [−1.8, +17.9] / +12.5 k [−8.7, +33.3] | 0.56–0.57 | +43 k / −28 k (lsim) | 1,081 / 3,662 | **positive, not yet proven** | Beyond always-CE: ix +46.8 k (S+), tbI +24.1 k (S+); stocks ~0. Spot D+3 positive 10 of 11 years. |
| VIX above median → CE, else PE (VIX2) | `lsim` | +1.2 k [−5.5, +7.6] | 0.500 / 1.03 | −2.6 k / +6.0 k | 2,645 / >99,999 | **no evidence (long run)** | In 2025–26 and Sep 2026 VIX was mostly low → PE 70–84% of the time → large positive (live +41 k, S+). That is a PE tilt in falling periods, not a rule. |
| MACD lane side, own cross, lane exits | `liveS` / `ix` / `tbS` / `tbI` | +11.6 k [+1.3, +16.5] / +11.2 k [−4.3, +30.5] / +1.9 k [−2.9, +6.7] / +1.1 k | 0.50–0.57 / ~1.0 | both positive (liveS, ix) | 24 / 21; 96 / 475; 200 / 2,480 | **positive, not yet proven** (S+ only live) | At a fixed 4-session hold: ix +6.2 k, tbI −16.5 k, tbS +0.5 k. Same day (to 15:15): tbI +5.6 k, tbS +1.7 k, liveS +6.1 k (P). |
| Premium MACD state at fixed times (MACDr / MACDx) | `tbI` / `tbS`, D+3 | −13.8 k [−25.7, −1.9] / −4.9 k [−8.8, −1.0] | 0.46–0.49 | negative in both | – | **negative** (multi-day, 2025–26) | The option whose MACD is higher has already moved; multi-day it mean-reverts. Intraday ~0. |
| Index-lane ridge (champion analogue) | `lsim` | −0.7 k [−5.5, +4.4] | 0.510 / 0.95 | +0.8 k / −2.6 k | 2,152 / >99,999 | **no evidence** | +5.5 k / +3.6 k on 2025–26 index premiums (P, need ~6,000). |
| Live champion picks | `liveI`, D+3 | +41.0 k [−37.4, +87.1] | 0.462 / 1.89 | – / +41.0 k | 14 / 66 | **no evidence** beyond tilt | −29.9 k beyond its 79% PE tilt. |
| Combinations (DST&ST15, DST&PIVP, T20&DST, EV&DST, EV&T20 …) | live stocks | +11–29 k (several S+) | 0.57–0.62 | negative in CE months | 20–27 / 13–33 | live: S+ (regime); elsewhere ≤ components | No combination beats its best component outside the live month; with the daily trend they are negative at D+3 on 2025–26 TradeBot indices (−21 to −22 k, N). |

## 7. Interpretation

1. **Side picking is a property of fresh index moves, not of states.** A spot Supertrend *flip* on an index carries
   +₹5–21 k per ₹1 L of side value across +30 min to D+3 on real 2025–26 premiums, positive in both month types, and the
   underlying continuation is positive in every year 2021–26. The *state* of the same Supertrend at a fixed time is
   much weaker (15-min ST at the grid, close: +2.1 k, P; D+3: −2.3 k). Fixed-time states mostly re-describe moves that
   already happened.
2. **Multi-day, 2025–26 was a mean-reverting year for indices.** Every "follow the move" state (ST30, MACD state, pivot
   location, daily ST, 20-day trend) was negative at D+3 on the three real panels; the "after stress lean CE" rules
   were positive. In September 2026 (persistent fall) the opposite held: the trend states won and stress → CE lost.
   This is the regime dependence the earlier lane research found for loose exits.
3. **The slow selectors' long-run edge is real-looking but small.** SER and T20 capture 4–7% of the oracle prize on
   3-session index holds (+₹4–9 k per ₹1 L when the prize is ±₹90–135 k). With the CE-vs-PE spread's standard
   deviation, that needs 2,500–10,000 index-sessions to confirm. The user's caution is right: they are *not
   disproven* — they are unprovable from live data in any useful time, and must be judged on long history plus
   mechanism.
4. **Regime capture is asymmetric.** In CE-favouring months trend selectors lock on at once; in PE-favouring months
   they lock on after 5–7 sessions (long-run medians) and give back what they made. September 2026 was the
   favourable case for a slow selector in a PE month: a slow, persistent fall that they caught from day 1 (T20/SER
   44–53% of the month's prize).
5. **The MACD lane's side is not where its edge comes from.** Its own-cross side value is ~0 at fixed holds and
   positive with lane exits (+₹1–12 k), i.e. the overlay's asymmetry does the work, consistent with the earlier lane
   studies. The premium-MACD state as a side selector at fixed times is negative multi-day.
6. **Payoff asymmetry.** Because CE−PE is nearly symmetric, a side selector cannot be "right less than half the time
   but win big" unless it is right on the larger moves. The cases where it is: the VIX-median rule on TradeBot indices
   (hit 0.518, payoff 1.28, capture +0.157), the 30-min ST flip on NIFTY/SENSEX (payoff 1.65), and the live trend rules
   (payoff 3.1–3.5 on indices, i.e. they were on the right side of the few big September days).
7. **What the absolute P&L says.** At a 4-session hold even a correct side loses on 2025–26 premiums (straddle per leg
   −₹8.2 k NIFTY/SENSEX, −₹4.6 k TradeBot indices, −₹2.9 k stocks): side value is necessary, not sufficient. With lane
   exits the 15-min ST state chose a leg that made +₹4.7 k per ₹1 L on NIFTY/SENSEX, the 15-min flip +₹5.1 k, the
   VIX-median rule +₹3.8 k, against always-CE −₹6.0 k and always-PE +₹5.2 k.

## 8. Recommendations

1. **Separate the two decisions in the lanes: side (slow, index-level) and timing/exit (MACD cross + smart overlay).**
   - Preferred slow side input: **series-to-date return sign (SER)**, then the 20-day trend (T20). Not the daily
     Supertrend, the 5-day trend or the index-lane ridge model.
   - Use it as a **tilt, not a gate** (e.g. full size on the agreeing side, half or skip on the other). The evidence is
     "positive, not yet proven", and in choppy regimes (2025–26) gating on it lost money.
   - Pre-register the rule now and judge it on the long simulated history (validated: CE−PE corr 0.98, subtract the
     +6 pp CE bias) plus accumulating live data. Do not promote or reject it on one month.
2. **For index options, use the spot Supertrend flip as the side picker at its own time.** 15-min ST(14,2) (or 5-min
   ST(10,3)) flip on NIFTY/BANKNIFTY/SENSEX/MIDCPNIFTY: buy the flip's side. It is the only selector that is positive &
   significant on real premiums and replicated in simulation and live. Keep the earlier shadow-log plan; extend the
   hold to D+1 or lane exits (D+1 +₹15.3 k, lane +₹10.5 k per ₹1 L on NIFTY/SENSEX). Do not apply it to stocks.
3. **Treat "VIX high / big |1-day move| → CE" as a mild CE tilt with a kill-switch.** It works in mean-reverting
   regimes and failed badly in September 2026 when VIX rose during a persistent fall. A natural guard (to be tested,
   not yet evidence): require the slow trend not to be down.
4. **Keep the MACD lane; do not use the premium-MACD state as a multi-day side selector.**
5. **Change how side rules are evaluated** (any lane, any model):
   - always report side value vs the opposite leg, capture of the oracle, hit + payoff, CE-month and PE-month results
     separately, and the value beyond the rule's own CE/PE tilt;
   - plan sample size: intraday index side value needs ~100–250 sessions; slow multi-day selectors need thousands of
     index-sessions (use simulated history); a 19–30 session live window can only detect effects of ±₹15–40 k per
     trade at D+3, i.e. the size of the regime itself;
   - classify as positive & significant / positive not yet proven / no evidence / negative, never "rejected" on an
     underpowered BH failure.
6. **Stocks:** no side selector here has material side value on stock options (best: pivot location +₹0.4 k for 30
   min). For stock lanes the side should come from the index/market regime (e.g. the index's SER), and stock-level
   signals should be used for timing only. That last point is a hypothesis this study did not test directly.

## 9. Caveats

- **Real-premium samples are short and few-regime:** NIFTY/SENSEX 1-min 214 sessions (NIFTY only to Aug 2025),
  TradeBot 204–208 sessions (monthly contracts, 30-min bars, about 2 strikes per side), live 30 sessions in one falling
  month. Long-history conclusions rest on simulated premiums (validated for multi-day CE−PE at corr 0.98, sign
  agreement 97%, CE bias +6 pp per 4-session hold).
- **Hindsight month labels:** CE- / PE-favouring months are defined by outcome and used only to split results.
- **No liquidity cap** in the paired evaluation (₹1 L notional per leg); TradeBot 30-min fills are coarse.
- **VIX for stocks** is the index VIX; stock-level IV was not used.
- **Same-strike legs:** ATM CE and PE share a strike; moneyness differs slightly between them (one is a little ITM),
  which can bias side value by a fraction of a point; it is the same for every method on the same rows.
- **Combination rules and thresholds** (ER ≥ 0.35, VIX percentile ≥ 2/3, |move| ≥ 70th percentile) were fixed in
  advance and not tuned.
- The earlier `index_study` daily-feature results are reused for the models (walk-forward predictions); their
  limitations apply.

## 10. Scripts and outputs

Folder: `/private/tmp/claude-501/-Users-ramachandran-MACD-mini-ADV/77c677ac-a2ad-4b4d-977f-6fcea088fa80/scratchpad/side_selection/`.
Copied unchanged from `st_pivot_system/`: `spc.py`, `spc_bs.py`, `systems.py`, `st_lib.py`, `lanes_lib.py`. Datasets
(`ds_live.pkl`, `ds_ix.pkl`, `ds_tb.pkl`, `ds_fy.pkl`) are read from `st_pivot_system/` (mounted read-only); index
panel, simulated labels and walk-forward model predictions from `index_study/out/` (read-only). `run.sh` (engine image,
DB volume, every SQLite `mode=ro`, `--network none`) runs everything; `tb.sh` is kept for the TradeBot image.

| script | does | output |
|---|---|---|
| `side_lib.py` | method states (daily table, intraday Supertrends, pivots, VIX), own-event finders, paired ATM chooser, leg returns, premium MACD | – |
| `build_rows.py <ix\|tb\|live\|fy>` | every decision (grid + own events): all method sides, spot moves, CE and PE returns for h30 / cls / d1 / d3 / lane | `rows_<ds>.pkl` |
| `long_sim.py` | 14:45 index panel 2016–26 with simulated 3-session labels, daily methods, ridge models | `rows_lsim.pkl` |
| `stats_lib.py` | block bootstrap, power, classification, beyond-tilt bootstrap, BH, payoff profile | – |
| `ana.py` | side value per panel × decision set × exit × method (3,704 cells) | `res/cells.pkl`, `res/cells.csv` |
| `regime.py month\|series` | dominant side per underlying-month / expiry series, lock-on, capture | `res/regime_month.pkl`, `res/regime_series.pkl`, `res_regime_*.log` |
| `spot_skill.py` | signed spot move after the decision, by year | `res/spot.pkl`, `res_spot.log` |
| `valid.py` | simulated vs real CE, PE and CE−PE on matched decisions | `res_valid.log` |
| `tables.py`, `headline.py` | markdown tables used here (full set: `res/tables.md`, 1,948 lines incl. the complete power table) | `res/tables.md`, `res/headline.md` |
| helpers | `peek.py`, `probe.py`, `chk_rows.py`, `chk2.py`, `view.py` | – |
