"""Which advances coincide with an unmistakable event footprint in the tape?

Web search is a poor instrument for this: coverage of Indian mid-caps on a
specific intraday date is patchy, and a headline found is not a headline that
moved the price. The tape is better evidence and it is complete. ASTRAL is the
worked example -- the Q1 result was reported as a +2.7% day, while the actual
move was the NEXT session, +8.7% on 12.3 million shares against a 200k norm.
No search phrasing would have found that; the volume did, instantly.

So: daily spot volume over its own trailing 20-session median, measured on the
session where the premium peaked, plus the same for the option. A 3x day is a
soft event marker, 10x is not ambiguous.

    docker compose exec api python /app/scripts/august_event_volume.py
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, "/app/scripts")
sys.path.insert(0, "/app/src")

from august_runup_research import RUNTIME, SYMBOL, bars, connect, spot_symbol  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
LOOKBACK_SESSIONS = 20


def daily_volume(rows) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    for row in rows:
        day = datetime.fromtimestamp(row.timestamp, UTC).astimezone(IST).date().isoformat()
        totals[day] += row.volume
    return dict(totals)


def ratios(totals: dict[str, int]) -> dict[str, float]:
    days = sorted(totals)
    out: dict[str, float] = {}
    for position, day in enumerate(days):
        window = [totals[d] for d in days[max(0, position - LOOKBACK_SESSIONS): position]]
        median = statistics.median(window) if window else 0
        out[day] = (totals[day] / median) if median else 0.0
    return out


def main() -> int:
    db = connect()
    enriched = json.loads((RUNTIME / "august_news_signature.json").read_text())
    episodes = {(row["symbol"], row["onset_ist"]): row for row in enriched}
    raw = json.loads((RUNTIME / "august_runup_research.json").read_text())["episodes"]
    peak_by_key = {(row["symbol"], row["onset_ist"]): row["peak_timestamp"] for row in raw}

    spot_ratio_cache: dict[str, dict[str, float]] = {}
    rows_out = []
    for key, row in episodes.items():
        parsed = SYMBOL.match(row["symbol"])
        if not parsed:
            continue
        underlying = spot_symbol(parsed["ex"], parsed["root"])
        if underlying not in spot_ratio_cache:
            spot_ratio_cache[underlying] = ratios(daily_volume(bars(db, underlying)))
        peak_stamp = peak_by_key.get(key)
        if peak_stamp is None:
            continue
        peak_day = datetime.fromtimestamp(peak_stamp, UTC).astimezone(IST).date().isoformat()
        rows_out.append({
            **row,
            "peak_date": peak_day,
            "spot_volume_x_onset": spot_ratio_cache[underlying].get(row["onset_date"], 0.0),
            "spot_volume_x_peak": spot_ratio_cache[underlying].get(peak_day, 0.0),
        })

    (RUNTIME / "august_event_volume.json").write_text(json.dumps(rows_out))

    peaks = [r["spot_volume_x_peak"] for r in rows_out if r["spot_volume_x_peak"] > 0]
    peaks.sort()
    pick = lambda q: peaks[min(len(peaks) - 1, int(q * len(peaks)))]  # noqa: E731
    print(f"episodes: {len(rows_out):,}\n")
    print("spot daily volume on the PEAK session, vs its own trailing 20-session median")
    print(f"  p10 {pick(.10):.2f}x   median {pick(.50):.2f}x   p75 {pick(.75):.2f}x   "
          f"p90 {pick(.90):.2f}x   p99 {pick(.99):.2f}x   max {peaks[-1]:.1f}x")
    for threshold in (2, 3, 5, 10):
        hits = sum(1 for value in peaks if value >= threshold)
        print(f"  >= {threshold:>2}x : {hits:>5,}  ({100*hits/len(peaks):.1f}%)")
    print()
    combined = [r for r in rows_out
                if r["spot_volume_x_peak"] >= 3 and (r["excess_move_pct"] or 0) >= 3]
    print(f"volume >= 3x AND excess move >= 3% (hard event marker): "
          f"{len(combined):,} of {len(rows_out):,} ({100*len(combined)/len(rows_out):.1f}%)")
    print()
    print("Highest-conviction event days (volume x, on the peak session):")
    seen = set()
    for row in sorted(rows_out, key=lambda r: -r["spot_volume_x_peak"]):
        marker = (row["root"], row["peak_date"])
        if marker in seen:
            continue
        seen.add(marker)
        if len(seen) > 20:
            break
        print(f"  {row['root']:<13} peak {row['peak_date']}  vol {row['spot_volume_x_peak']:>6.1f}x  "
              f"spot {row['spot_move_pct']:>6.2f}%  net {(row['excess_move_pct'] or 0):>6.2f}%  "
              f"best premium {max(x['tradable_pct'] for x in rows_out if x['root']==row['root'] and x['peak_date']==row['peak_date']):>6.0f}%  "
              f"names {row['names_that_day']:>3}")
    print()
    quiet = [r for r in rows_out if r["spot_volume_x_peak"] < 1.5]
    print(f"Advances with NO volume expansion at all (<1.5x): {len(quiet):,} "
          f"({100*len(quiet)/len(rows_out):.1f}%)")
    print(f"  their median premium gain: {statistics.median(r['tradable_pct'] for r in quiet):.0f}%")
    print(f"  their median spot move:    {statistics.median(r['spot_move_pct'] for r in quiet):.2f}%")
    loud = [r for r in rows_out if r["spot_volume_x_peak"] >= 3]
    print(f"Advances on a >=3x volume session: {len(loud):,}")
    print(f"  their median premium gain: {statistics.median(r['tradable_pct'] for r in loud):.0f}%")
    print(f"  their median spot move:    {statistics.median(r['spot_move_pct'] for r in loud):.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
