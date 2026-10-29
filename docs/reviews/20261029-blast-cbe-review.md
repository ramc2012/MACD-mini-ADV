# Blast, CBE and session performance review

Review prepared at **2026-10-29 18:02:24 IST**. Performance snapshot captured at
**2026-10-29 17:57:31 IST**; CBE fill audit captured at **2026-10-29 17:55:33 IST**.
The accounting window is **2026-10-29 00:00:00 IST** through before
**2026-10-30 00:00:00 IST**.

This review used existing local GET APIs, SQLite opened with `mode=ro`, whitelisted
non-secret settings fields, and isolated temporary-book probes. It did not fetch
broker history, call an LLM, place orders, edit books/settings, restart strategy services,
or change broker connections. Paper trading remains the scope.

**Scope at preparation:** the three Blast engine findings below are unresolved.
The MACD-style Blast frontend redesign is deployed and browser-verified separately.
The three engine issues remain unchanged and are not claimed as fixed.

## Recorded session performance

| Book | Booked closing P&L | Open positions | Open cost-basis unrealized P&L | Equity observation |
| --- | ---: | ---: | ---: | --- |
| Active Blast HMA/KAMA subset | −₹88,131.78 | 52 | +₹4,91,982.93 | ₹54,03,851.14; +₹4,03,851.14 over rebased ₹50 lakh capital |
| MACD | +₹1,23,816.60 | 117 | +₹11,28,972.14 | ₹1,53,27,586.27; session change +₹7,61,826.03 (+5.2303%) |
| Fresh funded CBE | +₹6,855.48 after ₹4,000.95 fees | 0 | ₹0.00 | ₹50,08,251.51; session change +₹6,855.48 (+0.1371%) |

Blast and MACD record zero fill fees in these books. Their prices include the
configured paper spread/slippage effects, but the reported booked figures are
not net of statutory charges. CBE applies its separate modeled fees. Differences
in capital, exposure, holding periods, costs and mark quality prevent these
percentages alone from ranking strategy quality.

### Blast: positive open marks, losing closed trades, funding discontinuity

The active book contains 78 retained HMA/KAMA fills, all within the review window:
65 buys and 13 sells. The first fill was at **2026-10-29 09:46:02 IST**, the last at
**2026-10-29 15:20:43 IST**. Thirteen distinct positions closed, with four winners
and nine losers; closed-position profit factor is 0.4083. Four exits were hard
stops and nine were HMA-below-KAMA exits. Of the 52 open positions, 46 are CE and
six are PE. The journal contains 89 HMA/KAMA judgments: 65 taken and 24 refused.
Every refusal records a stale execution quote, not a model or capital rejection.

Cash is **−₹64,971.01** and marked exposure is ₹54,68,822.15. The negative cash
reconciles to a funding rebase with retained fills:

| Funding evidence | Amount |
| --- | ---: |
| Saved pre-rebase Blast initial capital | ₹50 crore |
| Current Blast initial capital | ₹50 lakh |
| Retained HMA/KAMA buy notional | ₹62,51,234.7847 |
| Retained HMA/KAMA sell proceeds | ₹11,86,263.7758 |
| Net cash outflow | ₹50,64,971.0089 |
| Rebased capital less net outflow | −₹64,971.0089 |

Only safe Blast configuration fields were read inside the engine container. The
saved pre-rebase settings file's modification time is **2026-10-29 16:21:59 IST**;
the current file's is **2026-10-29 16:23:09 IST**. Strategy, maximum-position count
and target ticket agree across those settings. This explains the present deficit;
it does not prove that an original fill bypassed its then-current cash gate.
No cash or position correction was made.

`blast_trader.before-hmakama-reset-20261029.sqlite3` retains the same 78 HMA/KAMA
fills plus two old-position exits within today's window. Those two exits lost
₹6,572.50, giving −₹94,704.28 booked for the prior full book. The active and archived
totals overlap and must not be added. The archive retains the older funded equity
series, while the active book has **zero `equity_points` rows**. Consequently
active equity/new capital of **+8.0770% is a rebased subset observation**, not a
continuous full-session return on ₹50 lakh funding.

Open marks match existing snapshot tick LTPs, stamped from
**2026-10-29 15:29:40 IST** through **2026-10-29 15:39:59 IST**. They are paper marks,
not sale proceeds. All of today's journal excursion windows remain unresolved.

### MACD: day movement includes carried positions

The prior equity anchor is ₹1,45,65,760.2435 at
**2026-10-28 23:57:14 IST**. No capital-change rows intervene. The ₹7,61,826.0250 day
increase reconciles to booked closes of ₹1,23,816.60 plus ₹6,38,009.4250 improvement
in open unrealized P&L. Current unrealized P&L is measured from each open trade's
entry, so adding that entire amount to today's closes would overstate today's P&L.

There were 261 fills: 150 buys and 111 sells. The 111 closing slices cover 85
distinct position IDs, including staged exits; the closing-slice profit factor
is 1.2538. Current open exposure is ₹1,04,12,025.15 and cash is ₹49,15,561.12.
Latest saved equity is **2026-10-29 16:23:21 IST**. One open mark,
`BOSCHLTD26NOV44500CE`, has a pre-open timestamp of **2026-10-29 05:30:00 IST**
(market value ₹87,242.50). Its current-session freshness is unverified; the
reported day MTM includes it. The other 116 open marks carry session stamps.

### CBE: actual fresh paper fills

CBE made ten entries and ten exits, leaving no open positions. All exits were
`TIME_EXIT` at **2026-10-29 15:15:04 IST**. Price P&L after modeled spread/slippage
but before fees was ₹10,856.43; ₹4,000.95 fees left **₹6,855.48 net**. Equity rose
from ₹50,01,396.03 at **2026-10-29 09:15:01 IST** to ₹50,08,251.51. Four trades won
and six lost; average winner ₹3,531.57 versus average loser −₹1,211.80, profit
factor 1.9429. Observed session-equity maximum drawdown was 0.2303%.

KOTAKBANK contributed ₹6,621.83, **96.6% of net**; the other nine combined earned
₹233.65. Fees consumed 36.85% of pre-fee P&L. Cumulative fresh-account net is
₹8,251.51. Two sessions and twenty trades do not establish an edge; today's
positive payoff asymmetry also has substantial single-name concentration.

The twenty fill quotes were same-session and 0.66–8.05 seconds old; checked fill
prices obey adverse slippage/tick rounding and SQLite `quick_check` passed. Ten
fills exceed contemporaneous displayed L1 size under the owner-authorized
session-volume model. Queueing and market impact remain unmodeled.

The auxiliary CBE shadow series reports ten quote-to-quote outcomes with mean
+1.32727% before costs. It has no fills and is **not CBE financial P&L**. Original
retained Nomad history is a third, separate history and is not included here.

## Blast: actual strategy and review findings

The active runtime is `hma_kama`, enabled, with paper auto-trade enabled. It uses
HMA(20) crossing above KAMA(10,2,30) on closed 30-minute option-premium bars, a
fixed target ticket of ₹1 lakh, a 30% hard stop, no trailing stop and no scale-outs
or pyramiding. It exits when HMA is below KAMA on a closed bar, at its fourth
subsequent-session cutoff, or through expiry/stock T−2 protection. Runtime capital
is ₹50 lakh and the maximum-position setting is 1,000. Liquidity caps may reduce
the ticket. Unknown selection liquidity leaves that particular cap unavailable.

Owning source: `engine/src/macd_trader/blast_engine.py:130–141` (identity/constants),
`:305–318` (stop/session cap), `:773–837` (HMA/KAMA decisions), `:1013–1064`
(liquidity/sizing), `:1206–1229` (expiry), `:1279–1288` (runtime strategy description).

`engine/docs/blast_lane.md` and the module's opening docstring primarily describe
the older one-minute MACD signal-line screen. Their historical screen evidence,
premium/breadth/off-high gates and trailing rules are not the current HMA/KAMA
strategy. The HMA/KAMA replay evidence remains retrospective and not proven
prospectively. Its journal stores premium/spot context, but writes `breadth=null`
and no VIX field; the interface must not imply these are active gates or measured
HMA/KAMA context fields.

### 1. [P1] Disabling entries also disables tick risk management

`BlastEngine.on_tick` returns at `blast_engine.py:1180–1181` when `blast_enabled`
is false, before `execution.on_tick` at `:1183`. This stops per-tick marking,
hard-stop checks, resting-order processing and the session-cap check. A temporary
book with disabled entries confirmed that risk execution is never called.

Keep entry admission disabled while continuing held-position quote/risk handling.
The current frontend promise that disabling entry judgments leaves stops active
is not supported by this code. Closed-bar and independent expiry paths are
separate; they do not replace the skipped tick protections.

### 2. [P1] The accepted current quote reaches execution after its signal is evaluated

`Engine.on_tick` accepts the tick into `latest_ticks` at `engine.py:1671` and calls
`blast.on_minute_bar` at `:1683`. Blast `_enter` then reads
`execution._fresh_price` at `blast_engine.py:1032`, but the execution quote is
updated later through `blast.on_tick` at `engine.py:1712`.

The first tick after a quiet period can therefore close a valid signal bar while
the entry reads a stale preceding tick. All 24 journal refusals in this session
name stale prices. The ordering defect explains how that occurs; it does not
establish what the skipped trades would have earned. Refresh only an accepted,
current-session executable quote before evaluating the closed bar, preserving
stale/off-session protections. Do not loosen the freshness threshold.

### 3. [P2] Intraday gaps can admit an old signal bucket as fresh

`blast_engine.py:660` measures lateness from the incoming minute's end. The
HMA/KAMA branch at `:665–666` checks that value and session equality, while
`_fold_minute` closes any previous bucket at `:724–730`. It does not compare the
decision with the completed signal bucket's end.

An isolated probe formed the bucket beginning **2026-10-29 09:15:00 IST**, then
delivered the next minute at **2026-10-29 12:15:00 IST**. The old signal bucket was
passed to `_hma_kama_bar` with `late=False`. Six taken rows this session were
recorded over six minutes after their nominal bucket end (OFSS, PAGEIND, VBL,
NHPC, IEX and COLPAL). The largest such delay was about 9.7 minutes.

Bound entry decisions by the completed signal bucket's end plus the configured
grace, including correct session-end handling. Preserve the existing ability of
late bars to close a holding; do not turn them into new entries. Existing tests
at `engine/tests/test_blast_lane.py:897` cover the overnight case but not this
intraday gap.

## CBE: current strategy

CBE is currently a **long-only cash-stock daily trend/relative-strength selector
with next-session intraday execution**. The inherited Compression-Before-Expansion
name does not accurately describe its present trigger. It does not buy options
or enter on a premium MACD crossover.

1. Read completed daily OHLCV for the 213 active F&O stock underlyings and NIFTY.
   Require at least 60 common observed closes and all latest 50 exchange sessions;
   do not fill missing prices. Require current completed-session market profile
   and at least 80% current daily breadth.
2. Qualify only stock close > EMA20 > EMA50, positive five- and twenty-session
   momentum, and positive twenty-session relative momentum versus NIFTY. Rank
   momentum/relative strength normalized by volatility and freeze the top ten
   eligible cash longs. Today's frozen cohort was published at
   **2026-10-29 15:51:23 IST**, after the completed close. Its entry window is
   **2026-10-30 09:20:00 IST** through before **2026-10-30 09:55:00 IST**.
3. Use the deterministic, untrained `lane-local-context-v1-cbe` review; Qwen is
   not required. Preserve the frozen source session and daily/MP requirements;
   wait if current cash ask is below EMA20. Rank context using daily 35%, profile
   20%, continuation 15%, sector 10%, source rank 10% and positioning 10%.
   Optional stale groups are neutral and disclosed. Priority is not expected return.
4. Use a separate ₹50 lakh paper account, a fixed ₹2 lakh ticket, ten-position
   limit, 100% gross/30% sector caps and a 3% daily-loss entry stop. Require fresh
   same-session two-sided quotes, positive quantities/volume and spread ≤1%.
   Apply a 5% position stop, 10% target and entry-session time exit. Today's time
   exit was **2026-10-29 15:15:04 IST**.
5. Buy at ask plus 5bp and sell at bid less 5bp, with adverse tick rounding and
   10bp fees each side. The owner override sizes against 1% of session volume
   rather than displayed best-level size. Sale cash settles at the next exchange
   open. Missing executable quotes wait; fills are never backdated.

Source references: `shadow_lanes/daily_market.py:111–147`;
`shadow_lanes/vendor/daily_stock_selection.py:112–129`;
`shadow_lanes/source.py:23–32`; `paper_lanes/local_intelligence.py:10–41`;
`paper_lanes/manager.py:139–165`; `paper_lanes/book.py:27–45`, `:171–196`,
`:257–287`.

The original observation-only activation was superseded by separate funded paper
books. CBE's current account reset was owner-authorized at
**2026-10-27 08:09:14 IST**, before its fills. Retained Nomad history and older
epochs stay separate. No recovery claim is expanded by this review.

An additional source mismatch remains unresolved: the local positioning scorer
recognizes `short_cover` at `paper_lanes/local_intelligence.py:69`, while
`vanguard/features/m2_flow.py:113–122` emits `short_covering`. That state receives
neutral rather than bullish ordering weight. This is an ordinal-priority defect,
not an admission-gate bypass. Its effect on today's fills was not established.

## Frontend direction and validation boundary

Align Blast's navigation and account/portfolio/ledger presentation with the MACD
lane. Show current strategy and mode explicitly, distinguish today's booked P&L
from open unrealized P&L, expose cash/exposure and funding caveats, and use a
strategy-specific candidate funnel. In HMA/KAMA mode, premium/breadth/off-high/VIX
controls and legacy research must not be presented as active strategy rules.
Use full IST timestamps and robust ISO journal-day formatting. Candidate MFE/MAE
are excursions, not booked returns; unresolved windows are not a completed sample.

The read-only backend journal endpoint returned 60 rows for an explicit session
date despite the previous UI showing no latest candidates. Frontend date/request
handling requires validation independently from the persisted journal summary.
Runtime counters reset on process restart; persisted journal counts do not.

Only the isolated risk-dispatch and sparse-bar probes were run for this engine
review. No full engine suite or new strategy validation was claimed. Frontend
build, tests and browser verification belong to the accompanying UI change.

## Evidence and reproducibility

Working evidence is under `/tmp/mi-lane-review-20261029/`: `performance.json`,
`performance-review.md`, `audit.py`, `cbe-review-20261029.json`, and
`cbe-strategy-review.md`. The compact reviewed values and limitations above are
preserved here so the review does not depend on temporary files remaining present.
Blast raw GET captures are `/tmp/blast-review-snapshot.json` and
`/tmp/blast-review-journal-all.json`. No credentials or settings file contents are
copied into this review.

Owning API routes are `engine/src/macd_trader/app.py:717` (snapshot), `:723`
(journal), `:742` (book). Financial definitions are
`engine/src/macd_trader/portfolio.py:95–103` (closing slices), `:173–182`
(cash/equity/marks), `engine/src/macd_trader/engine.py:2691–2707` (funding-adjusted
MACD day anchor), and `paper_lanes/book.py:281–287` (CBE net realized fills).

Reproduction reads each relevant book with SQLite URI `mode=ro`, filters fills
and closed rows to the stated accounting window, distinguishes closing slices
from distinct position IDs, and reconciles cash to buy/sell notionals and fees.
The HMA/KAMA active/reset archive comparison matches trade identities rather than
adding overlapping totals. CBE financial fills come from `/api/paper-lanes/cbe`;
generic observations from `/api/shadow-lanes/cbe` are kept separate.

## UI delivery and rollback

Verified at 2026-10-29 18:08:41 IST.

Blast now uses the MACD navigation pattern and the same scoped Portfolio/Orders/Trades ledger.
Status carries readiness, exposure, negative cash and order/fill reconciliation. Performance shows
a net-of-recorded-fees realized curve, open marks separately, and missing-equity/funding caveats.
HMA/KAMA journal lines use actual HMA/KAMA fields; inactive legacy controls are hidden.
ISO session dates restore the latest-candidate list. CBE wording reflects fixed tickets and session-volume caps.

Production frontend only was recreated from locally compiled assets, without downloads.
Engine/desk/shadow container IDs and start times matched before/after; books, settings,
broker sockets and execution code were not changed. Build and 101 frontend tests pass.
Evidence is in `20261029-evidence/`: performance-summary.json, performance-audit.py,
frontend-tests.txt and blast-performance.png.

Current frontend image: `macd-mini-parallel-frontend:blast-ui-20261029`
(manifest sha256:028435ebe2250252388a87ee0d052d3a34adb61dfc9c1561e547b4572465758e).
The service default tag points to that image. Rollback image retained as
`macd-mini-parallel-frontend:before-blast-ui-20261029`
(original image sha256:8aa3bdda7b79807dd0ad8ca01b0a37824afd8c8373f728bf27504d1efbea4a8d).
Rollback is a local tag restore followed by frontend-only compose recreation; no rollback was performed.
