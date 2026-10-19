# Index lane: review and training study (12 Oct 2026)

**Live model.**
- **Model:** `index-only-l100`, a ridge regression on 13 features. It is trained on about 31 days (1,607 index rows).
  The decision is driven by the 5-day return (−0.24), position in the 20-day range (+0.22) and the 20-day return
  (+0.21).
- **Record:** 39 walk-forward and live trades, 41% winners, 34 puts. Live scheduled trades: 1 win in 8.

**Data built here.**
- **Rows:** 5,063 index-days (NIFTY, BANKNIFTY and SENSEX from Apr 2021; MIDCPNIFTY from Oct 2022), as of 14:45, from
  Fyers 1-min spot.
- **Labels:** the lane's own plan (stop 1×ATR14, target 1.5×ATR14 on spot, 3-session time exit) for both sides, plus
  alternative target/stop plans.

## 1. Side prediction from features: nothing beats "always CE" (monthly walk-forward, 2022–2026)
| | Win | Mean R |
|---|---|---|
| Always CE | 49.6% | +0.062 |
| Always PE | 43.1% | −0.021 |
| 20-day trend (the live lean) | 47.4% | +0.040 |
| Ridge, live features | 47.7% | +0.027 (91% CE) |
| Ridge, all features | 46–49% | +0.01 to +0.05 |
| GBM, all features | ~45% | about −0.02 |

- **High-confidence days only:** trading just those did not raise the win rate.
- **Interpretation:** the models learn the recent drift (the live one puts, the 5-year ones calls), not side skill.

## 2. The plan sets most of the strike rate (always-CE, 2022–26)
| Plan (target / stop × ATR, hold) | Target hit first | Win | Mean R |
|---|---|---|---|
| 1.5 / 1.0, 3d (live) | 21% | 49.6% | +0.062 |
| 1.0 / 1.0, 3d | 38% | 52.8% | +0.038 |
| 0.75 / 1.0, 3d | 49% | 56.9% | +0.017 |
| 2.0 / 1.0, 3d | 10% | 49.3% | +0.084 |

## 3. Conditions with a consistent win-rate lift (≥4 of 5 years)
- **Last hour's direction:** CE +3 points in 5/5 years; PE +2 in 4/5.
- **Hourly RSI > 70 → CE:** 59% win, +10 points.
- **Daily RSI < 30 → CE (bounce):** 59%, n=99.
- **Today's move > 0.5 ATR, followed:** CE +4, PE +1.
- **Puts against an oversold daily chart:** 29% win (0/5 years).
- **Puts against an overbought hourly chart:** 34% win.
- **Adaptive "side that won lately":** no better than always CE.

## 4. Scorecard, strict out-of-sample
Conditions were re-selected each test year using only earlier years: a condition is kept if its lift was positive in
every prior year. Results at the 1.0/1.0 plan, net score ≥ 2:

| Test year | Scorecard win / R | 20-day trend rule win | Always CE win / R |
|---|---|---|---|
| 2024 (strong up year) | 52% / 0.00 | 49% | 56% / +0.07 |
| 2025 | 53% / +0.05 | 51% | 51% / +0.02 |
| 2026 (falling) | 59% / +0.15 | 53% | 46% / −0.13 |

- **Stable conditions:** last-hour direction (both sides), hourly RSI > 70 (CE), gap direction, today's move (PE).
- **Verdict:** it beats the live-style trend logic in all three test years, and always-CE in 2 of 3 (not in the 2024
  bull year). The lift is modest: about +2–6 points of win rate. It is not a large edge.

## 5. Deployed (12 Oct 2026, after the close; user chose to keep the 1.5/1.0 plan)
- **New module `engine/src/macd_trader/index_scorecard.py`.**
  - New table `agent_index_days`: 5,304 seeded rows (Fyers 2021 → 7 Oct), extended nightly from the minute store.
  - Conditions are selected on the 1.0/1.0 ATR outcome (`ce_sel`/`pe_sel`) and traded on the 1.5/1.0 plan.
  - Selecting on the 1.5/1.0 outcome itself generalised worse: 2026 OOS 48% / −0.10R, calls only.
- **`index_trades.train()`** uses the scorecard once the table is seeded; the ridge configs remain as the fallback.
- **Champion v12 selection:** CE on last_hour, rsi_h_follow, gap_follow; PE on last_hour, move_follow; min net 2;
  puts vetoed when daily RSI < 30 or hourly RSI > 70.
- **2026 year-to-date, out of sample** (selection used 2022–25 only): 190 trades of 756 index-days, 53.2% win,
  +0.14R, 97 CE / 93 PE. Always-CE on the same days: 40.7% / −0.19R.
- **Parity:** the app's feature code against the research build on 100 index-days (Sep–Oct 2026) gave identical
  decisions and identical labels.
- **Caveat:** on 9 Oct the scorecard would have bought calls on all four indices (gap up, last hour up). Monday fell
  and the old model's puts won. On 12 Oct it says BANKNIFTY PE only, no trade elsewhere.
