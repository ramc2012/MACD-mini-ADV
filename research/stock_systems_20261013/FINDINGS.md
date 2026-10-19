# Stock-picking systems: suitability for the option agent's 1–3 day CE/PE lists (13 Oct 2026)

## Data
- **Spot:** 210 F&O stocks, Fyers daily bars Jan 2021 – Oct 2026. Tested 2022 – Oct 2026 (2021 is warm-up).
  - Signals are taken at day t's close, standing in for 14:45.
  - Targets: next 1–3 day excess return over the universe (direction) and absolute return (magnitude).
- **Options:** the agent's own labelled stock candidates, 24 Aug – 7 Oct 2026 (31 sessions, 54.6k contracts; falling
  market).
  - Scores from the previous close.
  - Each list is the top-10 underlyings per side, compared with the same day's pool on y (mean premium return over
    D+1..D+3).
- **Caveats:**
  - Today's F&O list was used, so there is survivorship bias.
  - Spot returns are close-to-close.
  - The option sample is one regime.
  - About 20 systems were tested, so expect one false t > 2 by chance.

## Literature context
- **Momentum:** documented in India at 3–12 month formation and holding periods (Ansari & Khan 2012; Dhankar &
  Maheshwari, NSE 1997–2013).
- **Low volatility:** a long-horizon effect in India (Saraf & Kayal 2022: negligible under 3 years).
- **Neither** is a 1–3 day effect, which is our horizon.

## Spot results (2022 – Oct 2026)
- **Positive IC in all 5 years:**
  - IBS low, RSI(2) oversold, 5-day and 1-day reversal, Bollinger %b reversion. IC 0.015–0.025, t 3–6.
  - 12-1 momentum (h=3 IC 0.020, t 4.5).
- **Nothing:** 3-month momentum, 20-day relative strength, Donchian breakout, 200-day trend alone, low-vol, MAX, gap
  reversal.
- **Magnitude:** realised vol (IC 0.19) and the recent absolute move (0.11) predict the size of the move. NR7 and ATR
  contraction predict *smaller* moves; there is no "squeeze" at 1–3 days.

## Option results (31 sessions)
| Rule | Top-10 vs pool | t |
|---|---|---|
| CE: 12-1 momentum + 5-day loser (buy the dip in a winner) | **+7.2 pts** | 3.6 |
| CE: above 200-day + RSI(2) oversold + low IBS | +7.0 | 3.1 |
| CE: above 200-day + RSI(2) oversold | +5.9 | 2.5 |
| CE: our agent | **−10.5** | – |
| PE: 5-day winner | +12.5 | 1.9 |
| PE: Connors short (downtrend + overbought) | −2.2 | −0.6 |
| PE: our agent | **+17.9** | – |

- **The CE composites held in both halves of the window**, and on spot every year: h=3 +11–16 bps over the universe,
  t 4–4.7, positive 2022–2026.
- **The PE rules were not stable on spot.** The 5-day-winner rule changes sign by year.
- **Side selection** (CE vs PE for the same stock) from any of these signals: 47–53%, a coin flip.
- **Realised vol** picks bigger movers but not better options, because the move is priced in IV.

## Verdict
- **Suitable:** "buy the dip in a long-term winner" for the CE list. It is supported by 5 years of spot data and the
  live option sample, and it replaces the agent's worst component (its CE ranking, −10.5 pts vs random).
- **Keep the agent's PE ranking** (+17.9 pts). No PE system was robust.
- **Not suitable:** chasing short-term momentum or breakouts, low-vol/MAX, volume shocks, NR7. None of these systems
  can choose CE vs PE.
- **Absolute CE win rates stay low (32–35%) in a falling market.** The improvement is relative to the pool.

## Trendlines (added 13 Oct, at the user's request)
**Definition.** Algorithmic and with no lookahead (`trendlines.py`, `trendlines3.py`):
- A swing high or low is confirmed k bars after the fact (k = 3, 5, 10 daily; 3, 5, 8 on 30-min).
- Falling resistance runs through the last two swing highs; rising support through the last two swing lows.
- Events: breakout (first close > resistance + 0.1 ATR), breakdown, bounce up, bounce down.
- Also tested with "valid only after a third touch".

**Results:**
- **Stocks, daily, 2022–26.** Every event's hit rate is within 0.5–2.5 pts of base.
  - Bullish events underperform the market afterwards; bounce-up −5 to −12 bps over 3 days (t ≈ −2). That is
    short-term reversal again.
  - Breakdowns underperform by ~7–10 bps over 3 days (t 1.2–2.0, 4/5 years). Weak, and puts still lose in absolute
    terms in an up-drifting market.
- **Stock options, 24 Aug – 7 Oct.**
  - Breakout CE +1.3 pts and breakdown PE +1.7 to +3.1 pts vs pool (t ≤ 0.6).
  - Bounces −3.6 / −5.4 pts. No use.
- **Indices, daily.** Breakouts above a falling trendline: 55–61% up three days later vs ~50% base. But n = 43–148,
  the indices are correlated, and the lift held in only 3–4 of 5 years. Not proven.
- **Indices, 30-min, state by 14:45, scored on the index lane plan.** No event lifted the win rate in ≥ 4 of 5 years.
  The best was a k=5 breakdown, +4 to +6 pts in 3/5 years. Bounce-up hurt in 4/5 years.

**Verdict.** Not suitable as a picker for stocks or indices at a 1–3 day horizon. The index scorecard's yearly
selection rule (lift in every year) would not admit any trendline condition.

### Correction after the visual check (13 Oct)
**Bug.** The example charts exposed a bug: a broken trendline was rebuilt from the same two anchors on the next bar,
so dead lines could fire again. Fixed (`dead` set). Event counts fell by 30–50%.

**Corrected results** (`out/trendlines_3touch_fixed.txt`, `out/trendlines_followup_3touch_fixed.txt`):
- **Stocks, spot:** hit rates are still within about 3 pts of base. Bullish events still underperform afterwards.
- **Stock options:** worse than reported before.
  - Breakout CE −6.7 pts and breakdown PE −8.7 pts vs pool.
  - Bounce-up CE −8.3 pts (t −2.5).
- **Index daily:** k=5 breakouts were up 3 sessions later 72% of the time (4 of 5 years), but on only 39 events across
  four correlated indices. Not proven.
- **Index 30-min:** one of 12 variants, k=8 bounce-down → PE, lifted the PE win rate +4.4 pts in all 5 years
  (n = 194, mean +0.13R). With 12 variants tried, that could be chance. It is the only trendline condition worth
  adding to the scorecard's candidate pool, where the yearly selection rule would decide.

**Verdict unchanged:** trendlines are not a stock picker. See `out/trendline_examples.png`.

## Trendlines + filters (13 Oct, `tl_combos.py`, `tl_combo_options.py`)
**Setup:**
- 14,740 fixed-detector events (k=5, third touch), 210 stocks, 2022 – Oct 2026.
- 16 classic filters and all pairs (136 per event type, 544 in total): trend/200dma, 12-1 momentum, market vs
  50-dma, 20d relative strength, volume ≥ 1.5×, close strength (IBS), RSI14 stretch, RSI2 pullback, ATR% tercile.
- **Selection rule:** chosen on 2022–24 only. A filter must beat the unfiltered events on BOTH 3-day excess return
  and hit rate in each year, with ≥ 30 events a year.

**Results:**
- **9 of 544 passed.** That is what chance predicts, about 1.6% or ~9.
- **Out of sample (2025–26):** 5 of 9 still beat the unfiltered events, a coin flip. Every filtered breakout variant
  still lost to the market (−4 to −28 bps over 3 days).
- **Only coherent survivor:** PE on a stock lagging NIFTY over 20 days, rejected at a falling trendline (red candle)
  on ≥ 1.5× volume.
  - 2022–24: +30 bps over 3 days (n 215).
  - 2025–26: +25.5 bps, 55% hit (n 144).
  - It fires about 70 times a year across 210 stocks. In the 7-week option sample it fired 4 times, −42 pts vs the
    pool (meaningless at n=4).

**Verdict.** Filtering trendline signals does not reliably pick better trades; the filters selected are what chance
would select. The one coherent rule is too rare to validate on options yet. At most, log it in shadow.
