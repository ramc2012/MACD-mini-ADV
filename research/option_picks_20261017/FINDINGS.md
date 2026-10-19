> **17 Oct audit correction:** The original real-premium means below are retained for provenance, not validated executable returns. The old replay entered at the signal-day option close and accepted an earlier print on missing exit days. `study2.py` now uses next-session EOD, requires the exact exit day and positive volume, charges both sides, and writes separate `real_eod_*`, `coverage_eod_*`, and `report2_eod_corrected.txt` outputs. These EOD diagnostics still lack bid/ask execution evidence. Corrected aggregate returns have not yet been rerun; the original +5–10% claim must not support promotion. Profit-taking, time stops, and regime gates require frozen prospective validation.

# Picking the top-10 (CE) and bottom-10 (PE) F&O stocks for option trades (17 Oct 2026)

Every 5 sessions, each rule names 10 CE and 10 PE stocks out of ~200. Two judges:
1. **Option yardstick, 2021 → Sep 2026** (283 weekly decisions): ATM option held H sessions, premium estimated from
   the stock's own volatility (0.4 × 1.15 × 20-day vol × √T), 3% spread. `study.py` → `report.txt`.
2. **Real monthly stock options, Apr 2025 → Mar 2026** (TradeBot archive, ~45 decisions): buy the ATM option of the
   nearest monthly expiry alive at the exit at the day's last 30-min close, sell H sessions later. `study2.py` →
   `report2.txt`, `real_h10.csv`, `real_h20.csv`. One year only: indicative, not proof.

## Finding the movers (hit@10 = share of picks that became the period's actual top/bottom 10; random ≈ 5%)
| Rule | CE hit@10 | CE in wrong decile | PE hit@10 | PE in wrong decile |
|---|---|---|---|---|
| Random | 4.9% | 9.8% | 4.7% | 9.7% |
| Improving→Leading / Weakening→Lagging (daily RRG) | 4.1% | 9.8% | 4.9% | 10.9% |
| same, weekly RRG | 4.7% | 8.9% | 4.9% | 11.6% |
| same + most volatile 40% | 7.4% | 11.2% | 7.7% | 14.5% |
| Leading (already moved) / Lagging | 6.4% | 12.5% | 3.9% | 9.0% |
| RS 60d strongest / weakest | 11.9% | 17.7% | 6.7% | 12.8% |
| Volatile + RS 60d | 12.8% | 18.7% | 7.7% | 14.6% |
| ML ranker (walk-forward) | 13.0% | 18.3% | 11.8% | 20.1% |
(H = 10 sessions, 2021+; H = 5 and 20 agree.)

- **Rotation entries (Improving / Weakening) do not find the next movers**: no better than random on daily or
  weekly RRG. Adding volatility raises the hits but raises the wrong-way picks just as much.
- **Volatility predicts the size of the move, not its direction**: every rule that leans to volatile names lands
  more often in both tails. The option market charges for that volatility, so it is not an edge by itself.
- **Strength persists**: 60-day RS leaders become top-10 movers 2.4× as often as random. The "move has already
  happened" worry is not borne out on 10–20 session horizons.
- The bottom 10 are much harder to call than the top 10 (best rule-based PE hit 7–8%).

## Option P&L
| Rule (CE side) | Yardstick 2021+ H=10: mean / median / win | Real options 2025–26 H=10 | Real H=20 |
|---|---|---|---|
| Random | +2% / −44% / 34% | −12% / −33% / 35% | −15% / −48% / 27% |
| RS 60d | +7% / −43% / 34% | **+7% / −16% / 43%** | +5% / −29% / 35% |
| Volatile + RS 60d | +6% / −45% / 34% | **+7% / −18% / 41%** | **+10% / −24% / 39%** |
| Leading (already moved) | +13% / −39% / 35% | +3% / −16% / 41% | −5% / −47% / 32% |
| Improving→Leading weekly | +6% / −42% / 35% | +10% / −26% / 37% | −8% / −41% / 32% |
| Improving→Leading weekly + volatile | +7% / −32% / 35% | −7% / −28% / 32% | −19% / −54% / 29% |
| Squeeze + trend (cheap-vol idea) | +27% / −27% / 37% | +0.5% / −28% / 35% | −2% / −45% / 34% |
| ML on option edge | +42% / −17% / 40% | −7% / −35% / 34% | −14% / −49% / 28% |

- **PE side: nothing worked.** Every rule's puts lost on the yardstick (−12% to −42% per trade) and on real options
  (−9% to −33%); puts on the weakest names taken only when NIFTY was below its 50-day average were the worst
  (−44% to −74%). Weak stocks bounce, and put premium decays.
- **The "cheap option" ideas were an artefact of the estimate.** Quiet stocks' realised volatility drops but their
  implied volatility does not, so the real premiums were not cheap. The real-premium check matters.
- **CE on 60-day RS leaders (optionally the volatile ones)** is the only rule that beat random calls on both judges and
  on both horizons: ~+5 to +10% per trade vs −12 to −15% for random calls in 2025–26, 39–43% winners averaging about
  +90–120% against losers about −55%. Thin, and from one real year.
- Holding to the horizon gives medians of −16% to −50%: the edge sits in the winners, so exits (profit-taking on the
  big winners, a time stop) decide the result more than the pick list does.

## Trader's verdict
- Use relative strength (60-day) to choose CE names; the rotation hook (Improving→Leading) does not add anything.
- Prefer CE in a NIFTY uptrend (yardstick: RS 60d CE +13% when NIFTY > 50-day average vs −2% below it).
- Do not buy stock puts off a "bottom 10" list; bearish views are better expressed on the index, or not at all.
- Not proven: one real year of premiums, before slippage beyond 3% and without exits. Paper-trade it first.
