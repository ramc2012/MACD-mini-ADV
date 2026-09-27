"""Summarising the session measurements into base rates.

Lives in the package rather than in research/ because two callers need exactly
the same arithmetic: the offline report and the terminal's auction page. A rate
that differed between the two would be worse than either.

Every rate is returned as ``(hits, total)`` and never as a bare percentage. The
interesting rates here are the rare ones — the 80% rule fires on a minority of
sessions — and a percentage without its denominator invites reading 50% from
ten triggers as though it meant something.
"""
from __future__ import annotations

import sqlite3

from .regimes import REGIMES, regime_for, spans_a_break

EXTENSION_THRESHOLDS = (0.25, 0.5, 1.0, 2.0)


def _rate(rows: list[dict], predicate, among=None) -> tuple[int, int]:
    pool = [row for row in rows if among is None or among(row)]
    return sum(1 for row in pool if predicate(row)), len(pool)


def median(values: list) -> float | None:
    ordered = sorted(value for value in values if value is not None)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def summarise(rows: list[dict]) -> dict:
    """The base-rate table for one homogeneous group of sessions."""
    measured = [row for row in rows if row.get("ib_width")]
    out = {
        "sessions": len(rows),
        "sessions_with_ib": len(measured),
        "ib_broken": _rate(measured, lambda r: r.get("break_side") not in (None, "none")),
        "break_up_only": _rate(measured, lambda r: r.get("break_side") == "up"),
        "break_down_only": _rate(measured, lambda r: r.get("break_side") == "down"),
        "break_both": _rate(measured, lambda r: r.get("break_side") == "both"),
        "first_break_in_C": _rate(
            measured, lambda r: r.get("first_break_bracket") == "C",
            among=lambda r: r.get("first_break_bracket") is not None),
        "first_break_in_D": _rate(
            measured, lambda r: r.get("first_break_bracket") == "D",
            among=lambda r: r.get("first_break_bracket") is not None),
        "median_extension_ratio": median([r.get("extension_ratio") for r in measured]),
        "median_range_ib_ratio": median([r.get("range_ib_ratio") for r in measured]),
        "opened_outside_value": _rate(
            rows, lambda r: r.get("opened_outside_prior_value") == 1,
            among=lambda r: r.get("opened_outside_prior_value") is not None),
        "returned_to_value": _rate(
            rows, lambda r: r.get("returned_to_prior_value") == 1,
            among=lambda r: r.get("opened_outside_prior_value") == 1),
        "rule80_triggered": _rate(
            rows, lambda r: r.get("rule80_triggered") == 1,
            among=lambda r: r.get("opened_outside_prior_value") == 1),
        "rule80_completed": _rate(
            rows, lambda r: r.get("rule80_completed") == 1,
            among=lambda r: r.get("rule80_triggered") == 1),
        "gapped": _rate(rows, lambda r: r.get("gap") == 1,
                        among=lambda r: r.get("gap") is not None),
        "gap_filled": _rate(rows, lambda r: r.get("gap_filled") == 1,
                            among=lambda r: r.get("gap") == 1),
    }
    for threshold in EXTENSION_THRESHOLDS:
        out[f"extension_over_{int(threshold * 100)}pct"] = _rate(
            measured, lambda r, t=threshold: (r.get("extension_ratio") or 0) >= t)
    return out


def load(database_path: str, symbol: str, start: str = "",
                      end: str = "") -> list[dict]:
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in connection.execute(
            "SELECT * FROM session_base_rates WHERE symbol = ? ORDER BY day", (symbol,))]
    except sqlite3.OperationalError:
        return []
    finally:
        connection.close()
    return [row for row in rows
            if (not start or row["day"] >= start) and (not end or row["day"] <= end)]


def grouped_summaries(rows: list[dict], by_regime: bool = True) -> dict:
    """Pooled and per-regime tables, plus a warning when pooling is unsafe."""
    if not rows:
        return {"span": None, "groups": [], "crosses": []}
    span = (rows[0]["day"], rows[-1]["day"])
    groups = [{"key": "all", "title": "All sessions", "sessions": len(rows),
               "summary": summarise(rows)}]
    if by_regime:
        for regime in REGIMES:
            members = [row for row in rows if row.get("regime_id") == regime.regime_id]
            if members:
                groups.append({
                    "key": regime.regime_id, "title": regime.summary,
                    "start": regime.start, "sessions": len(members),
                    "summary": summarise(members),
                })
    return {"span": span, "groups": groups, "crosses": spans_a_break(*span)}


def compare_session(measurement: dict, summary: dict) -> list[dict]:
    """Where one session sits against the base rate for its own regime.

    The point of the table is not the session's numbers on their own — it is
    whether today is ordinary. A 40% extension means nothing until you know
    that 58% of sessions extend at least that far.
    """
    out = []
    ratio = measurement.get("extension_ratio")
    if ratio is not None:
        for threshold in EXTENSION_THRESHOLDS:
            hits, total = summary.get(f"extension_over_{int(threshold * 100)}pct", (0, 0))
            if total:
                out.append({
                    "label": f"extension ≥ {int(threshold * 100)}% of IB",
                    "today": ratio >= threshold,
                    "base_rate": round(100 * hits / total, 1),
                    "sample": total,
                })
    for key, label, among in (
            ("ib_broken", "IB broken", "break_side"),
            ("gapped", "gapped beyond prior range", "gap"),
            ("opened_outside_value", "opened outside prior value",
             "opened_outside_prior_value")):
        hits, total = summary.get(key, (0, 0))
        value = measurement.get(among)
        if total and value is not None:
            today = value not in (None, "none", 0)
            out.append({"label": label, "today": today,
                        "base_rate": round(100 * hits / total, 1), "sample": total})
    return out


def regime_of(day: str) -> dict:
    regime = regime_for(day)
    return {"regime_id": regime.regime_id, "summary": regime.summary,
            "start": regime.start, "session_end": regime.session_end,
            "nifty_lot": regime.nifty_lot, "banknifty_lot": regime.banknifty_lot,
            "nifty_futures_tick": regime.nifty_futures_tick,
            "weekly_expiry": regime.nifty_weekly_expiry}
