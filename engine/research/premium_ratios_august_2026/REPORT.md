# August premium ratios: no established directional forecasting edge

Research date: 31 August 2026. Status: **partial study completed; full-ladder
validation blocked by missing input coverage and denied Upstox access.**

## Answer

The available two-strike ITM/OTM ratios mostly describe the move already taking
place. They did not provide a consistent lead on the next 30-minute underlying
move. Do not add a directional entry rule from this result.

The archive cannot faithfully reconstruct all three requested ratios together:
there were no eligible three-strike ladders at the fixed historical selection
time. No ATM premiums were synthesized. The calculations below concern actual
two-leg ITM/OTM ratios, not a completed ITM/ATM + ATM/OTM + ITM/OTM study.

## 1. Coverage and provenance

- Used an isolated, read-only SQLite backup of MACD Mini's historical database;
  `PRAGMA quick_check(1)` returned `ok`. Production database/ledger untouched.
- Catalogue: **1,234 August-expiry contracts, 212 underlyings, 3,469,178 August
  minute rows** before expiry/session/quality exclusions. Expiries observed:
  13, 18, 20, 25 and 27 August. NSE monthly expiry was 25 August; BSE 27 August.
- Missing expiry metadata on 185 contracts was resolved from unambiguous
  contract symbols and exchange monthly expiry metadata; no metadata conflicts.
  Post-expiry observations and off-session/invalid OHLC rows were excluded.
- Full-ladder gate: **0 eligible ladders in 7,268 side/expiry/day attempts**.
  At 09:44, positive-volume quote coverage was one strike on 3,879 side-days,
  two strikes on 1,429 side-days, and three or more on zero side-days.
  This does not prove no three-strike overlap existed at every other time.
- There were 600 side-days with two quotes bracketing spot. Selection/quality
  limits yielded 401 frozen two-leg side-days and 114 underlyings with usable
  observations. Complete-minute panel: **11,681 overlapping observations**
  across 5m/15m/30m bars and 15 dates—not 11,681 independent trades.
- Train: 3–13 August; validation: 14–18 August; test: 19–27 August, bounded by
  contract expiry. Actual eligible test coverage is only **2–3 dates**.
- The paired CE+PE primary comparison has training observations but **no
  eligible validation or test observations**. No combined-side forecasting
  conclusion can be drawn. All displayed results are secondary single-side
  comparisons. No cross-expiry or prospective validation is established.

The historical universe was selected by earlier collection workflows, not a
complete point-in-time exchange chain. A physically consistent SQLite file
does not independently certify vendor prices or eliminate selection bias.

## 2. What was calculated

At 09:45 each day, use only quotes available at 09:44 and the historical spot
close. Select the nearest quoted strike strictly below spot and strictly above
spot, each within 2%. Freeze both contracts for the session. For CE, lower
strike = ITM; for PE, higher strike = ITM. Names describe selection-time
moneyness; they are not continuously relabeled as spot moves.

`R = ITM premium close / OTM premium close`

- CE bullish score: `-change(log(R))`.
- PE bullish score: `+change(log(R))`; a negative score predicts a down move.
- Signals use completed, 09:15-anchored bars with every expected source minute,
  positive closing-minute volume and premiums >=₹5 in both consecutive bars.
- Outcomes begin at the **next minute's open**, ending at the fixed 30/60-minute
  horizon. No same-candle fills, overnight labels, stale forward-fills or
  comparisons spanning a strike switch.
- The nearer quoted leg is explicitly only a near-ATM proxy for price controls
  and hypothetical option-return checks. It is not a fabricated third leg.

When a true three-leg ladder is available, `ITM/OTM = (ITM/ATM)*(ATM/OTM)`.
These three ratios are not three independent confirmations.

## 3. Next-30-minute directional accuracy

Fixed, unoptimized ratio-compression rule; chronological test only. Exact-flat
underlying outcomes excluded from hit-rate denominators. Rows overlap in time
and the CE/PE panels are different underlying/date samples.

| Bar interval | CE hit rate | CE observations | PE hit rate | PE observations |
|---|---:|---:|---:|---:|
| 5m | 47.66% | 663 | 44.09% | 372 |
| 15m | 48.44% | 225 | 40.83% | 120 |
| 30m | 52.00% | 100 | 47.92% | 48 |

For 5m observations, simple 30m spot-momentum direction achieved 51.58% on the
CE sample and 44.09% on the PE sample. A constant direction chosen using the
training majority achieved 61.54% and 57.80%, respectively: the test samples
were directionally imbalanced, so a 50% benchmark alone is inadequate.

The ratio-only regression's superficially attractive **61.54% CE accuracy was
exactly the constant-down benchmark**: it predicted down on every test row.
That is not evidence that ratios identified which moves would happen.

## 4. Describing the present versus predicting the future

Spearman rank correlation of the signed compression score with underlying
returns; all matching complete observations, including exact-flat outcomes.

| Bar interval | CE same-bar | CE next 30m | PE same-bar | PE next 30m |
|---|---:|---:|---:|---:|
| 5m | +0.228 | -0.047 | +0.498 | -0.098 |
| 15m | +0.263 | -0.098 | +0.717 | -0.207 |
| 30m | +0.336 | -0.094 | +0.765 | -0.117 |

The current-bar relationship is materially stronger than the forward one.
The negative forward ranks are not, by themselves, validation of a reversal
strategy: samples are short and their signs/stability need fresh testing.
Premiums also depend on volatility, time, strike and underlying price, so a
ratio change is not uniquely a directional-demand measure. See
[OIC's option-price explanation](https://www.optionseducation.org/referencelibrary/faq/option-price-behavior).

## 5. Incremental value and robustness

Fixed ridge models used training-only clipping/scaling and no test-set tuning.
The price/control baseline included spot and quoted-option momentum, session
time/return/range, strike distances and DTE. Adding ratio level/change features:

| Interval | CE baseline → augmented accuracy | PE baseline → augmented accuracy |
|---|---:|---:|
| 5m | 54.90% → 54.90% | 49.73% → 46.77% |
| 15m | 51.11% → 47.56% | 53.33% → 59.17% |
| 30m | 49.00% → 52.00% | 56.25% → 50.00% |

Isolated improvements do not persist across sides and intervals. In particular,
the 15m PE equal-date lift was +2.67 percentage points, with date-resampling
interval [-3.45, +8.79]. Only two dates underlie that interval.

The 5m CE lift remains zero after requiring premiums >=₹10 or DTE>=2. The PE
5m lift stays negative with the higher-premium filter. Removing DTE<2 leaves
only one PE test date. There were no eligible expiry-day rows, so excluding
expiry day cannot demonstrate expiry robustness.

Relaxing source-minute coverage to >=80% gives compression-rule accuracies:
CE 47.48%, 49.38%, 50.47%; PE 44.09%, 40.50%, 47.92%, at 5m/15m/30m. The main
interpretation is unchanged. The 60m outcomes are recorded in the CSVs and
also fail to establish a stable edge.

Date-clustered resampling (2,000 draws) avoids treating overlapping bars as
independent. With only 2–3 test dates, even these intervals are descriptive and
cannot establish generalization across market regimes. No multiplicity-adjusted
claim is made from the many prespecified comparisons.

## 6. Hypothetical option-return check

Buy the actual quoted near-ATM proxy only for direction-aligned compression
scores above the TRAIN 75th percentile of absolute scores. Use a fixed 30m
decision grid, no overlapping same-side/underlying trade within each lane, and
require a complete future minute path. These are independent lane experiments,
not a funded portfolio backtest.

| Signal interval / side | Candidate signals | Usable trades | Mean gross return | After assumed 1% round-trip premium cost |
|---|---:|---:|---:|---:|
| 5m CE, hold 30m | 26 | 25 | -5.40% | -6.40% |
| 5m PE, hold 30m | 19 | 15 | -2.90% | -3.90% |

Means are equal-trade-weighted; the PE equal-date mean differs because one of
its two dates has very few observations. Missing future outcomes are disclosed
as attrition, not filled in. All fixed-rule test rows remain negative on pooled
mean after the assumed 1% cost; 0.5% and 2% scenarios are in `trade_metrics.csv`.
No observed bid/ask, historical lot-size/fee reconstruction, fill depth, capital
constraint or execution guarantee is available. These are cost sensitivities,
not measured executable net P&L.

## 7. Upstox via Nomad Curie: data-access blocker

The user supplied Nomad Curie as the credential source. Its saved analytics
credential and a stored OAuth credential were found; the OAuth JWT expiry
claim was current. Credentials stayed in memory and were never printed/saved.

- Docker-side API attempt: DNS resolution failure.
- Host-side expired-contract and ordinary historical-data requests: HTTP 403.
- Explicit response: Cloudflare **1010**, `browser_signature_banned`;
  `retryable=false`, `owner_action_required=true`. Requests stopped after that
  explicit denial. No user-agent spoofing or alternate access-rule bypass.
- No August Upstox candles were downloaded; no subscription was changed.
- Nomad's checked local compressed index archive had no `expiry=2026-08`
  files; its separate inspected catalogue files were last written in June.
  No claim is made that every other Nomad database contains no August data.

This is NOT evidence that the token is expired or that Plus is absent.
An approved API connection/site-owner resolution is needed before testing
expired-data entitlement. Upstox documents expired-contract access under
[its Plus endpoints](https://upstox.com/developer/api-documentation/get-expired-option-contracts/).
See [Cloudflare error 1010](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1010/).

## 8. Next decision and reproduction

Keep ratios as descriptive research context, not a production entry gate.
Once approved expired-history access works, acquire complete point-in-time
three-strike CE/PE ladders, retain quote/liquidity provenance and repeat the
frozen protocol on additional expiries/prospective dates. Do not optimize
thresholds on this already-inspected test sample.

Artifacts:

- `audit.json` and `coverage_probe.json`: full-ladder data gate.
- `two_leg/observations.csv.gz`: actual computed ITM/OTM observations.
- `two_leg/ladders.csv`: exact contracts and historical selection times.
- `two_leg/direction_metrics.csv`: every train/validation/test result.
- `two_leg/feature_correlations.csv`: contemporaneous versus forward ranks.
- `two_leg/incremental_lift.csv`: paired baseline deltas and sensitivities.
- `two_leg/trade_metrics.csv`, `test_trade_observations.csv`, `test_by_date.csv`,
  `test_by_symbol.csv`: cost assumptions, attrition and concentration.
- `two_leg/frozen_models.json`: training parameters and fixed thresholds.

From the repository root, with Python providing numpy and pandas:

```sh
python -m unittest discover -s research -p 'test_premium_ratio_research.py' -v
python research/premium_ratio_research.py --database research/premium_ratios_august_2026/snapshot-20260831.sqlite3 --output research/premium_ratios_august_2026/two_leg --two-leg --reuse-observations
```

13 synthetic tests passed. No trading code, credentials, positions, strategy
settings or running services were changed by this research.
