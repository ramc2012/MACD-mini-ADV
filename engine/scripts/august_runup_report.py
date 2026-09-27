"""Summarise august_runup_research.json: who ran, and what the spot was doing.

Reads the episode dump produced by august_runup_research.py and prints the
comparison that matters -- the spot's state at the onset of a >=100% premium
advance, against the same measurements taken over every spot bar in the same
window. A feature is only interesting where those two distributions differ.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

PAYLOAD = Path("/app/runtime/august_runup_research.json")


def quantiles(values: list[float]) -> dict:
    clean = sorted(v for v in values if v is not None and v == v)
    if not clean:
        return {}
    def at(q):
        return clean[min(len(clean) - 1, int(q * len(clean)))]
    return {"n": len(clean), "p10": at(0.10), "p25": at(0.25), "median": at(0.50),
            "p75": at(0.75), "p90": at(0.90), "mean": statistics.fmean(clean)}


def line(name: str, onset: dict, base: dict) -> str:
    if not onset:
        return f"| {name} | - | - | - | - |"
    def fmt(x):
        return "-" if x is None else f"{x:,.2f}"
    lift = ""
    if base and base.get("median") is not None and onset.get("median") is not None:
        lift = f"{onset['median'] - base['median']:+,.2f}"
    return (f"| {name} | {fmt(onset['median'])} | {fmt(base.get('median'))} | {lift} | "
            f"{fmt(onset['p25'])} .. {fmt(onset['p75'])} |")


def main() -> int:
    data = json.loads(PAYLOAD.read_text())
    episodes = data["episodes"]
    baseline = data["baseline_bars"]
    print(f"# August 2026 series: premium advances of +100% or more\n")
    print(f"Generated {data['generated_at']}  ·  series {data['series']}")
    p = data["parameters"]
    print(f"Timeframe {p['timeframe_seconds']//60}m  ·  onset premium >= Rs {p['min_onset_premium']:.0f}  "
          f"·  |moneyness| <= {p['max_abs_moneyness_pct']:.0f}%")
    print(f"Contracts in series {data['contracts_in_series']}, scanned {data['contracts_scanned']}, "
          f"underlyings covered {data['spots_covered']}")
    print(f"\n**{len(episodes)} qualifying advances** across "
          f"{len({e['symbol'] for e in episodes})} contracts and "
          f"{len({e['root'] for e in episodes})} underlyings.\n")

    # ---- the instruments -------------------------------------------------
    print("## Biggest advances\n")
    print("| Contract | Onset IST | DTE | Entry Rs | Peak Rs | Return | Hours held |")
    print("|---|---|---:|---:|---:|---:|---:|")
    for e in sorted(episodes, key=lambda x: -x["tradable_pct"])[:25]:
        print(f"| {e['symbol']} | {e['onset_ist'][:16].replace('T',' ')} | {e['days_to_expiry']:.1f} | "
              f"{e['onset_premium']:,.2f} | {e['peak_premium']:,.2f} | {e['tradable_pct']:,.0f}% | "
              f"{e['hours_to_peak']:.1f} |")

    print("\n## Where the advances came from\n")
    by_root = Counter(e["root"] for e in episodes)
    print("Underlyings with the most qualifying advances: " +
          ", ".join(f"{root} ({count})" for root, count in by_root.most_common(12)))
    sides = Counter(e["side"] for e in episodes)
    print(f"\nCE {sides['CE']} · PE {sides['PE']}")

    buckets = defaultdict(list)
    for e in episodes:
        dte = e["days_to_expiry"]
        key = ("0-1 (expiry day)" if dte <= 1 else "1-3" if dte <= 3 else "3-7" if dte <= 7
               else "7-14" if dte <= 14 else "14-30" if dte <= 30 else "30+")
        buckets[key].append(e)
    print("\n| Days to expiry at onset | Advances | Median return | Median hours held | Median entry Rs |")
    print("|---|---:|---:|---:|---:|")
    for key in ("0-1 (expiry day)", "1-3", "3-7", "7-14", "14-30", "30+"):
        rows = buckets.get(key) or []
        if not rows:
            continue
        print(f"| {key} | {len(rows)} | {statistics.median(r['tradable_pct'] for r in rows):,.0f}% | "
              f"{statistics.median(r['hours_to_peak'] for r in rows):.1f} | "
              f"{statistics.median(r['onset_premium'] for r in rows):,.1f} |")

    by_time = Counter(e.get("onset_time_ist", "?") for e in episodes)
    print("\n| Onset bar (IST) | Advances | Share |")
    print("|---|---:|---:|")
    for slot, count in sorted(by_time.items()):
        print(f"| {slot} | {count} | {100*count/len(episodes):.1f}% |")

    by_date = Counter(e["onset_date"] for e in episodes)
    print("\n| Onset date | Advances |")
    print("|---|---:|")
    for day, count in sorted(by_date.items()):
        print(f"| {day} | {count} |")

    # ---- the spot state --------------------------------------------------
    print("\n## Spot state at the onset bar, against every spot bar in the window\n")
    print("Direction-adjusted: PE readings are flipped so \"higher\" always means "
          "\"in favour of the option\". The baseline is undirected, so it sits near "
          "the neutral value by construction.\n")
    print(f"Onset observations: {len(episodes):,} · baseline bars: {len(baseline):,}\n")
    print("| Spot feature at onset | Onset median | Baseline median | Shift | Onset p25..p75 |")
    print("|---|---:|---:|---:|---|")

    aligned = [e["aligned"] for e in episodes]
    base_rows = baseline

    def base_neutral(key, transform):
        return quantiles([transform(r) for r in base_rows])

    rows = [
        ("Close vs EMA20 (ATR)", [a["close_vs_ema20_atr"] for a in aligned],
         base_neutral(None, lambda r: (r["close"] - r["ema20"]) / r["atr"] if r["atr"] else None)),
        ("Close vs EMA50 (ATR)", [a["close_vs_ema50_atr"] for a in aligned],
         base_neutral(None, lambda r: (r["close"] - r["ema50"]) / r["atr"] if r["atr"] else None)),
        ("EMA9 vs EMA20 (ATR)", [a["ema9_vs_ema20_atr"] for a in aligned],
         base_neutral(None, lambda r: (r["ema9"] - r["ema20"]) / r["atr"] if r["atr"] else None)),
        ("Close vs EMA20 (%)", [a["close_vs_ema20_pct"] for a in aligned],
         base_neutral(None, lambda r: 100 * (r["close"] / r["ema20"] - 1) if r["ema20"] else None)),
        ("Close vs SMA50 (%)", [a["close_vs_sma50_pct"] for a in aligned],
         base_neutral(None, lambda r: 100 * (r["close"] / r["sma50"] - 1) if r["sma50"] else None)),
        ("MACD histogram", [a["macd_hist"] for a in aligned],
         base_neutral(None, lambda r: r["macd_hist"])),
        ("RSI(14)", [a["rsi14"] for a in aligned], base_neutral(None, lambda r: r["rsi14"])),
        ("KAMA-RSI(14)", [a["kama_rsi"] for a in aligned], base_neutral(None, lambda r: r["kama_rsi"])),
        ("KAMA ROC(5)", [a["kama_roc"] for a in aligned], base_neutral(None, lambda r: r["kama_roc"])),
        ("Bollinger %B", [a["bb_percent_b"] for a in aligned],
         base_neutral(None, lambda r: r["bb_percent_b"])),
        ("Move from day open (%)", [a["from_day_open_pct"] for a in aligned],
         base_neutral(None, lambda r: r["from_day_open_pct"])),
        ("Position in day's range", [a["day_range_position"] for a in aligned],
         base_neutral(None, lambda r: r["day_range_position"])),
    ]
    for name, onset_values, base_stats in rows:
        print(line(name, quantiles(onset_values), base_stats))

    undirected = [
        ("ATR (% of price)", [e["spot"]["atr_pct"] for e in episodes],
         quantiles([r["atr_pct"] for r in base_rows])),
        ("Bollinger width (%)", [e["spot"]["bb_width_pct"] for e in episodes],
         quantiles([r["bb_width_pct"] for r in base_rows])),
        ("Volume vs 20-bar average", [e["spot"]["volume_ratio"] for e in episodes],
         quantiles([r["volume_ratio"] for r in base_rows])),
    ]
    print("\n| Spot feature (undirected) | Onset median | Baseline median | Shift | Onset p25..p75 |")
    print("|---|---:|---:|---:|---|")
    for name, onset_values, base_stats in undirected:
        print(line(name, quantiles(onset_values), base_stats))

    # ---- how often is the state actually distinctive ---------------------
    print("\n## Hit rates at the onset bar\n")
    print("| Condition at onset | Share of advances | Share of baseline bars |")
    print("|---|---:|---:|")
    checks = [
        ("Spot above its EMA20 (in the option's favour)",
         lambda a: a["close_vs_ema20_atr"] > 0, lambda r: r["close"] > r["ema20"]),
        ("EMA9 separated from EMA20 by >= 0.5 ATR",
         lambda a: a["ema9_vs_ema20_atr"] >= 0.5, lambda r: abs(r["ema9"] - r["ema20"]) / r["atr"] >= 0.5 if r["atr"] else False),
        ("Spot MACD histogram positive in favour",
         lambda a: a["macd_hist"] > 0, lambda r: r["macd_hist"] > 0),
        ("Spot MACD above its signal in favour",
         lambda a: a["macd_above_signal"], lambda r: r["macd"] > r["macd_signal"]),
        ("Spot RSI(14) >= 55 in favour",
         lambda a: (a["rsi14"] or 0) >= 55, lambda r: (r["rsi14"] or 0) >= 55),
        ("Spot KAMA-RSI >= 65 in favour",
         lambda a: (a["kama_rsi"] or 0) >= 65, lambda r: (r["kama_rsi"] or 0) >= 65),
        ("Bollinger %B >= 0.8 in favour",
         lambda a: (a["bb_percent_b"] if a["bb_percent_b"] is not None else 0) >= 0.8,
         lambda r: (r["bb_percent_b"] if r["bb_percent_b"] is not None else 0) >= 0.8),
        ("Bollinger %B <= 0.2 in favour (buying the low)",
         lambda a: (a["bb_percent_b"] if a["bb_percent_b"] is not None else 1) <= 0.2,
         lambda r: (r["bb_percent_b"] if r["bb_percent_b"] is not None else 1) <= 0.2),
        ("Spot in the lower third of the day's range, in favour",
         lambda a: (a["day_range_position"] if a["day_range_position"] is not None else 1) <= 0.33,
         lambda r: (r["day_range_position"] if r["day_range_position"] is not None else 1) <= 0.33),
        ("Spot volume >= 1.5x its 20-bar average",
         None, lambda r: (r["volume_ratio"] or 0) >= 1.5),
    ]
    for name, onset_check, base_check in checks:
        if onset_check is None:
            share = sum(1 for e in episodes if (e["spot"]["volume_ratio"] or 0) >= 1.5) / max(1, len(episodes))
        else:
            share = sum(1 for a in aligned if onset_check(a)) / max(1, len(aligned))
        base_share = sum(1 for r in base_rows if base_check(r)) / max(1, len(base_rows))
        print(f"| {name} | {100*share:.1f}% | {100*base_share:.1f}% |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
