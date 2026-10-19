# Baseline correction and paper-book reconciliation

3 October 2026. This supersedes interpretations of the simplified Supertrend/pivot replay as evidence about the actual deployed MACD strategies. The numerical replay outputs remain available for inspection; the earlier preference for the joint filter is withdrawn until baseline parity is established.

## What the supplied report says

The user-supplied report covers 3,298 signals on 25 August–1 October, across 27 sessions. It reports +₹53.2 L under current rules with unlimited cash; +₹63.5 L in a ranked, dispersion-sized ₹50 L book over 19 out-of-sample sessions; and −₹10.9 L in its first-come book. Its delayed-entry/wider-exit combination reports +₹86.1 L in the ranked book. These are benchmarks quoted from the attachment, not independently reproduced results, not September-only profit, and not fills booked in the newly launched smart lanes.

## Why the independent replay is not comparable

| Dimension | Supplied report | Independent simplified replay |
|---|---|---|
| Event dates | 25 August–1 October | 20 August–25 September |
| Event count | 3,298 | 3,298; matching count is not matching events |
| Cash-book test window | 19 out-of-sample sessions | 10–25 September, 11 entry days, plus exit runoff |
| Signal timing | Intrabar crosses, with delayed/confirmed variants | Completed 30-minute zero crosses |
| Execution | Minute-by-minute | Conservative five-minute OHLC, stops before favourable targets |
| Position management | Pyramiding and staged exits | Four starting lots, staged exits, no pyramiding |
| Current-rule holding limit | Four-session cap is a tested addition | Four-session cap imposed on both baseline lanes |
| Allocation | Ranking, skipping and dispersion sizing | First-come, fixed tickets up to ₹1 L |
| Costs | Report assumes 5 bp slippage | 5 bp slippage plus 10/20 bp buy/sell scenario fees and ₹20 per fill |
| Missing paths | Event-level records unavailable here | 2,188 eligible signals excluded unless all compared exit paths resolve |

The independent “smart” lane also uses the wider-stop/delayed-entry research configuration; it is not the report's current-rule baseline. Historical ATM selection and point-in-time lot sizes were not reconstructed in the simplified study. These differences prevent direct comparison of total P&L and prevent transferring even relative overlay improvements to the actual strategy. No decomposition has yet measured which differences account for how much of the discrepancy.

## Actual app MACD paper book

Read at 15:48 IST on 3 October through authenticated local APIs and SQLite connections using `mode=ro` and `PRAGMA query_only=ON`. The rendered application at port 3200 displays the same MACD values. No settings, positions, orders or source databases were changed.

| Latest carried-mark measure | Value |
|---|---:|
| Starting capital | ₹50,00,000.00 |
| Equity | ₹51,86,297.21 |
| Net marked P&L | **+₹1,86,297.21 (+3.726%)** |
| Realised P&L | −₹1,54,735.23 |
| Unrealised P&L | +₹3,41,032.44 |
| Cash | ₹3,32,409.96 |
| Open positions | 61 |
| Durable fills replayed | 517 |

The equity identity reconciles: starting capital + realised + unrealised = equity. A separate weighted-average inventory replay of every durable fill matches the API's cash and realised P&L exactly, and matches every open quantity and average price. All recorded MACD fill fees are zero; the research's extra fees were assumptions rather than the live ledger's charged fees. The latest stored MACD equity point is 05:38 IST on 2 October; these are carried marks during market closure, not fresh executable prices.

| Last retained equity point by IST date | Equity | P&L from ₹50 L |
|---|---:|---:|
| 29 September | ₹50,25,439.38 | +₹25,439.38 |
| 30 September | ₹47,95,848.25 | −₹2,04,151.75 |
| 1 October | ₹51,86,297.21 | +₹1,86,297.21 |

These are the last recorded marks on each date, not independently refreshed exchange-close valuations. The active MACD fill history begins at 09:15 IST on 29 September. There is no full-month September history in this active ledger, so it cannot independently establish the September monthly account return. It does establish the positive latest aggregate marked profit.

The ₹1 L and ₹50 L smart books have no recorded fills. Their equity histories begin on 2 October. Positive historical replay figures must not be attributed to those live paper books. The separate Blast lane is outside this MACD reconciliation conclusion; its current API marks differ from its last persisted trade-time marks.

## Consequence for the Supertrend/pivot study

The correct present answer is **unknown incremental benefit on the actual MACD strategy**, rather than “the actual MACD strategy loses money” or “Supertrend/pivots cannot improve it.” The positive MACD book is independently verified on the latest marks. The positive supplied historical benchmark is recorded but not independently reproduced. The simplified negative replay answers a different question.

Before choosing an overlay:

1. Freeze the actual signal timing, historical contract eligibility, lot sizes, pyramiding/staged exits, ranker, skip rule, dispersion sizing and cash allocation.
2. Reproduce actual retained fills and accounting, including open marks; account for minute-bar uncertainty against real tick fills. The fill-accounting part now passes; historical signal/decision/execution parity does not yet pass.
3. Reproduce the supplied benchmark only if its event/fill ledger and walk-forward allocation records are available. The attachment's aggregate table and static model export are insufficient for exact reproduction.
4. Change only the Supertrend or pivot rule, keeping the same baseline, opportunities, costs and chronological cash handling. Evaluate total book P&L, drawdown and the preservation of large winners, not just conditional mean returns.

## Evidence and reproduction

- `paper_book_source.json.gz`: raw read-only ledger/API capture; no credentials are exported.
- `paper_book_reconciliation.json`: source-payload hash, timestamps, book coverage, daily endpoints and accounting residuals.
- `reconcile_books.py`: rerun with Python to capture and reconcile current paper books. It reads source databases and writes only local research outputs.
- `FINDINGS.md`, `study.ipynb` and `notebook_preview.html`: amended to suspend application-strategy conclusions; calculated diagnostic tables are retained.
- User attachment: `Pasted text.txt`, titled “This builds on the 3,298-signal replay…”; benchmark values above are attributed to that report.
