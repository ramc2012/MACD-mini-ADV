"""Replaying a recorded session, and the stored ladders a composite merges.

The blueprint asks for "the same engine on replay". MPEngine cannot be pointed
at a recorded day — it keys the session off the wall clock and rejects ticks
dated another day, both deliberately — so ReplaySession drives the objects the
engine drives, in the order it drives them. What these tests pin is that the
surface a replay serves is the surface the live page reads, that seeking is
monotone in what it has consumed, and that a day with nothing recorded says so
rather than serving an empty session as a real one.
"""
from __future__ import annotations

import sqlite3

from macd_trader import auction_views
from macd_trader.profile_history import (ensure_tables, session_bounds,
                                         value_area_from_levels)
from macd_trader.replay import ReplaySession

SYMBOL = "NSE:NIFTY26SEPFUT"
DAY = "2026-09-01"
OPEN, CLOSE = session_bounds(DAY)

TICK_SCHEMA = """
CREATE TABLE tick_symbols (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT UNIQUE);
CREATE TABLE ticks (symbol_id INTEGER, ts_ms INTEGER, ltp REAL, cum_volume INTEGER,
    last_qty INTEGER, bid REAL, ask REAL, bid_qty INTEGER, ask_qty INTEGER,
    oi INTEGER, tbq INTEGER, tsq INTEGER);
CREATE TABLE tick_session_ladder (symbol_id INTEGER, day TEXT, price REAL,
    buy_volume INTEGER, sell_volume INTEGER);
CREATE TABLE tick_minute_flow (symbol_id INTEGER, minute_ts INTEGER, open REAL,
    high REAL, low REAL, close REAL, volume INTEGER, buy_volume INTEGER,
    sell_volume INTEGER, delta INTEGER, trades INTEGER, ticks INTEGER, vwap REAL,
    quote_n INTEGER, mid_n INTEGER, tick_n INTEGER, zero_tick_n INTEGER,
    pending_n INTEGER, conflict_n INTEGER, unclassified INTEGER, avg_spread REAL,
    ofi REAL, ofi_events INTEGER, avg_confidence REAL);
"""


def _session_rows():
    """A walk-up then a walk-down, one print every ten seconds, alternating the
    aggressor by trading at the ask and then at the bid."""
    rows = []
    cum = 1000
    for index in range(60):
        price = round(100.0 + (index if index < 30 else 60 - index) * 0.1, 2)
        cum += 10
        at_ask = index < 30
        rows.append(((OPEN + index * 10) * 1000, price, cum, 10,
                     price - 0.1 if at_ask else price,
                     price if at_ask else price + 0.1,
                     500, 500, 0, 100_000 - index * 10, 100_000))
    return rows


def _ticks_db(path: str) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(TICK_SCHEMA)
    connection.execute("INSERT INTO tick_symbols (symbol) VALUES (?)", (SYMBOL,))
    connection.executemany(
        "INSERT INTO ticks VALUES (1,?,?,?,?,?,?,?,?,?,?,?)", _session_rows())
    connection.commit()
    connection.close()


def _composite_db(history_path: str, ticks_path: str) -> None:
    """Three stored sessions with a ladder each, the shape the nightly job
    leaves behind."""
    history = sqlite3.connect(history_path)
    ensure_tables(history)
    with history:
        history.executemany(
            """INSERT INTO session_profiles
               (symbol, day, open, high, low, close, poc, vah, val, ib_high, ib_low,
                volume, buy_volume, sell_volume, cumulative_delta, imbalance, trades,
                day_type, single_prints, value_migration, levels, source, built_at)
               VALUES (?,?,100,104,98,103,101,102,100,103,99,
                       1000,600,400,200,0.2,50,'balanced_day','[]','higher',3,'ladder','')""",
            [(SYMBOL, "2026-08-27"), (SYMBOL, "2026-08-28"), (SYMBOL, "2026-08-31")])
    history.close()

    ticks = sqlite3.connect(ticks_path)
    ticks.executescript(TICK_SCHEMA)
    ticks.execute("INSERT INTO tick_symbols (symbol) VALUES (?)", (SYMBOL,))
    ladder = []
    flow = []
    for day, weight in (("2026-08-27", 1), ("2026-08-28", 2), ("2026-08-31", 3)):
        for price in (100.0, 101.0, 102.0):
            ladder.append((1, day, price, 10 * weight, 10 * weight))
        flow.append((1, session_bounds(day)[0], 100, 102, 100, 101, 60 * weight,
                     30 * weight, 30 * weight, 0, 5, 20, 101.0,
                     5, 0, 0, 0, 0, 0, 0, 0.1, 0.0, 10, 0.7))
    ticks.executemany("INSERT INTO tick_session_ladder VALUES (?,?,?,?,?)", ladder)
    ticks.executemany(
        "INSERT INTO tick_minute_flow VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        flow)
    ticks.commit()
    ticks.close()


# --------------------------------------------------------------------------
# rebuilding a recorded session
# --------------------------------------------------------------------------

def test_a_replay_serves_the_same_surface_the_live_footprint_route_does(tmp_path):
    """The page swaps one URL for another and renders unchanged, so every key
    the chart reads has to be present — a replay that omitted `flow` or `ofi`
    would blank half the workspace with no error to explain it."""
    path = str(tmp_path / "ticks.sqlite3")
    _ticks_db(path)

    payload = ReplaySession(SYMBOL, DAY).build(path).payload(CLOSE, bars=60)

    assert set(payload) >= {"bars", "profile", "flow", "ofi", "tape_speed",
                            "session", "setup", "replay", "tape", "dom"}
    assert payload["replay"]["day"] == DAY
    assert payload["replay"]["ticks"] == 60
    assert payload["setup"]["setup"] is None, "a replay shows what the desk SAW"


def test_the_replay_stops_at_the_moment_it_was_asked_for(tmp_path):
    """The scrubber's whole contract: nothing after `at` may have been fed, or
    the chart shows the reader a future they are meant to be predicting."""
    path = str(tmp_path / "ticks.sqlite3")
    _ticks_db(path)
    session = ReplaySession(SYMBOL, DAY).build(path)

    early = session.payload(OPEN + 100, bars=60)

    assert early["replay"]["position"] == 11        # ticks at OPEN + 0..100
    assert max(bar["t"] for bar in early["bars"]) <= OPEN + 100
    assert early["flow"]["trades"] == 10, "the first tick is the volume baseline"


def test_playing_forward_only_feeds_the_ticks_between_the_two_moments(tmp_path):
    """A 1 s scrubber step must cost a few hundred ticks, not a rebuild — and
    the state it lands on must be identical to a rebuild's, or scrubbing and
    playing would disagree about the same moment."""
    path = str(tmp_path / "ticks.sqlite3")
    _ticks_db(path)

    stepped = ReplaySession(SYMBOL, DAY).build(path)
    for moment in (OPEN + 100, OPEN + 200, OPEN + 300):
        stepped.payload(moment, bars=60)
    forward = stepped.payload(OPEN + 300, bars=60)

    direct = ReplaySession(SYMBOL, DAY).build(path).payload(OPEN + 300, bars=60)

    assert forward["flow"]["cumulative_delta"] == direct["flow"]["cumulative_delta"]
    assert forward["ofi"]["cumulative"] == direct["ofi"]["cumulative"]
    assert forward["replay"]["position"] == direct["replay"]["position"]


def test_seeking_backwards_rebuilds_rather_than_leaving_the_future_in_place(tmp_path):
    """The objects only accumulate, so a backward seek that did not reset would
    show 15:30's profile under a 10:00 clock."""
    path = str(tmp_path / "ticks.sqlite3")
    _ticks_db(path)
    session = ReplaySession(SYMBOL, DAY).build(path)

    session.payload(CLOSE, bars=60)
    rewound = session.payload(OPEN + 100, bars=60)

    assert rewound["replay"]["position"] == 11
    assert rewound["flow"]["trades"] == 10


def test_the_clock_is_clamped_to_the_session_it_is_replaying(tmp_path):
    """`at=0` is what the page sends before it has been told where the session
    starts; it must mean "the open", not 1970."""
    path = str(tmp_path / "ticks.sqlite3")
    _ticks_db(path)
    session = ReplaySession(SYMBOL, DAY).build(path)

    assert session.payload(0, bars=60)["replay"]["at"] == OPEN
    assert session.payload(CLOSE + 86_400, bars=60)["replay"]["at"] == CLOSE


def test_a_day_with_no_recorded_ticks_builds_an_empty_session(tmp_path):
    """Raw ticks live for `tick_retention_days`; older sessions were condensed
    to a ladder and carry no prints. The caller turns this into a 404 rather
    than serving an empty auction as a real one."""
    path = str(tmp_path / "ticks.sqlite3")
    _ticks_db(path)

    assert ReplaySession(SYMBOL, "2026-08-31").build(path).ticks == []
    assert ReplaySession("NSE:NOTHING", DAY).build(path).ticks == []
    # A desk with tick capture switched off has no archive file at all, and
    # that is the same answer, not a 500 out of the route.
    assert ReplaySession(SYMBOL, DAY).build(str(tmp_path / "absent.sqlite3")).ticks == []


# --------------------------------------------------------------------------
# the stored days a replay and a composite can reach
# --------------------------------------------------------------------------

def test_the_replayable_days_are_the_ones_whose_raw_ticks_survive(tmp_path):
    path = str(tmp_path / "ticks.sqlite3")
    _ticks_db(path)

    assert auction_views.raw_tick_days(path, SYMBOL) == [DAY]
    assert auction_views.raw_tick_days(path, "NSE:NOTHING") == []
    assert auction_views.raw_tick_days(str(tmp_path / "missing.sqlite3"), SYMBOL) == []


def test_a_composite_merges_the_stored_ladders_by_the_rule_its_days_were_built_with(tmp_path):
    """A composite POC and a stored weekly POC have to be one rule, or the two
    tiers of the same page disagree about where value was."""
    history_path = str(tmp_path / "history.sqlite3")
    ticks_path = str(tmp_path / "ticks.sqlite3")
    _composite_db(history_path, ticks_path)

    out = auction_views.composite(history_path, ticks_path, SYMBOL, 3, "2026-09-01")

    assert (out["days"], out["from"], out["to"]) == (3, "2026-08-27", "2026-08-31")
    merged = {row["price"]: row["volume"] for row in out["levels"]}
    assert merged == {100.0: 120, 101.0: 120, 102.0: 120}
    assert (out["poc"], out["vah"], out["val"]) == value_area_from_levels(
        {price: float(volume) for price, volume in merged.items()})


def test_a_composite_reports_how_many_sessions_it_actually_found(tmp_path):
    """A freshly rolled futures series or an option contract has a handful of
    stored days at most. "3 of 20" is a different picture from "20", and the
    pane has to be able to say so."""
    history_path = str(tmp_path / "history.sqlite3")
    ticks_path = str(tmp_path / "ticks.sqlite3")
    _composite_db(history_path, ticks_path)

    out = auction_views.composite(history_path, ticks_path, SYMBOL, 20, "2026-09-01")

    assert (out["days"], out["requested_days"]) == (3, 20)


def test_a_composite_never_reaches_into_the_session_it_is_a_reference_for(tmp_path):
    """The overlay is prior value. Including today would make the reference
    move with the thing it is meant to be judged against."""
    history_path = str(tmp_path / "history.sqlite3")
    ticks_path = str(tmp_path / "ticks.sqlite3")
    _composite_db(history_path, ticks_path)

    out = auction_views.composite(history_path, ticks_path, SYMBOL, 5, "2026-08-28")

    assert out["to"] == "2026-08-27" and out["days"] == 1


# --------------------------------------------------------------------------
# the badge strip the live snapshot serves
# --------------------------------------------------------------------------

def _desk_with_history(tmp_path):
    from datetime import datetime, timedelta
    from macd_trader.market_profile import IST
    from macd_trader.mp_engine import MPEngine, MPSettings

    history = str(tmp_path / "history.sqlite3")
    _composite_db(history, str(tmp_path / "ticks.sqlite3"))
    desk = MPEngine(str(tmp_path / "mp.sqlite3"),
                    settings=MPSettings(enabled=True, auto_trade=False),
                    history_database_path=history)
    today = datetime.now(IST)
    desk.session_day = today.date().isoformat()
    midnight = today.replace(hour=0, minute=0, second=0, microsecond=0)
    # Opens at 103, above the stored prior session's VAH of 102 and inside its
    # 98-104 range, so the open-location badge has something definite to say.
    for minute, price in ((9 * 60 + 20, 103.0), (9 * 60 + 50, 103.5), (10 * 60 + 20, 102.5)):
        desk.profiles.on_print(SYMBOL, price, 100, midnight + timedelta(minutes=minute))
    return desk


def test_the_live_badge_strip_reads_the_stored_prior_session(tmp_path):
    """MPEngine.snapshot() called session_view with context=None, so the
    AUCTION tab's strip permanently showed "value unknown", no open-location
    badge and an all-None prior day -- three badges rendered as measured-and-
    unknown when in fact nothing had been looked up."""
    desk = _desk_with_history(tmp_path)

    session = desk.snapshot()["session"]

    assert session["prior_day"]["vah"] == 102
    assert session["prior_day_date"] == "2026-08-31"
    assert session["value_relationship"] != "unknown"
    assert session["open_location"] == "above_value"
    desk.repository.close()


def test_the_stored_reference_is_read_once_a_minute_not_once_a_poll(tmp_path):
    """The night job writes it and it cannot change during a session, while
    this route polls every three seconds — and it is a sqlite read on the
    FastAPI event loop."""
    desk = _desk_with_history(tmp_path)

    first = desk.snapshot()["session"]["prior_day_date"]
    stamp = desk._context_cache[SYMBOL][0]
    for _ in range(5):
        desk.snapshot()

    assert desk._context_cache[SYMBOL][0] == stamp, "re-read the reference per poll"
    assert desk.snapshot()["session"]["prior_day_date"] == first
    desk.repository.close()


def test_the_snapshot_takes_one_profile_snapshot_not_two(tmp_path, monkeypatch):
    """profile.snapshot() was taken directly for the `profile` key and again
    inside session_view. It is the most expensive call on the route (measured
    at 19.1 ms on a full-session profile before the value-area walk was cached)
    and it runs on the loop that ingests live ticks."""
    from macd_trader.market_profile import Profile

    desk = _desk_with_history(tmp_path)
    taken = []
    original = Profile.snapshot

    def counted(self, *args, **kwargs):
        taken.append(self.symbol)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Profile, "snapshot", counted)
    payload = desk.snapshot()

    assert taken == [SYMBOL]
    # and both readers describe the same snapshot
    assert payload["session"]["open_type"] == payload["profile"]["open_type"]
    assert payload["session"]["extension"] == payload["profile"]["extension"]
    desk.repository.close()


def test_the_reference_is_never_read_on_the_event_loop(tmp_path):
    """auction_views.context costs 7-16 ms against the live history database
    (naked POCs walk back 40 sessions) and MPEngine.snapshot() is served by an
    `async def` route, so the refresh runs in a thread and the strip is one
    three-second poll behind on the first read of a session rather than the
    loop being stalled once a minute per underlying."""
    import asyncio

    desk = _desk_with_history(tmp_path)

    async def poll():
        first = desk.snapshot()["session"]["prior_day_date"]
        for _ in range(200):
            await asyncio.sleep(0.01)
            if desk._context_cache[SYMBOL][1] is not None:
                break
        return first, desk.snapshot()["session"]["prior_day_date"]

    first, second = asyncio.run(poll())

    assert first is None, "the history database was read on the event loop"
    assert second == "2026-08-31"
    desk.repository.close()
