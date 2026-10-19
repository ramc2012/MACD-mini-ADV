# Can relative strength pick the month's top performers in real (absolute) returns? (17 Oct 2026)

92 months (Feb 2019 – Sep 2026), daily closes. Pick the top 3 of 13 sector indices / top 20 F&O stocks by a signal,
either at the last close before the month (M0, held all month) or after the month's 3rd session (D3, held to month end),
and score against the month's actual absolute returns. Code `picks.py`, `split.py`; output `report.txt`, `split.txt`,
per-month CSVs. Before costs. Stocks shown for names listed since 2019 (175) to limit survivorship; the full 210 agree.

## Headline numbers (M0, picked before the month)
| | Sectors: hit top 3 | bottom 3 | picks vs avg /mo | Stocks: hit top 20 | bottom 20 | caught #1 | picks vs avg /mo (t) |
|---|---|---|---|---|---|---|---|
| Random | 23% (theory) | 23% | ~0 | 11% | 11% | 11% | ~0 |
| RS 20d | 32% | 29% | −0.18% | 15% | 16% | 21% | +0.48% (1.5) |
| RS 60d | 29% | 26% | +0.27% | 17% | 16% | 24% | +0.80% (2.3) |
| RS 120d | 25% | 27% | −0.02% | 18% | 16% | 24% | +0.97% (2.5) |
| Most volatile (control) | 27% | 30% | +0.02% | 19% | 20% | **54%** | +1.00% (2.4) |
| Volatile 40%, then RS 60d | 27% | 30% | +0.08% | 18% | 17% | 30% | +0.90% (2.5) |
| RRG Leading | 21% | 26% | −0.58% | 12% | 12% | 5% | −0.12% |

## What it means
1. **The month's top performers are mostly volatile names, and so are its worst.** Simply picking the 20 most volatile
   stocks contains the month's #1 stock in 54% of months, and lands in the top 20 more often than any RS signal, but
   just as often in the bottom 20. Raw "hit the top performers" rates flatter any signal that leans to volatile names.
2. **Relative strength chooses the side, and that part is real for stocks.** RS 60d/120d picks beat the average stock in
   *both* NIFTY-up months (+0.7%) and NIFTY-down months (+1.0 to +1.25%; 46–49% of picks' months positive vs 33% for
   random). The volatility control wins only in up months (+2.2%) and loses in down months (−0.6%): that is beta, not
   selection. Every year 2019–2024 was positive for RS 60d stock picks; 2025–26 so far is slightly negative (−0.5%/mo).
3. **Sectors: RS barely helps.** 13 sectors, top 3 picks: hit rates are within the noise of the random baseline
   (which itself swung 18–24% by chance). RS 60/120d sector picks lost ~0.6% less than average in down months and did
   nothing in up months: a defensive tilt, not a way to find the month's best sector.
4. **Short RS (5–20d) and RRG quadrants are the weakest selectors.** RRG "Leading" is at or below random everywhere.
   Longer RS (60–120d) is the useful horizon for monthly picking; 20d was best at landing near the top but also the
   bottom.
5. **Real returns still come from the market.** In NIFTY-up months RS stock picks made +6.0% on average, in down months
   −1.6% (the average stock −2.6%). Holding picks only when NIFTY was above its 50-day average skipped 39 of 92 months,
   raised the hit rate of positive months (77–83% invested months > 0) but lowered the average per month, because it
   also sat out recoveries.
6. **After 3 sessions:** the month's eventual top performers are often already visible (first-3-session leaders are
   in the final top 3 sectors 45% of months vs 22% random), but that is largely the gain already made; buying them on
   day 3 earned only +0.06% more than average over the rest of the month. For stocks, 60–120d RS on day 3 still added
   +0.8%/mo over the rest of the month (t 2.4–2.9), in up and down months alike.

## Use in the app
- For stock selection (stock-option buying), rank by 60–120-day relative strength; restrict to volatile names when the
  aim is a big absolute move, since RS alone picks steadier names. Expect ~+1%/month over the average stock, not a
  reliable route to the month's #1.
- For sectors, treat RS as context (which side of the market is stronger) rather than a picker.
- The Sector wheel's 60D ring is the most useful of its rings for this; a 120D ring and a "volatile + strong" pick
  list are natural additions.
