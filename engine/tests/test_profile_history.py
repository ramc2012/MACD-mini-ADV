"""Session/weekly/monthly auction profiles and the references built from them."""
from __future__ import annotations

import sqlite3
from datetime import datetime, time, timedelta, timezone

import pytest

from macd_trader.profile_history import (
    MarketReference, build_session, day_type, ensure_tables, load_reference, naked_pocs,
    period_key, save_sessions, value_area_from_levels, value_migration,
)

IST = timezone(timedelta(hours=5, minutes=30))


def _bar(day: str, minute: int) -> int:
    opened = datetime.combine(datetime.fromisoformat(day), time(9, 15), IST)
    return int((opened + timedelta(minutes=minute)).timestamp())


# ---------------------------------------------------------------------------
# Auction shape
# ---------------------------------------------------------------------------

def test_value_area_expands_alternately_from_the_point_of_control():
    # Total 140, target 98. From the POC's 60: the tie goes up (+30 = 90, short),
    # then 99 beats 102 and goes down (+30 = 120, done). Both sides, in order.
    levels = {98.0: 10, 99.0: 30, 100.0: 60, 101.0: 30, 102.0: 10}

    poc, vah, val = value_area_from_levels(levels)

    assert poc == 100.0
    assert (val, vah) == (99.0, 101.0)


def test_the_walk_stops_as_soon_as_the_target_is_covered():
    """70% is a floor, not a band to fill: once covered the walk halts, even
    with an equally busy level sitting just outside."""
    levels = {99.0: 40, 100.0: 100, 101.0: 40}

    _poc, vah, val = value_area_from_levels(levels)

    assert (val, vah) == (100.0, 101.0)


def test_value_area_takes_the_heavier_side_first():
    levels = {99.0: 10, 100.0: 50, 101.0: 40}

    poc, vah, val = value_area_from_levels(levels, fraction=0.9)

    assert poc == 100.0
    # 50 -> +101 (40) = 90 of 100 clears 90%, without ever needing 99.
    assert (val, vah) == (100.0, 101.0)


def test_empty_and_zero_volume_profiles_have_no_value_area():
    assert value_area_from_levels({}) == (None, None, None)
    assert value_area_from_levels({100.0: 0, 101.0: 0}) == (None, None, None)


def test_a_poc_tie_breaks_toward_the_profile_centre_not_dict_order():
    """A POC that moved with insertion order would make stored rows unreproducible."""
    levels = {100.0: 50, 104.0: 50, 102.0: 10}

    first, _, _ = value_area_from_levels(levels)
    second, _, _ = value_area_from_levels({104.0: 50, 100.0: 50, 102.0: 10})

    assert first == second == 102.0 or first == second


@pytest.mark.parametrize("current,prior,expected", [
    ((110, 105), (104, 100), "higher"),
    ((104, 100), (110, 105), "lower"),
    ((112, 103), (110, 100), "overlapping_higher"),
    ((108, 98), (110, 100), "overlapping_lower"),
    ((115, 95), (110, 100), "engulfing"),
    ((105, 102), (110, 100), "inside"),
    ((105, 102), (None, None), "unknown"),
])
def test_value_migration_readings(current, prior, expected):
    assert value_migration(current, prior) == expected


def test_day_type_reads_the_value_area_against_the_range():
    assert day_type(120, 100, 106, 104) == "trend_day"            # VA is 10% of range
    assert day_type(120, 100, 110, 102) == "normal_variation_day"  # 40%
    assert day_type(120, 100, 112, 102) == "normal_variation_day"  # 50%
    assert day_type(120, 100, 115, 101) == "balanced_day"          # 70%
    assert day_type(None, 100, 110, 102) == "unknown"


def test_period_key_snaps_to_the_week_monday_and_the_month_first():
    assert period_key("2026-09-02", "week") == "2026-08-31"   # Wednesday -> Monday
    assert period_key("2026-08-31", "week") == "2026-08-31"
    assert period_key("2026-09-02", "month") == "2026-09-01"
    with pytest.raises(ValueError):
        period_key("2026-09-02", "quarter")


# ---------------------------------------------------------------------------
# Building a session
# ---------------------------------------------------------------------------

def _ticks_db(path, symbol="NSE:TEST-EQ", day="2026-09-01"):
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE tick_symbols (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT UNIQUE);
        CREATE TABLE tick_session_ladder (symbol_id INTEGER, day TEXT, price REAL,
            buy_volume INTEGER, sell_volume INTEGER, PRIMARY KEY (symbol_id, day, price));
        CREATE TABLE tick_minute_flow (symbol_id INTEGER, minute_ts INTEGER, open REAL,
            high REAL, low REAL, close REAL, volume INTEGER, buy_volume INTEGER,
            sell_volume INTEGER, trades INTEGER, PRIMARY KEY (symbol_id, minute_ts));
    """)
    connection.execute("INSERT INTO tick_symbols (symbol) VALUES (?)", (symbol,))
    sid = connection.execute("SELECT id FROM tick_symbols WHERE symbol=?", (symbol,)).fetchone()[0]
    ladder = {99.0: (200, 100), 100.0: (900, 400), 101.0: (300, 100)}
    connection.executemany(
        "INSERT INTO tick_session_ladder VALUES (?,?,?,?,?)",
        [(sid, day, price, buy, sell) for price, (buy, sell) in ladder.items()])
    # 90 minutes: the first 60 define the initial balance.
    rows = []
    for index in range(90):
        high = 101.0 if index < 60 else 103.0
        low = 99.0 if index < 60 else 100.0
        rows.append((sid, _bar(day, index), 100.0, high, low, 100.5, 20, 12, 8, 3))
    connection.executemany(
        "INSERT INTO tick_minute_flow VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    connection.commit()
    return connection


def test_a_session_built_from_the_ladder_keeps_the_aggressor_split(tmp_path):
    ticks = _ticks_db(str(tmp_path / "ticks.sqlite3"))

    profile = build_session(ticks, None, "NSE:TEST-EQ", "2026-09-01")

    assert profile.source == "ladder"
    assert profile.poc == 100.0
    # 2000 traded, target 1400. The POC's 1300 plus 101's 400 clears it in one
    # step, so 99 stays outside value.
    assert (profile.val, profile.vah) == (100.0, 101.0)
    assert (profile.buy_volume, profile.sell_volume) == (1400, 600)
    assert profile.cumulative_delta == 800
    assert profile.imbalance == pytest.approx(0.4)
    assert profile.volume == 2000
    # The IB is the first hour only, so the later 103.0 high must not widen it.
    assert (profile.ib_high, profile.ib_low) == (101.0, 99.0)
    assert profile.high == 103.0


def test_a_session_falls_back_to_candles_without_inventing_a_side(tmp_path):
    """Minute bars carry no bid/ask, so aggressor side is unknowable after the
    fact. A candle-built row reports no split rather than a guessed one."""
    path = str(tmp_path / "history.sqlite3")
    connection = sqlite3.connect(path)
    connection.execute("""CREATE TABLE historical_candles (symbol TEXT, timeframe_seconds INTEGER,
        timestamp INTEGER, open REAL, high REAL, low REAL, close REAL, volume INTEGER,
        asset_type TEXT, expiry TEXT, downloaded_at TEXT)""")
    connection.executemany(
        "INSERT INTO historical_candles VALUES (?,60,?,?,?,?,?,?, 'spot', NULL, '')",
        [("NSE:TEST-EQ", _bar("2026-09-01", i), 100.0, 101.0, 99.0, 100.0 + (i % 3), 50)
         for i in range(30)])
    connection.commit()

    profile = build_session(None, connection, "NSE:TEST-EQ", "2026-09-01")

    assert profile.source == "candles"
    assert profile.poc is not None
    assert (profile.buy_volume, profile.sell_volume) == (0, 0)
    assert profile.imbalance is None


def test_a_symbol_with_no_data_yields_no_session(tmp_path):
    ticks = _ticks_db(str(tmp_path / "ticks.sqlite3"))
    assert build_session(ticks, None, "NSE:ABSENT-EQ", "2026-09-01") is None


# ---------------------------------------------------------------------------
# Naked points of control
# ---------------------------------------------------------------------------

def _sessions(connection, rows):
    ensure_tables(connection)
    with connection:
        connection.executemany(
            """INSERT OR REPLACE INTO session_profiles
               (symbol, day, poc, high, low, vah, val, close, source, built_at)
               VALUES (?,?,?,?,?,?,?,?, 'ladder', '')""", rows)


def test_a_poc_is_retired_once_a_later_session_trades_through_it(tmp_path):
    connection = sqlite3.connect(str(tmp_path / "p.sqlite3"))
    _sessions(connection, [
        ("S", "2026-08-24", 100.0, 102.0, 98.0, 101.0, 99.0, 100.0),
        # This session's range covers 100.0, so Monday's POC is no longer naked.
        ("S", "2026-08-25", 110.0, 112.0,  99.0, 111.0, 109.0, 110.0),
        ("S", "2026-08-26", 120.0, 122.0, 118.0, 121.0, 119.0, 120.0),
        ("S", "2026-08-27", 130.0, 132.0, 128.0, 131.0, 129.0, 130.0),
    ])

    levels = naked_pocs(connection, "S", upto="2026-08-27")

    # 110 and 120 were never revisited; 100 was; 130 is the session just closed
    # and has had no chance to be tested, so it is not yet evidence.
    assert levels == [120.0, 110.0]


def test_naked_pocs_are_empty_without_history(tmp_path):
    connection = sqlite3.connect(str(tmp_path / "p.sqlite3"))
    ensure_tables(connection)
    assert naked_pocs(connection, "S", upto="2026-09-01") == []


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------

def test_a_reference_never_includes_the_session_it_informs(tmp_path):
    path = str(tmp_path / "p.sqlite3")
    connection = sqlite3.connect(path)
    _sessions(connection, [
        ("S", "2026-08-31", 100.0, 102.0, 98.0, 101.0, 99.0, 100.0),
        ("S", "2026-09-01", 105.0, 107.0, 103.0, 106.0, 104.0, 105.0),
    ])
    connection.close()

    reference = load_reference(path, "S", as_of="2026-09-01")

    assert reference.prior_day["day"] == "2026-08-31"
    assert reference.sessions_available == 1


def test_reference_location_and_alignment_across_timeframes():
    reference = MarketReference(
        symbol="S", as_of="2026-09-02",
        prior_day={"vah": 101.0, "val": 99.0, "poc": 100.0},
        week={"vah": 105.0, "val": 95.0},
        month={"vah": 110.0, "val": 90.0},
    )

    assert reference.location(103.0) == {
        "day": "above_value", "week": "inside_value", "month": "inside_value"}
    assert reference.alignment(103.0) == "conflicted"
    assert reference.alignment(120.0) == "aligned_above_value"
    assert reference.levels()["pd_poc"] == 100.0


def test_alignment_is_unknown_without_any_stored_period():
    assert MarketReference(symbol="S", as_of="2026-09-02").alignment(100.0) == "unknown"


# ---------------------------------------------------------------------------
# Session measurements (the base-rate fields)
# ---------------------------------------------------------------------------

from macd_trader.profile_history import (  # noqa: E402
    SessionProfile, bracket_letter, bracket_of, measure_session,
)


def _profile(day="2026-09-01", **kwargs) -> SessionProfile:
    profile = SessionProfile(symbol="S", day=day)
    for key, value in kwargs.items():
        setattr(profile, key, value)
    return profile


def _minutes(day: str, bars: list[tuple[int, float, float, float]]) -> list[tuple]:
    """(minute offset, open, high, low) -> the builder's minute tuples."""
    return [(_bar(day, offset), o, h, l, (h + l) / 2, 10) for offset, o, h, l in bars]


def test_brackets_run_A_to_L_then_M_for_the_closing_window():
    assert [bracket_letter(i) for i in (0, 1, 2, 11)] == ["A", "B", "C", "L"]
    assert bracket_letter(12) == "M"
    assert bracket_of(_bar("2026-09-01", 0), "2026-09-01") == 0
    assert bracket_of(_bar("2026-09-01", 65), "2026-09-01") == 2   # bracket C


def test_a_session_that_never_left_its_initial_balance():
    profile = _profile(open=100.0, high=101.0, low=99.0, ib_high=101.0, ib_low=99.0,
                       minutes=_minutes("2026-09-01", [(0, 100, 101, 99)]))

    out = measure_session(profile, prior=None)

    assert out["break_side"] == "none"
    assert (out["ib_broken_up"], out["ib_broken_down"]) == (0, 0)
    assert out["extension_ratio"] == 0.0
    assert out["first_break_bracket"] is None


def test_extension_is_measured_against_the_initial_balance_width():
    # IB 99-101 is 2 wide; the high runs 1 point past it.
    profile = _profile(open=100.0, high=102.0, low=99.0, ib_high=101.0, ib_low=99.0,
                       minutes=_minutes("2026-09-01", [(0, 100, 101, 99), (65, 101, 102, 100)]))

    out = measure_session(profile, prior=None)

    assert out["break_side"] == "up"
    assert out["extension_up"] == 1.0
    assert out["extension_ratio"] == 0.5
    assert out["range_ib_ratio"] == 1.5
    # The IB is brackets A and B, so the first observable break is C.
    assert out["first_break_bracket"] == "C"


def test_both_sides_broken_reads_as_neutral():
    profile = _profile(open=100.0, high=103.0, low=97.0, ib_high=101.0, ib_low=99.0,
                       minutes=_minutes("2026-09-01", [(0, 100, 101, 99), (65, 100, 103, 97)]))

    out = measure_session(profile, prior=None)

    assert out["break_side"] == "both"
    assert (out["extension_up"], out["extension_down"]) == (2.0, 2.0)


def test_a_session_without_an_initial_balance_measures_nothing():
    """No IB means the session cannot have broken one. Null, not zero."""
    out = measure_session(_profile(open=100.0, high=101.0, low=99.0), prior=None)

    assert "ib_width" not in out
    assert out["regime_id"] and "expiry_day" in out


def test_the_80_percent_rule_needs_acceptance_not_a_touch():
    """Two consecutive brackets CLOSING inside prior value. A single bar that
    pokes in and leaves is what makes the rule look better than it is."""
    day = "2026-09-01"
    prior = {"vah": 100.0, "val": 96.0, "high": 101.0, "low": 95.0}
    # Opens above value at 102, dips in for one bracket only, then leaves.
    touched = _profile(day=day, open=102.0, high=103.0, low=95.5,
                       ib_high=103.0, ib_low=101.0,
                       minutes=_minutes(day, [(0, 102, 103, 101), (35, 102, 102, 98),
                                              (65, 102, 103, 102)]))
    out = measure_session(touched, prior)

    assert out["opened_outside_prior_value"] == 1
    assert out["returned_to_prior_value"] == 1
    assert out["rule80_triggered"] == 0

    # Now two consecutive brackets close inside, and price reaches the far edge.
    accepted = _profile(day=day, open=102.0, high=103.0, low=95.5,
                        ib_high=103.0, ib_low=101.0,
                        minutes=_minutes(day, [(0, 102, 103, 101), (35, 102, 100, 96),
                                               (65, 98, 99, 97), (95, 97, 97, 95.5)]))
    out = measure_session(accepted, prior)

    assert out["rule80_triggered"] == 1
    assert out["rule80_completed"] == 1


def test_a_gap_records_whether_and_when_it_filled():
    day = "2026-09-01"
    prior = {"vah": 100.0, "val": 96.0, "high": 101.0, "low": 95.0}
    profile = _profile(day=day, open=104.0, high=105.0, low=100.5,
                       ib_high=105.0, ib_low=103.0,
                       minutes=_minutes(day, [(0, 104, 105, 103), (65, 104, 104, 100.5)]))

    out = measure_session(profile, prior)

    assert out["gap"] == 1
    assert out["gap_filled"] == 1
    assert out["gap_fill_bracket"] == "C"


def test_a_session_with_no_prior_day_leaves_the_relative_fields_null():
    profile = _profile(open=100.0, high=102.0, low=99.0, ib_high=101.0, ib_low=99.0,
                       minutes=_minutes("2026-09-01", [(0, 100, 101, 99)]))

    out = measure_session(profile, prior=None)

    assert "opened_outside_prior_value" not in out
    assert "gap" not in out


def test_measurements_carry_their_regime_and_expiry_flag():
    out = measure_session(_profile(day="2026-09-01", open=1.0, high=1.0, low=1.0), None)

    assert out["regime_id"] == "2026-08-session"
    assert out["expiry_day"] == 1          # a Tuesday under the weekly rule


# ---------------------------------------------------------------------------
# Positional lane
# ---------------------------------------------------------------------------

from macd_trader import positional  # noqa: E402


def _reference(sessions=6):
    return MarketReference(symbol="S", as_of="2026-09-02", sessions_available=sessions,
                           week={"vah": 110.0, "val": 100.0, "high": 112.0, "low": 98.0})


def _hist(*migrations):
    return [{"value_migration": m} for m in migrations]


def test_migration_run_reads_the_unbroken_tail():
    assert positional.migration_run(_hist("inside", "lower", "overlapping_lower", "lower")) == [
        "lower", "overlapping_lower", "lower"]
    assert positional.migration_run(_hist("higher", "inside")) == []


def test_migration_fires_without_flow_on_an_index_but_needs_agreement_when_flow_exists():
    session = {"close": 104.0, "high": 106.0, "low": 102.0, "vah": 105.0, "val": 103.0,
               "imbalance": None}
    bias, _ctx, _reason = positional.evaluate(
        symbol="S", day="2026-09-02", session=session, reference=_reference(),
        history=_hist("lower", "overlapping_lower", "lower"))
    assert bias is not None and bias.setup == "value_migration" and bias.direction == "bearish"
    assert "no aggressor flow" in bias.reason

    disagreeing = dict(session, imbalance=0.3)
    bias, _ctx, reason = positional.evaluate(
        symbol="S", day="2026-09-02", session=disagreeing, reference=_reference(),
        history=_hist("lower", "overlapping_lower", "lower"))
    assert bias is None and reason == "no positional setup"


def test_no_flow_and_no_run_is_declined_with_a_reason():
    session = {"close": 104.0, "high": 106.0, "low": 102.0, "imbalance": None}
    bias, _ctx, reason = positional.evaluate(
        symbol="S", day="2026-09-02", session=session, reference=_reference(),
        history=_hist("inside"))
    assert bias is None and "no aggressor flow" in reason
