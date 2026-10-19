# Self-healing after outages (15 Oct 2026)

Built after the host crashes of 5, 8 and 15 Oct 2026. Each one needed the same manual repair; each step now runs
on its own when evidence is sufficient. Damaged SQLite files now block startup for writer-isolated repair.

| Failure | What heals it | Where |
|---|---|---|
| Container exits | `restart: unless-stopped` on every service | `compose.yaml` |
| Event loop wedged (process alive, answering nothing) | `LoopWatchdog`: a thread exits the process (code 70) after 300 s without a loop beat; Docker restarts it. Enabled by `MACD_LOOP_WATCHDOG=1` (compose only, never in tests). | `self_heal.py`, `app.py`, `desk_app.py` |
| SQLite stores damaged by a crash | Read-only quick-check before Python starts after an unclean shutdown. Any failed/unknown integrity check blocks startup. No recovery pipeline, database replacement, WAL deletion, or recent-check bypass. Preserve originals; stop every writer before manual recovery and validate protected records before installing it. Log: `/app/data/backups/doctor.log`. | `engine/db_doctor.sh`, `Dockerfile` |
| Session minutes not captured (host down, feed dead) | `repair_outage`, on every feed connect and BEFORE warm-up. The last stored NIFTY 50 minute bar marks when capture stopped. Every symbol's missed session minutes are downloaded from broker history (4 at a time, 15-min cap). Reported in health under `self_heal.outage_repair`. | `self_heal.py`, `engine.connect_feed` |
| Contract expired while offline | `settle_expired_books` at startup and after each close (15:35). Expired holdings in the MACD, blast and smart books are closed at intrinsic value from the underlying's final regular-session minute on expiry day; missing evidence leaves the position unresolved and visible in health, reason `EXPIRED_SETTLED_WHILE_OFFLINE`. | `self_heal.py`, `ExecutionManager.settle_expired` |
| Nightly memory built on a half day | Requires all continuous-session minute timestamps for every nightly input, using its own instrument/date session rules. Read errors and missing bars block processing, including after 21:30; per-symbol expected/missing counts appear in the nightly report. | `desk._nightly_loop`, `self_heal.session_history_coverage` |
| QuestDB `bars_1m` unreadable | Analytics drops and recreates it after 3 unreadable minutes. A zero timestamp on the last row counts as damage. The bars also live in SQLite. | `analytics/src/pipeline.rs` |
| QuestDB write storm | Bars are sent once a minute, sorted, so QuestDB appends instead of rewriting the day's partition. | `analytics/src/pipeline.rs` |

## Not automatic (host settings the owner controls)

- After a reboot nothing starts until someone logs in to macOS.
  - Docker Desktop's "Start when you sign in" brings the stack up after login.
  - Automatic login (System Settings → Users & Groups) closes the gap entirely. It needs FileVault off: a security
    trade-off for the owner.
- "Start up automatically after a power failure" (Energy settings, or `sudo pmset -a autorestart 1`).
- `docker compose up` waits on the engine's health, which the doctor can hold for about 5 min. If a deploy leaves the
  desk or analytics in "Created", `docker start` them. A host reboot is not affected, because Docker restarts
  containers without the compose dependency wait.

## Boot sequence and timings (16 Oct 2026)

After a power cut the order is fixed: Docker restarts the containers, the database doctor runs, then the app starts.
Engine health reports it under `boot` (start, doctor result, warm-up seconds and downloads, ready, process-to-ready).

| Phase | Clean restart | After a power cut |
|---|---|---|
| Docker up after macOS login | about 1 min | about 1 min (login is manual until auto-login is set) |
| Database doctor | skipped (clean-shutdown marker) | about 3 min (all stores checked in parallel; the 10.9 GB historical store dominates) |
| Contract selection | about 1 s | about 1 s |
| Warm-up of about 670 symbols | **36 s** (was 134–154 s) | 36 s, plus any broker downloads |
| Background 90-day history for new contracts | about 1 min, after ready | same |

What changed on 16 Oct:

- **Stored-history loads run one at a time.** 666 loads take 18 s serially and 88 s six at a time: SQLite releases
  the GIL and the CPU-bound aggregation fights for it.
- **Broker downloads have their own lane** (4 at a time, paced 0.2 s), so loads never wait behind them.
- **Session filtering resolves each day's window once** (4.3× faster; identical bars on 43 symbols, 10,857 bars).
- **Quiet contracts are not re-downloaded.** A contract with no newer bars was downloaded on every boot (35 of 71 on
  16 Oct). `history_checked.json` records that the broker was asked after the last session.
- **Warm-up order:** held positions, then underlyings, then the option band.
