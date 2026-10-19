"""Render the declared experiment's saved outputs; no policy evaluation here."""
import argparse
import csv
import datetime as dt
import gzip
import json
import pathlib
from zoneinfo import ZoneInfo

HERE=pathlib.Path(__file__).resolve().parent
IST=ZoneInfo("Asia/Kolkata")
arg=argparse.ArgumentParser();arg.add_argument("--audited",action="store_true");args=arg.parse_args()
summary=json.loads(HERE.joinpath("evaluation_summary.json").read_text())
collection=json.loads(HERE.joinpath("collection_summary.json").read_text())
with HERE.joinpath("comparison.csv").open() as f:rows=list(csv.DictReader(f))
events=json.loads(HERE.joinpath("trigger_events.json").read_text())
with gzip.open(HERE/"inputs.json.gz","rt") as f:inputs=json.load(f)
allrows=[r for h in inputs["histories"].values() for r in h["rows"]]
metadata={"unique_option_minute_rows":len(allrows),"expiry_null_rows":sum(r[7] is None for r in allrows),
    "asset_types":sorted({r[6] for r in allrows}),"downloaded_at_min":min(r[8] for r in allrows),"downloaded_at_max":max(r[8] for r in allrows)}
HERE.joinpath("metadata_quality.json").write_text(json.dumps(metadata,indent=2))
def cash(x):return ("-" if x<0 else "+" if x>0 else "")+"₹"+format(abs(x),",.0f")
def local(x):return dt.datetime.fromtimestamp(float(x),IST).strftime("%d %b %H:%M")
names={"band_failure":"Band extreme → failure","divergence_failure":"Confirmed divergence → failure","gain_giveback50":"Original +50% / 25% reference"}
primary=summary["primary"]
status=("Independent audit passed: **104,244 assertions, 13,869 numeric checks, 1,563 feature bars, 234 prefix checks and 354 cash rows; zero errors**. See the [detector/accounting audit receipt](../exhaustion_review/detector_accounting_audit.json)." if args.audited else "**Provisional: independent detector and accounting audit is still pending.**")
parts=[f"""# Option-price exhaustion: frozen paper experiment

{status}

This tests the exact held option's premium extreme and subsequent failure, or a confirmed momentum divergence and subsequent failure. It does not identify the future maximum in advance. Definitions were frozen in [PLAN.md](PLAN.md) before evaluating outcomes; no thresholds were searched or changed. Calls and puts both use rising **option premium**, so the signal has the same interpretation for either long option.

All work is offline research. Production code, settings, holdings and ledgers are unchanged. These prototypes sell **all remaining whole lots** after retaining earlier actual partial cash. A proposed future half-harvest plus runner schedule is **untested** here.

## Inputs, context and common cohort

The frozen original single-BUY sample contains {collection['positions']} completed lifecycles on {collection['unique_contract_symbols']} exact contracts. The new indexed read-only extraction contains {collection['unique_minute_rows']:,} unique one-minute OHLCV rows, from 24 September through 9 October 2026. Three prior derivative sessions were requested. No extra ten-session queries were needed: every position had at least 100 complete three-minute bars somewhere before entry, but only {collection['preentry_last100_grid_bins_complete_positions']} had 100 consecutive complete bars immediately before entry.

The session grid is 09:15–15:40 IST under the post-3-August-2026 derivatives regime; 2 October is excluded. Three-minute bars align to 09:15 and require all constituent minute rows. The one-minute tail at 15:39 is not fabricated into a three-minute bar. Missing/invalid expected three-minute bars reset indicators and pivot state; legitimate overnight/weekend/holiday gaps do not. The session/calendar sources match the original evidence hashes, and their full text plus regime source are frozen in the new input. No prior-regime or pre-2026 data is blended into this sample.

The {collection['holding_complete_valid_positions']} original holding paths with complete exact-minute coverage are unchanged: comparison with the earlier frozen input found **zero changed, added or removed overlapping minute rows**. The 100-consecutive-bar warm-up and original holding-minute gates together retain {summary['quality_eligible_positions_before_zero_volume_filter']} lifecycles; {summary['quality_excluded_positions']} fail at least one gate. Exclusion reasons overlap: 61 lack the required immediate warm-up, 64 have original holding-minute gaps and eight are subminute. Collector and detector warm-up calculations disagree on **{summary['warmup_gate_disagreements']}** positions.

An additional {summary['zero_volume_positions_removed_from_every_policy']} positions have a zero-volume hypothetical fill minute under at least one compared policy. Removing each from **every** policy leaves **{summary['primary_shared_positions']} identical entries, quantities and baseline exits**: 84 MACD, 23 Blast and four Smart ₹50L. Twelve retain actual partial-sale lifecycles. This differs from the earlier 145-position study: stricter warm-up and the common three-policy fill filter change the cohort. Compare the new policies with the reference recomputed on **111**, not with the earlier aggregate on 145.

Positive full-minute fill volume is a retrospective data-quality filter, not information available at that minute's opening print. It does not prove opening liquidity, spread or depth. The unfiltered sensitivity is saved separately.

## Exactly what was tested

- **Band extreme → failure:** at a completed post-entry three-minute close, arm above the preceding-20 upper band with RSI14 ≥70. For ten subsequent complete bars in the same session, exit only after a later close returns below the contemporaneous prior-20 upper band and below the preceding bar's low. New extremes refresh arming. Signal volume and estimated residual net liquidation profit must be positive.
- **Confirmed divergence → failure:** a premium swing high requires two strictly lower highs on each side and becomes available only when the second right-hand bar closes. Consecutive confirmed highs must be separated by 3–20 complete bars; the newer must be at least 0.2% higher with RSI ≥60, while RSI falls ≥5 points or MACD histogram falls to ≤80% of an earlier positive histogram. Within ten bars of confirmation in the same session, a close below the intervening neckline can exit, with positive signal volume and residual net liquidation profit. A break already present at confirmation is evaluated at confirmation, never backdated.
- **Original reference:** preserve the earlier completed **one-minute** +50% arm and 25% favorable-profit-giveback rule. It receives no new RSI, divergence, signal-volume or residual-profit gates. This exception preserves the named original rule instead of silently changing the reference.

New entries, changes in entry size, additions and reinvestment are not replayed. The runner preserves actual entry price/quantity, all actual sales at or before the alternative fill, and their proceeds. It sells only the remaining whole-lot quantity and replaces later sales. Both choices pay ₹20 per entry/sale order; only new modeled exits receive 5 bps adverse slippage, once. Existing actual fills already contain their production slippage. The stress scenario adds 20 bps to every exit proceed under both choices. The recorded-fee scenario is retained separately. Statutory taxes, spread, impact and depth are incomplete.

## Same-cohort results

All amounts below use the standardized ₹20/order scenario. The common actual-exit net is **{cash(primary['band_failure']['all']['baseline_net'])}**.

| Alternative | Changed / 111 | Improved / worsened | Net result | Difference from actual | Difference under added 20 bps exit stress |
|---|---:|---:|---:|---:|---:|
"""]
for policy in names:
    a=primary[policy]["all"]
    parts.append(f"| {names[policy]} | {a['triggered']} | {a['improved']} / {a['worsened']} | {cash(a['alternative_net'])} | {cash(a['delta'])} | {cash(a['stress_delta'])} |\n")
parts.append("\nEvery alternative remains net negative. A higher retrospective difference is not an established strategy edge or a probability of finding a maximum.\n\n| Book | Positions | Band difference | Divergence difference | Same-cohort reference difference |\n|---|---:|---:|---:|---:|\n")
for lane,label in (("macd_trader","MACD"),("blast_trader","Blast"),("smart_50l","Smart ₹50L")):
    a=primary["band_failure"]["by_lane"][lane]
    parts.append(f"| {label} | {a['positions']} | {cash(a['delta'])} | {cash(primary['divergence_failure']['by_lane'][lane]['delta'])} | {cash(primary['gain_giveback50']['by_lane'][lane]['delta'])} |\n")
parts.append(f"""
The book tradeoffs differ. Band protection improves the retained MACD cohort while harming Blast. Divergence improves Blast while slightly harming MACD. These observations do not authorize selecting the best policy separately for each book after seeing outcomes.

The twelve actual-partial lifecycles have baseline net **{cash(primary['band_failure']['by_cohort']['actual_partial_sells']['baseline_net'])}**. Band changes all twelve with a net difference of **{cash(primary['band_failure']['by_cohort']['actual_partial_sells']['delta'])}**; divergence changes four with a difference of **{cash(primary['divergence_failure']['by_cohort']['actual_partial_sells']['delta'])}**. The old reference changes five with a difference of **{cash(primary['gain_giveback50']['by_cohort']['actual_partial_sells']['delta'])}**. Early protection therefore also cuts runners that later recover or extend.

Summed negative differences are **{cash(primary['band_failure']['all']['sum_negative_deltas'])}** for band, **{cash(primary['divergence_failure']['all']['sum_negative_deltas'])}** for divergence and **{cash(primary['gain_giveback50']['all']['sum_negative_deltas'])}** for the reference. Each harmed lifecycle in this run was profitable under actual exits; that statement was checked against baseline net rather than inferred from the field label. Their eventual actual profits were foregone in varying amounts. Future highs are not used by either detector.

The zero-volume-permitted sensitivity has 118 positions and gives differences of {cash(summary['unfiltered_zero_volume_sensitivity']['band_failure']['all']['delta'])}, {cash(summary['unfiltered_zero_volume_sensitivity']['divergence_failure']['all']['delta'])} and {cash(summary['unfiltered_zero_volume_sensitivity']['gain_giveback50']['all']['delta'])}, respectively. These larger optimistic figures are not the headline.

## Deterministic examples

These are the two largest favorable and two most harmful primary differences per extreme detector, sorted by difference then position key. They illustrate failure modes; they were not used to adjust definitions. Signal times are IST, at completed-bar close; fills use the next eligible minute open, which may share that close timestamp.

| Detector / example | Exact option | Signal IST | Prior actual sales | Residual quantity | Actual lifecycle net | Alternative lifecycle net | Difference |
|---|---|---|---:|---:|---:|---:|---:|
""")
example_rows=[]
for policy in ("band_failure","divergence_failure"):
    selected=sorted([r for r in rows if r["policy"]==policy and r["triggered"]=="True"],key=lambda r:(float(r["delta"]),r["position_key"]))
    for label,chosen in (("most harmful",selected[:2]),("most favorable",list(reversed(selected[-2:])))):
        for r in chosen:
            parts.append(f"| {names[policy]} / {label} | {r['symbol']} | {local(r['decision_ts'])} | {r['prior_actual_sell_count']} | {r['remaining_quantity']} | {cash(float(r['actual_net']))} | {cash(float(r['alternative_net']))} | {cash(float(r['delta']))} |\n")
            example_rows.append({**r,"event":events[r["position_key"]+"::"+policy]})
HERE.joinpath("example_events.json").write_text(json.dumps(example_rows,indent=2))
parts.append(f"""
For example, band failure protected ICICIBANK from a large later loss after its premium spike failed, but sold NIFTY 22700PE near ₹160.22 before the actual eventual exit produced much more profit. Confirmed divergence protected TVSMOTOR, but exited the TATASTEEL residual after one actual partial sale and forfeited ₹31,185 of the eventual actual lifecycle net. The complete as-of feature values, swing confirmation times and reasons are in `example_events.json` / `trigger_events.json`.

## Sentiment and availability boundary

The tested input schema is exact option OHLCV, asset type, expiry and historical `downloaded_at`. It contains no IV, Greeks, OI, PCR, skew, aggressor side, signed flow or sentiment. All {metadata['unique_option_minute_rows']:,} rows identify asset type `option`, but expiry metadata is NULL in {metadata['expiry_null_rows']:,} rows; no DTE-normalized rule was claimed or tested. Option volume alone cannot identify opening bullish demand, short covering, hedging or spread activity.

The [timestamped availability audit](../exhaustion_review/AVAILABILITY.md) finds direct chain context for only nine of the 220 original lifecycles; 211 stock/SENSEX lifecycles lack it. All 3,471 bounded composite fields are NULL, with only 0–6 prior sessions against 20 needed. Existing collector timestamps precede fetching and do not establish an `available_at` time. No causal chain/sentiment feature is credited to these results, and missing context is not neutral sentiment. The [research synthesis](../exhaustion_review/RESEARCH.md) and [prospective specification](../exhaustion_review/PROSPECTIVE.md) cover optional future as-of context joins.

Historical `downloaded_at` can postdate the trade/decision. The frozen OHLCV reconstructs market bars, not proof that every feature was present in the live engine then. Original fills lack historical source-mode provenance. The present sample is retrospective, mixed-policy and selected for completed single-BUY paths; open winners, additions, missing data and short warm-up are excluded. There is no untouched holdout, out-of-sample parameter selection or full production portfolio replay. The experiment offers inspectable candidate behavior; it does not validate production promotion or claim to detect an absolute maximum.

## Reproduction and receipts

- `collect_context.py`, `inputs.json.gz`, `collection_summary.json`, `lookback_coverage.csv` and `metadata_quality.json`: frozen source data, exact schemas, query plan, source hashes and missingness.
- `PLAN.md`, `declaration.json`, `detector.py`: declared rules and causal feature implementation.
- `evaluate.py`, `evaluation_summary.json`, `comparison.csv`, `comparison_optimistic.csv`, `trigger_events.json`, `quality_exclusions.csv`, `warmup_checks.csv`: identical-cohort accounting and decisions. Evaluation summary records plan/detector/runner/input hashes.
- `build_findings.py` and `example_events.json`: deterministic report construction and examples.

Recompute only from the frozen input with the bundled Python:

```sh
/Users/ramachandran/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 /Users/ramachandran/MACD-mini-ADV/research/extreme_profit_taking_20261009/exhaustion_analysis/evaluate.py
```

Refreshing the collector is a new snapshot, not reproduction of these frozen inputs.
""")
HERE.joinpath("FINDINGS.md").write_text("".join(parts))
print(HERE/"FINDINGS.md")
