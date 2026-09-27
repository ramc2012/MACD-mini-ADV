# Order flow: what it is worth, and how to normalise it per symbol

`research/of_validation.py` answers two questions in one pass over
`tick_minute_flow`, and refreshes the per-symbol baselines the desk needs to
compare a reading on NIFTY futures with a reading on a Rs 2 option.

    python research/of_validation.py                     # measure, write nothing
    python research/of_validation.py --write-baselines   # + refresh baselines
    python research/of_validation.py --json out.json     # machine-readable

Started on the host it pipes its own source into `macdmini-api-1` and re-runs
there, because host reads of the bind-mounted `ticks.sqlite3` return stale or
torn pages while the container's writer is live. It is stdlib-only and 3.9-safe
so the bootstrap starts on whatever python the caller has. Safe to run daily
once the tick store has condensed the session; `--days` defaults to every day
carrying at least 5,000 minute rows, so the daily refresh takes no argument.

## The caveat that governs every number below

NSE publishes trade price, quantity, time and the best bid/ask. It **never**
publishes the aggressor. Every `buy_volume`, `sell_volume` and `delta` here is
an **inference** (Lee-Ready: quote rule first, tick test as fallback). Nothing
in this document is a measured order-flow number.

Worse for the figures below: the two sessions in the store
(**2026-08-21** and **2026-08-25**, 1,053 symbols, 354,834 active minute bars)
were written by the **legacy classifier**, before the 2026-08-27 fix. That
classifier fed its tick test quote/OI ticks (its own echo), had no zero-tick
carry-forward, and deleted unclassified volume instead of carrying a residual.
So this measures the *legacy estimator*, and every baseline row is stamped
`estimator = 'legacy_pre_2026-08-27'`.

## Method, and why it is built the way it is

Features are computed at bar `t` from bars `<= t` only (expanding mean/sd and
median, floor 20 prior bars, reset each session — a later bar can never move an
earlier feature, and there is a test for that). Only bars with a real print take
part. A target bar must sit **exactly** `h` minutes later, so a gap in the tape
is never silently stretched into a longer holding period.

Every feature is scored against three targets, because the obvious one is
contaminated:

| target | definition | why |
|---|---|---|
| `close` | `close(t+h)/close(t) - 1` | standard, and **shares `close(t)` with the feature bar**. If that close printed at the bid, the next return is mechanically biased up. A negative delta *is* "the last print was at the bid", so it earns a spurious negative IC for free. |
| `oc` | `close(t+h)/open(t+1) - 1` | **tradeable and clean**: enter at the next bar's open, so `close(t)` is not in the return at all. This is the column that decides anything. |
| `vwap` | `vwap(t+h)/vwap(t) - 1` | bounce-damped but **drift-contaminated** — `vwap(t)` averages over bar `t`, so anything carrying bar `t`'s drift correlates with it mechanically. Cross-check only. |

Significance is reported two ways. The pooled `1/sqrt(n)` standard error is
**optimistic**: 300k rows inside 1,271 symbol-sessions are not independent. The
honest test is the mean per-symbol IC against its **cross-symbol** standard
error (`t` in the tables). `partIC` removes the previous bar's return from both
sides — what the flow feature adds over price alone.

## (a) What was measured

**Sanity first — does inferred delta agree with its own bar's direction?** Not a
prediction test; the agreement is mechanical, and it is the floor. Rank IC of
`delta/volume` against the same bar's open-to-close return:

| instrument | series | IC mean | ±SE | t |
|---|---|---|---|---|
| EQ | 416 | **+0.0903** | 0.0034 | 26.7 |
| FUT | 6 | +0.0698 | 0.0410 | 1.7 |
| OPT | 803 | **−0.0446** | 0.0056 | −8.0 |

**On options the legacy classifier's sign is inverted.** Aggressive buying, as
it labelled it, went with the bar closing *below* its open. That is the single
most important line in this file: legacy option delta is not weakly informative,
it is backwards. Equities are the right sign but weak (+0.09 where a working
aggressor tag would be several times that).

**Prediction, horizon 1 minute, target `oc` (the clean one), 914 symbols:**

| feature | pooled IC | ±SE | sym IC | ±SE | t | partIC | part t | hit rate |
|---|---|---|---|---|---|---|---|---|
| delta | −0.0189 | 0.0018 | −0.0325 | 0.0025 | −13.1 | −0.0230 | −8.5 | 0.4915 |
| **nd** (delta/volume) | −0.0267 | 0.0018 | **−0.0437** | 0.0026 | −17.1 | −0.0329 | −11.9 | 0.4915 |
| buy_share | −0.0280 | 0.0018 | −0.0463 | 0.0026 | −17.7 | −0.0337 | −12.2 | 0.4915 |
| delta_z | −0.0169 | 0.0019 | −0.0319 | 0.0026 | −12.4 | −0.0241 | −8.5 | 0.4921 |
| persist3 | −0.0102 | 0.0018 | −0.0153 | 0.0023 | −6.7 | −0.0136 | −5.1 | 0.4969 |
| rvol → \|ret\| | +0.0451 | 0.0019 | **+0.0239** | 0.0030 | +7.9 | +0.0242 | +7.1 | n/a |
| *ret_prev (control)* | −0.0532 | 0.0019 | −0.0659 | 0.0032 | −20.7 | — | — | 0.4764 |

Three things to read off it:

1. **Every directional flow feature is negative.** Aggressive buying is followed
   by a *lower* next bar. Order flow is a weak contrarian signal here, not a
   momentum one.
2. **The price-only control beats all of them** (−0.0659 vs −0.0437) and
   survives partialling. Most of what delta "knows" is what the last bar's
   return already said.
3. It is not an artefact of the classifier echoing itself. Controlling for
   `nd(t+1)` — the mediation path, since flow autocorrelates at +0.16 (OPT) /
   +0.20 (EQ) — leaves the option IC unchanged (−0.0546 → −0.0555) and makes the
   equity IC *stronger* (−0.0079 → −0.0280). The reversal is real, small, and in
   the data.

On the contaminated `close` target the same features look 2× stronger
(nd: −0.0794, t −28.2). That gap **is** the bid-ask bounce. Anyone quoting the
`close` numbers is quoting microstructure.

`rvol` is the only feature with a stable positive sign across all three targets
and both horizons: **volume predicts next-bar volatility**, not direction
(+0.024 at h=1, +0.045 at h=5, target `oc`). It is the most robust result here
and it is also the least surprising one in finance.

## (b) Does classification quality matter? Yes — and backwards

Split on `nd`'s per-symbol IC (target `oc`, h=1):

| quote coverage (quote_n/trades) | symbols | IC mean | ±SE | t |
|---|---|---|---|---|
| <0.50 | 161 | −0.0466 | 0.0067 | −7.0 |
| 0.50–0.70 | 465 | −0.0532 | 0.0038 | −14.1 |
| 0.70–0.85 | 111 | −0.0456 | 0.0060 | −7.6 |
| 0.85–0.95 | 164 | −0.0150 | 0.0042 | −3.6 |
| ≥0.95 | 13 | −0.0138 | 0.0281 | −0.5 |

| median spread (bps of price) | symbols | IC mean | ±SE | t |
|---|---|---|---|---|
| <2 | 40 | −0.0086 | 0.0057 | **−1.5 (inside its own error)** |
| 2–5 | 171 | −0.0076 | 0.0031 | −2.4 |
| 20–100 | 100 | −0.0419 | 0.0065 | −6.4 |
| ≥100 | 601 | −0.0568 | 0.0035 | −16.4 |

| instrument | symbols | IC mean | ±SE | t |
|---|---|---|---|---|
| EQ | 208 | −0.0079 | 0.0028 | −2.8 |
| FUT | 5 | +0.0063 | 0.0081 | +0.8 |
| OPT | 701 | −0.0547 | 0.0031 | −17.6 |

The apparent predictive power is **largest where classification is worst** and
**vanishes where it is best**. Quote coverage, spread and instrument type are
the same split seen three ways: coverage is poor precisely on wide-spread
illiquid options. Where the desk can actually trade — tight-spread equities and
index futures — the flow signal is inside its own error bar.

So the answer to "which symbols should the desk trust order flow on?" is not
"the ones with good coverage, because that is where it works". It is:

- **≥0.85 quote coverage / <5 bps spread**: the classifier is behaving (positive
  contemporaneous IC, right sign), and there is **no usable forward signal**.
- **<0.85 coverage / wide spread / options**: there is an apparent forward
  signal, the classifier's own sign is **inverted** on that same population, and
  the spread swallows it. Do not trade it.

## (c) Is the edge bigger than the spread?

`|IC| × the symbol's own per-minute return sd` against one full quoted spread.
Order of magnitude only, and generous — the IC is in-sample, and brokerage and
impact are not in the cost.

| instrument | symbols | edge (bps) | cost (bps) | ratio, median | ratio, p90 |
|---|---|---|---|---|---|
| EQ | 208 | 0.157 | 2.67 | **0.059** | 0.168 |
| FUT | 5 | 0.027 | 1.46 | 0.018 | 0.061 |
| OPT | 701 | 15.47 | 190.4 | **0.082** | 0.196 |

The edge is 2–8% of the spread, and under 20% even at the 90th percentile.
**Nothing here is standalone-tradeable at a one-minute horizon.** Order flow's
role is as context on a position taken for another reason, or as an execution
input — not as an entry.

## (d) The normalisation baselines

Written to `of_symbol_baseline` in `ticks.sqlite3` (additive:
`CREATE TABLE IF NOT EXISTS`, nothing existing is altered), keyed
`(symbol, as_of)` so each daily run adds a dated row rather than overwriting
history. Read the current picture from the view **`of_symbol_baseline_latest`**.
1,049 rows written for `as_of = 2026-08-25`.

Why it is needed, in one table:

| symbol | bars | volume median | \|delta\| median | nd p25 | nd p75 | spread bps |
|---|---|---|---|---|---|---|
| NSE:BSE-EQ | 742 | 1,038 | 275 | −0.381 | 0.174 | 1.5 |
| NSE:HDFCBANK-EQ | 744 | 3,630 | 1,071 | −0.235 | 0.450 | 1.4 |
| NSE:NIFTY26AUG24200PE | 385 | 16,900 | 2,470 | −0.059 | 0.200 | 23.7 |
| BSE:SENSEX26AUGFUT | 295 | 2,180 | 1,920 | −1.000 | 0.966 | 4.0 |
| NSE:SAIL26AUG170PE | 21 | 23,500 | 23,500 | −1.000 | 0.600 | 3,897 |

A delta of 2,000 is a routine minute on HDFCBANK, an extreme one on BSE, and
meaningless on SAIL's option where 21 bars is the whole sample. `nd` (delta over
volume) is already scale-free, but its *spread* is not: NIFTY's IQR is
[−0.06, 0.20], SENSEX futures' is [−1.00, 0.97]. The same `nd = 0.5` is a
non-event on one and a session extreme on the other.

### Columns

| column | meaning |
|---|---|
| `symbol`, `as_of`, `symbol_id`, `instrument` | key and class (`EQ`/`FUT`/`OPT`/`INDEX`/`OTHER`) |
| `sessions`, `bars` | **sample size**: sessions, and active minute bars behind every statistic in the row |
| `volume_median/p25/p75` | per-minute traded volume |
| `abs_delta_median/p25/p75` | per-minute \|delta\| — the scale a raw delta should be read against |
| `nd_median/p25/p75` | per-minute delta/volume — the scale-free reading and its own dispersion |
| `trades_median/p25/p75` | per-minute print count |
| `delta_mean`, `delta_sd` | for a z-score, when the desk wants one |
| `ret_sd` | per-minute close-to-close return sd — the volatility unit for sizing |
| `spread_median`, `spread_bps_median`, `price_median` | typical spread, absolute and relative |
| `quote_share`, `classified_share`, `unclassified_trade_share` | **how much of the tape the classifier could place** — the weight to apply to anything derived from delta |
| `estimator`, `updated_at` | which classifier wrote the source bars, and when the row was built |

**Nothing is defaulted.** A statistic below its floor (median: 3 bars, IQR: 8
bars, sd: 3 bars) is written `NULL`, never `0`. Of the 1,049 rows, 7 sit under
20 bars and 3 have no IQR at all; `bars` is there so a consumer can refuse a row
rather than trust a thin one.

### Suggested use

    score = (reading - <stat>_median) / max(<stat>_p75 - <stat>_p25, epsilon)

with the row rejected outright when `bars` is small, `classified_share` is low,
or `estimator` is `legacy_pre_2026-08-27` and the reading is live.

## What should happen next

1. **Re-run this after a session captured by the FIXED classifier.** Every
   number above measures the legacy estimator, and the inverted option sign is
   exactly the failure the 2026-08-27 fix targets. The single cheapest test of
   whether that fix worked is whether the `open_to_close` contemporaneous IC on
   options turns positive. Until it does, option delta should be treated as
   unsigned.
2. **Two sessions is two draws.** The cross-symbol t-statistics are large, but
   they measure consistency across symbols on the same two days, not across
   time. Anything that survives should be re-measured on a rolling window before
   it is believed.
3. **Do not build an entry on this.** Section (c) is unambiguous. The place for
   these features is context and execution, and `rvol` → volatility is the only
   one that stood up cleanly.
