# Vanguard/CBE live data and admission repair

Verified after close at 2026-10-27 19:03:00 IST. Paper only. This is a deployed repair, not an observed post-open trading cycle or evidence of profitable selection.

## Findings

- The Market tab polled completed daily snapshots, so current ticks and same-day intraday features were hidden behind the previous completed daily date. Fresh watchlists also used retained Nomad view shapes.
- The feature worker sequentially repeated historical M2 flow and a full 60-day GEX scan on every 30-minute bar. Publication waited behind that work. Vanguard's ten candidates were actually frozen at **2026-10-27 14:49:54 IST**; the UI did not expose them clearly.
- Only three Vanguard orders were queued. All three exhausted the former infrastructure retry counter, with **zero completed Qwen entry verdicts and zero fills**. Seven other candidates failed exact-contract admission because retained Upstox token metadata described expired contracts. Upstox instrument keys can be reused; identity must include underlying, expiry, side and strike.
- The context merger rejected a 174-character `daily.history_basis` field against its 160-character limit before calling Ollama. This was an integration defect, not a model rejection. Nine Vanguard and approximately 73 CBE context captures preceded the repair, with no completed reviews today. Previous error handling hid this distinction.
- CBE required a successful Qwen planning call before publishing anything. One failed context request therefore prevented the entire watchlist. Its previous after-close publication window also ended too early for recovery.

## Deployed changes

- One lane-side consumer subscribes to existing MI NATS ticks and existing `mi-features` flow. No additional broker websocket or duplicate order-flow calculation. Recorded ticks/flow seed reconnects without changing exchange timestamps. SSE coalesces UI updates at one second; the feature view refreshes every 15 seconds.
- Market tables show latest quote and shared tick CVD separately from completed daily indicators. Each derived feature retains its own source date/time. Historical session views stay historical. Closed/stale quotes have muted dots and explicit ages; reading a feature snapshot does not imply its underlying observation is new.
- Fresh Vanguard and CBE Watchlist tabs show frozen candidates, exact contracts, recorded/current marks, paper states, completed entry verdicts, service failures and fills. Information remains under note buttons.
- Feature calculation runs separately from publication/admission. M2's daily aggregate runs after close, matching its prior-session selector use. GEX uses a once-per-completed-session raw-derived baseline and current snapshots. No scoring formula was changed.
- Current hub master identities replace stale retained token metadata for new option admission and context. Full underlying/expiry/side/strike checks protect marks and exits too. Retained historical catalog rows were not rewritten. Ten of ten current frozen option identities now validate; all 213 active cash identities matched the current master.
- Executable shared bid/ask/depth is reused first. Missing/stale quotes fall back through the existing budgeted hub. Exact candidates join the existing Upstox subscription during market hours. Fresh quote, ATM, whole-lot, depth, spread, cash, exposure, expiry, daily-loss and paper-only guards remain authoritative. Exits do not wait for Qwen.
- Context text now fits the bounded schema; rich context supports at most 384 scalar facts, each string at most 160 characters, with the existing 20 KB request limit. Group-prefix compaction preserves values. Validation, transport and schema failures are recorded separately. Infrastructure retries use bounded backoff and do not count as model verdicts.
- Deterministic CBE candidate pools freeze independently of optional planning inference. Actual paper entry still requires fresh Qwen approval. Same-session after-close planning is allowed until 20:00 IST; no historical publication or missed fills were replayed.

## Actual paper/Qwen state

CBE published ten fresh cash-stock candidates at **2026-10-27 18:42:23 IST**, cohort `be1f13a3c2a55a88bda2c687`. They are `PENDING_AI`, eligible only during **2026-10-28 09:20:00 IST–2026-10-28 09:55:00 IST**. All F&O stocks are screened using daily indicators/MP/context; options are not traded in CBE.

Both fresh books retain ₹50 lakh cash, zero positions and zero fills. Vanguard's three old failed orders and its frozen pool were preserved. New Vanguard cohorts use current exact ATM options and mandatory fresh entry reviews; today's missed trades were not recreated. The seven original MACD-family execution-table fingerprints and settings hash matched before/after this repair.

Two explicitly `validation_only` CBE reviews completed after the context repair: 45.24 and 43.36 seconds. Both returned `WAIT`. The second completed at **2026-10-27 18:58:00 IST**. Despite explicit planning flags, its reason still asked for live entry confirmation during after-close planning. This proves context delivery/inference works, **not that Qwen improves decisions**. Diagnostic reviews do not approve queued orders. Actual entry reviews remain separate and require live executable prices. The original local `qwen3.5:9b` is serving; no research adapter was promoted.

## Verification and limits

- After deployment: MI bus connected, 450 distinct recorded quotes, shared flow for 220 symbols, ten current option identities, no bus error. Seed and newly received tick counters are separate. At the after-close check, live-received tick count was zero; these were recorded observations, not a claimed live-market test.
- Full versus incremental GEX: 219/219 exact regime matches, maximum GEX and percentile difference zero; 49.32 seconds versus 7.01 seconds. Parity check did not write regime rows.
- Revised full feature refresh: 58.06 seconds, all nine stages successful, zero broker requests. Daily and MP coverage: 213/213 each. UI quote freshness is independent of these slower completed-bar calculations.
- Final isolated Python suite: 127 passed, five optional training tests skipped; focused lane/context subset: 114 passed. Frontend: 88 passed; TypeScript/Vite build passed. Both lane SQLite books passed `quick_check(1)`.
- Authenticated frontend SSE returned its first 450-quote/220-flow frame in 0.017 seconds. Rendered Market, Vanguard Watchlist, CBE account and Watchlist checked in browser.
- Shadow-lanes and local-decisions restarted; frontend rebuilt offline and recreated. Original strategy engine, desk and broker sockets were not restarted. No packages, images or historical broker data downloaded.
- Rollback frontend image: `macd-mini-parallel-frontend:before-lane-live-20261027`. Consistent pre-migration lane backups: `runtime/paper_lanes/archives/live-repair-20261027/`. Evidence: `runtime/lane-live-20261027/` (private runtime, not committed).

Next-session validation remains outstanding: exchange ticks must actually advance through SSE, fresh option/spot depth must pass admission, Qwen must produce useful timely entry verdicts, and any paper fill must reconcile to cash and exact contracts. CVD producer restart completeness remains unverified and is disclosed in the feature context. No claim of edge, guaranteed entries, or live-order readiness is made.
