# Vanguard and CBE port

**Current upgrade at 2026-10-25 23:50:30 IST:** both lanes are deployed with fresh funded paper books, native
financial ledgers, Qwen entry gates and independent exits. Read [PAPER_LANES.md](PAPER_LANES.md) for current
policy, data blockers and evidence. The incident and cutover notes below are historical records; Docker services
were subsequently recovered. Retained Nomad histories remain separate and read-only.

**Current incident at 2026-10-25 12:37:37 IST:** Docker's containers, images and volumes disappeared after the
verified activation below. Both contexts point to the same empty daemon; all services are now down. Owner
clarification about Docker reset/maintenance is pending. No restore or empty replacement database has been started.
All seven local MACD book backups pass integrity checks; the protected Nomad dump, rollback image archive and host
market archive survive. Full market-database coverage is unverified. Read the incident at the top of the platform's
`docs/LANE_PORT_20261025.md`. Deployment success and screenshots below describe the earlier verified state.

Prepared at 2026-10-25 11:26:49 IST. Owner selected **fresh shadow lanes; retain old history**.

## What is implemented

- Native `#/VANGUARD` and `#/CBE` pages with Overview, Selections, Journal, Outcomes and Nomad history tabs.
- A separate `shadow-lanes` Python process. Its GET API is proxied through the existing authenticated MACD engine.
  It has no broker order interface, order/fill simulation, funded account, capital transfer or portfolio writer.
- A fresh SQLite observation journal at `runtime/shadow_lanes/observations.sqlite3`. One immutable cohort per
  lane and source session. Historical diagnostics cannot publish. Every selected row is non-actionable.
- Vanguard reuses the copied M2/M3/M4/M5 evaluation and existing frozen direction/option-payoff artifacts.
  It ranks exact stock options for 1–3 sessions, top ten per CE/PE side, with the inherited 2,000-contract floor.
  Completed current-session spot bars and fresh combined features must each cover at least 80% of the active
  stock catalog. It freezes only inside the inherited pre-close publication window.
- CBE reuses the current Nomad `daily-stock-direction-v1-20261017` completed-session policy: absolute and relative
  5/20-session momentum plus EMA20/50 agreement. It retains long-only top-ten observations and the fixed twelve
  aligned cash bars through the 15:15 observation. This is the daily policy already used by Nomad's CBE scanner.
- Outcomes are additive exact-instrument **quote changes before costs**, not fills, realised P&L, model-target
  validation or evidence of edge. Vanguard D1/D2/D3 observations use the next trading sessions' completed
  14:15–14:45 bars; its BTST observation uses the next completed 09:15–09:45 bar. Baselines are the frozen chain
  snapshot marks, which can precede the decision bar. These observations differ from the inherited model's
  planned 15:15 entry/exit labels. CBE observes the next completed fixed daily bar. Missing marks stay missing.
- The eight allowed feature subprocesses derive market features only. Schema management is disabled for the
  market-only role. Existing public participant data is mirrored from the platform database; no download occurs
  in the feature process. Redis is optional and disabled here.
- The platform's opt-in `lane-feed` profile runs `vanguard/ingest/hub_feed.py`: stock chains and contiguous completed
  three-minute cash bars through hub budgeted market-data endpoints; contract reference metadata from the hub's
  already-loaded master. It retains the legacy market cache and also publishes contract-v1 chain snapshots to
  platform NATS for the existing recorder. No source table or frozen book is deleted.

## Preserved history and boundaries

Original Vanguard tables and CBE `paper_positions.json` remain in their original locations. CBE history is mounted
read-only and never seeds a new position. Vanguard history is SELECT-only and shown separately. No Nomad source
file was edited for this port. Its original copied source was left available; only the runtime modules listed in
`vanguard/runtime-manifest.txt` are committed here. Old copied research outputs and unrelated studies remain local.
Copied credential-bearing database fallback strings were removed; runtime connection values come only from the
existing platform container environment. No credentials are printed or written into these sources.

`market-data-grants.sql` provisions only reference/raw/derived market-data writes and the ingest volume baseline.
It does not grant writes to tickets, orders, fills, positions, accounts, paper outcomes or frozen watchlists.
`nomadcurie_db` must remain running because it also hosts `market_intelligence`. The new lanes still depend on
that retained market/model database; this is an application-process retirement, not a database migration.

## Evidence

- Read-only retained-session diagnostic at 2026-10-25 11:15:22 IST: 213 expected stocks, 212 completed spot histories;
  Vanguard combined fresh-feature coverage 80 and publication blocked. CBE can rank the retained session; no fresh
  publication was created. The original CBE archive exposes 1 open and 206 closed records separately.
- Initial port/inherited logic suite: 141 passed, 3 skipped (optional PostgreSQL clock-expression checks).
  Subsequent feed-validation/reference-rollover tests bring the port suite to 28 tests; inherited suite remains
  116 passed, 3 skipped. Authenticated GET proxy: 3 passed. Frontend: TypeScript/Vite build and 88 tests pass.
  Platform Plumbing: 24 passed. Offline engine/frontend images built; no package/image download was used.
- A pre-existing macOS case collision between `LedgerCounts.tsx` and `ledgerCounts.ts` blocked the host build.
  The pure helper is now `ledgerCountState.ts`; behavior and the existing tests are unchanged.
- SQL grants were validated in a transaction and **rolled back**. Existing `mi` role has no write privilege on
  orders, fills, positions, tickets, Vanguard paper books or frozen watchlist tables.
- Native browser preview: `http://127.0.0.1:3201/#/VANGUARD` and `#/CBE`. Scheduler disabled. Screenshots and the
  diagnostic-only SQLite file are in the platform's `backups/lane-port-20261025/preview/`.

## Cutover status and procedure

**Activated and closed-session verified at 2026-10-25 12:30:46 IST.** Owner approved the cutover. MACD engine/desk,
frontend, new shadow worker and platform lane feed are deployed; market-only grants are applied. Both schedulers
are enabled, execution is disabled, and prospective journals have zero cohorts/outcomes. Retained Vanguard runs
and CBE history are accessible from the native pages. The new feed reports a closed session and made no Sunday
broker requests. Live-session coverage, recorder persistence/parity and market replay remain unverified.
The inherited rankers remain unproven research models.

Nomad's app workers and Redis are stopped; its database remains healthy. Its application recovery registration
initially restarted the stopped workers, so the registration now retains only the database and disables old app
outage-data recovery. The original registration is backed up under the platform's
`backups/nomad-20261025-retirement/`. Its macOS LaunchAgent was not modified. All 34 protected Nomad book/history
tables match the pre-cutover baseline; only `app_runtime_state` changed. CBE history bytes match. Seven fields in
Nomad's credential file were re-encrypted by the incidental restart; comparison inside an isolated container with
the existing app environment confirmed unchanged values. Commodity control heartbeat/commentary fields changed.

All seven MACD book databases pass integrity checks. Positions and cash match across all six existing portfolios;
settings and credentials are byte-identical. Sixty protected table comparisons match the quiesced backups after
excluding `saved_at`/`updated_at`. Pre-stop checkpoint payloads were flushed during shutdown. The fresh journal
retains its four readiness events and zero publications/outcomes across a cold restart.

An independent Docker cleanup removed the original spare images. Rollback images were rebuilt locally from
`f3def38` source with the already-installed dependencies; all 87 rollback source/installed modules match that
commit. The frontend rollback uses the same harmless helper rename needed on macOS. These are equivalent-source
rollback images, not the original image identities. Both are also saved in
`backups/lane-port-20261025/activation/macd-rollback-images.tar` on the platform. Their identities and preservation
evidence are recorded in `docs/LANE_PORT_20261025.md` there. No images or packages were downloaded.
Vanguard may remain blocked until sufficient prior-session feature coverage is available; never relax its gates
or download/backfill missing data without the owner's authorization.

The following cutover procedure was completed after the owner's approval:

1. Record existing container/image identities and resource use, file hashes of retained CBE books and settings,
   and protected execution-table counts/hashes. Stop the Nomad app writers, then take a stable preservation snapshot.
   Keep `nomadcurie_db`, platform services, MACD services and the unrelated TBT recorder running. Do not remove volumes.
2. Run `market-data-grants.sql` against `nomadcurie` using its existing administrator inside the DB container.
   Verify effective market-only privileges and that protected book writes remain denied.
3. Tag the current MACD images for rollback. Deploy the prepared engine/frontend images, enable
   `SHADOW_WORKER_ENABLED=true` in the existing local .env by a script without printing its contents, and start
   the `shadow-lanes` and platform `lane-feed` profiles with `--no-build --pull never`.
4. Reload platform console for the native lane links and lane-specific Plumbing probes. Verify proxy auth, both
   native pages, retained histories, empty prospective journals on this closed session, paper locks, mount/source
   parity, ledger integrity and protected-state equality. Save evidence before claiming deployment.
5. Stop the Nomad backend, strategies, research-sync, Vanguard cycle and Redis only when the replacement paths are
   ready. Frontend and edge-research were previously stopped. Retain all container definitions, volumes and history.
   Confirm there is exactly one raw-market-cache writer after cutover. Report observed resource use separately.
6. On the next open session verify expanded stock-chain coverage, no budget/429 failures, exact bar grids and
   source times, platform `md_chain` persistence, feature-stage success and publication gates. No closed-session
   status or populated old data proves this check.

Rollback: stop the two new profile services, restore saved MACD image tags and prior .env worker flag, recreate
the existing MACD services without build/pull, restart the retained Nomad app services. Keep all databases/journals.
Restore the backed-up application self-heal registration if Nomad app recovery should be resumed.
New market-cache rows are retained; no historical data is deleted to roll back. Recheck paper locks and provenance.

## Rebuild without downloads

From the frontend directory: `npm ci --offline --ignore-scripts --no-audit --no-fund && npm run build`.
From the MACD repository:

```sh
docker build --pull=false --network=none -f frontend/Dockerfile.offline -t macd-mini-parallel-frontend:shadow-lanes-20261025 frontend
docker build --pull=false --network=none -f docs/lane-port/Engine.offline.Dockerfile -t macd-mini-parallel-engine:shadow-lanes-20261025 engine
```

The offline frontend Dockerfile copies already-built assets. Both offline Dockerfiles reuse the retained
`before-shadow-lanes-20261025` rollback images. If those tags are missing, load the local rollback image archive;
do not fetch a substitute. Default Dockerfiles remain available for a later
owner-approved dependency build. Never substitute a network build silently when the offline cache is incomplete.
