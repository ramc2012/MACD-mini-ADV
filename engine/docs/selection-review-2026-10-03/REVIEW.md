# App and trade-selection review — 3 October 2026

The running app is MACD-mini-ADV (`http://localhost:3200`, gateway 8201),
with strategy and desk services built from `/Users/ramachandran/MACD-mini-ADV`.
The original MACD Mini checkout in this chat is a legacy copy and has unrelated
uncommitted work. It was reviewed without editing or deploying it.

The bounded changes improve causal signal evaluation, eligibility and research
selection integrity. They do not establish profitable selection. Current paper
results and archived validation remain diagnostic; see [research evidence](research-evidence.md).

## Findings and changes

| Finding | Resulting behavior |
|---|---|
| A new candle was previewed before the prior candle committed its indicator state. | Commit the prior close first, preventing duplicate/spurious crosses at a bucket boundary. |
| Ranked entries waited in a queue without rechecking the cross. | Before a new entry, require its originating bar, fresh executable quote, standing MACD cross and enabled KAMA/RSI/ROC confirmations. Reject unavailable or failed validation without placing an order. |
| Closed-bar scoring included the triggering positive MACD in its preceding-dip history. | Scoring uses only indicator points preceding the signal bar, aligning closed-bar and intrabar feature timing. |
| Retained expired contracts could receive new buys. | Known expired contracts cannot receive new BUY orders or resting BUY fills in MACD, Blast or Smart lanes. SELL exits and durable position records remain available. |
| Static entry-model decisions lacked model identity, and documentation promised a nightly refit that does not exist. | New journal decisions carry the exact exported-model SHA256; status describes the static update mode. Older journal rows retain their existing data. |
| Options agent emitted before its advertised feature cutoff. | Schedule emission at 14:45:15 IST, after the 14:45 cutoff. Reject premature manual emission and show the schedule from the API in the UI. |
| Sparse old option/underlying prints were treated as decision prices; incomplete closes could become labels. | Require a completed minute ending at the cutoff or no more than one minute earlier. Require elapsed, sufficiently fresh per-symbol session closes; use the actual next three calendar trading sessions. |
| Retraining replaced historical model weights in place. | Each option/index refit receives a new version. Earlier versions retain their weights and training windows. |
| Backfills or re-emissions could rewrite features/prices behind frozen lists. | Preserve the original candidate universe, decision prices and features; reuse it for re-emissions and bootstrap labelling. |
| Scheduled/manual emission and backfill writes could race with candidate freezing. | Share an agent-job lock and atomically capture the canonical daily universe in SQLite. Concurrent runs score the same frozen prices/features; scheduled deduplication is rechecked under the writer lock. |
| A temporary absence of cutoff bars could permanently freeze an empty list. | An initial empty universe creates no run. The scheduler marks completion only after successful emission and retries unsuccessful/error attempts with a one-minute backoff. |
| Missing/failed index decisions bypassed the index no-trade gate in the generic lists. | Indices remain excluded until an explicit index decision supplies their permitted side; stock lists can still be produced. |
| Missing option quotes used an underlying selection reference as the premium, and unchanged prices could retain cached Greeks indefinitely. | Require each leg's own finite, positive, fresh exchange quote. Invalid inputs clear cached results; calculations expire after five seconds. |
| Feature, pattern and strategy-lab searches used their test results to select winners. | Eligibility, thresholds and ordering use training evidence only. Report held-out losses and missing coverage honestly. Purge known outcome overlap in feature discovery and mark unverified legacy evidence explicitly. |
| Leaving Options Agent after its equity charts loaded crashed the terminal during series cleanup. | Remove series only while their owning chart is still active; route unmount does not call into a disposed chart. |

## What the evidence supports

The current paper books are losing over the short inspected cohort. The agent's
call ranking trails its candidate benchmark, while the put advantage is uncertain.
The static entry/Smart classifiers optimize positive-P&L probability rather than
verified cost-adjusted expectancy, and their reproducible trainers were absent
from the inspected checkouts. Previously inspected test periods cannot be made
untouched by changing the selector now. Previously overwritten model weights
cannot be reconstructed by this patch.

The next ranking experiment should freeze a small set of shadow hypotheses:
spot-direction alignment, emerging premium trend, and avoiding entries after
large premium expansion. Compare them against the current baseline on new
chronological sessions, with matured outcomes, per-side results, expiry coverage,
funded cash/concurrency rules, executable spreads/depth, fees, cost stress and
leave-one-large-winner-out sensitivity. Use net premium expectancy for selection
and allow no trade when evidence is insufficient. No new optimized thresholds
or model weights were enabled during this review.

This protocol follows the multiple-testing concern documented in
[Bailey et al., The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).
Option premium also depends on implied volatility and time decay, alongside
underlying movement; this supports testing volatility/Greek features rather
than assuming direction alone predicts premium returns. See
[OIC, Volatility and the Greeks](https://prd-web.optionseducation.org/advancedconcepts/volatility-the-greeks).
Bid/ask prices, quantities, volume, OI and IV are distinct fields in the
[NSE option chain](https://www.nseindia.com/option-chain); turnover alone does
not establish an executable spread or fill.

## Runtime and preservation

Observed on Saturday, 3 October: market closed, Fyers token expired, feed not
alive, and no newly warmed indicators. Next calendar trading day reported by
the app is Monday, 5 October. Live-session signal quality remains unverified
until authentication and advancing market data are restored.

Before deployment: MACD had 61 open positions, Blast 18, MP zero. The paper
order lock was intact. All three execution books passed SQLite `quick_check`.
An expired Sensex contract remains in the Blast book; it was preserved without
inventing a settlement, changing its cost basis or deleting history. Reconciliation
of that record requires verified expiry/settlement evidence.

Baseline MACD durable counts: 3,702 orders, 517 fills, 813 signals. Options-agent
archive: 17 runs, 340 picks, two option-model versions and one index-model version.
No research report regeneration, model retraining, backfill or P&L reset was run.
Consistent online backups of all five paper execution books (including the two
empty Smart books) were created and
integrity-checked before deployment in `runtime/backups/selection-review-20261003/paper-books/`.

## Verification

Backend: **911 tests passed** in an isolated Docker runtime with the reviewed
source, tests, research and scripts mounted read-only. The production image
retains exactly the same installed dependency versions as the tested runtime.
All Python modules parse, and changed source has no trailing whitespace.

The engine, desk and frontend were rebuilt and recreated. Both engine services
are healthy, and all **68** import-visible source modules in each service match
the ADV checkout. The frontend's **16** served asset files match its running
image. Only these three services were recreated; other services and volumes
were retained.

After restart, all five execution books passed SQLite `quick_check`. Hashes of
orders, fills, signals, closed positions, all six agent-archive tables, open
position identity/quantity/cost basis, cash, realized P&L, settings and execution
risk configuration match the pre-deployment snapshot. The 61 MACD and 18 Blast
holdings remain; MP and both Smart books have zero positions. The agent retains
17 runs, 340 picks, 51,252 candidates, 68 index decisions, two option-model
versions and one index-model version. Paper mode and the live-order lock remain
intact. Machine-readable evidence is in [preservation.json](preservation.json).

Source snapshots from the running image before changes are retained in
`deployed-before/`; the running database is the Docker `engine_data` volume,
not the empty `engine/runtime/historical.sqlite3` file. The original checkout's
unrelated uncommitted work remains unchanged.

Frontend: **81 tests passed**, and the final TypeScript/Vite production build
passed. Rendered MACD, Smart, Blast, Auction Desk and Options Agent views were
inspected. The populated Options Agent page switched to Smart lanes and reopened
successfully after the chart repair, with no new browser errors. The UI and API
both report emission at **14:45:15 IST**, and the existing run 17 still displays
its original calls, puts and index plans.

Live-feed and next-session signal quality remain unverified while authentication
is expired and the session is closed. No new model weights, ranking thresholds,
funding/sizing configuration, backfills or historical result rewrites were
activated by this review.
