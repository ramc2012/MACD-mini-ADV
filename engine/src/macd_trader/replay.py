"""Replay a recorded session through the live desk's own building blocks.

The blueprint asks for "the same engine on replay". MPEngine cannot be
pointed at a recorded day -- it keys the session off the wall clock and
rejects ticks dated another day, both deliberately -- so the replay drives
the objects the engine drives, in the order the engine drives them:
OrderFlowTracker (classification, CVD, tape speed), ProfileBook (TPO
structure), FootprintBook (clusters, markers) and OFIState (book pressure).
What it does not run is the setup evaluator and the paper book; a replay
shows what the desk SAW, not what it would have traded.

Only sessions whose raw ticks are still on disk (``tick_retention_days``) can
be replayed. Condensed days carry minute flow and a ladder, not prints.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from threading import Lock

from .footprint import FootprintBook
from .market_profile import ProfileBook
from .models import Tick
from .mp_engine import session_view
from .ofi import OFIState
from .orderflow import OrderFlowTracker
from .profile_history import session_bounds


def load_session_ticks(ticks_path: str, symbol: str, start: float, end: float) -> list[Tick]:
    """One symbol's raw ticks in [start, end) epoch seconds, oldest first.

    Read-only, and read from INSIDE the container that owns the writer: host
    reads of this bind-mounted file return torn pages while the tick writer is
    live. A missing archive is an empty session, not an error.
    """
    try:
        connection = sqlite3.connect(f"file:{ticks_path}?mode=ro", uri=True, timeout=30)
    except sqlite3.OperationalError:
        return []
    try:
        rows = connection.execute(
            """SELECT t.ts_ms, t.ltp, t.cum_volume, t.last_qty, t.bid, t.ask,
                      t.bid_qty, t.ask_qty, t.oi, t.tbq, t.tsq
               FROM ticks t JOIN tick_symbols s ON s.id = t.symbol_id
               WHERE s.symbol = ? AND t.ts_ms >= ? AND t.ts_ms < ? ORDER BY t.ts_ms""",
            (symbol, int(start * 1000), int(end * 1000))).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        connection.close()
    return [
        Tick(symbol, float(ltp), int(cum_volume or 0),
             timestamp=datetime.fromtimestamp(ts_ms / 1000.0, UTC),
             bid=bid, ask=ask, bid_qty=bid_qty, ask_qty=ask_qty, last_qty=last_qty,
             total_buy_qty=tbq, total_sell_qty=tsq, open_interest=oi)
        for ts_ms, ltp, cum_volume, last_qty, bid, ask, bid_qty, ask_qty, oi, tbq, tsq in rows
    ]


def session_prints(ticks_path: str, symbol: str, day: str) -> list:
    """Today's stored ticks for ``symbol`` classified into prints, oldest first.

    The same OrderFlowTracker the live desk runs, over the same ticks in the
    same order, so these are the prints the desk classified during the
    session. Seeds a footprint opened mid-session from the open rather than
    from the tracker's last 400 prints -- on 29 Sep 2026 a HDFCBANK chart
    opened after the close began at 15:05. CPU-bound: call it off the loop.
    """
    start, end = session_bounds(day, symbol)
    tracker = OrderFlowTracker()
    prints = []
    for tick in load_session_ticks(ticks_path, symbol, start, end):
        row = tracker.on_tick(tick)
        if row is not None:
            prints.append(row)
    return prints


class ReplaySession:
    """One symbol, one recorded IST session, rebuilt as of any moment.

    The objects advance monotonically: playing forward feeds only the ticks
    between the last position and the new one, so a 1 s scrubber step costs a
    few hundred ticks. Seeking BACKWARD cannot rewind them, so it rebuilds from
    the open -- the whole session at worst.

    **That rebuild MUST NOT run on the event loop.** Measured in the container
    on the docstring's own case (120k ticks, ~10^5 on a NIFTY future) it is
    2.5 s, not the "well under a second" this once claimed, and a single drag
    of the scrubber from 15:20 back to 15:00 rebuilds ~95% of the session. On
    the FastAPI loop that is 2.5 s in which live tick ingestion, position
    marking and the forced-exit check do not run. Declare the route ``def``
    (FastAPI then runs it in a worker thread) or await it in an executor.

    Checkpointing state at bracket boundaries was measured as the worse trade:
    deep-copying the four objects thirteen times cost 110 MB of RSS for one
    session and doubled the cost of the FORWARD walk -- the common case, since
    playback only ever moves forward -- to buy a backward seek that still took
    0.6 s. A worker thread costs nothing and removes the actual harm.
    """

    def __init__(self, symbol: str, day: str, timeframe_seconds: int = 60):
        self.symbol = symbol
        self.day = day
        self.timeframe = timeframe_seconds
        self.session_start, self.session_end = session_bounds(day, symbol)
        self.ticks: list[Tick] = []
        self._lock = Lock()
        self._reset()

    # -- loading -------------------------------------------------------------

    def build(self, ticks_path: str) -> "ReplaySession":
        self.ticks = load_session_ticks(ticks_path, self.symbol, self.session_start, self.session_end)
        return self

    # -- driving -------------------------------------------------------------

    def _reset(self) -> None:
        self.flow = OrderFlowTracker()
        self.profiles = ProfileBook()
        self.book = FootprintBook(timeframe_seconds=self.timeframe)
        self.book.watch(self.symbol, day=self.day)
        self.ofi = OFIState(symbol=self.symbol)
        self._position = 0
        self._at = self.session_start

    def _advance(self, at: float) -> None:
        if at < self._at:
            self._reset()
        while self._position < len(self.ticks):
            tick = self.ticks[self._position]
            stamp = tick.timestamp.timestamp()
            if stamp > at:
                break
            self._position += 1
            # Same order as MPEngine.on_tick: flow first (it decides whether
            # this update is a print), profile from every tick, book and OFI
            # from every quote, clusters from the print.
            print_row = self.flow.on_tick(tick)
            self.profiles.on_print(self.symbol, tick.ltp, print_row.size if print_row else 0,
                                   tick.timestamp)
            self.book.on_quote(self.symbol, tick)
            self.ofi.update(stamp, tick.bid, tick.ask, tick.bid_qty, tick.ask_qty)
            if print_row is not None:
                self.book.on_print(self.symbol, print_row.timestamp, print_row.price,
                                   print_row.size, print_row.side,
                                   print_row.method, print_row.confidence)
        self._at = at

    def payload(self, at: float, bars: int = 60, timeframe_seconds: int | None = None,
                context: dict | None = None, scale: dict | None = None) -> dict:
        """Everything the live /api/mp/footprint returns, as of ``at`` (epoch s)."""
        moment = min(max(float(at), float(self.session_start)), float(self.session_end))
        with self._lock:
            self._advance(moment)
            flow = self.flow.snapshot(self.symbol)
            out = self.book.payload(self.symbol, bars, timeframe_seconds=timeframe_seconds,
                                    flow=flow)
            profile = self.profiles.get(self.symbol)
            # The same ATR rows the live desk publishes for this symbol and day.
            profile_snapshot = profile.snapshot(
                row_size=scale["row_size"] if scale else None, scale=scale) if profile else None
            out["profile"] = profile_snapshot
            out["flow"] = flow or None
            out["ofi"] = (self.ofi.snapshot(moment, timeframe_seconds or self.timeframe)
                          if self.ofi.last is not None else None)
            out["tape_speed"] = self.flow.tape_speed(self.symbol, at=moment)
            # One profile snapshot, read twice -- session_view took its own.
            out["session"] = session_view(
                profile, flow=self.flow.states.get(self.symbol), context=context,
                reference_price=profile.last_price, day=self.day,
                snapshot=profile_snapshot,
            ) if profile else None
        out["setup"] = {"setup": None, "reason": "replay: setups are not evaluated"}
        out["replay"] = {
            "day": self.day, "at": int(moment),
            "session_start": self.session_start, "session_end": self.session_end,
            "ticks": len(self.ticks), "position": self._position,
        }
        return out


def merge_seed_prints(stored: list, recent: list) -> list:
    """Stored session prints joined to the live tracker's newest, without overlap.

    The tick store flushes in batches, so the newest seconds are only in the
    tracker's ``recent``. Exchange stamps have one-second resolution, so the
    join is on a whole second: when ``recent`` reaches back to the store's
    last second, that second comes from ``recent`` alone (it holds every print
    of it); otherwise the store's copy is kept and only later prints added.
    """
    if not stored:
        return list(recent)
    edge = stored[-1].timestamp
    if recent and recent[0].timestamp <= edge:
        return [row for row in stored if row.timestamp < edge] + [row for row in recent if row.timestamp >= edge]
    return list(stored) + [row for row in recent if row.timestamp > edge]
