# DD Nifty "4-indicator option buying" system: replication (9 Oct 2026)

**Source.** Upsurge Club podcast, ["The 4-Indicator System for Option Buying | Ft. DD Nifty"](https://www.youtube.com/watch?v=NzqslNnAGiw). Rules are taken from 8:12–26:00 and 32:00–34:00.

**Rules**
- **Long entry:** a 3- or 5-minute index candle closes with all four true:
  - above Supertrend(10,3);
  - RSI(14) > 70;
  - above the previous day's pivot R1;
  - above the upper Bollinger Band (20, 2).
- **Exit:** the first candle that closes below Supertrend, which is both the initial and the trailing stop.
- **Strike:** the call whose premium is about 1% of the index.
- **Expectations he states:** win rate 40–45%, with a few trades making the year.
- **Puts:** only hinted at. The mirror tested here is below Supertrend, RSI < 30, below S1 and below the lower band.

**How it was replicated.** Intraday only:
- entries from bars ending by 15:00, forced exit at 15:15;
- one trade at a time;
- fill at the next 1-minute open.

**Data**
- **Spot:** Fyers 1-minute NIFTY, BANKNIFTY and SENSEX, Jan 2021 – 8 Oct 2026.
- **Real premiums:**
  - TradeBot 1-minute chains: NIFTY Apr–Aug 2025, SENSEX Apr 2025 – Feb 2026.
  - App DB: index options, Aug–Oct 2026.
- **Costs:** Rs 20 × 2 + 0.05%/0.15% + 0.25% slippage each side.
- **Control:** random entries on Supertrend-up bars, with the same count per year and the same exit, 30 draws.

## 1. The filters work as he says, on spot (2021–2026, 5-min)
| NIFTY | Trades | Win | Points/trade | Total points |
|---|---|---|---|---|
| Supertrend only | 1,853 | 41% | +2.5 | +4,630 |
| + RSI > 70 | 841 | 44% | +4.3 | +3,623 |
| + above R1 | 568 | 48% | +8.3 | +4,716 |
| + upper Bollinger (all four) | 504 | 48% | +8.0 | +4,024 |
| Random Supertrend-up entries | 504 | 41% | +3.0 | +1,259 |

- All four beat the random draws: 97% (NIFTY, BANKNIFTY) and 100% (SENSEX).
- 3-minute bars show the same pattern.

## 2. But the call version is a bull-market tool that has stopped working
**NIFTY spot points per year, all four, 5-min:** 2021 +91, 2022 +1,232, 2023 +1,349, 2024 +1,308, 2025 +545, 2026 −502. SENSEX was −1,858 in 2026.

**Real premiums, Rs 50k per trade**

| Dataset | Bars | Trades | Result | Win | vs random |
|---|---|---|---|---|---|
| NIFTY, Apr–Aug 2025 | 3-min | 42 | +Rs 35k | 38% | above 60% of draws; profit came from ≤5 trades |
| NIFTY, Apr–Aug 2025 | 5-min | 35 | −Rs 19k | | |
| SENSEX, 2025–26 | 3-min | 81 | −Rs 45k | | |
| SENSEX, 2025–26 | 5-min | 69 | −Rs 88k | | |
| App, Aug–Oct 2026 (NIFTY, BANKNIFTY, SENSEX) | 3- and 5-min | 3–9 each | losses on all | 0–33% | |

**Estimated yearly result (5-min, Rs 50k per trade), all four filters.** Premium change was fitted to index move and hold time on 2,276 real trades (R² 0.88):

| Year | NIFTY | SENSEX | BANKNIFTY |
|---|---|---|---|
| 2021 | −0.5 L | +0.2 L | |
| 2022 | +2.3 L | +2.6 L | |
| 2023 | +2.2 L | +1.7 L | |
| 2024 | +1.7 L | +1.8 L | −1.5 L |
| 2025 | +0.4 L | +0.9 L | |
| 2026 | −1.1 L | −1.2 L | |

This is the same shape as a viewer's backtest posted under the video: +2.7 L in 2022, decaying to −0.8 L in 2026.

## 3. The put mirror covers the years the call version loses
**Spot, 5-min, all four, NIFTY points per year:** 2024 +2,551, 2026 +3,077; small or negative in 2021–2022 and 2025.

**Real premiums, put version**
- **SENSEX 2025–26, 5-min:** +Rs 1.39 L on 64 trades, above all 10 random draws.
- **NIFTY Aug–Oct 2026:** +Rs 84–86k on 14–18 trades, above all 10 random draws.
- **NIFTY Apr–Aug 2025 and BANKNIFTY 2026:** negative.

**Estimated yearly result for both sides together, NIFTY, 5-min:** 2021 −1.2 L, 2022 +1.7 L, 2023 +2.5 L, 2024 +5.6 L, 2025 +0.5 L, 2026 +3.5 L. Each side catches its own regime's trend days. SENSEX was positive every year except 2021.

## Caveats
- **Real option prices cover only Apr 2025 – Oct 2026**, the system's weakest stretch. Samples there are small: 3–87 trades per cell. The 2021–24 numbers are estimates from index moves, not traded premiums.
- **Profit is thin and concentrated:** about +4–5 bps of index per trade, and the top 5 trades often exceed the total. At 1% slippage per side, most of the edge disappears.
- **Not tested on stocks.** The earlier 15-min stock study (`research/strsi_r1_20261008`) found no edge there.

## Files
- `bt_spot.py`, `bt_spot_pe.py`: spot backtests, with ablation and random controls.
- `opt_tb.py`, `opt_live.py`: real premiums.
- `spot_report*.py`, `opt_report*.py`: reports; output in `out/*.txt`.
- `data/idx1m.json`: Fyers 1-min data, 2021–2026.

## 4. Exact rules on stocks (added 9 Oct; replaces the reference to the 15-min study)
The 8 Oct 15-min study used different rules: no Bollinger filter, first signal of the day, ATM strike, holds up to 3
days. It is not evidence about this system. Here the exact video rules are applied to 214 stocks, 20 Aug – 7 Oct 2026,
on 3- and 5-min bars, intraday, with a Supertrend exit. The option is the stored strike with premium nearest 1% of spot
(median achieved 1.2%).

| | Spot bps/trade (all four) | Random entries | Real option, mean/trade | Win | Random option | Rs 50k/trade total |
|---|---|---|---|---|---|---|
| Call system, 5-min | −0.3 | −1.9 | −3.5% | 32% | −3.7% | −25.4 L (1,453 trades) |
| Call system, 3-min | −2.3 | −1.5 | −2.8% | 30% | −2.7% | −26.1 L (1,856 trades) |
| Put mirror, 5-min | +6.1 | +4.4 | +5.0% | 44% | +2.1% | +56.0 L (2,248 trades) |
| Put mirror, 3-min | +5.1 | +2.9 | +5.1% | 44% | +1.9% | +71.4 L (2,813 trades) |

**On stocks the filters add little.** Win rate rises only from 35–37% to 38–41%, unlike the indices.

**The call system lost**, about the same as random call entries.

**The put mirror made money**, but:
- Random puts also made +2% per trade in this falling market.
- The filter's extra ~3 points per trade is not significant when days are the unit (t 0.2–0.4).
- August was flat or negative; the gains are September–October.

So on stocks, this window's result is the market's direction, not the system.
