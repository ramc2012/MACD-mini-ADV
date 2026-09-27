"""The profile objects the ladder and the session badge strip read.

The blueprint's Part 5 panel asks the reader for structure the desk was
computing internally and throwing away: the volume POC beside the TPO one, how
far the range extended past the initial balance, whether an extreme was
rejected (a tail) or merely stopped at (a poor high), and what the value area
looked like at every bracket rather than only now. Each one is published here
under its own name, because a blended reading answers none of the questions.
"""
from __future__ import annotations

from macd_trader.market_profile import SESSION_OPEN_MINUTE, Profile, letter_for
from macd_trader.mp_engine import session_view


def _profile(prints) -> Profile:
    """``prints`` are (minutes past the 09:15 open, price, size)."""
    profile = Profile("NSE:NIFTY26SEPFUT", "2026-09-01")
    for minute, price, size in prints:
        profile.add(price, size, SESSION_OPEN_MINUTE + minute)
    return profile


def test_profile_snapshot_marks_sampling_and_can_return_every_price_row():
    profile = Profile("NSE:NIFTY26SEPFUT", "2026-09-01")
    for index in range(110):
        profile.add(100 + index * 0.05, index + 1, SESSION_OPEN_MINUTE)

    compact = profile.snapshot()
    full = profile.snapshot(max_levels=None)

    assert compact["levels_total"] == full["levels_total"] == 110
    assert compact["levels_returned"] == len(compact["levels"]) == 90
    assert compact["levels_sampled"] is True
    assert full["levels_returned"] == len(full["levels"]) == 110
    assert full["levels_sampled"] is False
    assert full["tick_size"] == 0.05
    assert compact["single_prints_total"] == 110
    assert compact["single_prints_sampled"] is True
    assert len(full["single_prints"]) == 110
    assert full["single_prints_sampled"] is False
    assert sum(level["volume"] for level in full["levels"]) == sum(range(1, 111))


def test_late_joined_one_bracket_profile_does_not_claim_a_full_day_auction():
    profile = _profile([
        (300, 49.00, 100), (300, 49.05, 200), (300, 49.10, 300),
    ])
    snapshot = profile.snapshot(max_levels=None)
    view = session_view(profile, day=profile.day, snapshot=snapshot)

    assert snapshot["first_bracket"] == 10  # K, beginning at 14:15 IST
    assert snapshot["partial_capture"] is True
    assert snapshot["tail_high"] == snapshot["tail_low"] == 0
    assert snapshot["poor_high"] is snapshot["poor_low"] is None
    assert view["day_type_va"] == "unobserved"
    assert view["open_location"] is None
    assert view["value_relationship"] is None


# --------------------------------------------------------------------------
# volume point of control
# --------------------------------------------------------------------------

def test_the_volume_poc_is_reported_apart_from_the_time_poc():
    """They answer different questions — where price spent the most TIME
    against where the most SIZE changed hands — and a session where they sit
    far apart is itself the reading."""
    profile = _profile([
        (0, 100.0, 10), (30, 100.0, 10), (60, 100.0, 10),   # three brackets, thin
        (0, 101.0, 500),                                    # one bracket, heavy
    ])

    assert profile.poc == 100.0
    assert profile.vpoc == 101.0


def test_a_profile_with_no_traded_volume_has_no_volume_poc():
    """Indices stream no volume at all, so a TPO structure exists and a volume
    one does not. None says that; 0.0 would name a price."""
    profile = _profile([(0, 100.0, 0), (30, 100.0, 0), (60, 100.5, 0)])

    assert profile.poc == 100.0
    assert profile.vpoc is None


# --------------------------------------------------------------------------
# range extension
# --------------------------------------------------------------------------

def test_range_extension_is_published_in_initial_balance_widths():
    """day_type() computes this ratio internally and publishes only its
    verdict, but the base-rate table is keyed on the number — so the figure the
    verdict came from belongs on the wire beside it."""
    profile = _profile([
        (0, 100.0, 10), (30, 110.0, 10),        # IB 100-110, width 10
        (90, 115.0, 10), (120, 98.0, 10),       # up 5, down 2
    ])
    extension = profile.extension()

    assert (extension["up"], extension["down"]) == (5.0, 2.0)
    assert extension["ratio"] == 0.5


def test_extension_is_none_before_the_initial_balance_exists():
    profile = _profile([(0, 100.0, 10)])

    assert profile.extension() == {"up": None, "down": None, "ratio": None}


# --------------------------------------------------------------------------
# poor extremes and tails
# --------------------------------------------------------------------------

def test_an_extreme_row_traded_by_two_brackets_is_a_poor_high():
    """No excess: the auction was not rejected there, it stopped. Dalton's
    reading is that the level gets revisited."""
    profile = _profile([
        (0, 100.0, 10), (30, 101.0, 10), (60, 102.0, 10), (90, 102.0, 10),
    ])
    extremes = profile.poor_extremes()

    assert extremes["poor_high"] is True
    assert extremes["poor_low"] is False, "100.0 was touched by one bracket only"


def test_a_run_of_single_print_rows_at_an_extreme_is_a_tail():
    """One lone TPO at the extreme is the last print of a bracket, not a
    rejection; two consecutive rows is the shortest run that means anything."""
    profile = _profile([
        (0, 100.0, 10), (0, 101.0, 10),         # A alone at the two low rows
        (30, 102.0, 10), (60, 102.0, 10),       # B and C share the top row
    ])
    extremes = profile.poor_extremes()

    assert extremes["tail_low"] == 2
    assert extremes["tail_high"] == 0
    assert extremes["poor_high"] is True


def test_a_single_lone_row_at_an_extreme_does_not_count_as_a_tail():
    profile = _profile([
        (0, 100.0, 10), (30, 101.0, 10), (60, 101.0, 10),
    ])

    assert profile.poor_extremes()["tail_low"] == 0


# --------------------------------------------------------------------------
# the developing value area
# --------------------------------------------------------------------------

def test_the_value_area_at_the_final_bracket_is_the_session_value_area():
    """"Developing" and "completed" have to be the same rule applied to more or
    fewer rows. Two implementations would be free to disagree at the close,
    which is the one moment the desk compares them."""
    profile = _profile([
        (0, 100.0, 10), (0, 101.0, 10), (30, 101.0, 10), (30, 102.0, 10),
        (60, 101.0, 10), (60, 103.0, 10),
    ])
    last = max(profile.brackets_seen)
    poc, vah, val = profile.value_area_at(last)

    assert (poc, (vah, val)) == (profile.poc, profile.value_area())
    rows = profile.va_by_bracket()
    assert [row["bracket"] for row in rows] == sorted(profile.brackets_seen)
    assert rows[-1] == {"bracket": last, "letter": letter_for(last),
                        "poc": poc, "vah": vah, "val": val}


def test_the_bracket_walk_is_cached_but_still_follows_the_structure():
    """va_by_bracket() recomputed the whole history on every call, and
    snapshot() is taken up to three times per three-second poll from `async
    def` routes -- measured at 16.6 ms of that on a full-session profile, i.e.
    tens of milliseconds of event-loop blocking on the loop that ingests live
    ticks. It is now cached against the same structure version the POC and the
    session value area use, so this pins both halves: the cache is returned,
    AND it is invalidated the moment a new level or bracket is touched."""
    profile = _profile([(0, 100.0, 10), (0, 101.0, 10), (30, 101.0, 10)])
    first = profile.va_by_bracket()

    assert profile.va_by_bracket() is first, "recomputed with the structure unchanged"

    # A print at a level/bracket pair already seen is not a structure change.
    profile.add(101.0, 10, SESSION_OPEN_MINUTE + 30)
    assert profile.va_by_bracket() is first

    profile.add(105.0, 10, SESSION_OPEN_MINUTE + 60)
    rebuilt = profile.va_by_bracket()
    assert rebuilt is not first
    # ...and the one-pass walk still agrees with the per-bracket rule, row for
    # row. An incremental statistic that drifted from value_area_at() would put
    # a developing value area on screen that the close could not reproduce.
    for row in rebuilt:
        poc, vah, val = profile.value_area_at(row["bracket"])
        assert (row["poc"], row["vah"], row["val"]) == (poc, vah, val)


def test_the_value_area_at_the_first_bracket_ignores_everything_that_came_later():
    profile = _profile([
        (0, 100.0, 10), (30, 120.0, 10), (60, 121.0, 10),
    ])
    poc, vah, val = profile.value_area_at(profile.first_bracket)

    assert (poc, vah, val) == (100.0, 100.0, 100.0)


# --------------------------------------------------------------------------
# the day type's own history
# --------------------------------------------------------------------------

def test_each_bracket_latches_the_day_type_it_closed_on():
    """The live reading is recomputed on every tick, so without a latch the
    estimate "as of C" is gone the moment D opens — and a reader watching the
    classification settle, or flip, has nothing to watch."""
    profile = _profile([
        (0, 100.0, 10), (30, 110.0, 10),        # A, B: initial balance
        (90, 130.0, 10),                        # C: a wide extension up
        (120, 131.0, 10),                       # D
    ])
    snap = profile.snapshot()
    history = {row["bracket"]: row["day_type"] for row in snap["day_type_by_bracket"]}

    # Every bracket that has CLOSED is latched; the one still open is not, and
    # its reading is the live day_type beside it.
    assert sorted(history) == sorted(profile.brackets_seen)[:-1]
    assert history[sorted(profile.brackets_seen)[-2]] == "trend_day_up"
    assert snap["day_type"] == profile.day_type()


def test_the_snapshot_carries_the_whole_ladder_the_page_draws():
    profile = _profile([
        (0, 100.0, 10), (0, 101.0, 400), (30, 101.0, 10), (60, 102.0, 10),
    ])
    snap = profile.snapshot()

    assert snap["vpoc"] == 101.0
    assert snap["extension"]["ratio"] is not None
    assert set(snap) >= {"poor_high", "poor_low", "tail_high", "tail_low",
                         "va_by_bracket", "day_type_by_bracket", "single_prints"}
