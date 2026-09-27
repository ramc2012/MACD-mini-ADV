"""The professional footprint markers, and the grid they all depend on.

Two themes run through these tests:

* **Nothing in the marker set is buildable on a broken price grid.** Stacked
  imbalance, unfinished auction, value area and LVNs all need contiguous
  populated rows. A tick size hard-coded at 0.05 for every instrument, plus a
  row that is one raw exchange tick wide, guarantees there are none.
* **An inference must never be published with the authority of a
  measurement.** Volume-at-price is exact; the aggressor side is not. Every
  assertion below about ``basis``, ``suppressed``, ``grade`` and ``None`` is
  guarding that line.
"""
import pytest

from macd_trader.footprint import (BASIS, EXHAUST_VOLUME_FRACTION,
                                   FootprintBook, IMBALANCE_RATIO,
                                   LOW_CONFIDENCE_BAR, MIN_IMBALANCE_VOLUME,
                                   STACKED_IMBALANCE_MIN, confidence_grade,
                                   quantise, row_index)
from macd_trader.market_profile import Profile
from macd_trader.orderflow import Print


def _levels(bar):
    return {row["p"]: row for row in bar["levels"]}


def _one_bar(book, symbol="X", flow=None):
    return book.payload(symbol, flow=flow)["bars"][0]


# Session-scale coverage good enough to clear every suppression gate, so a
# geometry test can assert on geometry. Absent `flow`, quote share is
# UNMEASURED and the strict gates suppress — which is the point of
# test_a_stack_is_suppressed_when_quote_share_was_never_measured below.
GOOD_FLOW = {"classified_share": 1.0, "quote_share": 0.80,
             "trades": 100, "depth_ticks": 100}


# --------------------------------------------------------------------------
# the price grid
# --------------------------------------------------------------------------

def test_tick_size_is_measured_per_symbol_not_hard_coded_at_five_paise():
    """A 0.10-tick instrument: the diagonal must be measured against the row
    that exists, not against a phantom 0.05 below it.

    With the tick hard-coded at 0.05 the neighbour lookup addressed a price no
    trade can occur at, so the buy branch was structurally dead (an audit
    measured 0 imbalanced cells out of 165 on an affected contract).
    """
    book = FootprintBook(timeframe_seconds=60)
    book.watch("TENPAISA")
    for i, price in enumerate([200.00, 200.10, 200.20, 200.30]):
        book.on_print("TENPAISA", 1_755_000_000 + i, price, 20, -1)
    book.on_print("TENPAISA", 1_755_000_010, 200.40, 60, 1)
    book.on_print("TENPAISA", 1_755_000_011, 200.50, 20, -1)
    book.on_print("TENPAISA", 1_755_000_012, 200.60, 20, -1)

    payload = book.payload("TENPAISA")
    assert payload["tick_size"] == 0.10
    assert payload["tick_size_source"] == "observed"
    assert payload["tick_size_samples"] >= 6

    level = _levels(payload["bars"][0])[200.40]
    assert level["imb_buy"] is True
    # The ratio is MEASURED (60 lifted against 20 resting one row below), not
    # the unmeasurable "nothing was resting" case a wrong tick would produce.
    assert level["imb_ratio"] == pytest.approx(3.0)


def test_tick_size_that_cannot_be_determined_says_so_rather_than_defaulting_silently():
    book = FootprintBook(timeframe_seconds=60)
    book.watch("THIN")
    book.on_print("THIN", 1_755_000_000, 100.00, 10, 1)
    payload = book.payload("THIN")
    assert payload["tick_size_source"] == "default"
    assert payload["tick_size"] == 0.05
    assert payload["tick_size_samples"] == 1


def test_explicit_tick_size_wins_and_is_labelled_as_such():
    book = FootprintBook(timeframe_seconds=60)
    book.watch("Y", tick_size=0.01)
    book.on_print("Y", 1_755_000_000, 100.00, 10, 1)
    payload = book.payload("Y")
    assert (payload["tick_size"], payload["tick_size_source"]) == (0.01, "explicit")


def test_wide_range_instrument_gets_rows_wide_enough_to_be_contiguous():
    """1,944 grid rows for ~20 populated ones is an unreadable chart AND a dead
    imbalance: adjacent rows are never both populated, so the diagonal
    neighbour is always missing. Aggregate ticks into rows."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("BSE:SENSEXFUT")
    stamp = 1_755_000_000
    for i in range(20):
        price = 77_800.00 + 5 * i + 0.05 * (i % 3)
        book.on_print("BSE:SENSEXFUT", stamp + i, price, 20, -1)
    book.on_print("BSE:SENSEXFUT", stamp + 30, 77_850.00, 200, 1)

    payload = book.payload("BSE:SENSEXFUT")
    assert payload["tick_size"] == 0.05           # still a five-paise instrument
    assert payload["row_ticks"] > 1                # but not a five-paise ROW
    assert payload["row_size"] == payload["tick_size"] * payload["row_ticks"]

    bar = payload["bars"][0]
    keys = sorted(row["k"] for row in bar["levels"])
    assert len(keys) <= 30                         # readable
    assert keys == list(range(keys[0], keys[-1] + 1))   # and contiguous

    fired = [row for row in bar["levels"] if row["imb_buy"] or row["imb_sell"]]
    assert fired, "diagonal imbalance must be able to fire on a wide-range contract"


def test_row_index_is_integer_arithmetic_and_survives_float_drift():
    """orderflow.absorption already documents 100.20 - 100.00 = 0.20000000000000284.
    The ladder must not be subject to that at all."""
    assert quantise(100.20) - quantise(100.00) == 2000
    units = 500                                     # 0.05 rows
    assert row_index(quantise(100.20), units) - row_index(quantise(100.15), units) == 1

    book = FootprintBook(timeframe_seconds=60)
    book.watch("DRIFT", tick_size=0.05)
    book.on_print("DRIFT", 1_755_000_000, 100.15, 20, -1)
    book.on_print("DRIFT", 1_755_000_001, 100.20, 60, 1)
    level = _levels(_one_bar(book, "DRIFT"))[100.20]
    assert level["imb_buy"] is True and level["imb_ratio"] == pytest.approx(3.0)


def test_bar_ohlc_stays_on_raw_prices_after_rows_aggregate():
    """A bucketed high is wrong by up to a row once rows aggregate, and the
    unfinished-auction test would then key off a quantised extreme."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("RAW", tick_size=0.05)
    book.on_print("RAW", 1_755_000_000, 100.02, 10, 1)
    book.on_print("RAW", 1_755_000_001, 100.23, 10, 1)
    bar = _one_bar(book, "RAW")
    assert bar["o"] == 100.02 and bar["h"] == 100.23


# --------------------------------------------------------------------------
# volume conservation
# --------------------------------------------------------------------------

def test_unclassified_volume_survives_in_the_ladder_and_on_the_bar():
    """Measured live: v = 1980 against sum(bid+ask) = 1900, the 80-lot gap
    exactly the one side-0 print in that bar's tape, with no field from which
    any reader could recover it."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.00, 60, 1)
    book.on_print("X", 1_755_000_001, 100.00, 20, -1)
    book.on_print("X", 1_755_000_002, 100.05, 80, 0)      # no side could be inferred
    bar = _one_bar(book)
    assert bar["v"] == 160 and bar["u"] == 80
    assert sum(row["bid"] + row["ask"] + row["u"] for row in bar["levels"]) == bar["v"]
    assert bar["classified_share"] == pytest.approx(0.5)


def test_conservation_survives_read_time_aggregation():
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.00, 60, 1)
    book.on_print("X", 1_755_000_060, 100.05, 40, 0)
    book.on_print("X", 1_755_000_120, 100.10, 20, -1)
    for bar in book.payload("X", timeframe_seconds=300)["bars"]:
        assert sum(r["bid"] + r["ask"] + r["u"] for r in bar["levels"]) == bar["v"]
    merged = book.payload("X", timeframe_seconds=300)["bars"][0]
    assert merged["v"] == 120 and merged["u"] == 40


# --------------------------------------------------------------------------
# 3.1 single-row diagonal imbalance
# --------------------------------------------------------------------------

def test_absent_or_empty_neighbour_is_the_strongest_imbalance_not_the_absence_of_one():
    """The old guards `below[0] > 0` / `above[1] > 0` skipped exactly the
    infinite-ratio case every platform flags."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.00, 20, -1)
    book.on_print("X", 1_755_000_001, 100.10, 60, 1)      # nothing rested at 100.05
    book.on_print("X", 1_755_000_002, 100.15, 20, -1)
    level = _levels(_one_bar(book))[100.10]
    assert level["imb_buy"] is True
    assert level["imb_ratio"] is None                      # not a fabricated infinity


def test_both_diagonals_are_independent_tests_and_both_are_published():
    """The old `elif` discarded the sell finding on a row satisfying both."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.00, 10, -1)
    book.on_print("X", 1_755_000_001, 100.05, 60, 1)       # heavy ask ...
    book.on_print("X", 1_755_000_002, 100.05, 60, -1)      # ... and heavy bid, same row
    book.on_print("X", 1_755_000_003, 100.10, 10, 1)
    level = _levels(_one_bar(book))[100.05]
    assert level["imb_buy"] is True and level["imb_sell"] is True
    assert level["imb"] in ("buy", "sell")                 # old clients still get one


def test_imbalance_floor_scales_with_the_bar_and_is_published():
    """20 lots is sub-lot on a NIFTY future and material on a thin option."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("FAT", tick_size=0.05)
    for i in range(6):
        book.on_print("FAT", 1_755_000_000 + i, 100.00 + 0.05 * i, 4_000, 1)
    bar = _one_bar(book, "FAT")
    assert bar["imb_floor"] > MIN_IMBALANCE_VOLUME

    book.watch("THINNER", tick_size=0.05)
    book.on_print("THINNER", 1_755_000_000, 50.00, 30, 1)
    assert _one_bar(book, "THINNER")["imb_floor"] == MIN_IMBALANCE_VOLUME


def test_an_empty_row_inside_the_bar_is_evidence_but_one_outside_the_range_is_not():
    """The auction went through an empty interior row and nothing was resting
    there. A neighbour beyond the bar's extreme was simply never reached —
    flagging it would mark almost every bar's own high and low, which is
    exactly where a stack must not be fabricated."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.00, 60, 1)      # bottom row
    book.on_print("X", 1_755_000_001, 100.05, 60, 1)      # interior: nothing rested below
    book.on_print("X", 1_755_000_002, 100.10, 60, -1)     # top row
    levels = _levels(_one_bar(book))
    assert levels[100.05]["imb_buy"] is True and levels[100.05]["imb_edge"] is False
    assert levels[100.00]["imb_edge"] is True and levels[100.00]["imb_buy"] is False
    assert levels[100.10]["imb_edge"] is True and levels[100.10]["imb_sell"] is False


def test_a_row_below_the_floor_is_not_flagged():
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.00, MIN_IMBALANCE_VOLUME - 1, 1)
    assert _levels(_one_bar(book))[100.00]["imb_buy"] is False


# --------------------------------------------------------------------------
# 3.2 stacked imbalance
# --------------------------------------------------------------------------

def _stacked_book(aggressed=(100.05, 100.10, 100.15), method=None):
    """Buyers running three consecutive rows, with resting size below the run
    and a traded row above it so neither end of the stack is a bar extreme."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.00, 20, -1)         # resting sellers
    for i, price in enumerate(aggressed):
        book.on_print("X", 1_755_000_001 + i, price, 60, 1, method)
    book.on_print("X", 1_755_000_009, 100.20, 20, -1)
    return book


def test_a_stack_is_suppressed_when_quote_share_was_never_measured():
    """The strictest gate in the file must treat "nobody measured" as failing.

    A stack is N ratio tests over ~2N inferred cells, so it needs direct quote
    evidence more than any other marker. The gate previously read
    `quote_share is not None and quote_share < min_quote`, which let a bar with
    NO quote coverage at all sail through while a bar with poor-but-real
    coverage was suppressed — exactly inverted. The stack is still FOUND and
    published; it is flagged so the surface can stay quiet about it.
    """
    bar = _one_bar(_stacked_book())          # no flow => quote share unmeasured
    stack = bar["stacks"][0]
    assert stack["suppressed"] is True
    assert stack["reason"] == "quote share unmeasured"


def test_three_consecutive_same_side_rows_make_one_stack():
    bar = _one_bar(_stacked_book(), flow=GOOD_FLOW)
    assert len(bar["stacks"]) == 1
    stack = bar["stacks"][0]
    assert stack["side"] == "buy" and stack["rows"] == STACKED_IMBALANCE_MIN
    assert stack["from"] == 100.05 and stack["to"] == 100.15
    assert stack["extreme"] == 100.15          # buy stack: the top row
    assert stack["volume"] == 180
    assert stack["suppressed"] is False


def test_a_missing_row_breaks_the_run_it_does_not_count_as_imbalanced():
    bar = _one_bar(_stacked_book(aggressed=(100.05, 100.10, 100.20)))
    assert bar["stacks"] == []


def test_a_sell_stack_marks_its_lowest_row_as_the_extreme():
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.20, 20, 1)          # resting buyers above
    for i, price in enumerate([100.15, 100.10, 100.05]):
        book.on_print("X", 1_755_000_001 + i, price, 60, -1)
    book.on_print("X", 1_755_000_009, 100.00, 20, 1)
    stack = _one_bar(book)["stacks"][0]
    assert stack["side"] == "sell" and stack["extreme"] == 100.05


def test_a_stack_is_suppressed_but_still_published_when_confidence_is_low():
    """A stack is N ratio tests over ~2N inferred cells: it compounds
    classifier error rather than averaging it out. The API stays honest, the
    surface stays quiet."""
    book = _stacked_book()
    poor = {"classified_share": 0.42, "quote_share": 0.05, "trades": 100,
            "methods": {"quote": 5, "mid": 5, "tick": 90, "zero_tick": 0}}
    payload = book.payload("X", flow=poor)
    assert payload["confidence"]["grade"] == "low"
    stack = payload["bars"][0]["stacks"][0]
    assert stack["suppressed"] is True and stack["reason"]


def test_exact_markers_are_never_suppressed_by_a_poor_feed():
    """POC, value area and LVNs use total volume at price, which the exchange
    publishes. They survive a 42% classified share; the client must be able to
    see that structurally."""
    book = _stacked_book()
    poor = {"classified_share": 0.42, "quote_share": 0.05, "trades": 100}
    bar = book.payload("X", flow=poor)["bars"][0]
    assert bar["poc"] is not None and bar["vah"] is not None
    assert "poc" in BASIS["volume"] and "vah" in BASIS["volume"]
    assert "stacks" in BASIS["inferred"] and "cvd" in BASIS["inferred"]


def test_a_stack_built_entirely_from_zero_tick_carry_is_suppressed():
    stack = _one_bar(_stacked_book(method="zero_tick"))["stacks"][0]
    assert stack["suppressed"] is True
    assert "zero-tick" in stack["reason"]


# --------------------------------------------------------------------------
# 3.3 unfinished auction
# --------------------------------------------------------------------------

def _extremes_book(prints):
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    for i, (price, size, side) in enumerate(prints):
        book.on_print("X", 1_755_000_000 + i, price, size, side)
    return _one_bar(book)


def test_both_sides_at_the_top_row_is_an_unfinished_auction():
    bar = _extremes_book([(100.00, 100, 1), (100.05, 60, 1), (100.05, 60, -1)])
    assert bar["unfinished"]["high"] == "unfinished"
    assert bar["unfinished"]["high_price"] == 100.05


def test_no_ask_at_the_top_row_means_the_auction_finished():
    bar = _extremes_book([(100.00, 100, 1), (100.05, 60, -1)])
    assert bar["unfinished"]["high"] == "finished"


def test_a_minority_side_below_the_floor_is_weak_not_unfinished():
    """One stray print at the extreme must not flip a claim: the aggressor is
    inferred, and extremes are where the tick rule is weakest."""
    bar = _extremes_book([(100.00, 10_000, 1), (100.05, 500, 1), (100.05, 1, -1)])
    assert bar["unfinished"]["high"] == "weak"
    # the reader can see BY HOW MUCH, not just the word
    assert bar["unfinished"]["high_minority"] == 1
    assert bar["unfinished"]["floor"] == pytest.approx(0.02 * 10_501, abs=0.01)


def test_a_wholly_unclassified_extreme_publishes_null_not_finished():
    """'Finished' is a claim. Nobody measured it."""
    bar = _extremes_book([(100.00, 100, 1), (100.05, 60, 0)])
    assert bar["unfinished"]["high"] is None


def test_a_one_row_bar_has_no_auction_verdict_at_either_end():
    bar = _extremes_book([(100.00, 100, 1)])
    assert bar["unfinished"]["high"] is None and bar["unfinished"]["low"] is None


# --------------------------------------------------------------------------
# 3.4 per-bar value area and POC
# --------------------------------------------------------------------------

def test_bar_value_area_matches_the_session_profile_algorithm_on_the_same_counts():
    volumes = {100.00: 10, 100.05: 30, 100.10: 50, 100.15: 20, 100.20: 5}
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    for i, (price, size) in enumerate(volumes.items()):
        book.on_print("X", 1_755_000_000 + i, price, size, 1)
    bar = _one_bar(book)

    profile = Profile("X", "2026-08-27")
    profile.tpo = {price: set(range(count)) for price, count in volumes.items()}
    profile._structure_version = 1
    vah, val = profile.value_area()

    assert (bar["vah"], bar["val"]) == (vah, val)
    assert bar["poc"] == profile.poc
    assert bar["va_method"] == "single_row_alternating"
    assert bar["va_share"] == pytest.approx(100 / 115, abs=1e-3)
    assert _levels(bar)[100.10]["va"] is True
    assert _levels(bar)[100.20]["va"] is False


def test_poc_ties_break_by_proximity_to_centre_not_by_which_price_printed_first():
    """max(self.levels, key=...) broke ties by dict insertion order, so the POC
    could move between polls without a single trade."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.00, 50, 1)      # printed first
    book.on_print("X", 1_755_000_001, 100.10, 50, 1)
    book.on_print("X", 1_755_000_002, 100.05, 50, 1)      # centre row, printed last
    assert _one_bar(book)["poc"] == 100.05


def test_value_area_is_null_on_a_single_row_bar():
    bar = _extremes_book([(100.00, 100, 1)])
    assert bar["vah"] is None and bar["val"] is None and bar["va_share"] is None


# --------------------------------------------------------------------------
# 3.8 low-volume nodes and single prints
# --------------------------------------------------------------------------

def test_a_local_volume_minimum_far_below_the_poc_is_a_low_volume_node():
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    sizes = {100.00: 100, 100.05: 100, 100.10: 5, 100.15: 100,
             100.20: 100, 100.25: 100}
    for i, (price, size) in enumerate(sizes.items()):
        book.on_print("X", 1_755_000_000 + i, price, size, 1)
    bar = _one_bar(book)
    assert [zone["from"] for zone in bar["lvn"]] == [100.10]
    assert bar["lvn"][0]["volume"] == 5
    assert _levels(bar)[100.10]["lvn"] is True
    assert _levels(bar)[100.00]["lvn"] is False


def test_a_missing_neighbour_fails_the_local_minimum_test():
    """An LVN is defined against both its neighbours; an absent row is not
    evidence of a low-volume node."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    for i, (price, size) in enumerate([(100.00, 100), (100.05, 100), (100.10, 100),
                                       (100.15, 100), (100.20, 100), (100.30, 5)]):
        book.on_print("X", 1_755_000_000 + i, price, size, 1)
    assert _one_bar(book)["lvn"] == []


def test_single_print_rows_are_reported_with_the_window_they_were_measured_over():
    """'Single print over 8 bars' and 'over 80' are different claims."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.00, 40, 1)
    book.on_print("X", 1_755_000_001, 100.05, 40, 1)      # bar 1 only
    book.on_print("X", 1_755_000_060, 100.00, 40, 1)      # bar 2 revisits 100.00
    payload = book.payload("X")
    assert payload["single_print_rows"] == [100.05]
    assert payload["single_print_window_bars"] == 2


# --------------------------------------------------------------------------
# 3.6 absorption and exhaustion
# --------------------------------------------------------------------------

def test_heavy_one_sided_volume_that_fails_to_move_price_is_absorption():
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.00, 20, 1)
    book.on_print("X", 1_755_000_001, 100.05, 20, 1)
    book.on_print("X", 1_755_000_002, 100.10, 400, 1)     # heavy buying, price stalls
    hit = _one_bar(book)["absorption"][0]
    assert hit["side"] == "buyers_absorbed"               # named for the AGGRESSOR
    assert hit["price"] == 100.10 and hit["volume"] == 400
    assert hit["pressure"] == pytest.approx(1.0)


def test_heavy_volume_that_did_move_price_is_not_absorption():
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.00, 400, 1)
    book.on_print("X", 1_755_000_001, 100.05, 20, 1)
    book.on_print("X", 1_755_000_002, 100.50, 20, 1)      # buyers got well through
    assert _one_bar(book)["absorption"] == []


def test_a_high_priced_instrument_can_absorb_at_all():
    """Regression for the session-level gate that measured span_ticks 1943.0
    against a <= 4.0 threshold because the tick was fixed at 0.05."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("BSE:SENSEXFUT")
    stamp = 1_755_000_000
    for i in range(12):
        book.on_print("BSE:SENSEXFUT", stamp + i, 77_800.00 + 0.05 * i, 20, 1)
    book.on_print("BSE:SENSEXFUT", stamp + 20, 77_800.30, 4_000, 1)
    hits = _one_bar(book, "BSE:SENSEXFUT")["absorption"]
    assert hits and hits[0]["side"] == "buyers_absorbed"


def test_a_thin_extreme_after_a_move_is_exhaustion():
    """An exhausted high: price ran up into a thin top row whose ONLY trades
    were sell-initiated — buyers stopped paying up, so there is no ask left at
    the extreme. That zero-ask shape is what makes the auction 'finished'."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    for i, (price, size) in enumerate([(100.00, 200), (100.05, 100),
                                       (100.10, 50)]):
        book.on_print("X", 1_755_000_000 + i, price, size, 1)
    book.on_print("X", 1_755_000_003, 100.15, 10, -1)   # sellers hitting the bid
    exhaustion = _one_bar(book)["exhaustion"]
    assert exhaustion["end"] == "high" and exhaustion["price"] == 100.15
    assert exhaustion["ratio"] <= EXHAUST_VOLUME_FRACTION
    assert exhaustion["volume_test"] is True and exhaustion["auction_test"] is True
    assert exhaustion["detected"] is True


def test_a_thin_high_still_being_lifted_is_not_exhaustion():
    """The inverted shape, which used to detect. Identical volumes to the test
    above, but the top row's trades are BUY-initiated: zero bid, live ask.
    That is a high being aggressively bought, not one buyers walked away from.

    _unfinished called any one-sided extreme 'finished' without asking WHICH
    side was empty, so this fired an exhaustion marker — the exact opposite of
    what the function's own docstring defines, and a reversal signal pointing
    the wrong way.
    """
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    for i, (price, size) in enumerate([(100.00, 200), (100.05, 100),
                                       (100.10, 50), (100.15, 10)]):
        book.on_print("X", 1_755_000_000 + i, price, size, 1)
    bar = _one_bar(book)
    assert bar["unfinished"]["high"] == "one_sided"
    exhaustion = bar["exhaustion"]
    assert exhaustion["volume_test"] is True, "the volume leg is still true"
    assert exhaustion["auction_test"] is False
    assert exhaustion["detected"] is False, "a bought high is not an exhausted high"


def test_exhaustion_and_an_unfinished_auction_are_never_both_true_at_one_end():
    """An exhausted high has no ask at the top row; an unfinished high has
    both sides. A bar cannot be both."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    for i, (price, size, side) in enumerate([(100.00, 200, 1), (100.05, 100, 1),
                                             (100.10, 50, 1), (100.15, 5, 1),
                                             (100.15, 5, -1)]):
        book.on_print("X", 1_755_000_000 + i, price, size, side)
    bar = _one_bar(book)
    assert bar["unfinished"]["high"] in ("unfinished", "weak")
    assert bar["exhaustion"]["auction_test"] is False
    assert bar["exhaustion"]["detected"] is False
    # the exact half of the test is still reported
    assert bar["exhaustion"]["volume_test"] is True


def test_exhaustion_reports_the_two_tests_separately_when_the_auction_is_unknown():
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    for i, (price, size, side) in enumerate([(100.00, 200, 1), (100.05, 100, 1),
                                             (100.10, 50, 1), (100.15, 10, 0)]):
        book.on_print("X", 1_755_000_000 + i, price, size, side)
    bar = _one_bar(book)
    assert bar["exhaustion"]["volume_test"] is True
    assert bar["exhaustion"]["auction_test"] is None      # nobody measured it
    assert bar["exhaustion"]["detected"] is False


# --------------------------------------------------------------------------
# 3.5 bar-level delta divergence
# --------------------------------------------------------------------------

def test_a_new_price_extreme_the_cumulative_delta_does_not_confirm():
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.0, 100, 1)
    book.on_print("X", 1_755_000_060, 101.0, 100, 1)
    book.on_print("X", 1_755_000_120, 105.0, 10, 1)
    book.on_print("X", 1_755_000_130, 104.0, 300, -1)
    divergence = book.payload("X")["divergence_bars"]
    assert divergence["kind"] == "bearish"
    assert divergence["at_bar"] == 1_755_000_120
    # The other leg is published: a divergence you cannot see is unfalsifiable.
    assert divergence["reference_bar"] == 1_755_000_060
    assert divergence["reference_price_extreme"] == 101.0
    assert divergence["price_extreme"] == 105.0


def test_a_new_extreme_the_delta_confirms_is_not_a_divergence():
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.0, 100, 1)
    book.on_print("X", 1_755_000_060, 101.0, 100, 1)
    book.on_print("X", 1_755_000_120, 105.0, 400, 1)
    assert book.payload("X")["divergence_bars"]["kind"] is None


def test_divergence_needs_the_extreme_in_the_most_recent_bar():
    """The session implementation splits the window in halves, which fires
    whenever the extreme merely happens to fall late."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.0, 100, 1)
    book.on_print("X", 1_755_000_060, 105.0, 100, 1)      # the high is here ...
    book.on_print("X", 1_755_000_120, 101.0, 300, -1)     # ... not in the last bar
    assert book.payload("X")["divergence_bars"]["kind"] is None


# --------------------------------------------------------------------------
# 3.7 the CVD anchor
# --------------------------------------------------------------------------

def test_watching_with_a_session_delta_anchors_cvd_to_the_session():
    """Two CVD numbers with different anchors are not two views of one thing.
    Measured live on one contract at one instant: 1,460 against 16,840."""
    seed = [Print(1_755_000_000, 10.00, 40, -1, "quote"),
            Print(1_755_000_005, 10.05, 90, 1, "quote")]
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", seed_prints=seed, session_delta=16_840)
    payload = book.payload("X")
    assert payload["bars"][-1]["cvd"] == 16_840
    assert payload["cvd_basis"] == "session"
    assert payload["cvd_anchor"] == 16_840 - 50           # seed replays to the session figure
    assert payload["session_cumulative_delta"] == 16_840


def test_without_a_session_delta_the_basis_says_window_rather_than_pretending():
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X")
    book.on_print("X", 1_755_000_000, 10.0, 40, 1)
    payload = book.payload("X")
    assert payload["cvd_basis"] == "watch_window"
    assert payload["bars"][-1]["cvd"] == 40


def test_cvd_band_is_the_hard_bound_the_unclassified_volume_implies():
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.00, 60, 1)
    book.on_print("X", 1_755_000_001, 100.00, 40, 0)
    payload = book.payload("X")
    assert payload["cvd_band"] == 40 and payload["cvd_band_basis"] == "watch_window"

    session = payload = book.payload("X", flow={"unclassified_volume": 2_710.0})
    assert session["cvd_band"] == 2_710.0 and session["cvd_band_basis"] == "session"


def test_cvd_band_is_null_rather_than_zero_when_nothing_was_measured():
    book = FootprintBook(timeframe_seconds=60)
    assert book.payload("NEVER_WATCHED")["cvd_band"] is None


# --------------------------------------------------------------------------
# 3.9 the confidence overlay
# --------------------------------------------------------------------------

def test_an_unmeasured_classified_share_grades_to_nothing_at_all():
    """Rendering 'low' there would be a claim about a measurement nobody
    took."""
    assert confidence_grade(None, 0.9) == (None, None)
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X")
    payload = book.payload("X", flow={"classified_share": None, "quote_share": None})
    assert payload["confidence"]["grade"] is None
    assert payload["confidence"]["classified_share"] is None


def test_grade_thresholds_are_fixed_here_not_left_to_the_client():
    assert confidence_grade(0.95, 0.7)[0] == "high"
    assert confidence_grade(0.80, 0.40)[0] == "fair"
    assert confidence_grade(0.95, 0.10)[0] == "low"       # quote share drags it down
    assert confidence_grade(0.50, 0.90)[0] == "low"


def test_an_unmeasured_quote_share_grades_on_the_volume_share_and_says_so():
    grade, basis = confidence_grade(0.95, None)
    assert grade == "high" and basis == "classified_share"


def test_confidence_reports_depth_share_beside_quote_share():
    """Full depth coverage with a 0.61 quote share invites the reader to
    conclude the feed was thin when it was perfect — 144 prints simply landed
    inside the spread."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X")
    book.on_print("X", 1_755_000_000, 100.0, 10, 1)
    flow = {"classified_share": 0.91, "quote_share": 0.606, "trades": 416,
            "depth_ticks": 416,
            "methods": {"quote": 252, "mid": 144, "tick": 20, "zero_tick": 0}}
    confidence = book.payload("X", flow=flow)["confidence"]
    assert confidence["depth_share"] == 1.0
    assert confidence["method_mix"]["mid"] == 144
    assert confidence["grade"] == "high"


def test_per_bar_coverage_is_published_so_one_bad_bar_can_be_spotted():
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.00, 100, 1)
    book.on_print("X", 1_755_000_060, 100.05, 50, 1)
    book.on_print("X", 1_755_000_061, 100.05, 50, 0)      # depth outage in bar 2
    bars = book.payload("X")["bars"]
    assert bars[0]["classified_share"] == 1.0
    assert bars[1]["classified_share"] == 0.5


def test_bar_method_mix_is_null_when_no_print_carried_a_method():
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X")
    book.on_print("X", 1_755_000_000, 100.0, 10, 1)
    assert _one_bar(book)["methods"] is None

    book.watch("Q")
    book.on_print("Q", 1_755_000_000, 100.0, 10, 1, "quote")
    assert _one_bar(book, "Q")["methods"]["quote"] == 1


def test_coverage_states_how_much_of_the_session_the_clusters_cover():
    """A chart opened at 14:00 shows a few minutes of clusters and otherwise
    looks identical to one that captured all day."""
    seed = [Print(1_755_000_000, 10.00, 40, -1, "quote")]
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", seed_prints=seed)
    coverage = book.payload("X")["coverage"]
    assert coverage["seeded_prints"] == 1
    assert coverage["first_bar_t"] == 1_755_000_000
    assert coverage["seed_truncated"] is None             # a list cannot say


def test_seed_truncation_is_knowable_from_a_bounded_container():
    from collections import deque

    seed = deque([Print(1_755_000_000 + i, 10.00, 1, 1, "quote") for i in range(3)],
                 maxlen=3)
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", seed_prints=seed)
    assert book.payload("X")["coverage"]["seed_truncated"] is True


# --------------------------------------------------------------------------
# payload shape and cost
# --------------------------------------------------------------------------

def test_payload_publishes_the_grid_and_the_basis_map():
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.0, 10, 1)
    payload = book.payload("X")
    assert payload["spec_version"] == 1
    assert payload["imbalance_ratio"] == IMBALANCE_RATIO
    assert payload["stacked_imbalance_min"] == STACKED_IMBALANCE_MIN
    assert set(payload["basis"]) == {"volume", "inferred"}
    assert not set(payload["basis"]["volume"]) & set(payload["basis"]["inferred"])


def test_the_old_level_shape_still_reads_the_way_the_chart_expects():
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.00, 50, 1)
    book.on_print("X", 1_755_000_001, 100.00, 20, -1)
    level = _levels(_one_bar(book))[100.00]
    assert {"p", "bid", "ask", "d", "imb", "poc"} <= set(level)
    assert level["ask"] == 50 and level["bid"] == 20 and level["d"] == 30


def test_row_ticks_can_be_overridden_by_the_caller():
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.00, 10, 1)
    book.on_print("X", 1_755_000_001, 100.05, 10, 1)
    coarse = book.payload("X", row_ticks=5)
    assert coarse["row_ticks"] == 5 and coarse["row_size"] == pytest.approx(0.25)
    assert len(coarse["bars"][0]["levels"]) == 1          # both prices share a row


def test_closed_bars_are_not_recomputed_on_every_poll():
    """The workspace polls at 3s with up to 80 bars across 16 watched symbols;
    a closed bar never changes."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.00, 40, 1)
    book.on_print("X", 1_755_000_060, 100.05, 40, 1)
    first = book.payload("X")["bars"][0]
    second = book.payload("X")["bars"][0]
    assert first is second

    book.on_print("X", 1_755_000_061, 100.05, 10, -1)     # only the live bar moves
    assert book.payload("X")["bars"][0] is first
    assert book.payload("X")["bars"][1]["v"] == 50


def test_soft_markers_are_suppressed_when_the_grade_was_never_measured():
    """A null grade means the classified share was never measured. Every marker
    below it is an inference over classified cells, so none of them has a basis.

    Only `low` used to suppress, and null fell through as if it were `fair` —
    putting absorption, exhaustion and divergence on precisely the bars with no
    support at all. They are still published (with a reason); what changes is
    that the surface can now tell they are unsupported.
    """
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    for i, (price, size) in enumerate([(100.00, 200), (100.05, 100),
                                       (100.10, 50)]):
        book.on_print("X", 1_755_000_000 + i, price, size, 1)
    book.on_print("X", 1_755_000_003, 100.15, 10, -1)
    bar = _one_bar(book, flow={"classified_share": None})
    exh = bar["exhaustion"]
    assert exh["suppressed"] is True
    assert exh["reason"] == "confidence grade unmeasured"


def test_a_measured_grade_still_lets_soft_markers_through():
    """The companion to the test above: the suppression must key on UNMEASURED,
    not fire on everything and make the surface uniformly silent."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    for i, (price, size) in enumerate([(100.00, 200), (100.05, 100),
                                       (100.10, 50)]):
        book.on_print("X", 1_755_000_000 + i, price, size, 1)
    book.on_print("X", 1_755_000_003, 100.15, 10, -1)
    bar = _one_bar(book, flow=GOOD_FLOW)
    exh = bar["exhaustion"]
    assert exh["suppressed"] is False and exh["reason"] is None
    assert exh["detected"] is True


# --------------------------------------------------------------------------
# per-bar classification confidence
# --------------------------------------------------------------------------

def test_a_bars_confidence_is_the_volume_weighted_mean_of_its_prints():
    """Weighted by size, not by count: one 900-lot print carried by a lone tick
    rule and one 10-lot print with three rules agreeing do not average to a
    confident bar."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.00, 100, 1, "quote", 0.9)
    book.on_print("X", 1_755_000_010, 100.05, 100, -1, "tick", 0.4)
    bar = _one_bar(book)

    assert bar["confidence"] == 0.65
    assert bar["low_confidence"] is False, f"0.65 sits above {LOW_CONFIDENCE_BAR}"


def test_a_bar_carried_by_the_tick_rule_alone_is_flagged_low_confidence():
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.00, 100, 1, "tick", 0.4)
    book.on_print("X", 1_755_000_010, 100.05, 100, 1, "tick", 0.4)
    bar = _one_bar(book)

    assert bar["confidence"] == 0.4
    assert bar["low_confidence"] is True


def test_a_bar_whose_prints_nobody_scored_publishes_no_confidence_at_all():
    """None, never 0.0: zero would read as "every vote failed", which is a claim
    about prints nobody voted on — a bar seeded from an older checkpoint."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.00, 100, 1)
    bar = _one_bar(book)

    assert bar["confidence"] is None
    assert bar["low_confidence"] is False


def test_the_weighted_cvd_anchors_the_same_way_the_raw_one_does():
    """Two lines share the sub-pane, so they have to share a session origin.
    Anchoring one to the session and leaving the other on the watch window
    would put a constant offset between them and make every crossing false."""
    seed = [Print(1_755_000_000, 100.00, 100, 1, "quote", 0.8)]
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", seed_prints=seed, tick_size=0.05,
               session_delta=500.0, session_weighted_delta=400.0)
    book.on_print("X", 1_755_000_100, 100.05, 50, 1, "quote", 0.8)
    bars = book.payload("X")["bars"]

    # The seed replays back onto the session totals it was measured against.
    assert bars[0]["cvd"] == 500 and bars[0]["wcvd"] == 400
    assert bars[-1]["cvd"] == 550 and bars[-1]["wcvd"] == 440


def test_the_shading_threshold_travels_with_the_payload():
    """The chart shades bars below it, so publishing the number is what keeps
    the API and the picture from disagreeing about which bars are weak."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.00, 100, 1, "quote", 0.7)

    assert book.payload("X")["low_confidence_bar"] == LOW_CONFIDENCE_BAR
    assert "confidence" in BASIS["inferred"] and "low_confidence" in BASIS["inferred"]


# --------------------------------------------------------------------------
# freeze-size and large prints
# --------------------------------------------------------------------------

def test_a_cluster_equal_to_the_freeze_quantity_is_marked_on_the_bar():
    """1,800 units is the largest single NIFTY order the exchange accepts, so a
    cluster landing exactly there is a whale's child order — the same A2 test
    whale.detect runs offline, applied as the print arrives."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("NSE:NIFTY26SEP24000CE", tick_size=0.05, day="2026-09-01")
    book.on_print("NSE:NIFTY26SEP24000CE", 1_788_241_500, 100.00, 1800, 1, "quote", 0.7)
    bar = _one_bar(book, "NSE:NIFTY26SEP24000CE")
    mark = bar["marked_prints"][0]

    assert mark["kind"] == "freeze" and mark["s"] == 1800
    assert mark["lots"] == 27, "1,800 units at 65 a lot"
    assert book.payload("NSE:NIFTY26SEP24000CE")["freeze_quantity"] == 1800


def test_a_cluster_one_lot_short_of_the_freeze_quantity_is_not_marked():
    """The freeze test is an equality, not a threshold: it identifies an order
    capped by the rule, and 1,735 units was capped by nothing."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("NSE:NIFTY26SEP24000CE", tick_size=0.05, day="2026-09-01")
    book.on_print("NSE:NIFTY26SEP24000CE", 1_788_241_500, 100.00, 1735, 1, "quote", 0.7)

    assert _one_bar(book, "NSE:NIFTY26SEP24000CE")["marked_prints"] == []


def test_an_instrument_with_no_freeze_rule_publishes_none_rather_than_zero():
    """"No freeze rule applies" and "the freeze quantity is zero" are different
    statements, and only the first is true of a stock option."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("NSE:SBIN26SEP780CE", tick_size=0.05, day="2026-09-01")
    book.on_print("NSE:SBIN26SEP780CE", 1_788_241_500, 40.0, 1800, 1, "quote", 0.7)
    payload = book.payload("NSE:SBIN26SEP780CE")

    assert payload["freeze_quantity"] is None
    assert payload["bars"][0]["marked_prints"] == []


def test_a_print_ten_times_the_running_tape_median_is_marked_large():
    """The median needs a tape to be taken over; before thirty rows exist,
    "ten times the median" is ten times whatever happened to arrive first."""
    book = FootprintBook(timeframe_seconds=3600)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.00, 500, 1)      # nothing to rank against
    assert _one_bar(book)["marked_prints"] == []

    for i in range(1, 31):
        book.on_print("X", 1_755_000_000 + i, 100.00, 10, 1)
    book.on_print("X", 1_755_000_100, 100.00, 500, 1)
    marks = _one_bar(book)["marked_prints"]

    assert [m["kind"] for m in marks] == ["large"]
    assert marks[0]["s"] == 500 and marks[0]["ratio"] == 50.0


def test_the_marked_prints_are_declared_a_measurement_not_an_inference():
    """The quantity is exactly what traded; only the side it hangs on is
    inferred. Publishing it under `inferred` would let the chart dim it with
    the estimates, and publishing the SIDE as exact would be worse."""
    assert "marked_prints" in BASIS["volume"]


def test_window_classified_share_ties_out_to_the_bars_it_summarises():
    """The payload's window-level coverage figure must be reconstructible from
    the bars it ships alongside. If a reader cannot recompute it from `v` and
    `u`, the header number and the chart are two independent claims about one
    tape, and a drift between them is invisible."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.00, 60, 1)
    book.on_print("X", 1_755_000_061, 100.05, 40, 0)      # unclassifiable
    book.on_print("X", 1_755_000_122, 100.10, 20, -1)
    payload = book.payload("X")
    bars = payload["bars"]
    v = sum(b["v"] for b in bars)
    u = sum(b["u"] for b in bars)
    assert v == 120 and u == 40
    # abs=5e-5: the payload rounds this to 4dp, so the invariant holds to the
    # precision it is published at and no tighter.
    assert payload["confidence"]["window_classified_share"] == pytest.approx(
        (v - u) / v, abs=5e-5)
    # and the per-bar ladder conserves inside every bar
    for b in bars:
        assert sum(r["bid"] + r["ask"] + r["u"] for r in b["levels"]) == b["v"]


def test_the_bars_confidence_does_not_count_prints_that_got_no_side():
    """`confidence` is how much the three rules AGREED; `classified_share` is
    how much of the bar could be sided at all. combine_votes returns 0.0 for
    the "unknown" verdict, so folding sideless prints in at that score made the
    first a worse restatement of the second -- and the chart shades on both, so
    a column the coverage stripe already flags took a second warn wash and a
    tooltip reading "Bar conf 0.35 (weak)" that was a false statement about
    agreement."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    # 500 lots sided by the uncontested quote rule -- the strongest single-rule
    # verdict there is -- and 500 the classifier could not side at all.
    book.on_print("X", 1_755_000_000, 100.00, 500, 1, "quote", 0.7)
    book.on_print("X", 1_755_000_001, 100.05, 500, 0, "unknown", 0.0)

    bar = _one_bar(book, "X", flow=GOOD_FLOW)
    assert bar["classified_share"] == 0.5
    assert bar["confidence"] == 0.7
    assert bar["low_confidence"] is False
    # And the weighted delta still weights only what was actually sided.
    assert bar["wdelta"] == pytest.approx(0.7 * 500)


def test_a_bar_of_nothing_but_unsided_prints_reports_no_confidence_at_all():
    """Not 0.0, which reads as "every vote failed" -- no vote was cast."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.00, 500, 0, "unknown", 0.0)

    bar = _one_bar(book, "X", flow=GOOD_FLOW)
    assert bar["confidence"] is None
    assert bar["classified_share"] == 0.0


def test_the_rolling_tape_median_matches_a_sort_of_the_same_tape():
    """_mark took the median by sorting the 200-row tape on every print, which
    made on_print 8.6x slower on the ingest path and dominated a replay's
    session rebuild. The sorted index that replaced it must give the same
    number, print for print -- an incremental statistic that drifts would move
    the large-print threshold silently."""
    from macd_trader.footprint import _median

    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    sizes = [((i * 37) % 101) + 1 for i in range(600)]     # well past TAPE_LENGTH
    for i, size in enumerate(sizes):
        book.on_print("X", 1_755_000_000 + i, 100.00 + (i % 5) * 0.05, size,
                      1 if i % 2 else -1, "quote", 0.7)
        assert book._tape_sizes["X"] == sorted(row["s"] for row in book.tape["X"])
    assert _median(book._tape_sizes["X"]) == _median(row["s"] for row in book.tape["X"])


def test_forgetting_a_symbol_drops_its_tape_index_too():
    """The index is per symbol and shadows the tape; leaving it behind on an
    eviction would leak, and worse, would give a re-watched symbol a median
    taken over a tape it no longer has."""
    book = FootprintBook(timeframe_seconds=60)
    book.watch("X", tick_size=0.05)
    book.on_print("X", 1_755_000_000, 100.00, 50, 1, "quote", 0.7)
    book.bars.pop("X")                                     # as an eviction does
    book._forget("X")
    assert "X" not in book._tape_sizes

    book.watch("X", tick_size=0.05)
    assert book._tape_sizes["X"] == []
