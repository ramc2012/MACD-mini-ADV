# Expanded local Qwen context and feature recovery

Work started after the owner requested more signal context and use of Vanguard's market features for entries and next-session watchlists. The MACD lane's admission rules, settings and books remain unchanged. Its Smart intake receives a separate metadata copy. Vanguard/CBE retain their separate ₹50 lakh paper accounts, original risk gates, next-session entry windows and independent exits.

## Market context

The recovered `regime` table lacked its `(symbol, ts)` conflict index. Its writer failed and the old fail-first refresh skipped sector/lead-lag/timing. The index was restored after a zero-duplicate check; independent stages now continue and persist stage receipts. Completed-bar scheduling waits 90 seconds after the close, includes the final derivative bar, bounds retries, and runs market-profile/OI-positioning rollups after the session.

Qwen receives allowlisted scalar context from flow, regime/GEX, timing, sector strength, lead-lag, IV surface, chain/premium structure, futures OI, market profile, positioning, delivery, MWPL, participant/breadth sentiment, FX, relevant cross-asset beta, disclosed bulk/block trades, announcements, results and the applicable ban-list session. Each group carries source time, receipt basis, age and status. Exact-contract Greeks require matching underlying/expiry/strike/side; cash-universe observations correctly have no exact option Greeks. Missing optional features are explicit, not fabricated neutral values.

Current recorded futures levels are reused from the shared hub/master and database: 219 instruments, zero additional broker requests. Daily OHLC is not invented from partial ticks. The retained futures history stopped on 2026-10-14, so a new observation from 2026-10-26 cannot become a one-session OI change. Comparisons across a missing prior session or contract roll are suppressed; rolling statistics require contiguous history meeting the original 20-observation minimum in a 60-session window. This does not fill the missing history.

The owner approved current public reports. Fetch results were participant OI 24, equity daily 3,413, bulk 89, block 0, ban 3, board meetings 19 and announcements 20, with no remaining fetch errors. Repeated board-meeting keys initially broke a PostgreSQL batch; batches now deduplicate their natural keys before upsert. Existing local reports are mirrored insert-only into market-context tables. Original receipt times are retained. A second mirror inserted zero delivery/deal/announcement/ban/result rows. History downloads remain blocked. The opt-in current-session collector runs after 18:45, bounds retries and skips completed receipts.

At 2026-10-26 22:56:37 IST, the immutable universe capture covered 213 names. Coverage included chain/flow/sector/lead-lag 210 current names, delivery/profile/regime 212, timing/positioning 209 and surface 208; a few names remain missing or stale. FX and MWPL remain prior-session context. Twelve calculation receipts succeeded; this is separate from source freshness and prospective readiness. The UI exposes Feature refresh, Source coverage and Training capture tabs on both native lane pages.

## Serving and capture

One serialized local reviewer serves Smart, Vanguard and CBE. Each review uses one complete candidate, up to 320 bounded facts, a 20,000-character request budget and a 4,096/8,192-token context window. Original signal/risk facts take precedence over shared providers. Identical observations across Smart books can share one inference while the response is rebound to each caller's original candidate ID. Source failures wait, rather than silently submitting a bare prompt.

All active stocks are captured before selection, including names never selected. Exact observed review inputs are saved before model absence/failure/veto. Outcomes remain separate and must mature before becoming labels. Captures start at the real observation time; historical receipt availability is never backdated.

The old served review visibly confused the MACD signal EMA with a directional command. Smart input now explicitly identifies option-premium indicator basis, BUY action and the original trigger, and defines the signal EMA. No MACD strategy rule changed. These explanatory fields were added after freezing this research corpus; the completed adapter diagnostics are not a validation of the final serving prompt/input distribution.

Native research and Ollama must not allocate their large models together on this 16 GB Mac. A process watcher writes an atomic workload lease. New model-reviewed entries wait while it is active; an expired active lease also waits until explicitly cleared. Native jobs refuse market hours and preserve research checkpoints. Exits do not depend on Qwen.

## Frozen research run

The native base is the corrected local `base-9b-int4-int8` derivative. Older wrong-scale NVFP4 experiments are invalid. New runs adapt the final Q/V layer with rank 4, 69,632 trainable parameters, learning rate 0.00001, offline dependencies and a 9 GiB MLX allocation limit. Final adapter files and corpus hashes have receipts; no model registry import or automatic promotion exists.

The loss weights use training labels only. A first implementation normalized by minibatch weight, cancelling the effect at batch size one. Its Smart checkpoint 40 is retained as an unweighted warmup. The corrected Smart run starts from that warmup with a reset optimizer, then performs 124 balanced iterations at batch size two. Vanguard uses 50 iterations at batch size one; CBE uses 108 at batch size two. Five native numerical tests include a batch-size-one weighting check. Do not describe this as an optimizer-exact resume or a clean Smart scratch run.

Smart uses one capital book's actual MACD-source executed positions, exact order/trade/journal joins and all 22 recorded pre-entry features. It has 124 training records (38 positive, 86 negative), 14 validation and 25 test; 56 boundary-crossing outcomes are purged. Decisions before 2026-10-19 train, validation ends before 2026-10-22, and test decisions are 2026-10-22 through 2026-10-23 with mature outcomes. Fees have a 10 bps per-leg floor and a 50 bps stress case. Rejected-signal counterfactuals and a versioned historical exit policy are unavailable; the historical book's selection bias remains.

Vanguard has 25/6/7 train/validation/test examples; CBE has 108/12/7. Their context is reconstructed from prior-session market features. Historical receipt availability and L1 fills remain unverified. Strict availability-verified Vanguard/CBE labels remain zero. Large retained feature tables do not change this evidence quality.

| Lane | Frozen test observation | Serving decision |
| --- | --- | --- |
| Smart | 10/25 approved, 3 wins, mean net return -4.04%, profit factor 0.82. All 25 mean -2.65%. Stress mean of approved -4.83%. | Improvement not proven; adapter stays research-only. |
| Vanguard | 7/7 approved, 3 wins, mean +3.30%; also approves all 6 validation cases at mean -1.02%. | No selectivity demonstrated; adapter stays research-only. |
| CBE | Training/evaluation in progress; final receipt required. | Research-only. |

Derived-base verdict-head scoring approved zero Smart and Vanguard validation/test cases. These metrics are conditional-verdict, equal-notional closed-trade diagnostics, not a cash-constrained backtest, calibrated probabilities or prospective edge. Test outcomes will not be used to keep retuning until results look favorable. Original local Qwen remains served, with expanded context; its new decision quality needs prospective evidence.

## Deployment evidence and limits

Both images build offline from retained local rollback images. The installed engine has 88/88 source-module parity. All seven original protected MACD books match their pre-deployment table hashes; settings have no changed keys. Paper mode and live-order locks remain set. Current public report downloads are complete; broad historical recovery stays held. Original Nomad workers remain retired and their histories are retained.

Private evidence is in `runtime/qwen-context-v2/` and the platform's `backups/qwen-context-20261026/`. Research artifacts include immutable prompts, split records, predictions, adapter hashes and evaluation reports. The new market capture and UI were checked after deployment. The engine's startup read-only integrity scan briefly outlasted its health grace period; it completed successfully and the dependent desk was then started.

No missed cohort was replayed and no after-close list was backdated. Existing prospective publication windows remain Vanguard 14:45–15:15 and CBE 15:15–15:35; their next-session entries still require fresh quotes, original gates and a separate current review. Current EOD data is available for subsequent reviews, but it cannot create a cohort retroactively. A post-open live paper cycle has not been observed for this change.
