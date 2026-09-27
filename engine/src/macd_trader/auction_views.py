"""Read models for the auction page.

Everything built over the last two passes — session/period profiles, market
references, base rates, per-minute OFI, positional evaluations — is stored but
invisible. These are the queries the terminal needs to show it, kept out of
app.py so each one can be tested without standing up FastAPI.

All of them are read-only and degrade to empty rather than raising: this page
is context, and a missing table (a fresh install, a backfill not yet run) must
render an empty panel rather than a 500.
"""
from __future__ import annotations

import json
import sqlite3

from . import setups as setup_catalogue, whale as whale_layer
from .base_rates import compare_session, grouped_summaries, load, regime_of
from .profile_history import load_reference

MAX_FLOW_MINUTES = 400


def _read(path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def _rows(connection: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    try:
        return [dict(row) for row in connection.execute(sql, params)]
    except sqlite3.OperationalError:
        # The table has not been created yet — a backfill that has not run is
        # an empty panel, not an error.
        return []


def symbols(history_path: str) -> list[str]:
    connection = _read(history_path)
    try:
        return [row["symbol"] for row in _rows(
            connection, "SELECT DISTINCT symbol FROM session_profiles ORDER BY symbol")]
    finally:
        connection.close()


def sessions(history_path: str, symbol: str, limit: int = 120) -> list[dict]:
    connection = _read(history_path)
    try:
        rows = _rows(connection, """SELECT * FROM (
                SELECT * FROM session_profiles WHERE symbol = ? ORDER BY day DESC LIMIT ?
            ) ORDER BY day""", (symbol, limit))
    finally:
        connection.close()
    for row in rows:
        row["single_prints"] = _json_list(row.get("single_prints"))
    return rows


def periods(history_path: str, symbol: str, limit: int = 12) -> dict[str, list[dict]]:
    connection = _read(history_path)
    try:
        out: dict[str, list[dict]] = {}
        for period in ("week", "month"):
            rows = _rows(connection, """SELECT * FROM (
                    SELECT * FROM period_profiles WHERE symbol = ? AND period = ?
                    ORDER BY period_start DESC LIMIT ?
                ) ORDER BY period_start""", (symbol, period, limit))
            for row in rows:
                row["naked_pocs"] = _json_list(row.get("naked_pocs"))
            out[period] = rows
        return out
    finally:
        connection.close()


def _json_list(value) -> list:
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def context(history_path: str, symbol: str, as_of: str, price: float | None = None) -> dict:
    """The multi-timeframe read for one symbol on one session.

    ``price`` is the live mark when the desk is running and the prior close
    otherwise, so the same endpoint answers "where does this sit" during the
    session and after it.
    """
    reference = load_reference(history_path, symbol, as_of)
    latest = sessions(history_path, symbol, limit=1)
    mark = price
    if mark is None:
        mark = (reference.prior_day or {}).get("close")
    payload = {
        "symbol": symbol,
        "as_of": as_of,
        "price": mark,
        "regime": regime_of(as_of),
        "sessions_available": reference.sessions_available,
        "value_migration": reference.value_migration,
        "prior_day": reference.prior_day,
        "week": reference.week,
        "month": reference.month,
        "naked_pocs": reference.naked_pocs,
        "levels": reference.levels(),
        "location": reference.location(mark) if mark else {},
        "alignment": reference.alignment(mark) if mark else "unknown",
        "latest_session": latest[0] if latest else None,
    }
    return payload


def base_rates(history_path: str, symbol: str, as_of: str | None = None) -> dict:
    """Base-rate tables, plus how the newest measured session compares.

    The comparison uses the base rate for that session's OWN regime, never the
    pooled one: comparing today against an average that spans a lot-size change
    is how a normal session looks remarkable.
    """
    rows = load(history_path, symbol)
    grouped = grouped_summaries(rows)
    today = None
    if rows:
        measurement = rows[-1] if as_of is None else next(
            (row for row in rows if row["day"] == as_of), rows[-1])
        own = next((group for group in grouped["groups"]
                    if group["key"] == measurement.get("regime_id")), None)
        if own:
            today = {
                "day": measurement["day"],
                "regime_id": measurement.get("regime_id"),
                "regime_sessions": own["sessions"],
                "measurement": measurement,
                "comparisons": compare_session(measurement, own["summary"]),
            }
    return {"symbol": symbol, **grouped, "today": today}


def flow(ticks_path: str, symbol: str, day: str, limit: int = MAX_FLOW_MINUTES) -> dict:
    """Per-minute OFI, delta and classification confidence for one session.

    OFI and delta are returned as separate cumulative series on purpose. They
    are two different measurements of the same thing — one from the book, one
    inferred from prints — and where they diverge is the interesting part, so
    the page must never blend them into a single line.
    """
    connection = _read(ticks_path)
    try:
        rows = _rows(connection, """SELECT f.minute_ts, f.close, f.volume, f.delta,
                   f.buy_volume, f.sell_volume, f.trades, f.ofi, f.ofi_events,
                   f.avg_confidence, f.avg_spread
               FROM tick_minute_flow f JOIN tick_symbols s ON s.id = f.symbol_id
               WHERE s.symbol = ?
                 AND date(f.minute_ts,'unixepoch','+5 hours','+30 minutes') = ?
               ORDER BY f.minute_ts LIMIT ?""", (symbol, day, limit))
    finally:
        connection.close()
    cumulative_delta = cumulative_ofi = 0.0
    series = []
    for row in rows:
        cumulative_delta += row.get("delta") or 0
        cumulative_ofi += row.get("ofi") or 0
        series.append({
            "time": row["minute_ts"], "close": row.get("close"),
            "volume": row.get("volume") or 0, "delta": row.get("delta") or 0,
            "cvd": round(cumulative_delta, 2),
            "ofi": round(row.get("ofi") or 0, 2),
            "cumulative_ofi": round(cumulative_ofi, 2),
            "ofi_events": row.get("ofi_events") or 0,
            "confidence": row.get("avg_confidence"),
            "spread": row.get("avg_spread"),
        })
    confidences = [row["confidence"] for row in series if row["confidence"] is not None]
    return {
        "symbol": symbol, "day": day, "minutes": len(series), "series": series,
        "cumulative_delta": round(cumulative_delta, 2),
        "cumulative_ofi": round(cumulative_ofi, 2),
        # A sign disagreement between the two is the flag the blueprint calls
        # out: pressure entering the book that never becomes a print.
        "diverged": bool(series) and (cumulative_delta > 0) != (cumulative_ofi > 0),
        "mean_confidence": round(sum(confidences) / len(confidences), 3) if confidences else None,
    }


def flow_days(ticks_path: str, symbol: str, limit: int = 30) -> list[str]:
    connection = _read(ticks_path)
    try:
        rows = _rows(connection, """SELECT DISTINCT
                   date(f.minute_ts,'unixepoch','+5 hours','+30 minutes') AS day
               FROM tick_minute_flow f JOIN tick_symbols s ON s.id = f.symbol_id
               WHERE s.symbol = ? ORDER BY day DESC LIMIT ?""", (symbol, limit))
        return [row["day"] for row in rows]
    finally:
        connection.close()


def composite(history_path: str, ticks_path: str | None, symbol: str, days: int,
              as_of: str) -> dict:
    """An N-day composite profile, merged on demand from the stored daily ladders.

    The weekly and monthly tiers are built nightly on calendar boundaries;
    a trader's 3/5/20-day composite is a trailing window and would be stale
    by the afternoon if it were stored. Merging ladders is cheap enough to do
    per request, and it is the same merge build_period uses, so a composite
    POC here and a stored weekly one are one rule. ``days`` reports how many
    sessions were actually found -- an option contract or a freshly rolled
    futures series has few, and "3 of 20" is a different picture from "20".
    """
    from .profile_history import session_levels, value_area_from_levels

    history = _read(history_path)
    ticks = None
    try:
        if ticks_path:
            try:
                ticks = _read(ticks_path)
            except sqlite3.OperationalError:
                ticks = None
        stored = [row["day"] for row in _rows(
            history, "SELECT DISTINCT day FROM session_profiles WHERE symbol = ? AND day < ? ORDER BY day",
            (symbol, as_of))]
        chosen = stored[-days:] if days > 0 else []
        levels: dict[float, float] = {}
        for day in chosen:
            try:
                day_levels, _source, _extras = session_levels(ticks, history, symbol, day)
            except sqlite3.OperationalError:
                day_levels = {}
            for price, size in day_levels.items():
                levels[price] = levels.get(price, 0.0) + size
    finally:
        history.close()
        if ticks is not None:
            ticks.close()
    poc, vah, val = value_area_from_levels(levels) if levels else (None, None, None)
    return {
        "symbol": symbol, "days": len(chosen), "requested_days": days,
        "from": chosen[0] if chosen else None, "to": chosen[-1] if chosen else None,
        "poc": poc, "vah": vah, "val": val,
        "high": max(levels) if levels else None, "low": min(levels) if levels else None,
        "levels": [{"price": price, "volume": round(size)}
                   for price, size in sorted(levels.items(), reverse=True)],
    }


def raw_tick_days(ticks_path: str, symbol: str) -> list[str]:
    """IST sessions whose raw ticks are still on disk, newest first.

    Only these can be replayed print by print; older sessions have been
    condensed to minute flow and a ladder, which carry no individual prints.
    """
    try:
        connection = _read(ticks_path)
    except sqlite3.OperationalError:
        return []
    try:
        return [row["day"] for row in _rows(connection, """SELECT DISTINCT
                   date(t.ts_ms / 1000, 'unixepoch', '+5 hours', '+30 minutes') AS day
               FROM ticks t JOIN tick_symbols s ON s.id = t.symbol_id
               WHERE s.symbol = ? ORDER BY day DESC""", (symbol,))]
    finally:
        connection.close()


def positional(history_path: str, day: str | None = None, limit: int = 200) -> list[dict]:
    from .positional import evaluations
    try:
        return evaluations(history_path, day=day, limit=limit)
    except sqlite3.Error:
        return []


def setup_journal(history_path: str, symbol: str, regime_id: str | None = None) -> dict:
    connection = _read(history_path)
    try:
        return {
            "symbol": symbol, "regime_id": regime_id,
            "catalogue": setup_catalogue.catalogue(),
            "summary": setup_catalogue.journal_summary(connection, symbol, regime_id),
            "recent": setup_catalogue.journal_rows(connection, symbol, limit=60),
        }
    finally:
        connection.close()


WHALE_ROOTS = ("NIFTY", "BANKNIFTY")


def whale(history_path: str, day: str, symbol: str | None = None) -> dict:
    """Layer A events and aggression, the latest chain read, and per root the
    day's newest window, today's alerts, the last close and how much history
    the z-scores have to stand on.

    ``chains`` is always the newest snapshot whatever ``day`` says — it is the
    live chain block; ``windows``, ``alerts`` and ``eod`` are keyed by day.
    """
    connection = _read(history_path)
    try:
        events = whale_layer.events_for(connection, day, symbol)
        by_symbol: dict[str, list] = {}
        for row in events:
            by_symbol.setdefault(row["symbol"], []).append(
                whale_layer.WhaleEvent(row["symbol"], row["ts_ms"], row["kind"], row["side"],
                                       row["quantity"] or 0, row["price"] or 0.0,
                                       row["score"], row["evidence"] or ""))
        at = max((row["ts_ms"] for row in events), default=0)
        scores = {sym: whale_layer.aggression(evs, at) for sym, evs in by_symbol.items()}
        chains = {root: whale_layer.chain_window(connection, root) for root in WHALE_ROOTS}
        windows = {}
        for root in WHALE_ROOTS:
            rows = _rows(connection, """SELECT * FROM whale_windows WHERE underlying = ? AND day = ?
                                        ORDER BY ts DESC LIMIT 1""", (root, day))
            if rows:
                windows[root] = whale_layer.window_payload(connection, rows[0])
        eod = {row["underlying"]: _eod_payload(row) for row in _rows(
            connection, """SELECT * FROM whale_eod
                           WHERE day = (SELECT MAX(day) FROM whale_eod WHERE day <= ?)""", (day,))}
        alerts = [whale_layer.alert_payload(row) for row in _rows(
            connection, "SELECT * FROM whale_alerts WHERE day = ? ORDER BY ts DESC LIMIT 20", (day,))]
        # From the collector's own day ledger, not COUNT(DISTINCT date(ts, ...))
        # over every leg of every snapshot ever stored — that scan cannot use
        # an index and grows with the whole chain table, on every page load.
        chain_days = _rows(connection, "SELECT COUNT(DISTINCT day) AS n FROM chain_days")
        return {"day": day, "events": events[:80], "aggression": scores, "chains": chains,
                "windows": windows, "eod": eod, "alerts": alerts,
                "history": {"chain_days": chain_days[0]["n"] if chain_days else 0,
                            "required": whale_layer.HISTORY_DAYS}}
    finally:
        connection.close()


def _eod_payload(row: dict) -> dict:
    out = dict(row)
    for column in ("top_oi", "top_d_oi"):
        out[column] = _json_list(row.get(column))
    try:
        out["walls"] = json.loads(row.get("walls") or "{}")
    except (TypeError, ValueError):
        out["walls"] = {}
    return out


def whale_windows(history_path: str, underlying: str, day: str) -> dict:
    """The day's window series for one root: composite, the option and futures
    legs in rupees, and the divergence flag — what a chart of the day needs."""
    connection = _read(history_path)
    try:
        rows = _rows(connection, """SELECT ts, spot, net_dn, fut_dn, divergence, divergence_ratio,
                       composite, composite_decayed, z_a, z_b, z_c, pcr_oi, history_days
                   FROM whale_windows WHERE underlying = ? AND day = ? ORDER BY ts""", (underlying, day))
        return {"underlying": underlying, "day": day, "series": rows}
    finally:
        connection.close()


def whale_alerts(history_path: str, day: str, limit: int = 50) -> dict:
    """Composite alerts with what the spot did next, and the hit rate of the
    last twenty sessions at each horizon so the number is read with its
    denominator. An unresolved horizon counts in neither."""
    connection = _read(history_path)
    try:
        alerts = [whale_layer.alert_payload(row) for row in _rows(
            connection, "SELECT * FROM whale_alerts WHERE day = ? ORDER BY ts DESC LIMIT ?", (day, limit))]
        days = [row["day"] for row in _rows(
            connection, """SELECT DISTINCT day FROM whale_alerts WHERE day <= ? ORDER BY day DESC LIMIT ?""",
            (day, whale_layer.HISTORY_DAYS))]
        precision = {column: {"agreed": 0, "resolved": 0} for _, column in whale_layer.OUTCOME_HORIZONS}
        if days:
            for row in _rows(connection, "SELECT * FROM whale_alerts WHERE day >= ? AND day <= ?",
                             (days[-1], day)):
                decoded = whale_layer.alert_payload(row)
                for _, column in whale_layer.OUTCOME_HORIZONS:
                    outcome = decoded[column]
                    if outcome is None:
                        continue
                    precision[column]["resolved"] += 1
                    precision[column]["agreed"] += 1 if outcome.get("agreed") else 0
        return {"day": day, "alerts": alerts, "precision": precision, "sessions": len(days)}
    finally:
        connection.close()
