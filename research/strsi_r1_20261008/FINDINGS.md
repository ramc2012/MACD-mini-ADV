# Supertrend up + RSI > 70 + above daily R1 → buy ATM CE (8 Oct 2026)

**Hypothesis.** On spot, when the 15-minute Supertrend(10,3) is up, 15-minute RSI(14) is above 70 and price closes above
the previous day's floor-pivot R1, buying the ATM call gives sustained gains.

**Verdict.**
- **Stocks, 2026: no edge.** Calls bought on the signal did no better than calls bought at the same minute on
  random other stocks, and lost at every horizon in a falling market.
- **NIFTY/SENSEX, 2025–26: a short-lived intraday push.** The calls rose for about 15–30 minutes, but the samples
  are small (22 and 47 signals). This is not proven, and the gains did not last.

## Data and rules
- **Signal:** first completed 15-min spot bar of the day where all three conditions hold, ending by 15:00.
  Indicators run continuously across days. Pivots come from the previous session (P=(H+L+C)/3, R1=2P−L).
- **Entry:** open of the next 1-minute bar, using the real premium of the nearest-strike CE (and PE, for the side
  check). The series must expire after the third session ahead.
- **Costs:** Rs 20/order × 2 plus 0.2% of premium.
- **Exits measured:** +15m, +30m, +60m, 15:15 same day, D+1/D+2/D+3 15:15, and a Supertrend-flip exit (capped at
  D+3).
- **Controls:**
  - Live: 3 random other stocks at the same minute.
  - Index: the same clock time on 8 random other days.
- **t-statistics** use the day as the unit.

| Dataset | Signals with real premiums | Period |
|---|---|---|
| Live (214 stocks + indices, app DB) | 1,039 on 33 days (1,428 signals) | 20 Aug – 7 Oct 2026 |
| NIFTY (TradeBot 1-min chain) | 22 | Apr – Jun 2025 (contracts end Aug 2025) |
| SENSEX (TradeBot 1-min chain) | 47 | Apr 2025 – Feb 2026 |

## Results: CE return net of costs

| Horizon | Live: signal | Live: matched control | NIFTY: signal (win) | SENSEX: signal (win) |
|---|---|---|---|---|
| +15 min | −0.6% (43%) | −0.7% (38%) | **+2.4% (55%)** | 0.0% (55%) |
| +30 min | −1.1% (39%) | −0.9% (40%) | **+4.5% (59%)** | +1.4% (53%) |
| Same-day 15:15 | −2.9% (37%) | −2.7% (35%) | +3.6% (55%) | −0.2% (49%) |
| D+1 | −10.2% (29%) | −9.6% (32%) | +3.6% (41%) | −2.5% (45%) |
| D+3 | −21.7% (25%) | −21.3% (27%) | +25% (59%), controls +34% | −3.1% (40%), controls −33% |
| Supertrend exit | −8.3% (25%) | −3.0% (31%) | +7.8% (32%) | +4.6% (36%) |

**Live**
- **Against the matched control:** −0.6 points at 15 minutes and roughly zero at the close. It was −2.6 to −3.8
  points at D+1 to D+3 (t −0.9 to −1.6), and −6.6 points on the Supertrend exit (t −3.9).
- **Against the PE at the same moment:** about even intraday. By D+1 to D+3 the call trailed the put by 22–52
  points, which is the falling market.

**Did gains last?** Live calls often ran up but gave it back:
- 28% of signal calls touched +50% and 12% touched +100% within three sessions (controls: 25% and 7%).
- Only 9% were still above cost at the close, D+1, D+2 and D+3 together.

**Indices**
- **NIFTY:** the call beat the put in 73–77% of cases at 15–30 minutes (t 2.2 and 1.7).
- **SENSEX:** signal calls held value far better than time-matched calls at D+2 and D+3 (+23 and +31 points, t 2.2
  and 2.5), but were still slightly negative in absolute terms.
- **Not proven:** with about 8 horizons across 3 datasets, a t near 2 is expected by chance. Roughly 45 NIFTY or
  160 SENSEX signals (about 2–3.5 years) would be needed for t ≈ 2 at the observed effect.

## The conditions themselves (spot, ablation)
- **Supertrend adds nothing once RSI > 70.** Every RSI > 70 bar already had Supertrend up, so the RSI70 and
  ST+RSI70 sets were identical.
- **R1 adds little.** R1 alone, ST+R1 and the full set moved alike.
- **On live stocks, every set trailed the market**, by −2 to −3 bps in the first 30 minutes (t ≈ −2.3).
- **On the indices, RSI > 70 alone** gave the best 30-minute drift (SENSEX +5.6 bps, 68% up, t 2.5).

## Files
- `signals.py`: spot signals.
- `spot_outcomes.py`: spot horizons and controls.
- `live_options.py`: real premiums from the app DB, run read-only in the desk container.
- `index_options.py`: TradeBot chains.
- `analyze.py`: produces `out/results.json`.
- Data: `data/spot_min.csv` (1-min spot to 7 Oct) and `data/daily_all.json` (Fyers daily, Sep 2024 – Oct 2026).
