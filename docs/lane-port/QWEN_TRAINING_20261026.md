# Local Qwen training and data qualification

Progress checked at **2026-10-26 08:24:00 IST**. This document distinguishes research artifacts from serving
weights. The running reviewer still serves the original **qwen3.5:9b**; no trained adapter has been deployed.
All three adapter training passes completed and have finite-weight receipts. Full held-out evaluation is still
in progress; training completion is separate from evaluation completion.

The owner's latest instruction, **leave MACD lane as it is**, governs. Its current entry filter, signals,
execution/risk rules, cash and positions remain unchanged. Smart's optional Blast intake is disabled for new
candidates, so new Smart intake comes only from MACD signals. Existing Blast-origin Smart positions retain
original exit management. Qwen reviews apply to new entries; exits never wait on inference.

## Data contract

Private exports, immutable prompts, records and model artifacts are under `runtime/qwen-training/` (ignored,
directory mode 0700). Read-only sources are the preserved legacy and current MACD books, Smart audit records,
and retained Nomad market/research tables. Smart labels use original MACD outcomes, avoiding duplication across
Smart's two capital books.

| Lane and evidence tier | Train | Validation | Test | Boundary-purged |
|---|---:|---:|---:|---:|
| Smart: recorded paper closes | 519 | 177 | 91 | 66 |
| Vanguard: verified availability | 0 | 0 | 0 | 0 |
| CBE: verified availability | 0 | 0 | 0 | 0 |
| Vanguard: separate candle reconstruction | 25 | 6 | 7 | 23 |
| CBE: separate candle reconstruction | 108 | 12 | 7 | 8 |

Smart training decisions precede 2026-10-12; validation decisions precede 2026-10-19; test decisions fall in
2026-10-19 through 2026-10-23. Complete outcomes must mature before the next split; crossing trades are purged.
Training has 145 approve, 357 reject and 17 wait labels. The held-out test has 42 positive and 49 negative labels.
No valid held-out prediction results were used to choose the current hyperparameters.

Exact signal/order/fill/closed-position identities must join. Partial exits aggregate by position; incomplete
quantity, stale/future signal links, invalid values and non-option records are excluded. Inputs contain only the
original signal's MACD, signal line, histogram, price, time and source. Outcomes, excursions and exit reasons
never enter prompts. Fees retain recorded costs with a floor of ten basis points per leg; a fifty-basis-point
per-leg stress case is separate. Net returns above +0.5% supervise approve, below -0.5% reject, otherwise wait.

These are selected historical MACD executions under varying former admission and exit rules. They do not label
all unexecuted signals, reconstruct today's full Smart feature context, or simulate Smart's own exit policy.
Equal-notional outcome diagnostics cannot establish portfolio improvement or prospective profitability.

## Vanguard/CBE research boundary

All 320 retained Vanguard watchlist rows fail strict qualification: 300 entry marks precede the frozen decision;
20 lack compatible non-replayed runs. Retained CBE closes mix several former policies; only three are tagged
with the inherited daily-stock policy, all losing. Later backfills do not prove pre-decision availability of
benchmark/stock windows. The verified training population therefore remains zero for both lanes.

A separate reconstruction uses frozen Vanguard features created after their model artifact and current-policy
CBE event-time daily features. Archived three-minute bars supply exact option expiry/strike/side/underlying or
cash-symbol proxies. The simulation uses the next session's 09:21 open, current fixed stops/targets and time exits,
fees, slippage and assumed spread. Same-bar stop/target ambiguity chooses the stop; missing entries/exits and
session gaps are excluded. No historical trading book is rewritten and no retrospective cohort is published.

Actual receipt timestamps remain `label_available_at`; economic outcome time is separately `label_matures_at`.
Research splits use economic time and **do not prove historical availability**. Vanguard validation precedes
2026-10-14 and test consists of that decision session; CBE uses the Smart date boundaries. CBE's seven test rows
are all negative. Current-universe/cohort selection bias, unknown L1/queue execution and tiny samples prevent
production promotion. These examples are explicitly labeled `reconstructed_unverified_availability`; training
requires an explicit research opt-in. They never become verified historical fills or evidence of real edge.

## Trainer and compatibility

The approved isolated Apple-silicon environment contains MLX 0.32.4, MLX-LM 0.32.2 and Transformers 5.20.2.
All loading/training runs offline. No trade data was uploaded and no second model or market dataset downloaded.
The original Ollama checkpoint digest is
`d63f830f28d515fefea546f213fca7a75e9dd5176ef51a6deabc952d1c99a911`; original tensor files are untouched.

`prepare_mlx.py` builds a text-only research checkpoint from existing local tensor blobs. The installed checkpoint
stores NVFP4 global scale as a dequantization multiplier; MLX's optional dequantization argument instead expects
amax. **The first pilot used the wrong scale and is invalid. Its losses and evaluation-v1 predictions must never
be cited as evidence or reused.** The error was corrected and tested numerically, including native language sanity.

`repack_training.py` makes the separate `base-9b-int4-int8` training base: 168 NVFP4 layers become affine four-bit,
and 34 MXFP8 layers become affine eight-bit. Mean relative absolute tensor errors are 8.44% and 0.615%, respectively.
This added quantization is a compatibility tradeoff, not an improvement. Candidate adapters must be compared with
this exact derived baseline, and cannot be attached to the original Ollama model interchangeably.

`train_verdict.py` trains 69,632 LoRA parameters in the final attention layer Q/V projections, rank four, scale
eight, learning rate 0.00002. It optimizes approve/reject/wait classification at a forced JSON verdict slot, not
JSON boilerplate. Label tokens and subsequent padding are zeroed before the backbone forward pass; hidden state
is gathered before the target. Only three frozen output-head rows are projected. Native numerical tests verify
agreement with the full output head and its gradients. Both NumPy and MLX seeds are fixed before initialization.

The first 31 frozen layers use inference kernels while the adapted final attention layer remains in training
mode. Earlier generative and slow-kernel experiments are superseded; they are retained privately as provenance.
Smart uses batch four / 130 iterations; Vanguard batch one / 25; CBE batch four / 27. This is approximately one
pass, not evidence that the tiny lane datasets are sufficient. Partial checkpoints omit optimizer/RNG state;
loading an adapter to continue is not an exact optimizer resume.

Training and evaluation have a nine-GiB allocation limit and bounded cache. They refuse market-hours execution
and stop by **2026-10-26 08:30:00 IST** for this session. No OS settings or wired-memory limits were changed.
Only one full model training/evaluation process may run at once. These limits do not prove intraday inference safety.

Training completed: Smart 130 iterations at **2026-10-26 08:19:13 IST**; Vanguard 25 at
**2026-10-26 08:20:14 IST**; CBE 27 at **2026-10-26 08:23:55 IST**. Adapters and receipts are private;
`backups/qwen-training-20261026/training-complete.json` in the platform records hashes/status.

## Evaluation and serving

`evaluate_local.py` freezes prompt/record/model/adapter hashes, compares the derived untrained base with its
adapter and saves each prediction before computing outcome metrics. It can resume only with matching provenance.
Fixed argmax of approve/reject/wait token scores is **not a calibrated probability of profit**. Reports are marked
complete only after full validation and test splits finish. Rejecting all signals reports zero coverage, not success.
No invalid pilot baseline may be reused. No partial run may be reported as a completed held-out evaluation.

Complete JSON generation, production-checkpoint/import compatibility, economic diagnostics, latency/resource
checks and prospective paper evidence remain necessary before any serving change. The production reviewer is
still pinned to the original Qwen tag; no automatic adapter selection or promotion was implemented. The UI shows
served weights separately from the research manifest. Research adapter training does not itself fulfill a claim
of improved Smart, Vanguard or CBE profitability.

## Completed reconstructed-data diagnostics

Checked at **2026-10-26 08:26:00 IST**. Fixed argmax; no threshold tuning or weight changes after these results.
Returns below are equal-notional net trade-return diagnostics with the documented candle assumptions.

| Adapter | Validation approvals | Validation mean / approved | Test approvals | Test mean / approved |
|---|---:|---:|---:|---:|
| Vanguard | 3 / 6 | -0.85% | 2 / 7 | +6.23% |
| CBE | 0 / 12 | undefined | 0 / 7 | undefined |

Vanguard's approved validation set has one gain and two losses; test has one gain and one loss. With seven
reconstructed test cases, this is **not proven**, and the negative validation result remains material. Its untrained
derived base approved zero validation/test cases. CBE's derived base approved all twelve validation cases and six
test cases, with mean approved returns -0.23% and -2.05%; its adapter approves none. Zero approval is not trading
success, and an all-negative seven-row test cannot measure profitable-signal recall. Neither adapter is promoted.
Smart's full 177-validation/91-test comparison remains in progress and must not be inferred from these lane results.

## Prospective records and preservation

The shared reviewer now captures the exact fresh input and system prompt before inference, including veto,
model-unavailable and queue-failure cases. All three lane types are covered. Smart journals retain the original
signal ID, signal time and feature-availability time. Mature outcomes can be joined later; execution/risk failures
must remain distinct from counterfactual trade labels.

Saved-setting comparison found only `smart_blast_source` changed. Every other setting, including MACD's current
entry filter, matches. Seven protected MACD book table maps matched across deployment. Paper mode and the live-order
lock remain intact; source/installed engine parity is 88/88 modules. Original Nomad histories and the new ₹50-lakh
Vanguard/CBE paper books are not rewritten.

Validation evidence: platform `backups/qwen-training-20261026/`. The combined offline lane/service suite passed
114 tests; the final corpus suite passed eleven; four native MLX numeric/masking/mode tests passed. Frontend build
and 88 tests passed. Runtime status and book fingerprints are time-specific; refresh before readiness claims.

Reproduction: `local_decisions/{export_sqlite,export_pg,build_corpus,research_requests,label_reconstructed,
build_research_corpus,prepare_mlx,repack_training,train_verdict,evaluate_local}.py` and the three
`*_verdict_mixed.json` configs. Experimental configs are retained privately under the ignored training directory.
No automatic retraining, cloud call, Agentic test, push or real order was performed.

References: [MLX-LM LoRA/QLoRA](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/LORA.md),
[Ollama model import](https://docs.ollama.com/import).
