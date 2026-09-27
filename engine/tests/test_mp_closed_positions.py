"""A closed desk position stays on the page until the next morning.

Portfolio.apply_trade deletes a flat position outright, so the row vanished
from the Positions tab on the next poll after the exit fill, and nothing
recorded the exit price, time or reason per position. The desk now freezes
the round trip at the fill, keeps it until 06:00 IST the following day,
and tracks MFE/MAE per open position from the in-session marks.
"""
from __future__ import annotations

import asyncio
import tempfile
from datetime import UTC, datetime, timedelta, timezone

import pytest

from macd_trader.market_profile import IST
from macd_trader.models import Tick, Trade
from macd_trader.mp_engine import EXCURSION_KEYS, MPEngine, MPSettings

pytestmark = pytest.mark.usefixtures("fixed_auction_session_clock")

SYM = "NSE:SBIN26SEP780CE"
LOT = 375


def _desk(folder: str, **overrides) -> MPEngine:
    desk = MPEngine(f"{folder}/mp.sqlite3", settings=MPSettings(enabled=True, **overrides))
    desk.session_day = datetime.now(IST).date().isoformat()
    desk.set_lot_sizes({SYM: LOT})
    return desk


def _open(desk: MPEngine, price: float = 40.0, quantity: int = 750) -> None:
    """Open without the BUY session gate, the way test_directional_desk does,
    but through the trade book too so statistics() sees the same entry."""
    trade = Trade("entry", SYM, "BUY", quantity, price, lots=quantity // LOT,
                  lot_size=LOT, fees=20.0, timestamp=datetime.now(UTC) - timedelta(seconds=1))
    desk.portfolio.apply_trade(trade)
    desk.repository.save_trade(trade)
    desk.portfolio.positions[SYM].hard_stop = round(price * 0.75, 4)
    desk.entry_state[SYM] = {"setup": "responsive_buy", "option_type": "CE", "entry_fees": 20.0,
                             "entered_at": datetime.now(UTC).isoformat()}


def _mark(desk: MPEngine, price: float) -> None:
    desk.last_prices[SYM] = price
    desk.last_price_at[SYM] = datetime.now(UTC)
    desk._track_excursion(SYM, price, datetime.now(UTC))


def _tick(price: float, minute: int) -> Tick:
    """Stamped inside today's session (09:15 = 555) so the in-session mark runs."""
    today = datetime.now(IST).date()
    moment = datetime(today.year, today.month, today.day, tzinfo=IST) + timedelta(minutes=minute)
    return Tick(SYM, price, 5, timestamp=moment.astimezone(timezone.utc))


def test_hard_stop_exit_becomes_a_closed_today_row():
    with tempfile.TemporaryDirectory() as folder:
        desk = _desk(folder)
        _open(desk)
        for price in (44.0, 46.2, 38.9, 29.0):
            _mark(desk, price)
        asyncio.run(desk._manage_position(SYM, 29.0, 11 * 60))

        assert SYM not in desk.portfolio.positions
        assert SYM not in desk.entry_state
        row = desk.snapshot()["closed_positions"][0]
        assert row["symbol"] == SYM and row["exit_reason"] == "MP_HARD_STOP"
        assert row["setup"] == "responsive_buy" and row["partial"] is False
        assert row["entry_price"] == 40.0 and row["quantity"] == 750 and row["lots"] == 2
        assert row["max_price"] == 46.2 and row["min_price"] == 29.0
        assert row["max_return_pct"] == 15.5 and row["min_return_pct"] == -27.5
        assert row["visible_until"].endswith("T08:00:00+05:30")
        assert row["exit_time"].endswith("+05:30")
        # Same basis as the Trades tab round trip: exit fee plus pro-rata entry fee.
        assert round(row["pnl"], 4) == round(desk.statistics()["round_trip_rows"][-1]["pnl"], 4)
        assert desk.statistics()["open_by_setup"] == []


def test_eod_failsafe_close_records_the_note_as_the_reason():
    with tempfile.TemporaryDirectory() as folder:
        desk = _desk(folder)
        _open(desk)
        _mark(desk, 41.0)
        asyncio.run(desk.close_positions())
        row = desk.closed_positions[0]
        assert row["exit_reason"] == "MP_EOD_FAILSAFE"
        assert SYM not in desk.portfolio.positions

    with tempfile.TemporaryDirectory() as folder:
        desk = _desk(folder)
        _open(desk)
        _mark(desk, 41.0)
        assert asyncio.run(desk.submit(SYM, "SELL", 750)) is not None
        assert desk.closed_positions[0]["exit_reason"] == "MP_MANUAL"


def test_excursion_is_tracked_on_marks_and_published_in_snapshot_positions():
    with tempfile.TemporaryDirectory() as folder:
        desk = _desk(folder)
        _open(desk, price=100.0)
        for price, minute in ((100.0, 560), (118.0, 600), (92.0, 640), (105.0, 700)):
            asyncio.run(desk.on_tick(_tick(price, minute)))

        state = desk.entry_state[SYM]
        assert (state["max_price"], state["min_price"]) == (118.0, 92.0)
        assert (state["max_return_pct"], state["min_return_pct"]) == (18.0, -8.0)
        assert datetime.fromisoformat(state["max_at"]) <= datetime.fromisoformat(state["min_at"])

        position = desk.snapshot()["portfolio"]["positions"][0]
        assert all(key in position for key in EXCURSION_KEYS)
        assert position["max_return_pct"] == 18.0 and position["min_return_pct"] == -8.0

        # A pre-open re-broadcast marks the book but is not an excursion the
        # trade could have been exited on, exactly like peak_price.
        asyncio.run(desk.on_tick(_tick(50.0, 8 * 60 + 30)))
        assert desk.portfolio.positions[SYM].last_price == 50.0
        assert desk.entry_state[SYM]["min_price"] == 92.0


def test_closed_rows_survive_a_restart_and_the_date_keyed_session_reset():
    with tempfile.TemporaryDirectory() as folder:
        desk = _desk(folder)
        _open(desk)
        _mark(desk, 41.0)
        asyncio.run(desk.submit(SYM, "SELL", 750, note="MP_TRAIL"))
        closed_id = desk.closed_positions[0]["id"]

        # Restart: the row is on the page before any tick has arrived.
        restarted = MPEngine(f"{folder}/mp.sqlite3", settings=MPSettings(enabled=True))
        assert [row["id"] for row in restarted.closed_positions] == [closed_id]
        assert restarted.snapshot()["closed_positions"][0]["exit_reason"] == "MP_TRAIL"

        # The session roll clears the auction state and entry_state, not this.
        desk.session_day = (datetime.now(IST).date() - timedelta(days=1)).isoformat()
        desk.entry_state["NSE:OTHER"] = {"setup": "stale"}
        asyncio.run(desk.on_tick(_tick(41.0, 600)))
        assert desk.session_day == datetime.now(IST).date().isoformat()
        assert "NSE:OTHER" not in desk.entry_state
        assert [row["id"] for row in desk.closed_positions] == [closed_id]


def test_closed_rows_are_purged_after_the_roll_next_day():
    assert MPEngine._visible_until(datetime(2026, 9, 2, 14, 5, tzinfo=IST)) == datetime(2026, 9, 3, 8, 0, tzinfo=IST)
    # A UTC exit stamp after 18:30Z is already the next IST date.
    assert MPEngine._visible_until(datetime(2026, 9, 2, 20, 0, tzinfo=UTC)) == datetime(2026, 9, 4, 8, 0, tzinfo=IST)


def test_a_friday_auction_exit_survives_the_weekend():
    """The auction desk used to stamp the next CALENDAR day, so every Friday
    round trip disappeared on Saturday morning while the MACD lane kept its
    own until Monday. Both lanes now share portfolio.closed_visible_until."""
    friday = datetime(2026, 9, 4, 15, 20, tzinfo=IST)
    assert friday.weekday() == 4
    assert MPEngine._visible_until(friday) == datetime(2026, 9, 7, 8, 0, tzinfo=IST)


def test_both_lanes_stamp_the_same_boundary():
    from macd_trader.portfolio import closed_visible_until
    for moment in (datetime(2026, 9, 2, 14, 5, tzinfo=IST),
                   datetime(2026, 9, 4, 15, 20, tzinfo=IST),
                   datetime(2026, 9, 2, 20, 0, tzinfo=UTC)):
        assert MPEngine._visible_until(moment) == closed_visible_until(moment), moment

    with tempfile.TemporaryDirectory() as folder:
        desk = _desk(folder)
        _open(desk)
        _mark(desk, 41.0)
        asyncio.run(desk.submit(SYM, "SELL", 750))
        until = datetime.fromisoformat(desk.closed_positions[0]["visible_until"])

        desk._prune_closed(until - timedelta(seconds=1))
        assert len(desk.closed_positions) == 1
        # UTC on every read: the column is UTC-normalised and compared as a string.
        assert len(desk.repository.mp_closed_positions(
            (until - timedelta(seconds=1)).astimezone(UTC).isoformat(timespec="seconds"))) == 1

        desk._prune_closed(until + timedelta(seconds=1))
        assert desk.closed_positions == []
        assert desk.repository.mp_closed_positions(
            (until + timedelta(seconds=1)).astimezone(UTC).isoformat(timespec="seconds")) == []
        # Physically gone, not merely filtered: a restart must not resurrect it.
        assert desk.repository.mp_closed_positions("2000-01-01T00:00:00+00:00") == []


def test_partial_exit_records_a_partial_row_and_keeps_the_position():
    with tempfile.TemporaryDirectory() as folder:
        desk = _desk(folder)
        _open(desk)
        _mark(desk, 44.0)
        desk.entry_state[SYM]["exit_reason"] = "MP_TRAIL"
        asyncio.run(desk.submit(SYM, "SELL", 375))

        first = desk.closed_positions[0]
        assert first["partial"] is True and first["quantity"] == 375 and first["lots"] == 1
        assert first["exit_reason"] == "MP_TRAIL"
        assert first["fees"] == 30.0                    # exit leg + half the entry leg
        assert desk.portfolio.positions[SYM].quantity == 375
        assert desk.entry_state[SYM]["entry_fees"] == 10.0
        assert "exit_reason" not in desk.entry_state[SYM]

        asyncio.run(desk.submit(SYM, "SELL", 375))
        second = desk.closed_positions[0]
        assert second["partial"] is False and second["fees"] == 30.0
        assert second["exit_reason"] == "MP_MANUAL"
        assert SYM not in desk.entry_state and SYM not in desk.portfolio.positions
        rounds = desk.statistics()["round_trip_rows"]
        assert round(first["pnl"] + second["pnl"], 4) == round(sum(r["pnl"] for r in rounds), 4)


def test_carried_position_keeps_its_excursion_across_the_day_roll():
    with tempfile.TemporaryDirectory() as folder:
        desk = _desk(folder, allow_overnight_carry=True)
        _open(desk)
        for price in (46.2, 38.9):
            _mark(desk, price)
        desk.session_day = (datetime.now(IST).date() - timedelta(days=1)).isoformat()
        asyncio.run(desk.on_tick(_tick(41.0, 600)))

        assert SYM in desk.portfolio.positions
        state = desk.entry_state[SYM]
        assert (state["max_price"], state["min_price"]) == (46.2, 38.9)
        assert state["setup"] == "responsive_buy"


def test_a_restart_keeps_rows_that_are_still_inside_their_window():
    """The retention column is UTC-normalised and the repository compares it
    as a STRING. _restore used to pass an IST stamp, so on the same calendar
    date '2026-09-11T02:30:00+00:00' > '2026-09-11T07:36:31+05:30' compared
    False and every closed row vanished on restart -- from midnight IST, not
    at the retention hour at all -- while the rows sat intact in the database.
    """
    with tempfile.TemporaryDirectory() as folder:
        desk = _desk(folder)
        _open(desk)
        _mark(desk, 41.0)
        asyncio.run(desk.submit(SYM, "SELL", 750))
        assert len(desk.closed_positions) == 1
        stored = desk.closed_positions[0]["visible_until"]
        desk.repository.close()

        # A fresh engine on the same book, well before the window closes.
        restarted = MPEngine(f"{folder}/mp.sqlite3", settings=MPSettings(enabled=True))
        assert len(restarted.closed_positions) == 1, (
            f"row with visible_until {stored} was dropped on restart")
        restarted.repository.close()


def test_the_purge_does_not_delete_rows_that_are_still_visible():
    """The DELETE is a string comparison too: an IST stamp against a UTC
    column made it remove rows that had hours left to run."""
    with tempfile.TemporaryDirectory() as folder:
        desk = _desk(folder)
        _open(desk)
        _mark(desk, 41.0)
        asyncio.run(desk.submit(SYM, "SELL", 750))
        until = datetime.fromisoformat(desk.closed_positions[0]["visible_until"])

        desk._prune_closed(until - timedelta(hours=2))
        assert len(desk.closed_positions) == 1
        # and it must still be on disk for the next restart to find
        assert len(desk.repository.mp_closed_positions(
            (until - timedelta(hours=2)).astimezone(UTC).isoformat(timespec="seconds"))) == 1
        desk.repository.close()
