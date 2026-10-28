# Local Vanguard/CBE models and Option Agent cold-start repair

Owner follow-up: extend local intelligence to Vanguard/CBE and repair the **Option Agent tab**. Work is pre-open on 2026-10-28. Smart's preceding repair is recorded in `SMART_LOCAL_INTELLIGENCE_20261028.md`, commit `32a7cc7`.

## Findings

- Vanguard retains three rejected prior-session orders, zero completed entry reviews/fills, and ₹50 lakh cash. Their old reasons (`Qwen retry limit reached`) remain historical evidence. Exact-contract token reuse and slow feature publication were repaired in the preceding work; old orders are not reopened.
- CBE retains ten prospective cash-stock candidates from the previous completed session, with entry window **2026-10-28 09:20:00 IST–2026-10-28 09:55:00 IST**, ₹50 lakh cash and zero fills. Before this change their entries depended on Qwen. Their frozen identities, source data, ranks, entry windows and account epoch remain intact.
- Option Agent has zero saved models, candidates, runs and picks after recovery. Its emitter tried training, received `insufficient` (zero labelled days versus twelve needed), then returned `no_model` **before capturing candidates**. Learning consequently could never acquire the forward labels it needed. This is a cold-start cycle, not evidence of a quiet market.
- A query-only retained-session diagnostic found **632 eligible contracts, including 612 stock contracts, across 178 underlyings** in 39.19 seconds. It created no runs, picks, candidates or fills. Existing local market bars can supply the pipeline.

## Local decision models

`lane-local-context-v1-cbe` and `lane-local-context-v1-vanguard` are bounded, deterministic, untrained paper baselines. They have no Qwen dependency, order API or outcome input. They build the same causal shared feature context directly in-process and retain its source/receipt times. The full snapshot remains recorded by the shared context collector; decision records include model/code digest, context hash, components, weights, priority, freshness statuses and warnings.

| Component | CBE weight | Vanguard weight |
|---|---:|---:|
| Daily trend/MACD/RSI/relative strength | 35% | 15% |
| Market profile/value location | 20% | 20% |
| Sector relative strength | 10% | 15% |
| Fresh underlying continuation | 15% | 15% |
| Frozen source rank | 10% | 15% |
| Timing | — | 10% |
| OI positioning | 10% | 10% |

CBE requires completed daily indicators and a numeric market profile for the frozen source session; positive EMA/momentum/relative-strength alignment must still hold, and a current cash price below EMA20 waits. Vanguard requires an exact CE/PE candidate, fresh underlying continuation, and observed completed-bar regime/timing no more than one hour old. A published derivatives ban blocks new Vanguard exposure; it does not incorrectly ban CBE cash purchases. Regime/GEX is freshness/structure evidence, not a bullish/bearish sign. Optional stale/missing groups are neutral and disclosed. Other shared flow, IV/Greeks, events, public reports and cross-asset fields remain available in the recorded context; they are not presented as validated directional predictors.

Up to ten eligible orders are reviewed as a batch, locally. Actual paper admission is priority-first and rechecks existing expiry/ATM, price freshness, bid/ask, depth, volume, whole lots, slippage, estimated fees, cash, sector/gross exposure, daily loss and position limits. No quantity, capital or exit limit was relaxed. Waits and context outages do not masquerade as a model rejection or consume a Qwen retry budget. Approvals drifting/expiring can request another local review quickly; waits back off. Exits continue independently.

The persisted legacy state name `PENDING_AI` remains compatible. The UI describes it as pending local review. Historical Qwen failures are retained. Existing account risk-policy metadata remains immutable; current decision-policy identity is separately exposed by the API and stored on every new review.

An explicitly validation-only CBE context check read **255–265 facts per candidate in 2.40 seconds**. It used recorded marks, did not establish quote executability and did not apply approvals to any book. This is context-path evidence, not a trade result or profit claim.

## Option Agent

When no trained champion exists, the emitter now uses existing local rule controls: stock `momentum-rule` and index `trend-rule`. They are saved as **`baseline`, zero samples**, never as a trained champion. Models/champion promotion gates and after-close learning remain separate. Current candidate capture can now produce the future labels needed for training. Live emission no longer invokes a potentially heavy training job to escape an empty-model state.

Two baseline artifacts were initialized locally, version one each. Both champion lookups still return none. Before/after counts for runs, picks, candidates and index plans remain zero: no historical cohort or simulated entry was manufactured. The first scheduled fresh list is **2026-10-28 14:47:00 IST**, using the established completed snapshot. Cold-start scheduled emission also refuses a missed/previous-day window. Existing freshness, contract lifetime, premium, turnover, moneyness and uniqueness checks remain.

The UI labels these models untrained. Index trend scores display as underlying trend, not expected option-premium return. Option Agent retains its existing timed pick/plan tracking architecture; fully funded paper accounting remains in the Smart/Vanguard/CBE books.

## Preservation, deployment and tests

- Verified at **2026-10-28 08:38:46 IST**: seven MACD-family protected execution-table/settings fingerprints match before/after. Both lane accounts, orders, cohorts, positions, fills, decisions and settlements, plus frozen observation publications, match their separate fingerprints. No cash, holding, frozen selection or history was reset. Smart's corrected held stages remain intact, including ETERNAL at entry four / exit two.
- All four relevant routes return HTTP 200 through the actual frontend proxy. Fyers is connected with no lane errors; installed engine source matches the checkout. Both lane APIs report `qwen_required=false` and the loaded local model digest matches its source. The shared bus is connected with 232 recorded quotes and 219 flow symbols at this pre-open snapshot; this is not a claim of regular-session coverage. CBE still has ten pending entries, Vanguard zero new pending entries, and both retain ₹50 lakh with no fills.
- Consistent lane backups: `runtime/paper_lanes/archives/local-model-20261028/`. Detailed private evidence: `runtime/lane-local-20261028/`. Previous Smart backups and corrected checkpoints remain available.
- Offline builds reuse installed dependencies. Engine/desk/frontend were recreated and shadow-lanes restarted before market open. The engine reuses the single Fyers websocket; no new broker socket or flow tracker was added. No downloads, live orders, credential entry, or macOS settings changes occurred. Qwen/research data remain installed but are not required by these lane admissions.
- Lane/accounting/shared-context tests: **100 passed**. Option Agent/index tests: **42 passed**. Frontend: **89 passed**, production build successful. Final broad engine sweep: **1,139 passed, one skipped, one pre-existing RRG fixture failure**; that failure was independently reproduced on the unchanged pre-task checkout.
- Rollback tags: `macd-mini-parallel-engine:before-lane-local-20261028`, `macd-mini-parallel-engine:before-lane-local-desk-20261028`, `macd-mini-parallel-frontend:before-lane-local-20261028`. Keep the earlier Smart profit-stage correction when rolling back unrelated model changes; never overwrite current ledgers with an old backup without a separate recovery plan.

These local models are transparent baselines, not newly trained or validated profit optimizers. Prospective fills, costs, utilization, drawdown and policy-versioned outcomes must establish any improvement. At the pre-open check, no regular-session model-driven cycle or new fill has yet occurred. Four original MACD option contracts still lack warmup history under the historical-download hold; the new policies do not bypass their data gates.
