"""Re-run the end-of-session job for past days.

    python research/nightly_replay.py --days 2026-07-01:2026-09-01 --indices
    python research/nightly_replay.py --days 2026-08-26:2026-09-01 --all --whale NSE:NIFTY26SEPFUT

The job is idempotent, so replaying a range simply rewrites those days' rows.
Whale Layer A needs the day's raw ticks, which the archive keeps for five
sessions; older days get profiles, measurements and journal rows only.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from macd_trader import nightly  # noqa: E402
from macd_trader.profile_builder import _connect, available_days, candidate_symbols  # noqa: E402

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
INDICES = ["NSE:NIFTY50-INDEX", "NSE:NIFTYBANK-INDEX", "BSE:SENSEX-INDEX", "NSE:MIDCPNIFTY-INDEX"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ticks", default=str(RUNTIME / "ticks.sqlite3"))
    parser.add_argument("--history", default=str(RUNTIME / "historical.sqlite3"))
    parser.add_argument("--days", default="", help="START:END in IST dates")
    parser.add_argument("--symbol", action="append", dest="symbols")
    parser.add_argument("--indices", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--whale", action="append", dest="whale", default=[])
    args = parser.parse_args()

    start, _, end = args.days.partition(":")
    ticks = _connect(args.ticks, read_only=True)
    history = _connect(args.history, read_only=True)
    try:
        days = available_days(ticks, history, start, end)
        symbols = list(args.symbols or [])
        if args.indices:
            symbols += INDICES
        if args.all:
            symbols = candidate_symbols(ticks, history, days)
    finally:
        ticks.close(); history.close()
    if not days or not symbols:
        print("Nothing to replay."); return 1
    print(f"{len(days)} day(s), {len(symbols)} symbol(s), whale: {args.whale or '-'}")
    for day in days:
        report = nightly.run(args.ticks, args.history, day, symbols, args.whale)
        print(f"  {day}  sessions={report.get('sessions', 0):>4} measured={report.get('measurements', 0):>4} "
              f"journal={report.get('journal_rows', 0):>5} positional_fired={report.get('positional_fired', 0):>3} "
              f"whale={report.get('whale_events', 0)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
