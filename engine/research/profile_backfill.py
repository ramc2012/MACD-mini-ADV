"""Populate the session, weekly and monthly profile tiers from the archive.

The engine builds each session after its close. This rebuilds history — the
sessions that closed before the tier existed, or after a rule changed how they
should be measured.

    python research/profile_backfill.py --symbol NSE:NIFTY50-INDEX
    python research/profile_backfill.py --indices --days 2026-06-01:2026-09-01
    python research/profile_backfill.py --all          # every underlying, slow

Options are excluded unless asked for. A positional profile compares this week
to last week, and a contract that did not exist last week has nothing to
compare against; the contract that replaced it is a different instrument
wearing a similar name.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from macd_trader.profile_builder import (  # noqa: E402
    _connect, available_days, build_periods, build_sessions, candidate_symbols,
)

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
INDICES = ["NSE:NIFTY50-INDEX", "NSE:NIFTYBANK-INDEX", "BSE:SENSEX-INDEX",
           "NSE:MIDCPNIFTY-INDEX"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ticks", default=str(RUNTIME / "ticks.sqlite3"))
    parser.add_argument("--history", default=str(RUNTIME / "historical.sqlite3"))
    parser.add_argument("--out", default=str(RUNTIME / "historical.sqlite3"))
    parser.add_argument("--symbol", action="append", dest="symbols")
    parser.add_argument("--indices", action="store_true", help="the four index underlyings")
    parser.add_argument("--all", action="store_true", help="every non-option symbol with data")
    parser.add_argument("--include-options", action="store_true")
    parser.add_argument("--days", default="", help="START:END in IST dates")
    parser.add_argument("--periods-only", action="store_true")
    args = parser.parse_args()

    start, _, end = args.days.partition(":")
    ticks = _connect(args.ticks, read_only=True) if Path(args.ticks).exists() else None
    history = _connect(args.history, read_only=True) if Path(args.history).exists() else None
    try:
        days = available_days(ticks, history, start, end)
        symbols = args.symbols
        if args.indices:
            symbols = (symbols or []) + INDICES
        if args.all:
            symbols = candidate_symbols(ticks, history, days, args.include_options)
    finally:
        for connection in (ticks, history):
            if connection is not None:
                connection.close()

    if not days:
        print("No stored sessions in that range.")
        return 1
    print(f"{len(days)} session(s) {days[0]} → {days[-1]}"
          f" · {len(symbols) if symbols else 'auto'} symbol(s)")

    began = time.time()
    if not args.periods_only:
        seen = {"n": 0}

        def progress(day: str, written: int) -> None:
            seen["n"] += written
            print(f"  {day}  {written:>4} profiles   ({seen['n']} total)", flush=True)

        result = build_sessions(args.ticks, args.history, args.out, days, symbols,
                                args.include_options, progress)
        print(f"sessions: {result}")
    periods = build_periods(args.ticks, args.out, days, symbols, args.history)
    print(f"periods: {periods}  in {time.time() - began:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
