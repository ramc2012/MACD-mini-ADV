"""Live open interest, which the feed never carries.

The Fyers socket sends no OI on this account and /quotes has no OI field at all (checked 23 Oct 2026: 0 of
10,979 whale futures minutes since 29 Sep had one). Two REST calls do carry it, so the desk collects with those:

  index futures      /depth, one symbol per call, every minute -> oi_minutes (and the whale tracker's futures leg)
  index chains       options-chain-v3 every minute for NIFTY, BANKNIFTY, SENSEX, MIDCPNIFTY -> chain_snapshots
  stock chains       options-chain-v3 for every F&O stock, a few strikes around the money, in a sweep spread over
                     each STOCK_SWEEP_MINUTES window -> chain_snapshots (underlying = the stock's name); the next
                     series too in the last week of a series, when the lanes move their new risk to it

Research (research/oi_flow_20261024) found OI tells the SIZE of the coming move and the day type, not its
direction, so these rows feed size and day-type reads; nothing here is a signal on its own.
"""
from __future__ import annotations

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS oi_minutes (
  ts INTEGER NOT NULL,
  symbol TEXT NOT NULL,
  oi INTEGER NOT NULL,
  prev_oi INTEGER,
  ltp REAL,
  volume INTEGER,
  PRIMARY KEY (symbol, ts)
);
"""

NEXT_SERIES_WITHIN_DAYS = 7      # calendar days to the front expiry from which the next series is collected too
STOCK_STRIKES = 4                # strikes either side of the money per stock chain
STOCK_SWEEP_MINUTES = 15         # one sweep of every F&O stock per window
STOCK_CALL_GAP_SECONDS = 2.5     # spacing between stock chain calls (~24 a minute, well inside Fyers' 200/min)


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)


def save_oi(connection: sqlite3.Connection, ts: int, rows: dict[str, dict]) -> int:
    """One minute of futures OI: ``rows`` maps symbol -> the broker's open_interest() answer."""
    ensure_schema(connection)
    values = [(ts, symbol, int(row["oi"]), row.get("prev_oi"), row.get("ltp"), row.get("volume"))
              for symbol, row in rows.items() if row and row.get("oi") is not None]
    if values:
        with connection:
            connection.executemany("INSERT OR REPLACE INTO oi_minutes (ts, symbol, oi, prev_oi, ltp, volume) "
                                   "VALUES (?,?,?,?,?,?)", values)
    return len(values)


def next_expiry_token(chain, front: str, within_days: int, today) -> str | None:
    """The token of the series after ``front`` when ``front`` expires within ``within_days``, else None."""
    from .rollover import days_to_expiry
    left = days_to_expiry(front, today)
    if left is None or left > within_days:
        return None
    later = sorted((row for row in (getattr(chain, "expiries", None) or []) if row.date and row.date > front),
                   key=lambda row: row.date)
    return str(later[0].token) if later and later[0].token else None
