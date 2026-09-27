"""CE/PE MACD breadth across the ATM option cohort, and its durable store.

Until now this series existed only in the browser's localStorage: it was
computed from streamed indicator events, kept per IST day, and lost whenever
the tab's storage was cleared.  Nothing could be measured against it because
nothing outside that one browser had ever seen it.

This module owns one definition of the measurement and one table, written by
two producers:

* the live engine, sampling its own ATM cohort once a bar (``source='live'``);
* the offline reconstruction in ``research/dispersion_reconstruct.py``, which
  rebuilds the cohort from stored option minute bars (``source='reconstructed'``).

A live row always outranks a reconstructed one for the same bar.  The live row
is what the desk actually watched; the reconstruction only infers which
contracts *would* have been selected, because the daily ATM choice is recorded
nowhere durable -- ``runtime/atm_contracts.json`` is overwritten each morning.
So a re-run of the backfill never overwrites live history.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite

SCHEMA = """
CREATE TABLE IF NOT EXISTS dispersion_history (
  timeframe_seconds INTEGER NOT NULL,
  timestamp INTEGER NOT NULL,
  ce_above INTEGER NOT NULL,
  pe_above INTEGER NOT NULL,
  ce_eligible INTEGER NOT NULL,
  pe_eligible INTEGER NOT NULL,
  total INTEGER NOT NULL,
  source TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  PRIMARY KEY (timeframe_seconds, timestamp)
)
"""
LIVE = "live"
RECONSTRUCTED = "reconstructed"


@dataclass(frozen=True)
class DispersionPoint:
    timestamp: int
    ce_above: int
    pe_above: int
    ce_eligible: int
    pe_eligible: int
    total: int

    def as_dict(self) -> dict:
        return {
            "time": self.timestamp,
            "ceAbove": self.ce_above,
            "peAbove": self.pe_above,
            "ceEligible": self.ce_eligible,
            "peEligible": self.pe_eligible,
            "total": self.total,
        }


def breadth_series(rows: list[tuple[str, int, float]], total: int) -> list[DispersionPoint]:
    """Count positive-MACD contracts per bar.

    ``rows`` are ``(side, timestamp, macd)`` triples.  A contract counts toward
    a bar only if it actually printed that bar: a dormant contract keeps its
    last MACD forever, and carrying that value forward would make the breadth
    look precise while describing a market that has moved on.  Eligibility is
    therefore per bar, while ``total`` stays the size of the whole cohort so
    the coverage gap remains visible instead of being silently absorbed.
    """
    buckets: dict[int, list[tuple[str, float]]] = {}
    for side, timestamp, macd in rows:
        if macd is None or not isfinite(macd) or timestamp is None:
            continue
        buckets.setdefault(int(timestamp), []).append((side, float(macd)))
    points = []
    for timestamp in sorted(buckets):
        fresh = buckets[timestamp]
        ce = [macd for side, macd in fresh if side == "CE"]
        pe = [macd for side, macd in fresh if side == "PE"]
        points.append(DispersionPoint(
            timestamp=timestamp,
            ce_above=sum(1 for macd in ce if macd > 0),
            pe_above=sum(1 for macd in pe if macd > 0),
            ce_eligible=len(ce),
            pe_eligible=len(pe),
            total=total,
        ))
    return points


def latest_breadth(rows: list[tuple[str, int, float]], total: int) -> DispersionPoint | None:
    """The newest completed cohort, given one latest point per contract.

    This is the live engine's view and matches what the terminal has always
    shown: contracts still sitting on an older bar are excluded rather than
    mixed into the current count.
    """
    series = breadth_series(rows, total)
    return series[-1] if series else None


def ensure_table(connection: sqlite3.Connection) -> None:
    connection.execute(SCHEMA)


def record(database_path: str, timeframe_seconds: int, points: list[DispersionPoint], source: str) -> int:
    """Upsert breadth rows. A reconstruction never displaces a live row."""
    if not points:
        return 0
    guard = "" if source == LIVE else " WHERE dispersion_history.source <> 'live'"
    stamp = datetime.now(UTC).isoformat()
    connection = sqlite3.connect(database_path, timeout=30)
    try:
        ensure_table(connection)
        with connection:
            connection.executemany(
                f"""INSERT INTO dispersion_history
                    (timeframe_seconds,timestamp,ce_above,pe_above,ce_eligible,pe_eligible,total,source,recorded_at)
                    VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(timeframe_seconds,timestamp) DO UPDATE SET
                      ce_above=excluded.ce_above, pe_above=excluded.pe_above,
                      ce_eligible=excluded.ce_eligible, pe_eligible=excluded.pe_eligible,
                      total=excluded.total, source=excluded.source,
                      recorded_at=excluded.recorded_at{guard}""",
                [
                    (timeframe_seconds, point.timestamp, point.ce_above, point.pe_above,
                     point.ce_eligible, point.pe_eligible, point.total, source, stamp)
                    for point in points
                ],
            )
        return len(points)
    finally:
        connection.close()


def load(database_path: str, timeframe_seconds: int, since: int = 0, limit: int = 5000) -> list[dict]:
    connection = sqlite3.connect(database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        ensure_table(connection)
        rows = connection.execute(
            """SELECT * FROM (
                 SELECT timestamp,ce_above,pe_above,ce_eligible,pe_eligible,total,source
                 FROM dispersion_history
                 WHERE timeframe_seconds=? AND timestamp>=?
                 ORDER BY timestamp DESC LIMIT ?
               ) ORDER BY timestamp""",
            (timeframe_seconds, since, limit),
        ).fetchall()
        return [
            {
                "time": row["timestamp"], "ceAbove": row["ce_above"], "peAbove": row["pe_above"],
                "ceEligible": row["ce_eligible"], "peEligible": row["pe_eligible"],
                "total": row["total"], "source": row["source"],
            }
            for row in rows
        ]
    finally:
        connection.close()


def coverage(database_path: str) -> list[dict]:
    """Per-day row counts and provenance — what the series actually contains."""
    connection = sqlite3.connect(database_path, timeout=30)
    try:
        ensure_table(connection)
        return [
            {"day": day, "timeframe_seconds": timeframe, "bars": bars, "sources": sources}
            for day, timeframe, bars, sources in connection.execute(
                """SELECT date(timestamp,'unixepoch','+5 hours','+30 minutes') AS day,
                          timeframe_seconds, COUNT(*), GROUP_CONCAT(DISTINCT source)
                   FROM dispersion_history GROUP BY day, timeframe_seconds ORDER BY day, timeframe_seconds"""
            )
        ]
    finally:
        connection.close()
