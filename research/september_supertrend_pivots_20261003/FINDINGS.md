# September MACD: Supertrend and pivot results

3 October 2026. Applied to the actual old-app entry schedule, with a separate later-book check. No engine settings, paper ledgers, positions or orders were changed. This supersedes the earlier unmatched simplified replay for this question.

## Result

**The old book's September profits are confirmed. Supertrend/pivot additions do not show a consistent profit improvement across the tested periods.** Premium-only Supertrend is the useful risk observation: it improves the old active-window marked P&L slightly and reduces daily marked drawdown, but makes the later September book worse. The pivot-headroom filter selected on training data loses against baseline in both later checks. No fixed overlay passes the corrected entry-day holdout tests.

## Recorded book and baseline parity

The old stopped app retains **3,070 fills**, beginning 25 August and ending 18 September. Its realised P&L is Rs 1.46 L at August 31 and Rs 28.71 L at its last consistent endpoint: **September realised increase +Rs 27.25 L**. Its last retained marked equity, September 28, is Rs 68.15 L; that is not a September 30 valuation. The exact recorded-schedule baseline reproduces all September fill IDs, sizes, prices and realised money, including its legacy overdrafts. Beginning-of-September carry-in inventory is preserved: 75 positions and Rs 20,451.96 cash, with source-based research opening equity Rs 55.58 L.

The recorded book's minimum cash is **-Rs 9.61 L**. There are 248 recorded BUY fills across the retained history that exceed contemporaneous cash. Stored snapshots confirm the negative cash. The strict September funded baseline rejects 31 unaffordable additions while retaining all 621 actual new September episodes. The realised September increase becomes Rs 26.13 L. This cash repair is kept separate from the exact recorded reproduction; it is not silently presented as the app's original result.

Of 71,711 old equity samples, 66,739 reconcile to retained fills and recognised capital; 4,972 do not, including transient September 9 and September 13 states. Clean endpoint realised/cash accounting does reconcile. Daily curves below use independently reconstructed fills and causal archive marks, rather than treating every stored old curve point as trustworthy. No duplicate order IDs were found in the retained old fills.

## Entry overlays

All numbers in the table are **funded marked P&L in Rs lakh**, not average trade returns or promises. Each period preserves its own real carry-in inventory. Drawdown is measured from daily marks, not intraday peaks. The old active period has 12 actual entry sessions; its holdout has four. The separate later book has two sessions and fresh Rs 50 L funding.

| Rule | Old active Sep 1–18 | Old holdout Sep 15–18 | Later book Sep 29–30 | Old active daily drawdown |
|---|---:|---:|---:|---:|
| MACD baseline | +26.85 | +9.50 | -1.97 | 10.97 |
| Premium Supertrend | +27.65 | +11.92 | -3.05 | 4.75 |
| Underlying Supertrend | +23.25 | +9.02 | -2.16 | 8.90 |
| Underlying pivot headroom | +23.67 | +7.29 | -2.08 | 8.51 |
| Both ST + pivot headroom | +21.72 | +8.35 | -2.64 | 4.22 |

Premium Supertrend uses Wilder ATR(10) with multiplier 3 on completed 30-minute premium bars; both CE and PE premiums must be bullish. Underlying Supertrend requires bullish spot for CE and bearish spot for PE. Prior-day Traditional pivots use the prior trading session's H/L/C. Headroom is at least 0.5 completed-30-minute underlying ATR to the next P/R1/R2/S1/S2 in the trade direction. All eight fixed entry rules and three exit overlays are in `results.csv`.

Unknown readings preserve the recorded trade, rather than removing it from the baseline. Premium Supertrend is available on 601 of 621 old September new episodes. Underlying pivot/headroom has 19 unknown readings; premium daily P has 173, so the premium-pivot result has materially lower coverage. No current-day high/low or forming-bar Supertrend reading is used.

The old active baseline's calls contribute -Rs 5.59 L and puts +Rs 32.44 L. Premium Supertrend reduces call losses to -Rs 1.26 L while reducing put gains to +Rs 28.91 L. In the later book it again reduces call losses, but removes enough put profit to make total P&L worse by Rs 1.08 L. This is a risk/exposure tradeoff, not a stable directional advantage.

## Entire September data and the stopped-app distinction

Minute data cover the full September calendar using the old archive plus the current archive's extension. The old app contains no new fills after September 18. The full-month scenario holds its remaining recorded inventory, except modelled overlay exits, to September 30; 81 still-recorded expired holdings are valued using observed expiry metadata and intrinsic value from the expiry-day underlying close proxy. This is a **paper valuation scenario**, not an actual expiry fill, official settlement, physical-delivery implementation, or a replay of an app trading continuously through the missing sessions.

| September 30 terminal scenario | Funded P&L, Rs L | Change against baseline, Rs L |
|---|---:|---:|
| MACD baseline | +10.84 | +0.00 |
| Premium Supertrend | +9.06 | -1.78 |
| Underlying Supertrend | -0.96 | -11.80 |
| Underlying pivot headroom | +11.73 | +0.89 |
| Both ST + pivot headroom | +7.01 | -3.83 |
| Underlying ST exit | +21.33 | +10.49 |
| Premium pivot-loss exit | +19.73 | +8.89 |

This distinction changes interpretation. Underlying-ST exits appear +Rs 10.49 L better over the full-month scenario, yet lose Rs 4.79 L against baseline during active trading and Rs 0.15 L in the later book. Pivot-loss exits similarly appear +Rs 8.89 L better at month-end but lose Rs 4.32 L in the old active period and Rs 1.44 L later. Their post-stop benefit must not be sold as indicator edge against a continuously operating MACD strategy. Premium ST exits lose Rs 12.44 L during old active trading; early exits cut the large winning paths.

Unexpired positions with no same-day quote carry the last known price with an explicit stale-age flag. The old opening mark has one four-day-old quote; the active end has two one-day-old quotes. No missing price is counted as zero. The September 30 old-book figure uses the expiry-intrinsic scenario, while the separate current book's 60 open positions all have same-day option marks. No accounts or capital are concatenated.

## Holdout, costs and data-source checks

Selection uses only funded marked P&L from September 1–11, with baseline and no-new-entry controls included. The selected rule is **pivot headroom**, +Rs 1.35 L against training baseline. It then underperforms by **Rs 2.21 L on September 15–18** and **Rs 0.11 L on September 29–30** at recorded fill costs. These are four and two entry-day clusters, not hundreds of independent trades. Holm correction covers the 24 fixed comparisons across those two holdouts; none clears the entry-day test. Daily marked-book tests are retained as secondary diagnostics, with carry-in attribution separate.

`entry_cost_sensitivity.csv` adds 20/50 bp per side to actual already-slipped fills and compares each filter with the baseline at the **same cost**. Premium ST retains its old active/holdout advantage at those costs but continues to hurt the later book. Pivot headroom still fails the old holdout at both costs; some later differences flip as costs change funding. No thresholds were retuned.

The old/current extracts contain 4,706,544 overlapping minute rows; 918,052 (19.5%) disagree on OHLC. Primary calculations prefer the old archive before migration. A separate validation prefers the current archive throughout. Premium ST's old active improvement ranges from +Rs 0.80 L to +Rs 1.32 L; it remains negative in the later book and the full-month scenario. The selected headroom rule fails the old holdout under both source choices. Detailed results are in `source_sensitivity_current/`.

The old active baseline's top 32 September episodes (5%) contribute **Rs 31.31 L**, versus Rs 19.18 L net across all 621 new episodes. Premium ST excludes 9 of those 32 winners, worth Rs 8.18 L under baseline paths; the joint ST/headroom rule excludes 16, worth Rs 13.62 L. These are marked path attributions, not executable foreknowledge or MFE profits.

## Application decision

Supertrend and pivots have been applied to the historical study. Keep the running MACD trading rules unchanged. Premium-only ST merits further shadow evaluation for drawdown control; it has not earned a mandatory entry gate. Do not add underlying alignment, the combined hard filters, or these indicator exits on this evidence. Full historical signal/universe/ranking decisions and opportunities never funded by the original apps are not replayed here; this is a matched actual-entry-schedule experiment. A new independent regime and prospective filled-decision record are needed to establish a deployable improvement.

## Reproduction and evidence

`prepare.py` extracts only read-only source records; hashes and query details are in `source_receipt.json`. Old/current requested raw rows total 4,708,300 / 8,612,210 across 702 traded option contracts and 210 underlyings. Three invalid OHLC rows were rejected. Nine traded contracts lack usable expiry metadata; none of the September 30 old holdings uses an unknown expiry.

`study.py` calculates features and replays the exact recorded schedule plus strict cash books. `assess.py` validates the closing-price cache, performs same-cost checks and records training selection/tail attribution. `test_study.py` passes seven checks covering feature causality, prior pivots, cash/fees, funding, expiry valuation and exact recorded fills. `study.ipynb` is an executed companion. Original old-ledger SHA256 remains `c90d63c2c4d8a8479298dba7ba6dec58f4ad1ad30b34d33fe4e804a07918e58b`. Local research writes are isolated from both apps.

Use the bundled Python with `PYTHONPATH=../supertrend_pivots_20261003/.deps` from this folder: `python prepare.py`, `python study.py`, `python study.py --prefer-current`, `python assess.py`, `python test_study.py`, `python deliver.py`. Preparation requires the stopped old app files and the currently running engine's read-only candle API/database access. Saved analysis can be inspected without starting either broker connection.

Definitions: [Supertrend](https://www.tradingview.com/support/solutions/43000634738-supertrend/) and [Traditional pivots](https://www.tradingview.com/support/solutions/43000521824-pivot-points-standard/). These define indicators rather than establish profitability.
