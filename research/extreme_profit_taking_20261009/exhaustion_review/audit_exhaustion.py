"""Independent frozen-input, indicator, prefix, and cash audit; research only.

Reads frozen artifacts and imports only the side-effect-free research detector.
No application imports, broker calls, SQL writes, or strategy changes.
"""
import collections
import csv
import datetime as dt
import gzip
import hashlib
import importlib.util
import json
import math
import pathlib
import random
import statistics
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE.parent / "exhaustion_analysis"
IST = ZoneInfo("Asia/Kolkata")
spec = importlib.util.spec_from_file_location("audited_detector", DATA / "detector.py")
detector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(detector)
inputs = json.loads(gzip.decompress((DATA / "inputs.json.gz").read_bytes()))
summary = json.loads((DATA / "evaluation_summary.json").read_text())
events = json.loads((DATA / "trigger_events.json").read_text())
rows = list(csv.DictReader((DATA / "comparison_optimistic.csv").open()))
primary = list(csv.DictReader((DATA / "comparison.csv").open()))
errors = []
checks = collections.Counter()


def assert_true(value, label):
    checks["assertions"] += 1
    if not value:
        errors.append(label)


def near(actual, expected, label, tolerance=1e-8):
    checks["numeric_reconciliations"] += 1
    if actual is None or expected is None:
        assert_true(actual is None and expected is None, label + " missingness")
    elif abs(float(actual) - float(expected)) > tolerance * max(1, abs(float(expected))):
        errors.append({"label": label, "actual": actual, "expected": expected})


def timestamp(s):
    return dt.datetime.fromisoformat(s).timestamp()


def allowed(t):
    x = dt.datetime.fromtimestamp(t, IST)
    return x.weekday() < 5 and x.date() != dt.date(2026, 10, 2) and (9, 15) <= (x.hour, x.minute) < (15, 40)


def expected_3m(start, end):
    day = dt.datetime.fromtimestamp(start, IST).date()
    last = dt.datetime.fromtimestamp(end, IST).date()
    result = []
    while day <= last:
        opening = int(dt.datetime.combine(day, dt.time(9, 15), IST).timestamp())
        if allowed(opening):
            for i in range(128):
                t = opening + i * 180
                if t >= start and t + 180 <= end:
                    result.append(t)
        day += dt.timedelta(days=1)
    return result


def valid_minute(r):
    return (all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in r[1:5])
            and math.isfinite(r[5]) and r[5] >= 0
            and r[2] >= max(r[1], r[4]) and r[3] <= min(r[1], r[4]))


def independent_ema(values, period, index, alpha=None):
    """Closed-form weighted sum, independent of detector's stateful updates."""
    if index < period - 1:
        return None
    alpha = 2 / (period + 1) if alpha is None else alpha
    seed = sum(values[:period]) / period
    k = index - period + 1
    return seed * (1 - alpha) ** k + sum(
        alpha * values[j] * (1 - alpha) ** (index - j) for j in range(period, index + 1))


def check_features(bars, label):
    """Reconcile every valid segment against explicit standard formulas."""
    segment = []
    for b in bars:
        if not b["valid"]:
            segment = []
            continue
        segment.append(b)
        i = len(segment) - 1
        if i > 40 and i % 23:
            continue
        closes = [x["close"] for x in segment]
        prior = closes[-21:-1]
        upper = statistics.fmean(prior) + 2 * statistics.pstdev(prior) if len(prior) == 20 else None
        near(b["upper_band"], upper, label + " preceding20 population band")
        near(b["ema20"], independent_ema(closes, 20, i), label + " EMA20")
        gains = [max(0, closes[j] - closes[j - 1]) for j in range(1, len(closes))]
        losses = [max(0, closes[j - 1] - closes[j]) for j in range(1, len(closes))]
        ag = independent_ema(gains, 14, i - 1, 1 / 14)
        al = independent_ema(losses, 14, i - 1, 1 / 14)
        expected_rsi = (None if ag is None else 50 if ag == al == 0 else
                        100 if al == 0 else 100 - 100 / (1 + ag / al))
        near(b["rsi14"], expected_rsi, label + " Wilder RSI14")
        tr = [x["high"] - x["low"] if j == 0 else max(x["high"] - x["low"],
              abs(x["high"] - closes[j - 1]), abs(x["low"] - closes[j - 1]))
              for j, x in enumerate(segment)]
        near(b["atr14"], independent_ema(tr, 14, i, 1 / 14), label + " Wilder ATR14")
        near(b["atr14_previous"], independent_ema(tr, 14, i - 1, 1 / 14), label + " preceding ATR14")
        fast, slow = independent_ema(closes, 12, i), independent_ema(closes, 26, i)
        macd = fast - slow if slow is not None else None
        near(b["macd"], macd, label + " MACD12/26")
        macds = [independent_ema(closes, 12, j) - independent_ema(closes, 26, j)
                 for j in range(25, i + 1)]
        signal = independent_ema(macds, 9, len(macds) - 1)
        near(b["hist"], macd - signal if signal is not None else None, label + " MACD signal9 histogram")
        checks["independently_reconciled_feature_bars"] += 1


# Check every lifecycle's 100 immediately preceding expected bins and frozen rows.
enriched = {}
for key, p in inputs["positions"].items():
    raw_rows = inputs["histories"][p["symbol"]]["rows"]
    raw = {r[0]: r for r in raw_rows}
    bins = expected_3m(p["context_start"], p["entry_ts"])
    trailing = 0
    for t in reversed(bins):
        if not all(t + i * 60 in raw and valid_minute(raw[t + i * 60]) for i in range(3)):
            break
        trailing += 1
    assert_true((trailing >= 100) == p["coverage"]["preentry_last_100_grid_bins_complete"], key + " trailing100 collector")
    for r in p["original_holding_rows"]:
        assert_true(r == raw[r[0]][:6], key + " original holding unchanged")
    bars = detector.enrich(detector.build_bars(raw_rows, p["context_start"], max(raw) + 60))
    pre = [b for b in bars if b["end"] <= p["entry_ts"]]
    warm = bool(pre and pre[-1]["valid"] and pre[-1]["count"] >= 100)
    assert_true(warm == (trailing >= 100), key + " detector trailing100")
    enriched[key] = bars
    checks["lifecycle_input_and_warmup_checks"] += 1

# Sample all three lanes and irregular paths; deterministic selection, not outcomes.
sample_keys = list(inputs["positions"])[::17]
for key in sample_keys:
    bars = enriched[key]
    p = inputs["positions"][key]
    check_features(bars, key)
    for n in sorted({1, 20, 34, 99, 100, len(bars) // 3, len(bars) // 2, len(bars) - 1, len(bars)}):
        if n < 1 or n > len(bars):
            continue
        plain = [{k: v for k, v in b.items() if k in ("start", "end", "session", "valid", "open", "high", "low", "close", "volume")} for b in bars[:n]]
        prefix = detector.enrich(plain)
        assert_true(prefix == bars[:n], key + " feature prefix invariant " + str(n))
        for policy in ("band_failure", "divergence_failure"):
            args = (p["entry_ts"], p["actual_final_exit_ts"], p["single_entry"]["price"], p["single_entry"]["quantity"], p["actual_sells"], policy)
            full_event = detector.first_exit(bars, *args)
            prefix_event = detector.first_exit(prefix, *args)
            expected_event = full_event if full_event and full_event["decision_ts"] <= prefix[-1]["end"] else None
            assert_true(prefix_event == expected_event, key + " signal prefix invariant " + policy + " " + str(n))
            checks["policy_prefix_cases"] += 1

# Hand-check strict pivot confirmation timing and missing-bar resets.
opening = int(dt.datetime(2026, 9, 29, 9, 15, tzinfo=IST).timestamp())
fixture = [dict(start=opening + i * 180, end=opening + (i + 1) * 180, session="2026-09-29", valid=True,
                open=1., high=h, low=.5, close=1., volume=100.) for i, h in enumerate((2., 3., 8., 4., 5., 2., 9., 4., 3.))]
f = detector.enrich(fixture)
assert_true(all(b["confirmed_pivot"] is None for b in f[:4]), "pivot not published before two right bars")
assert_true(f[4]["confirmed_pivot"]["start"] == fixture[2]["start"] and f[4]["confirmed_pivot"]["available_at"] == fixture[4]["end"], "pivot published at second right bar close")
fixture[4]["high"] = 8.
assert_true(detector.enrich(fixture)[4]["confirmed_pivot"] is None, "equal high forbids strict pivot")
fixture[3] = {k: fixture[3][k] for k in ("start", "end", "session")}
fixture[3]["valid"] = False
f = detector.enrich(fixture)
assert_true(f[4]["count"] == 1 and f[4]["confirmed_pivot"] is None and f[4]["rsi14"] is None, "missing bar resets count indicators and pivots")

# Reconstruct every result from frozen actual cash and the new open fill.
for r in rows:
    key = r["position_key"]
    p = inputs["positions"][key]
    buy, sells = p["single_entry"], p["actual_sells"]
    qty, price = buy["quantity"], buy["price"]
    original = {x[0]: x for x in p["original_holding_rows"]}
    actual_proceeds = sum(s["quantity"] * s["price"] for s in sells)
    actual_fees = 20 * (1 + len(sells))
    near(r["actual_net"], actual_proceeds - qty * price - actual_fees, key + " baseline cash")
    near(r["actual_recorded_fee_net"], actual_proceeds - qty * price - buy["fees"] - sum(s["fees"] for s in sells), key + " baseline recorded fees")
    if r["triggered"] == "True":
        decision, fill = float(r["decision_ts"]), float(r["fill_ts"])
        assert_true(p["entry_ts"] <= decision <= fill <= p["actual_final_exit_ts"], key + " decision/fill horizon")
        expected_fill = math.ceil(decision / 60) * 60
        while not allowed(expected_fill):
            expected_fill += 60
        near(fill, expected_fill, key + " immediate next eligible open")
        prior = [s for s in sells if timestamp(s["timestamp"]) <= fill]
        residual = qty - sum(s["quantity"] for s in prior)
        assert_true(residual > 0 and residual % buy["lot_size"] == 0, key + " positive whole residual")
        near(r["remaining_quantity"], residual, key + " residual conservation")
        prior_cash = sum(s["quantity"] * s["price"] for s in prior)
        new_price = original[int(fill)][1] * .9995
        alt_proceeds = prior_cash + residual * new_price
        alt_fees = 20 * (2 + len(prior))
        near(r["prior_actual_proceeds"], prior_cash, key + " prior sale cash retained")
        near(r["hypothetical_residual_proceeds"], residual * new_price, key + " residual proceeds")
        near(r["new_exit_price"], new_price, key + " exit slip once")
        near(r["alternative_net"], alt_proceeds - qty * price - alt_fees, key + " alternative cash/fees")
        event = events[key + "::" + r["policy"]]
        near(event["decision_ts"], decision, key + " event decision")
        if r["policy"] != "gain_giveback50":
            b = next(b for b in enriched[key] if b["end"] == decision)
            remaining_at_signal = qty - sum(s["quantity"] for s in sells if timestamp(s["timestamp"]) <= decision)
            gate = (b["close"] * .9995 - price) * remaining_at_signal - 20 - 20 * remaining_at_signal / qty
            assert_true(b["volume"] > 0 and gate > 0, key + " positive signal volume and residual net")
            if r["policy"] == "divergence_failure":
                setup = event["setup"]
                assert_true(setup["pivot_later"]["available_at"] <= decision, key + " pivot confirmed before decision")
                assert_true(setup["pivot_later"]["start"] >= p["entry_ts"], key + " newer pivot postentry")
        checks["triggered_cash_and_time_rows"] += 1
    else:
        alt_proceeds, alt_fees = actual_proceeds, actual_fees
        near(r["alternative_net"], r["actual_net"], key + " untriggered unchanged")
    near(r["delta"], alt_proceeds - alt_fees - actual_proceeds + actual_fees, key + " incremental cash")
    near(r["actual_stress_net"], actual_proceeds * .998 - qty * price - actual_fees, key + " equal actual stress")
    near(r["alternative_stress_net"], alt_proceeds * .998 - qty * price - alt_fees, key + " equal alternative stress")
    near(r["stress_delta"], float(r["alternative_stress_net"]) - float(r["actual_stress_net"]), key + " stress difference")
    checks["accounting_rows"] += 1
bad = {r["position_key"] for r in rows if r["triggered"] == "True" and float(r["fill_bar_volume"]) <= 0}
assert_true({(r["position_key"], r["policy"]) for r in primary} == {(r["position_key"], r["policy"]) for r in rows if r["position_key"] not in bad}, "common three-policy paired quality filter")
for policy in ("band_failure", "divergence_failure", "gain_giveback50"):
    selected = [r for r in primary if r["policy"] == policy]
    for field in ("actual_net", "alternative_net", "delta", "stress_delta"):
        target_field = "baseline_net" if field == "actual_net" else field
        near(round(sum(float(r[field]) for r in selected), 2), summary["primary"][policy]["all"][target_field], policy + " aggregate " + field)

sha = lambda f: hashlib.sha256((DATA / f).read_bytes()).hexdigest()
result = {"audited_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "checks": dict(checks),
          "input_lifecycles": len(inputs["positions"]), "indicator_sample_lifecycles": len(sample_keys),
          "primary_positions": len(primary) // 3, "optimistic_positions": len(rows) // 3,
          "files_sha256": {f: sha(f) for f in ("PLAN.md", "detector.py", "evaluate.py", "inputs.json.gz", "comparison.csv", "comparison_optimistic.csv", "evaluation_summary.json")},
          "error_count": len(errors), "errors": errors}
(HERE / "detector_accounting_audit.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
