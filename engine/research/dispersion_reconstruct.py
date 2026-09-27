"""Rebuild CE/PE MACD breadth from stored option minute bars.

The live terminal computes dispersion from streamed indicator events and, until
now, kept it only in the browser.  This script recovers the same measurement
for days already in ``historical_candles`` and writes it to the shared
``dispersion_history`` table, so the series can be studied instead of watched.

What it can and cannot recover
------------------------------
The daily ATM selection is not archived — ``runtime/atm_contracts.json`` is
overwritten every morning.  So the cohort here is *inferred* from the desk's
rule rather than replayed: for each underlying, the listed strike nearest that
day's opening spot print, taking the CE and PE at that strike.  The live
selector additionally applies a liquidity screen and retains contracts that are
still held, so a reconstructed cohort can differ from the one the desk saw.
Rows are written as ``source='reconstructed'`` for exactly that reason and never
overwrite a live row.

Two further limits are properties of the archive, not of this code:

* the stored option universe per day swings widely (425 symbols on 21 Aug,
  1308 on 31 Aug), so a raw *count* above zero is not comparable across days.
  ``ce_eligible``/``pe_eligible``/``total`` are stored alongside the counts so
  any study can work in share-above-zero instead;
* the archived strike band is narrow and fixed per day, so an underlying whose
  spot ran to the edge of its band loses its ATM contract for that day and
  simply drops out of the cohort;
* option history was only downloaded broadly from late August. Earlier days
  hold two or three contracts, and a "breadth" over three contracts is noise
  wearing the same units as a real reading. ``--min-cohort`` refuses those
  days outright rather than writing rows that look like the others.

Usage
-----
    python research/dispersion_reconstruct.py --days 2026-08-24:2026-09-01
    python research/dispersion_reconstruct.py --timeframe 1800 --coverage
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
import re
import sqlite3
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from macd_trader.chart_history import aggregate_session_candles  # noqa: E402
from macd_trader.dispersion import (  # noqa: E402
    RECONSTRUCTED, DispersionPoint, breadth_series, coverage, record,
)
from macd_trader.indicators import IncrementalMACD  # noqa: E402
from macd_trader.models import Candle  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_DATABASE = Path(__file__).resolve().parents[1] / "runtime" / "historical.sqlite3"
MONTHS = {1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
          7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC"}
INDEX_SPOTS = {
    "NIFTY": "NSE:NIFTY50-INDEX", "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "SENSEX": "BSE:SENSEX-INDEX", "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
    "FINNIFTY": "NSE:FINNIFTY-INDEX",
}
STRIKE = re.compile(r"\d+(?:\.\d+)?$")


def expiry_codes(expiry: str) -> tuple[str, ...]:
    """Fyers writes a monthly as 26SEP and a weekly as 26908 (YY M DD)."""
    moment = date.fromisoformat(expiry)
    return (f"{moment.year % 100:02d}{MONTHS[moment.month]}",
            f"{moment.year % 100:02d}{moment.month}{moment.day:02d}")


def parse_option(symbol: str, expiry: str) -> dict | None:
    """Split ``NSE:LICI26SEP425CE`` into exchange, root, strike and side.

    The expiry column supplies the code, which is what makes this safe for
    roots that themselves contain digits (``360ONE``): the split is only
    accepted when everything after the code is a bare strike.
    """
    if ":" not in symbol or symbol[-2:] not in ("CE", "PE"):
        return None
    exchange, rest = symbol.split(":", 1)
    side, body = rest[-2:], rest[:-2]
    for code in expiry_codes(expiry):
        start = 0
        while (index := body.find(code, start)) != -1:
            root, strike = body[:index], body[index + len(code):]
            if root and STRIKE.fullmatch(strike):
                return {"exchange": exchange, "root": root, "strike": float(strike),
                        "side": side, "symbol": symbol}
            start = index + 1
    return None


def spot_symbol(exchange: str, root: str) -> str:
    return INDEX_SPOTS.get(root, f"{exchange}:{root}-EQ")


def session_bounds(day: str) -> tuple[int, int]:
    opened = datetime.combine(date.fromisoformat(day), time(9, 15), IST)
    return int(opened.timestamp()), int((opened + timedelta(hours=6, minutes=15)).timestamp())


def trading_days(connection: sqlite3.Connection, start: str, end: str) -> list[str]:
    rows = connection.execute(
        """SELECT DISTINCT date(timestamp,'unixepoch','+5 hours','+30 minutes') AS day
           FROM historical_candles WHERE asset_type='option'
           GROUP BY day HAVING day BETWEEN ? AND ? ORDER BY day""",
        (start, end),
    ).fetchall()
    return [row[0] for row in rows]


def day_minutes(connection: sqlite3.Connection, day: str) -> tuple[dict[str, list[Candle]], dict[str, tuple[str, str | None]]]:
    """Every option and spot minute bar for one session, keyed by symbol."""
    opened, closed = session_bounds(day)
    rows = connection.execute(
        """SELECT symbol,timestamp,open,high,low,close,volume,asset_type,expiry
           FROM historical_candles
           WHERE timeframe_seconds=60 AND timestamp>=? AND timestamp<?
           ORDER BY symbol,timestamp""",
        (opened, closed),
    )
    bars: dict[str, list[Candle]] = defaultdict(list)
    meta: dict[str, tuple[str, str | None]] = {}
    for symbol, stamp, o, h, low, close, volume, asset_type, expiry in rows:
        bars[symbol].append(Candle(symbol, int(stamp), o, h, low, close, int(volume), True))
        meta.setdefault(symbol, (asset_type, expiry))
    return bars, meta


def select_cohort(bars: dict, meta: dict) -> list[dict]:
    """One CE and one PE per underlying, at the strike nearest the open.

    The desk picks ATM from a pre-open spot snapshot; the closest thing the
    archive holds is the session's first spot print, which is used here.
    An underlying contributes only when both a CE and a PE exist at the chosen
    strike, so the two sides of the breadth always describe the same strikes.
    """
    by_root: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for symbol, (asset_type, expiry) in meta.items():
        if asset_type != "option" or not expiry:
            continue
        parsed = parse_option(symbol, expiry)
        if parsed and bars.get(symbol):
            by_root[(parsed["exchange"], parsed["root"])].append(parsed)

    cohort = []
    for (exchange, root), rows in sorted(by_root.items()):
        spot_bars = bars.get(spot_symbol(exchange, root))
        if not spot_bars:
            continue
        reference = spot_bars[0].open
        if not reference or reference <= 0:
            continue
        # Nearest listed strike among contracts that actually traded that day;
        # ties break to the lower strike, matching the live ladder's ordering.
        strikes = {row["strike"] for row in rows}
        atm = min(strikes, key=lambda value: (abs(value - reference), value))
        sides = {row["side"]: row for row in rows if row["strike"] == atm}
        if "CE" in sides and "PE" in sides:
            cohort.extend([sides["CE"], sides["PE"]])
    return cohort


def macd_points(candles: list[Candle], settings: dict) -> list[tuple[int, float]]:
    macd = IncrementalMACD(settings["fast"], settings["slow"], settings["signal"])
    return [(candle.timestamp, macd.update(candle.close).macd) for candle in candles]


def reconstruct_day(bars: dict, cohort: list[dict], timeframe: int, settings: dict) -> list[DispersionPoint]:
    rows: list[tuple[str, int, float]] = []
    for contract in cohort:
        candles = aggregate_session_candles(bars[contract["symbol"]], timeframe)
        rows.extend((contract["side"], stamp, value) for stamp, value in macd_points(candles, settings))
    return breadth_series(rows, total=len(cohort))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", default=str(DEFAULT_DATABASE))
    parser.add_argument("--days", default="", help="START:END in IST dates, default every stored option day")
    parser.add_argument("--timeframe", type=int, action="append", dest="timeframes",
                        help="seconds per bar; repeatable, default 300/900/1800")
    parser.add_argument("--fast", type=int, default=12)
    parser.add_argument("--slow", type=int, default=26)
    parser.add_argument("--signal", type=int, default=9)
    parser.add_argument("--min-cohort", type=int, default=20,
                        help="skip a session whose reconstructed cohort is smaller than this")
    parser.add_argument("--coverage", action="store_true", help="print what the table holds and exit")
    args = parser.parse_args()

    if args.coverage:
        for row in coverage(args.database):
            print(f"{row['day']}  {row['timeframe_seconds']:>5}s  {row['bars']:>4} bars  {row['sources']}")
        return 0

    start, _, end = args.days.partition(":")
    settings = {"fast": args.fast, "slow": args.slow, "signal": args.signal}
    timeframes = args.timeframes or [300, 900, 1800]
    connection = sqlite3.connect(args.database, timeout=60)
    try:
        days = trading_days(connection, start or "0000-00-00", end or "9999-99-99")
        if not days:
            print("No stored option sessions in that range.")
            return 1
        print(f"Reconstructing {len(days)} session(s) at {'/'.join(str(t) for t in timeframes)}s")
        written, skipped = 0, []
        for day in days:
            bars, meta = day_minutes(connection, day)
            cohort = select_cohort(bars, meta)
            if len(cohort) < args.min_cohort:
                skipped.append((day, len(cohort)))
                print(f"  {day}  cohort {len(cohort)} < {args.min_cohort} — skipped")
                continue
            for timeframe in timeframes:
                points = reconstruct_day(bars, cohort, timeframe, settings)
                written += record(args.database, timeframe, points, RECONSTRUCTED)
                if points:
                    last = points[-1]
                    share = 100 * (last.ce_eligible + last.pe_eligible) / last.total if last.total else 0
                    print(f"  {day} {timeframe:>5}s  {len(points):>3} bars  cohort {len(cohort):>4}"
                          f"  last CE {last.ce_above}/{last.ce_eligible} PE {last.pe_above}/{last.pe_eligible}"
                          f"  coverage {share:.0f}%")
                else:
                    print(f"  {day} {timeframe:>5}s  no cohort")
        print(f"Wrote {written} rows to dispersion_history in {args.database}")
        if skipped:
            print(f"Skipped {len(skipped)} session(s) with too few archived contracts: "
                  + ", ".join(f"{day} ({size})" for day, size in skipped))
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
