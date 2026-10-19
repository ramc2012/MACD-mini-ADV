# Why MACD-lane entries fell (15 Oct 2026)

| Day | Buy signals | Entries |
|---|---|---|
| 13 Oct | 340 | 85 |
| 14 Oct | 204 | 18 |
| 15 Oct (to 11:00) | 86 | 19 |

Cash is not the limit: ₹65 L sits idle on ₹1.09 Cr of equity. Capital was doubled to ₹1 Cr on 13 Oct.

## Cause: the entry gate (`entry_scorer.py`) skips far more than designed

- The gate is meant to skip the weakest 30% of signals. It uses a fixed cut (score 0.321) taken from the
  percentiles of its September training sample.
- Skip rate by day: 12–16% on 1 and 9 Oct, then 57% / 39% / 70% / 52% on 12–15 Oct.
- The driver is days to expiry, the model's heaviest input. Mean DTE at the signal fell from 22.3 to 13.8 days as the
  27 Oct monthly expiry approached. Its contribution fell by 0.40 in log-odds; the next inputs moved by less than 0.08.
- The gate therefore closes down in the back half of every monthly cycle and reopens after the roll.
- The model was trained on a single cycle (25 Aug – 29 Sep), so "longer to expiry is better" is one month's evidence.

## Did the gate pick better trades in October?

`gate_outcomes.py` replays the scored signals from 1–15 Oct on their real option bars:
- exits: −30% hard stop, 25% trail from +30%, otherwise the next session's close;
- costs: 1% round trip.

| | n | Mean per trade | Win rate |
|---|---|---|---|
| Taken | 286 | −2.1% | 47% |
| Skipped | 335 | −1.3% | 45% |

- Per day, taken minus skipped: −1.3 pp (t −0.46, 8 days).
- Score vs return correlation: 0.04. Score quintiles are not monotonic.
- **Out of sample, the gate shows no edge in October**, while it removes half or more of the entries.
- The replay is optimistic in absolute terms, but the comparison is paired on the same days.

## Options (the user's call)

1. Turn the gate off (`macd_entry_filter = false`).
2. Make the cut relative within each day: skip the day's weakest 30%, as the smart lanes do.
3. Refit the model without DTE.

## Retrain (user request, 15 Oct): days to expiry matched to the expected hold

Scripts: `retrain.py` (walk-forward), `final_fit.py` (export → `model_v2.json` → `engine/src/macd_trader/entry_model.py`).
The September model is kept in `MODEL_SEPTEMBER` and in `entry_model_september.py`.

**Expected hold H.** From 678 MACD-lane closed positions: median 0.9, mean 1.9, 75th percentile 2.9 calendar days. H = 2.

**New feature.** `theta_share = 1 − sqrt(max(D − H, 0) / D)` replaces raw `dte`. D is fractional calendar days to the
15:30 expiry. It is the share of time value that decays over the hold.

**Data.** 932 live-gate signals; 741 have a complete 3-session label. The label is a replay with the lane's exits
(−30% stop, 25% trail from +30%, 1% cost). Weights are ridge-anchored to the September model.

**Walk-forward over 5 test days (535 signals).** No variant ranks reliably. AUC: deployed 0.545, refits 0.48–0.52.
At a within-day 30% cut:

| | Kept | Skipped |
|---|---|---|
| Deployed (September) | −2.3% | −1.0% |
| Hold-matched, H = 2, prior strength 3 | −1.35% | −3.3% |

Not significant.

**What changed.** The fitted `theta_share` weight is ≈ 0 (−0.016). Across 8–25 days to expiry a 2-day hold loses only
4–13% of time value, and the outcomes show no effect there. This matches the user's point: expiry matters relative to
the hold, not as a raw count.
- Only 6 of 932 signals were under 8 days out, so the inside-the-hold case cannot be fitted. It is a structural
  refusal instead: `EXPIRES_WITHIN_HOLD` when `dte_frac < H`.
- **The cut is now the day's own 30% quantile** once 20 signals are scored (before that, the model percentile 0.4478).
  With a fixed cut, the retrained model would still swing from 7% to 100% skipped by day, because other inputs drift.

**Expected effect.** About 30% of a day's scored signals skipped, every day (it was 39–70% on 12–15 Oct), and no
expiry-cycle throttling.

**Status.** 976 tests pass. Deploying after the 15:30 close (scheduled 15:37).
