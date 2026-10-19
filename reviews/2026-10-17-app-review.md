# MACD-mini-ADV review — 17 October 2026

Reviewed the maintained macd-mini-parallel deployment, current source, rendered market pages, recovery paths, and the supplied research summary. This was a review: no application changes, restarts, strategy changes, or book repairs were performed.

## Assessment

The market views are deployed and usable. Recovery automation contains material data-preservation risks that should be fixed before relying on unattended repair. The option-picking results remain exploratory and do not yet establish an executable edge.

## Findings, in priority order

### P1 — A failed recovery can replace a database with an empty one

`engine/db_doctor.sh:56–60` pipes `.recover` into another sqlite3 process without checking both processes' success. It accepts the result solely on `quick_check`. A structurally valid empty database passes that check. There is no required-table, ledger-count, or protected-state comparison before replacement.

Reproduced in a disposable container using the deployed image: a deliberately invalid database made `.recover` report `file is not a database`; its destination nevertheless returned `ok` from `quick_check` and had zero schema objects. No production database was used in this reproduction.

Require explicit recovery success and semantic preservation checks before swapping files. Keep uncertain recoveries quarantined and report a blocked state. A backup is helpful but does not make silent replacement safe.

### P1 — The repair lock does not isolate live writers

`engine/db_doctor.sh:23–26,54–60` locks other doctor instances, but application writers do not participate in this lock. If one container restarts while its peer is running, the script can copy a changing database/WAL, delete WAL/SHM, and replace a shared database under open connections. Clean-start skips also allow a process to open stores without waiting for another doctor's repair.

Coordinate maintenance across all processes that share the volume, stop writers before recovery, and reopen their connections only after validation. This is a code-path risk; no concurrent corruption was induced during review.

### P1 — Automatic expiry settlement accepts stale prices as final

`engine/src/macd_trader/self_heal.py:130–147` accepts the latest underlying minute anywhere on expiry day as the expiry close. If unavailable, it accepts the option's last stored close from any earlier date. `settle_expired_books` then makes a durable closing entry from that estimate.

An interrupted expiry session can therefore turn a morning mark, or a much older option premium containing time value, into realized P&L. Require a verified settlement basis and close-time coverage; otherwise retain an unresolved settlement with an explicit blocker. Report estimated marks separately from final settlement.

### P2 — Nightly “complete history” is a weak proxy

`self_heal.py:221–231` checks only NIFTY's bar count, accepts 360 of 375 minutes, and returns true on SQLite errors. `desk.py:312–320` proceeds after 21:30 even if that check fails. Thus the summary's “waits for the full day's history” is too strong: other symbols can still have large gaps and read errors bypass the wait.

Check the inputs actually consumed by nightly analysis, expose missing coverage, and persist partial/failed quality status. Treat read errors as unknown, not complete.

### P2 — Sector-wheel freshness can misstate comparisons after partial refresh

`engine.py:1947–1948` marks the day's refresh done when any rows were stored, even if other symbols failed. Those symbols then receive no retry that session. `sector_wheel.py:97–119` computes rolling changes by each symbol's own available row positions, without aligning dates to the benchmark. Missing days can therefore compare different periods under the same 5D/20D/60D label. `sector_wheel.py:105` also has no closing-time upper bound and can label weekday evening data live.

Retry failed symbols, align return endpoints to the benchmark calendar, and show missing/stale data explicitly. Current snapshot: all 227 rows were dated 16 October, so the stale-row issue was not present in the reviewed response.

### P2 — Real-premium research still uses non-executable timing

`research/option_picks_20261017/study.py:84–91` forms signals from day t's completed close. `study2.py:109–124` buys using day t's option close, subtracts a flat 3%, and permits the last available print before the intended exit date. It neither requires a quote after signal availability nor exact exit-date coverage. Trades lacking an option path are omitted, so coverage by rule also needs reporting.

Replay frozen picks using the next available executable quote after the signal, with exact-contract timestamps, liquidity, two-sided costs, and missing-path accounting. The regime rule and profit-taking/time-stop proposal also need their own frozen evaluation; fixed-horizon results do not validate those exits. Current-universe historical research retains survivorship concerns.

The written findings are more cautious than parts of the supplied summary: one real-premium year is described as indicative. The table also shows some other call rules with positive means at one horizon; “only profitable rule” is too broad. RS60's consistency across the selected comparisons is the narrower claim.

## Verified deployment and UI

- Engine and desk: all 75 Python module files in the actual imported package match local source. The additional database-doctor script also matches.
- A clean frontend build in a temporary directory passed TypeScript/build checks; all 15 generated files matched the served build. The workspace's existing `frontend/dist` is old, but the deployed frontend matches current source.
- Engine environment: paper execution, live orders disabled. Compose applies the same locks to desk.
- Services are running; engine and desk health checks pass. Health reports no lane errors, 667/667 warmed indicators, closed Saturday session, and next trading day 19 October.
- Latest boot: 52.1 seconds warm-up, 65 seconds process-to-ready. The reported 36-second boot and the earlier outage's five-minute recovery were not independently reconstructed.
- Rendered daily RRG and sector wheel contain data. The ATM heat map populates after reload and preserves `#/MACD/MARKET/HEATMAP`.
- Current heat map: 227 rows, as of 16 October close. Historical 1 October request: 226 rows. Daily RRG endpoint: 226 symbols. Sector wheel: 227 rows, all dated 16 October.
- Tailscale-specific frontend binding is present in the running deployment; remote-device access and survival across a host reboot were not tested.
- Health's `boot.ready=true` describes connection/warm-up, not proven live-session readiness. Desk reports 298 lost bus messages and a prior unexpected EOF while currently connected; inspect recurrence during the next session before treating capture as lossless.

## Test validation

The complete engine suite passed: **995 tests in 32.74 seconds**, in a disposable container using a writable temporary copy of source, tests, scripts, and research helpers. No production data volumes or broker credentials were mounted. Earlier harness attempts exposed missing copied helpers and a read-only working directory; the complete final run resolved those harness issues. These tests do not cover the failed-recovery semantic validation demonstrated above.

Frontend TypeScript compilation and production build also passed in an isolated temporary copy.

## Persisted book counts

Read-only counts from the running deployment:

| Book | Trades | Orders | Closed slices | Saved open excursions |
|---|---:|---:|---:|---:|
| MACD | 1,328 | 4,513 | 756 | 32 |
| Blast | 118 | 5,380 | 55 | 8 |
| Smart 50L | 392 | 395 | 224 | 38 |
| Smart 1L | 66 | 66 | 31 | 7 |
| Auction | 216 | 216 | 0 | 0 |

These establish persisted records, not a full reconciliation of valuation or accounting correctness. No destructive integrity test or forced crash was run against production.

## Research context and priorities

The weekly findings show more than the three realized-P&L figures in the summary: Blast's +₹6.14L realized accompanies approximately −₹7.2L equity change including open marks; Smart 50L reports approximately −₹9.2L equity change. Giveback is a useful exit-research diagnostic, not a price known to be executable at the historical maximum. Weekly totals were read from the findings, not independently recomputed from every fill.

Prioritize safe database recovery and settlement, then frozen exit/selection replay. Keep sector/RRG displays as research context and the option-picks proposal in research/shadow status until causal evidence exists. A 120-day ring is a display enhancement and does not resolve the execution-evidence gaps.

For Monday 19 October, verify fresh current-session quotes, per-lane gates, minute coverage, bus loss counters, and persisted exit-state continuity. Saturday's healthy services cannot establish those conditions. The quoted 50-day NIFTY average was not independently recalculated in this review.
