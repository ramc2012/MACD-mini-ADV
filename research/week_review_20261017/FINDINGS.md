# Paper trading, week of 12-16 Oct 2026, against the ideal

Script: `review.py`; full tables in `report.txt`.

Caveats:
- 15 Oct lost 12:53–15:30 to a power cut.
- From 15 Oct, fills cross the bid-ask spread, so later trades carry more cost.
- Five sessions is a short sample.

## Lanes (closed positions; partial exits summed per position)

| Lane | Positions | Realised P&L | Win % | Avg / median return | Payoff | Best reached while held | Equity change incl. open |
|---|---|---|---|---|---|---|---|
| MACD | 245 | −₹2.86 L | 44 | +1.3% / −4.0% | 1.4 | +31.1% | about +₹3.8 L |
| Smart ₹50 L | 121 | −₹8.34 L | 38 | −4.3% / −6.5% | 1.2 | +29.9% | −₹9.2 L |
| Smart ₹1 L | 28 | −₹0.14 L | 36 | −6.3% | 1.0 | +28.8% | −₹0.11 L |
| Blast | 16 | +₹6.14 L | 50 | +33.0% / −0.7% | 3.1 | +127.9% | −₹7.2 L (open positions marked down) |
| Auction desk | 36 | −₹0.76 L | 39 | −2.3% | 0.8 | +9.6% | −₹0.56 L |
| Desk index shadow (2 days) | 15 | −₹0.50 L | 33 | −3.7% | 0.7 | +11.2% | −₹0.50 L |

The MACD equity change excludes the ₹50 L capital raise on 13 Oct; raw equity rose ₹53.8 L.

**Giving back.** Positions that were up at least +30% at some point while held:

| Lane | Positions | Average best | Average realised | Given back | Closed negative |
|---|---|---|---|---|---|
| MACD | 109 | +61.7% | +26.5% | 35 pp | 2 |
| Smart ₹50 L | 56 | +56.1% | +20.4% | 36 pp | **11** |
| Smart ₹1 L | 12 | +61.4% | +15.7% | 46 pp | 2 |
| Blast | 11 | +182.8% | +69.7% | 113 pp | 3 |

MACD's breakeven-floored trail kept nearly every big winner positive. The smart lanes' trail (30% from +40%) let 1 in 5
of them close at a loss.

**Side.** MACD: calls +4.1% (166 positions), puts −4.5% (79). Blast: 4 puts averaged +193%; 12 calls averaged −20%.

## The ideal: ATM option bought at the 09:15 open (120 underlyings, 263 underlying-days)

| | Mean per trade |
|---|---|
| Random side, held to 15:25 | **0.0%** (daily medians −0.3% to −7%: decay) |
| Perfect side, held to 15:25 | **+27.6%** (daily medians +9.5% to +29%) |
| Perfect side, sold at the day's high | **+47.7%** (daily medians +28% to +39%; best 10 a day +95% to +165%) |

Calls were the right side for 92% of underlyings on 13 Oct, and for 32–63% on the other days.

## Reading

- **Direction:** MACD (+1.3% per position) is slightly above side-blind buying (0%), far below perfect side (+27.6%).
  The smart lanes and the desk are below side-blind.
- **Exits:** the largest gap. Positions typically reach +30% while held and bank +1% on average (MACD captures about 4%
  of its best). Winners give back about 35 pp.
- **Not proven, but worth testing next:**
  - **Faster profit-taking on big winners.** The 2 Oct study found tight exits lose, so this needs a proper test, not
    a switch.
  - **A breakeven floor in the smart lanes,** which MACD already has.
