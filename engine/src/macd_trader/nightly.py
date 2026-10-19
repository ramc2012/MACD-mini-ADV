"""The end-of-session job that turns a closed day into memory.

Runs once after the close, over the underlyings only (options roll and have no
week-to-week identity). In order, because each step reads the one before it:

1. the session profile and its weekly/monthly composites;
2. the base-rate measurement (needs the prior session);
3. the setup journal (needs the measurement);
4. the positional evaluation (needs the reference built from 1);
5. whale Layer A over the day's raw ticks for the futures;
6. whale Layers B-E: the futures leg rebuilt minute by minute, every window
   of the day re-evaluated from the stored chain snapshots (a window the
   live loop already wrote is left alone), the close against the previous
   close, and the outcome of each alert the day raised;
7. the chain table condensed to one closing snapshot a day beyond the
   retention horizon.

Everything is idempotent — a second run for the same day overwrites its own
rows — so a missed night can be replayed with ``research/nightly_replay.py``
and the engine can retry without leaving half a day behind.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from . import positional, setups, whale
from .profile_builder import _connect, build_range, is_option
from .profile_history import (
    CANDLES, MEASUREMENT_SCHEMA, build_session, load_reference, measure_session, save_measurements,
)

IST = ZoneInfo("Asia/Kolkata")


REBUILD_LOOKBACK_DAYS = 21


def rebuild_candle_days(ticks_path: str, history_path: str, day: str, underlyings: list[str]) -> list[str]:
    """Rebuild recent sessions stored from candles once the tick store can give them a real buy/sell split.

    Rows built before the raw-tier fallback (and any built while ticks were missing) stay candle-sourced
    forever otherwise. Only days the tick store still holds -- as a condensed ladder or as raw ticks -- are
    rebuilt, so a day with no ticks is not retried every night.
    """
    from datetime import date, timedelta
    since = (date.fromisoformat(day) - timedelta(days=REBUILD_LOOKBACK_DAYS)).isoformat()
    history = _connect(history_path, read_only=True)
    ticks = _connect(ticks_path, read_only=True)
    try:
        days = [d for (d,) in history.execute(
            """SELECT DISTINCT day FROM session_profiles WHERE source = ? AND day < ? AND day >= ? ORDER BY day""",
            (CANDLES, day, since))]
        held = []
        for d in days:
            start = int(datetime.fromisoformat(f"{d}T00:00:00").replace(tzinfo=IST).timestamp() * 1000)
            if (ticks.execute("SELECT 1 FROM tick_session_ladder WHERE day = ? LIMIT 1", (d,)).fetchone()
                    or ticks.execute("SELECT 1 FROM ticks WHERE ts_ms >= ? AND ts_ms < ? LIMIT 1",
                                     (start, start + 86_400_000)).fetchone()):
                held.append(d)
    finally:
        history.close(); ticks.close()
    if held:
        build_range(ticks_path, history_path, history_path, held, underlyings)
    return held


def run(ticks_path: str, history_path: str, day: str, symbols: list[str],
        whale_symbols: list[str] | None = None, lots: dict[str, int] | None = None) -> dict:
    underlyings = sorted({s for s in symbols if not is_option(s)})
    report: dict = {"day": day, "symbols": len(underlyings)}
    if not underlyings:
        return report

    report.update(build_range(ticks_path, history_path, history_path, [day], underlyings))
    report["rebuilt_candle_days"] = rebuild_candle_days(ticks_path, history_path, day, underlyings)

    ticks = _connect(ticks_path, read_only=True)
    history_ro = _connect(history_path, read_only=True)
    out = _connect(history_path)
    try:
        out.executescript(MEASUREMENT_SCHEMA)
        out.executescript(setups.SCHEMA)
        positional.ensure_tables(out)
        measurements, journal, fired = [], [], 0
        for symbol in underlyings:
            profile = build_session(ticks, history_ro, symbol, day)
            if profile is None or profile.poc is None:
                continue
            prior = out.execute(
                """SELECT vah, val, high, low FROM session_profiles
                   WHERE symbol = ? AND day < ? ORDER BY day DESC LIMIT 1""",
                (symbol, day)).fetchone()
            prior_dict = dict(zip(("vah", "val", "high", "low"), prior)) if prior else None
            measurement = measure_session(profile, prior_dict)
            measurements.append(measurement)
            session_row = _session_row(out, symbol, day)
            if session_row:
                journal.extend(setups.evaluate(measurement, session_row))
                # Positional lane: the reference is everything BEFORE today.
                reference = load_reference(history_path, symbol, day)
                history = _recent_sessions(out, symbol, day)
                bias, context, reason = positional.evaluate(
                    symbol=symbol, day=day, session=session_row, reference=reference,
                    history=history)
                positional.record(history_path, symbol, day, bias, context, reason)
                fired += 1 if bias else 0
        report["measurements"] = save_measurements(out, measurements)
        report["journal_rows"] = setups.save(out, journal)
        report["positional_fired"] = fired

        # Layer A over every contract that traded, not only the index futures:
        # the whale name list. Feed-gap catch-ups are excluded, and the day's
        # earlier live-slice events for these symbols are replaced by the
        # full-day pass (A3's repeat history needs the whole session).
        # ...with each instrument's own tuned thresholds, and the improvement
        # loop behind them: fingerprint the day, label outcomes, recalibrate
        # the feature weights, refit tomorrow's thresholds (whale_tuning).
        from . import whale_tuning
        tuning = whale_tuning.run(ticks, out, day, lots=lots, whale_symbols=whale_symbols)
        report["whale_events"] = tuning["events"]
        report["whale_names"] = tuning["names"]
        report["feed_gaps"] = tuning["feed_gaps"]
        report["whale_entries_added"] = tuning["entries_added"]
        report["whale_tuning"] = tuning
        # Re-measure the minute-level edge over the trailing 60 days, walk-forward.
        from datetime import date as _date, timedelta as _timedelta
        from . import whale_study
        study = whale_study.run_study(out, (_date.fromisoformat(day) - _timedelta(days=60)).isoformat(), day)
        report["whale_study"] = {"study_id": study.get("study_id"), "verdict": study.get("verdict")}

        # Only the roots the chain collector covers; the tracker has no chain,
        # lot or freeze for SENSEX and would score it against nothing.
        futures_by_root = {whale.underlying_of(symbol): symbol for symbol in (whale_symbols or [])
                           if whale.underlying_of(symbol) in ("NIFTY", "BANKNIFTY")}
        whale.ensure_schema(out)
        windows = 0
        for root, fut_symbol in futures_by_root.items():
            whale.rebuild_flow_minutes(ticks, out, fut_symbol, day)
            for ts in whale.snapshot_times(out, root, day):
                result = whale.evaluate_window(
                    out, root, fut_symbol, ts, source="nightly",
                    eod_score=whale.prior_eod_score(out, root, day))
                windows += result.get("status") == "ok"
            report.setdefault("whale_eod", {})[root] = whale.eod(out, day, root, fut_symbol)["status"]
        report["whale_windows"] = windows
        # An hour past the close: every horizon that can resolve has, and the
        # ones that cannot are left NULL on purpose.
        report["whale_outcomes"] = whale.fill_outcomes(out, whale.session_span(day)[1] + 3600)
        # The chain table is the one research table with no retention rule:
        # ~77,000 rows a session, and nothing reads an old intraday chain once
        # its windows are stored. Beyond the horizon, keep each day's close.
        report["chain_condensed"] = whale.condense_chain_snapshots(out, day)
    finally:
        for connection in (ticks, history_ro, out):
            connection.close()
    return report


def _session_row(connection: sqlite3.Connection, symbol: str, day: str) -> dict | None:
    cursor = connection.execute(
        "SELECT * FROM session_profiles WHERE symbol = ? AND day = ?", (symbol, day))
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(zip([d[0] for d in cursor.description], row))


def _recent_sessions(connection: sqlite3.Connection, symbol: str, day: str,
                     limit: int = 10) -> list[dict]:
    cursor = connection.execute(
        """SELECT * FROM (SELECT * FROM session_profiles WHERE symbol = ? AND day <= ?
           ORDER BY day DESC LIMIT ?) ORDER BY day""", (symbol, day, limit))
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def today_ist(now: datetime | None = None) -> str:
    return (now or datetime.now(IST)).astimezone(IST).date().isoformat()
