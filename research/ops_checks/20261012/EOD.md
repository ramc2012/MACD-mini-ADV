# EOD review: Mon 12 Oct 2026

**Market:** NIFTY opened higher, then sold off.
- **NIFTY:** 22,609 open, 22,651 high, 22,304 low, 22,363 close (−0.70%).
- **BANKNIFTY:** −0.61%. **SENSEX:** −0.76%.

**Infrastructure:** no reboot (boot still Sun 16:53).
- **Feed:** up all day, 1.1–1.4M ticks/hour, 0 recoveries. No lane errors.
- **Overnight jobs:** the overnight job ran (217 symbols). The agent retrained: stock model v14 and index model v11, both kept.

| Book | Day result | Detail |
|---|---|---|
| MACD (Rs 1 Cr) | −4.43 L (equity 103.7 L → 99.3 L) | 121 fills; 73 closes realised −1.38 L. CE −1.69 L (56), PE +0.31 L (17). Invalidation exits −2.64 L (26), hard stops −2.12 L (7), profit/trail exits +3.38 L. Open: 29 CE −0.22 L, 20 PE +1.94 L. Cash 52.8 L. |
| Smart 50 L | −8.72 L (48.4 L → 39.7 L, −18%) | 54 fills; 33 closes −6.74 L (ANGELONE PE −1.83 L hard stop; invalidation exits 11). 34 open; cash Rs 1.4k, so fully invested. Since inception −20.6%. |
| Smart 1 L | about −0.9k | 3 open. |
| Blast | about −0.66 L | BOSCHLTD PE +1.83 L (trail), DABUR CE −0.27 L (hard stop); 4 open. |
| Auction (MP) | +9.2k | 12 trades, 7 wins. First entry 10:15 (new rule); 6 time exits. CE −46.6k, PE +55.9k. |
| Option agent | run 19 at 14:47, 814 candidates | 14:47 → close: CE picks −1.2%, PE picks −4.6%. Index puts −2.4% to −8.4%. |

**Flags**
- **Smart 50 L** is fully invested after an −18% day, so it can't enter tomorrow until exits free cash.
- **Invalidation exits** were the largest single loss bucket in MACD (−2.64 L) and Smart 50 L.
- **The MACD day-P&L baseline** (`day_baseline_equity`) predates the Rs 50 L top-up, so today's day-P&L reading is off by +50 L. It resets tomorrow.
