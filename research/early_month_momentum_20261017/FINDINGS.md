# Does following early performance give an edge? (17 Oct 2026)

Data: Fyers daily closes Jan 2019 → 16 Oct 2026 (92 complete months) for 16 sector/broad indices and today's 210 F&O
stocks (`fetch.py`, read-only); stored 1-minute bars Aug–Oct 2026 for the intraday test. Code: `study.py`, `control.py`;
output: `report.txt`, `control.txt`. Returns before costs.

## Month: first N sessions → rest of the month

| Universe | Signal | Rank IC | Top − bottom (rest of month) |
|---|---|---|---|
| Sector indices (top 3 vs bottom 3) | first 1 / 3 / 5 sessions | +0.03 / +0.01 / +0.02, t < 1 | +0.6 / +0.2 / +0.6 %/mo, t ≤ 1.4 |
| Sector indices | first 10 sessions | +0.07, t 1.8 | +1.0 %/mo, t 2.7 (but only ~half a month left to hold) |
| F&O stocks (top 20 vs bottom 20) | first 5 sessions | +0.01, t 0.4 | +1.0 %/mo, t 2.4, 60% of months |
| NIFTY 50 itself | sign of first 1/3/5/10 sessions | — | repeats 47–54% of months; below "always the majority side" |

Controls (stocks, `control.txt`):
- Ranking by the **previous full month** gives the same spread (+1.20 %/mo) as the first 5 days (+1.12). The stock
  effect is ordinary short-horizon cross-sectional momentum, not anything special about the start of a month.
- Keeping only names listed since 2019 (cuts recent listings) drops it to +0.72 %/mo, t 1.8; part of the effect is
  survivorship (today's F&O list holds yesterday's winners). Random ranking: ~0.
- Year by year the index spread flips sign (2024 −0.5, 2025 −0.8 %/mo); stocks were negative in 2019 and 2024.

## Day: first hour → 10:15–close (52 sessions)
Stocks IC −0.01; indices IC +0.06 (t 1.1); NIFTY 50's first-hour direction repeated 56% of sessions (+0.07%/day,
t 1.2). No edge shown on this sample.

## October 2026 (anecdote, one month)
Early leaders did keep leading in indices (first-3-session IC +0.40): IT led and then ran another +13.5%. On the stocks
the first-5-day ranking reversed (IC −0.25). One month says nothing about the edge either way.

## Verdict
Not proven for indices or NIFTY direction — in eight years the early-month index leaders did not reliably keep leading.
For stocks there is a small momentum tilt (≈ +0.7–1 %/mo top-vs-bottom before costs), but it is generic momentum,
partly survivorship, and unstable by year. As a filter it might nudge stock-option side selection (prefer CE on
month-to-date leaders, PE on laggards); as a stand-alone trade it is too thin once option spreads and theta are paid.
