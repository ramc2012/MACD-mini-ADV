"""NIFTY's own Market Profile base rates, split by regime and expiry.

The initial-balance and value-area statistics Market Profile traders quote are
measured on ES and NQ. Nothing equivalent is published for NIFTY, so every
number in the literature is borrowed from a different market with a different
tick, a different session and a different participant mix. This regenerates the
same table from the archive here.

    python research/base_rates.py --symbol NSE:NIFTY50-INDEX
    python research/base_rates.py --rebuild --days 2026-07-01:2026-09-01
    python research/base_rates.py --symbol NSE:NIFTY50-INDEX --by-regime --json out.json

Two things it refuses to do. It will not pool sessions across a rule change
without saying so: lot size, futures tick, weekly expiry weekday and session
end all moved between November 2024 and August 2026, and a statistic averaged
across one of those dates describes no market that ever existed. And it prints
the denominator beside every rate, because the interesting rates here are the
rare ones — a "50% completion" computed on ten triggers has a 95% interval of
roughly 19-81% and is not a finding.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from macd_trader.base_rates import load, summarise  # noqa: E402
from macd_trader.profile_history import (  # noqa: E402
    MEASUREMENT_SCHEMA, build_session, measure_session, save_measurements,
)
from macd_trader.profile_builder import _connect, available_days  # noqa: E402
from macd_trader.regimes import REGIMES, regime_for, spans_a_break  # noqa: E402

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
DEFAULT_TICKS = RUNTIME / "ticks.sqlite3"
DEFAULT_HISTORY = RUNTIME / "historical.sqlite3"


def rebuild(ticks_path: str, history_path: str, out_path: str, symbol: str,
            days: list[str]) -> int:
    """Recompute the measurements for one symbol over a range of sessions.

    Sessions are walked in order because most of the interesting fields are
    relative to the previous one: opening outside prior value, the 80% rule and
    gap fills all need yesterday's profile.
    """
    ticks = _connect(ticks_path, read_only=True) if Path(ticks_path).exists() else None
    history = _connect(history_path, read_only=True) if Path(history_path).exists() else None
    out = _connect(out_path)
    try:
        out.executescript(MEASUREMENT_SCHEMA)
        rows, prior = [], None
        for day in days:
            profile = build_session(ticks, history, symbol, day)
            if profile is None or profile.poc is None:
                continue
            rows.append(measure_session(profile, prior))
            prior = {"vah": profile.vah, "val": profile.val,
                     "high": profile.high, "low": profile.low}
        return save_measurements(out, rows)
    finally:
        for connection in (ticks, history, out):
            if connection is not None:
                connection.close()


def _fmt(hits: int, total: int) -> str:
    if not total:
        return "     \u2014  (0)"
    return f"{100 * hits / total:5.1f}%  ({hits}/{total})"


def render(title: str, summary: dict) -> str:
    lines = [f"\n{title}", "-" * len(title)]
    lines.append(f"  sessions                     {summary['sessions']} "
                 f"({summary['sessions_with_ib']} with a measurable IB)")
    for label, key in (
            ("IB broken by the close", "ib_broken"),
            ("  break up only", "break_up_only"),
            ("  break down only", "break_down_only"),
            ("  both sides (neutral)", "break_both"),
            ("first break in bracket C", "first_break_in_C"),
            ("first break in bracket D", "first_break_in_D"),
            ("extension >= 25% of IB", "extension_over_25pct"),
            ("extension >= 50% of IB", "extension_over_50pct"),
            ("extension >= 100% of IB", "extension_over_100pct"),
            ("extension >= 200% of IB", "extension_over_200pct"),
            ("opened outside prior value", "opened_outside_value"),
            ("  returned to value", "returned_to_value"),
            ("  80% rule triggered", "rule80_triggered"),
            ("  80% rule completed", "rule80_completed"),
            ("gapped beyond prior range", "gapped"),
            ("  gap filled same day", "gap_filled")):
        hits, total = summary[key]
        lines.append(f"  {label:<28} {_fmt(hits, total)}")
    for label, key in (("median extension / IB", "median_extension_ratio"),
                       ("median range / IB", "median_range_ib_ratio")):
        value = summary[key]
        lines.append(f"  {label:<28} {'—' if value is None else f'{value:.2f}'}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", default="NSE:NIFTY50-INDEX")
    parser.add_argument("--ticks", default=str(DEFAULT_TICKS))
    parser.add_argument("--history", default=str(DEFAULT_HISTORY))
    parser.add_argument("--out", default=str(DEFAULT_HISTORY),
                        help="where session_base_rates lives (default: the history database)")
    parser.add_argument("--days", default="", help="START:END, default every stored session")
    parser.add_argument("--rebuild", action="store_true", help="recompute before reporting")
    parser.add_argument("--by-regime", action="store_true",
                        help="one table per rule regime instead of one pooled table")
    parser.add_argument("--by-expiry", action="store_true")
    parser.add_argument("--json", default="", help="also write the summaries here")
    args = parser.parse_args()

    start, _, end = args.days.partition(":")
    if args.rebuild:
        ticks = _connect(args.ticks, read_only=True) if Path(args.ticks).exists() else None
        history = _connect(args.history, read_only=True) if Path(args.history).exists() else None
        days = available_days(ticks, history, start, end)
        for connection in (ticks, history):
            if connection is not None:
                connection.close()
        written = rebuild(args.ticks, args.history, args.out, args.symbol, days)
        print(f"Measured {written} sessions for {args.symbol}")

    rows = [row for row in load(args.out, args.symbol)
            if (not start or row["day"] >= start) and (not end or row["day"] <= end)]
    if not rows:
        print(f"No measured sessions for {args.symbol}. Run with --rebuild first.")
        return 1

    span = (rows[0]["day"], rows[-1]["day"])
    print(f"{args.symbol}: {len(rows)} sessions, {span[0]} to {span[1]}")
    breaks = spans_a_break(*span)
    if breaks and not args.by_regime:
        print(f"  WARNING: this window crosses {len(breaks)} rule change(s) "
              f"({', '.join(breaks)}). The pooled table below describes no single "
              f"market — re-run with --by-regime.")

    summaries: dict[str, dict] = {}
    groups: list[tuple[str, list[dict]]] = [("All sessions", rows)]
    if args.by_regime:
        groups = []
        for regime in REGIMES:
            members = [row for row in rows if row["regime_id"] == regime.regime_id]
            if members:
                groups.append((f"{regime.regime_id} — {regime.summary}", members))
    if args.by_expiry:
        expanded = []
        for title, members in groups:
            for label, flag in (("expiry sessions", 1), ("non-expiry sessions", 0)):
                subset = [row for row in members if row["expiry_day"] == flag]
                if subset:
                    expanded.append((f"{title} · {label}", subset))
        groups = expanded or groups

    for title, members in groups:
        summary = summarise(members)
        summaries[title] = summary
        print(render(title, summary))

    if args.json:
        Path(args.json).write_text(json.dumps(summaries, indent=1, default=str))
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
