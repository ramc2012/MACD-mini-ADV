"""Do the >100% premium advances look like news, or like leverage?

Two very different worlds produce the same episode label. If a premium doubles
because the underlying gapped 6% on an earnings release, that is a news trade
and headlines are the right place to look. If it doubles because a Rs 8 option
is 4% out of the money and the underlying drifted 1.2%, that is convexity, and
no headline exists to find.

Distinguishing them needs no news feed, only the data already stored:

  spot move required   how far the underlying actually travelled over the
                       episode, in percent and in its own ATR
  idiosyncratic?       the same move net of NIFTY over the same bars
  overnight gap        previous close to the day's open, for onsets on the
                       09:15 bar, which is where news lands
  breadth              how many other underlyings started an advance that day

    docker compose exec api python /app/scripts/august_news_signature.py
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
INDEX = "NSE:NIFTY50-INDEX"
# A move this large in the option's favour, net of the index, is an event.
EVENT_MOVE_PCT = 3.0
EVENT_GAP_PCT = 2.0


def series_index(rows) -> dict[int, int]:
    return {row.timestamp: position for position, row in enumerate(rows)}


def main() -> int:
    db = connect()
    payload = json.loads((RUNTIME / "august_runup_research.json").read_text())
    episodes = payload["episodes"]

    index_bars = bars(db, INDEX)
    index_at = series_index(index_bars)

    spot_cache: dict[str, tuple] = {}
    by_day_names: dict[str, set] = defaultdict(set)
    for episode in episodes:
        by_day_names[episode["onset_date"]].add(episode["root"])

    enriched = []
    for episode in episodes:
        parsed = SYMBOL.match(episode["symbol"])
        if not parsed:
            continue
        underlying = spot_symbol(parsed["ex"], parsed["root"])
        if underlying not in spot_cache:
            rows = bars(db, underlying)
            spot_cache[underlying] = (rows, series_index(rows))
        spot_rows, spot_at = spot_cache[underlying]
        start = spot_at.get(episode["onset_timestamp"])
        finish = spot_at.get(episode["peak_timestamp"])
        if start is None or finish is None or finish <= start:
            continue
        sign = 1.0 if episode["side"] == "CE" else -1.0
        entry = spot_rows[start].close
        # The best the SPOT did in the option's favour over the same bars.
        window = spot_rows[start: finish + 1]
        extreme = max(row.high for row in window) if sign > 0 else min(row.low for row in window)
        spot_move = sign * 100 * (extreme / entry - 1)

        index_start = index_at.get(episode["onset_timestamp"])
        index_finish = index_at.get(episode["peak_timestamp"])
        index_move = None
        if index_start is not None and index_finish is not None and index_finish > index_start:
            index_window = index_bars[index_start: index_finish + 1]
            index_extreme = (max(row.high for row in index_window) if sign > 0
                             else min(row.low for row in index_window))
            index_move = sign * 100 * (index_extreme / index_bars[index_start].close - 1)

        moment = datetime.fromtimestamp(episode["onset_timestamp"], UTC).astimezone(IST)
        first_bar = (moment.hour, moment.minute) == (9, 15)
        gap = None
        if first_bar and start > 0:
            previous = spot_rows[start - 1]
            previous_day = datetime.fromtimestamp(previous.timestamp, UTC).astimezone(IST).date()
            if previous_day != moment.date():
                gap = sign * 100 * (spot_rows[start].open / previous.close - 1)

        atr_moves = None
        if start >= 14:
            recent = spot_rows[start - 14: start]
            true_ranges = [row.high - row.low for row in recent]
            atr = statistics.fmean(true_ranges) if true_ranges else 0.0
            if atr > 0:
                atr_moves = (extreme - entry) * sign / atr

        enriched.append({
            **{k: episode[k] for k in ("symbol", "root", "side", "onset_ist", "onset_date",
                                       "tradable_pct", "onset_premium", "moneyness_pct",
                                       "days_to_expiry", "hours_to_peak")},
            "spot_move_pct": spot_move,
            "index_move_pct": index_move,
            "excess_move_pct": None if index_move is None else spot_move - index_move,
            "spot_move_atr": atr_moves,
            "first_bar": first_bar,
            "overnight_gap_pct": gap,
            "names_that_day": len(by_day_names[episode["onset_date"]]),
            "leverage": (episode["tradable_pct"] / spot_move) if spot_move > 0.01 else None,
        })

    out = RUNTIME / "august_news_signature.json"
    out.write_text(json.dumps(enriched))

    moves = [row["spot_move_pct"] for row in enriched]
    excess = [row["excess_move_pct"] for row in enriched if row["excess_move_pct"] is not None]
    leverage = [row["leverage"] for row in enriched if row["leverage"]]
    gaps = [row["overnight_gap_pct"] for row in enriched if row["overnight_gap_pct"] is not None]

    def band(values, label):
        clean = sorted(values)
        if not clean:
            print(f"{label}: none")
            return
        pick = lambda q: clean[min(len(clean) - 1, int(q * len(clean)))]  # noqa: E731
        print(f"{label:<38} p10 {pick(.10):>7.2f}   median {pick(.50):>7.2f}   "
              f"p90 {pick(.90):>7.2f}   max {clean[-1]:>8.2f}")

    print(f"episodes matched: {len(enriched):,}\n")
    band(moves, "spot move required (%)")
    band(excess, "  net of NIFTY (%)")
    band([row["spot_move_atr"] for row in enriched if row["spot_move_atr"]], "spot move (in its own ATR)")
    band(leverage, "premium % per 1% of spot")
    print()
    events = [row for row in enriched if (row["excess_move_pct"] or 0) >= EVENT_MOVE_PCT]
    gapped = [row for row in enriched if abs(row["overnight_gap_pct"] or 0) >= EVENT_GAP_PCT]
    print(f"onsets on the 09:15 bar        : {sum(1 for r in enriched if r['first_bar']):>5,} "
          f"({100*sum(1 for r in enriched if r['first_bar'])/len(enriched):.1f}%)")
    print(f"  of those, gap >= {EVENT_GAP_PCT}% in favour: {len(gapped):>5,} "
          f"({100*len(gapped)/len(enriched):.1f}% of all episodes)")
    band(gaps, "  overnight gap when first bar (%)")
    print(f"excess move >= {EVENT_MOVE_PCT}% (event-like) : {len(events):>5,} "
          f"({100*len(events)/len(enriched):.1f}%)")
    print(f"median names advancing same day: {statistics.median(r['names_that_day'] for r in enriched):.0f}")
    print()
    print("Most event-like advances (largest spot move net of index):")
    for row in sorted(enriched, key=lambda r: -(r["excess_move_pct"] or 0))[:18]:
        print(f"  {row['symbol']:<26} {row['onset_ist'][:16].replace('T',' ')} "
              f"spot {row['spot_move_pct']:>6.2f}%  net {row['excess_move_pct']:>6.2f}%  "
              f"gap {('%.2f' % row['overnight_gap_pct']) if row['overnight_gap_pct'] is not None else '   -':>6}  "
              f"prem {row['tradable_pct']:>6.0f}%  names {row['names_that_day']:>3}")
    print()
    print("Largest premium advances, and what the spot actually did:")
    for row in sorted(enriched, key=lambda r: -r["tradable_pct"])[:14]:
        print(f"  {row['symbol']:<26} prem {row['tradable_pct']:>6.0f}%  "
              f"spot {row['spot_move_pct']:>6.2f}%  net {(row['excess_move_pct'] or 0):>6.2f}%  "
              f"x{(row['leverage'] or 0):>5.1f}  names {row['names_that_day']:>3}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
