# Vanguard (Nomad Curie) vs our option agent: 13 Oct 2026

**Vanguard as deployed.** `nomadcurie_vanguard_cycle`, source in
`TradeBot/.claude/worktrees/directional-paper/vanguard`. It has ten modules:
- M1 data: participant OI, bhavcopy, bulk deals, announcements.
- M2 options flow: IV/IVS/skew/OI.
- M3 GEX regime.
- M4 sector RS and lead-lag.
- M5 microstructure timing.
- M6 fusion.
- M7 sizing.
- M8 backtest.
- M9 paper execution.
- M10 attribution.

Its actionable output moved to a pre-close **swing list**:
- **List:** 10 CE + 10 PE stock options (no indices).
- **Timing and contracts:** decided at 14:15 and entered at 14:45. ATM or 25-delta wing contracts, horizon D+1–D+3 per
  item.
- **Rankers:** listwise "direction" and "contract/option-payoff" rankers.

**Status:**
- Both rankers are shadow and fail their own promotion gates (test overlap@10 1.6 / 1.2, needs ≥ 2; ≥ 20 sessions).
- No actionable or sized entries. The only realised book is M9: 14 trades, 28–31 Aug, +Rs 17k.
- Its own IC study found M2 flow ~0, and M4 sector RS and M5 timing significantly negative (intraday fade beats
  momentum).
- The 12 Oct review notes missing lists 5–8 Oct and Fyers 401s.

## Head to head
The 12 sessions where both systems have lists, 8 Sep – 9 Oct. All marks at 14:45, 1% round-trip cost, stock options
only. Our September lists are walk-forward, and Vanguard's early-September lists are replays, so both are partly
retrospective. The benchmark is our own candidate pool, same day and same side.

| D+1 | n | Mean | Median | Win | vs pool |
|---|---|---|---|---|---|
| Vanguard all | 227 | −4.4% | −9.2% | 40% | −3.8 pts (t −1.5) |
| Vanguard CE / PE | 112 / 115 | −9.7% / +0.8% | | 30% / 50% | −1.8 / −5.7 |
| Our agent all | 229 | +1.3% | −9.8% | 38% | +1.9 pts (t +0.3) |
| Our agent CE / PE | 119 / 110 | −15.3% / +19.3% | | 24% / 53% | **−8.5 (t −2.2) / +13.2 (t +1.4)** |
| Pool CE / PE | | −6.4% / +3.7% | | 33% / 47% | |

- **Head to head:** ours beat Vanguard by +5.3 pts a day at D+1 (t 0.9, better on 6 of 12 days). Not significant.
- **Overlap:** the two systems shared only 14 of 240 names.
- **D+2 / D+3:** ours PE +24 / +34.5 pts vs pool (t 1.6 / 2.0), ours CE −11.9 / −20.2 (t −2.4 / −3.9). Vanguard has
  few D+2/D+3 marks (n 63 / 33).

## Reading
- **Neither system has proven picking skill**, and both medians are negative (lottery payoffs).
- **Our agent:** its put selection beat the pool, and its call selection was worse than random by a significant
  margin. The same pattern as the 9 Oct run.
- **Vanguard:** slightly below the pool on both sides.
- **The clearest lesson both systems point to:** momentum-style call picks fade. Vanguard's IC study found momentum and
  sector confirmation anti-predictive, our CE list underperformed, and the auction lane's breakouts reversed.
