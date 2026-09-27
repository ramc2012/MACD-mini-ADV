"""Book-based OFI, the three-vote classifier, and regime tagging."""
from __future__ import annotations

import asyncio
import tempfile
from datetime import date, datetime, timedelta, timezone

import pytest

from macd_trader.market_profile import IST
from macd_trader.models import Tick
from macd_trader.mp_engine import MPEngine, MPSettings
from macd_trader.ofi import BookState, OFIState, OFITracker, event_contribution, replay
from macd_trader.orderflow import (
    BATCHED_CONFIDENCE_FACTOR, BOTH_AGREE_CONFIDENCE, CONFLICT_CONFIDENCE,
    PENDING_ONLY_CONFIDENCE, QUOTE_ONLY_CONFIDENCE, TICK_ONLY_CONFIDENCE,
    classify_update, combine_votes, pending_vote, quote_vote, tick_vote,
)
from macd_trader.regimes import (
    REGIMES, group_by_regime, is_expiry_day, regime_for, regime_id_for, spans_a_break,
)

pytestmark = pytest.mark.usefixtures("fixed_auction_session_clock")


# ---------------------------------------------------------------------------
# OFI event decomposition
# ---------------------------------------------------------------------------

def _book(bid, ask, bid_qty, ask_qty):
    return BookState(bid, ask, bid_qty, ask_qty)


def _desk(folder):
    desk = MPEngine(f"{folder}/mp.sqlite3",
                    settings=MPSettings(enabled=True, auto_trade=False))
    desk.session_day = datetime.now(IST).date().isoformat()
    return desk


def _live_tick(symbol, price, volume, minute, bid, ask, bid_qty, ask_qty):
    """Stamped inside today's session so on_tick does not roll or reject it."""
    today = datetime.now(IST).date()
    moment = datetime(today.year, today.month, today.day, tzinfo=IST) + timedelta(minutes=minute)
    return Tick(symbol, price, volume, timestamp=moment.astimezone(timezone.utc),
                bid=bid, ask=ask, bid_qty=bid_qty, ask_qty=ask_qty, last_qty=volume)


def test_size_added_at_an_unchanged_bid_is_buying_pressure():
    """Both bid indicators fire, so the term collapses to the size difference."""
    contribution = event_contribution(_book(100.0, 100.1, 500, 500),
                                      _book(100.0, 100.1, 700, 500))

    assert contribution == 200


def test_size_pulled_from_an_unchanged_ask_is_also_buying_pressure():
    contribution = event_contribution(_book(100.0, 100.1, 500, 500),
                                      _book(100.0, 100.1, 500, 300))

    assert contribution == 200


def test_a_bid_that_steps_up_adds_its_whole_new_size():
    # Bid rises: +qb(n). Bid did not fall, so no subtraction. Ask unchanged
    # cancels out entirely.
    contribution = event_contribution(_book(100.0, 100.2, 500, 400),
                                      _book(100.1, 100.2, 600, 400))

    assert contribution == 600


def test_a_bid_that_is_taken_out_removes_the_size_that_left():
    contribution = event_contribution(_book(100.0, 100.2, 500, 400),
                                      _book(99.9, 100.2, 300, 400))

    assert contribution == -500


def test_the_signal_is_antisymmetric_between_the_two_sides():
    """A book event and its mirror image must cancel, or OFI carries a
    directional bias that would accumulate all session."""
    up = event_contribution(_book(100.0, 100.1, 500, 500), _book(100.0, 100.1, 900, 500))
    down = event_contribution(_book(100.0, 100.1, 500, 500), _book(100.0, 100.1, 500, 900))

    assert up == -down


# ---------------------------------------------------------------------------
# OFI state
# ---------------------------------------------------------------------------

def test_the_first_quote_only_seeds_the_book():
    state = OFIState(symbol="S")

    assert state.update(1.0, 100.0, 100.1, 500, 500) is None
    assert state.events == 0
    assert state.update(2.0, 100.0, 100.1, 700, 500) == 200
    assert state.cumulative == 200


@pytest.mark.parametrize("bid,ask,bid_qty,ask_qty", [
    (None, 100.1, 500, 500),      # missing quote
    (100.2, 100.1, 500, 500),     # crossed book
    (0.0, 100.1, 500, 500),       # empty bid
    (100.0, 100.1, -1, 500),      # negative size
])
def test_unusable_frames_are_skipped_not_clamped(bid, ask, bid_qty, ask_qty):
    """A crossed or empty frame is a feed artefact. Letting one through injects
    a spike into a cumulative series that never forgets it."""
    state = OFIState(symbol="S")
    state.update(1.0, 100.0, 100.1, 500, 500)

    assert state.update(2.0, bid, ask, bid_qty, ask_qty) is None
    assert state.cumulative == 0.0
    assert state.events == 0


def test_window_sums_only_the_trailing_period():
    state = OFIState(symbol="S")
    state.update(0.0, 100.0, 100.1, 500, 500)
    state.update(10.0, 100.0, 100.1, 600, 500)     # +100
    state.update(100.0, 100.0, 100.1, 700, 500)    # +100

    assert state.window(100.0, 60.0) == 100
    assert state.window(100.0, 300.0) == 200


def test_normalisation_is_withheld_until_the_depth_window_is_populated():
    """An un-normalised number wearing a normalised label is worse than a gap."""
    state = OFIState(symbol="S")
    state.update(0.0, 100.0, 100.1, 500, 500)
    state.update(1.0, 100.0, 100.1, 600, 500)

    assert state.normalised(1.0) is None

    for index in range(2, 40):
        state.update(float(index), 100.0, 100.1, 500 + index, 500)

    assert state.normalised(39.0) is not None
    assert state.snapshot(39.0)["depth_scale"] > 0


def test_reset_clears_the_session():
    """NSE has no overnight book: OFI carried across a close would describe
    two different auctions."""
    state = OFIState(symbol="S")
    state.update(0.0, 100.0, 100.1, 500, 500)
    state.update(1.0, 100.0, 100.1, 900, 500)
    assert state.cumulative == 400

    state.reset()

    assert (state.cumulative, state.events, state.last) == (0.0, 0, None)


def test_replay_rebuilds_the_same_series_as_the_live_path():
    rows = [(0.0, 100.0, 100.1, 500, 500), (1.0, 100.0, 100.1, 700, 500),
            (2.0, 100.1, 100.2, 300, 400)]
    live = OFIState(symbol="S")
    for stamp, bid, ask, bid_qty, ask_qty in rows:
        live.update(stamp, bid, ask, bid_qty, ask_qty)

    assert replay(rows).cumulative == live.cumulative


def test_tracker_keeps_symbols_apart():
    tracker = OFITracker()

    class Tick:
        def __init__(self, symbol, bid_qty):
            self.symbol, self.bid, self.ask = symbol, 100.0, 100.1
            self.bid_qty, self.ask_qty, self.timestamp = bid_qty, 500, None

    for symbol in ("A", "B"):
        tracker.on_tick(Tick(symbol, 500))
    tracker.on_tick(Tick("A", 800))

    assert tracker.states["A"].cumulative == 300
    assert tracker.states["B"].cumulative == 0


# ---------------------------------------------------------------------------
# Three-vote classification
# ---------------------------------------------------------------------------

def test_quote_vote_needs_a_sane_spread():
    assert quote_vote(100.1, 100.0, 100.1) == 1
    assert quote_vote(100.0, 100.0, 100.1) == -1
    assert quote_vote(100.05, 100.0, 100.1) == 0     # inside the spread
    assert quote_vote(100.1, None, 100.1) == 0
    assert quote_vote(100.1, 100.2, 100.1) == 0      # crossed


def test_pending_vote_reads_which_side_of_the_book_lost_its_size():
    # 1000 traded; the sell side of the book lost exactly that.
    assert pending_vote(1000, buy_pending_change=0, sell_pending_change=-1000) == 1
    assert pending_vote(1000, buy_pending_change=-1000, sell_pending_change=0) == -1
    # Both sides shed size: cancellations, not a clean lift.
    assert pending_vote(1000, buy_pending_change=-900, sell_pending_change=-900) == 0
    assert pending_vote(0, -1000, -1000) == 0
    assert pending_vote(1000, None, None) == 0


def test_tick_vote_carries_the_prior_side_through_an_unchanged_price():
    assert tick_vote(100.1, 100.0) == 1
    assert tick_vote(100.0, 100.1) == -1
    assert tick_vote(100.0, 100.0, last_side=-1) == -1
    assert tick_vote(100.0, None) == 0


def test_agreeing_votes_score_highest_and_a_lone_tick_scores_lowest():
    assert combine_votes(1, 1, 1).confidence == BOTH_AGREE_CONFIDENCE
    assert combine_votes(1, 0, 1).confidence == QUOTE_ONLY_CONFIDENCE
    assert combine_votes(0, -1, 1).confidence == PENDING_ONLY_CONFIDENCE
    assert combine_votes(0, 0, -1).confidence == TICK_ONLY_CONFIDENCE
    assert combine_votes(0, 0, 0).side == 0
    assert combine_votes(0, 0, 0).confidence == 0.0


def test_contradicting_votes_keep_the_quote_but_lose_confidence():
    """The quote rule reads the price actually paid, so it wins -- but a
    contested read is worth less than an uncontested weak one."""
    result = combine_votes(1, -1, 1)

    assert result.side == 1
    assert result.method == "conflict"
    assert result.confidence == CONFLICT_CONFIDENCE
    assert result.confidence < PENDING_ONLY_CONFIDENCE


def test_a_batched_update_is_discounted():
    single = combine_votes(1, 1, 1, single=True)
    batched = combine_votes(1, 1, 1, single=False)

    assert batched.side == single.side
    assert batched.confidence == pytest.approx(
        single.confidence * BATCHED_CONFIDENCE_FACTOR)


def test_classify_update_detects_a_single_trade_from_the_last_quantity():
    single = classify_update(
        price=100.1, traded=200, last_qty=200, prior_bid=100.0, prior_ask=100.1,
        prior_price=100.0, buy_pending_change=0, sell_pending_change=-200)

    assert single.single is True
    assert single.side == 1
    assert single.confidence == BOTH_AGREE_CONFIDENCE

    # Five hundred traded but the exchange's last print was 200: merged update.
    batched = classify_update(
        price=100.1, traded=500, last_qty=200, prior_bid=100.0, prior_ask=100.1,
        prior_price=100.0, buy_pending_change=0, sell_pending_change=-500)

    assert batched.single is False
    assert batched.confidence < single.confidence


def test_classification_uses_the_book_that_preceded_the_trade():
    """The trades hit the previous update's quotes. Classifying against the
    book left behind is how a lift at the ask reads as a sale."""
    lifted = classify_update(
        price=100.1, traded=100, last_qty=100,
        prior_bid=100.0, prior_ask=100.1, prior_price=100.0)

    assert lifted.side == 1

    # The same print measured against the book AFTER it moved up reads as a hit
    # on the bid -- the error this signature is designed to prevent.
    misread = classify_update(
        price=100.1, traded=100, last_qty=100,
        prior_bid=100.1, prior_ask=100.2, prior_price=100.0)

    assert misread.side == -1


# ---------------------------------------------------------------------------
# Regimes
# ---------------------------------------------------------------------------

def test_regimes_are_ordered_and_have_unique_ids():
    starts = [regime.start for regime in REGIMES]
    assert starts == sorted(starts)
    assert len({regime.regime_id for regime in REGIMES}) == len(REGIMES)


def test_a_date_lands_in_the_rule_set_in_force():
    assert regime_id_for("2026-09-01") == "2026-08-session"
    assert regime_id_for("2026-08-02") == "2026-07-freeze"
    assert regime_id_for("2026-06-30") == "2026-01-lots"
    assert regime_id_for("2025-08-31") == "2025-04-tick"
    assert regime_for(date(2026, 9, 1)).session_end == "15:40"


def test_the_lot_and_freeze_rules_follow_the_regime():
    current = regime_for("2026-09-01")
    assert (current.lot_size("NIFTY"), current.lot_size("BANKNIFTY")) == (65, 30)
    # 1,800 units at 65 a lot is 27 lots -- the largest single NIFTY order,
    # which is the size a whale's slices cluster on.
    assert current.freeze_lots("NIFTY") == 27
    assert current.freeze_lots("BANKNIFTY") == 20
    assert current.futures_tick("NIFTY") == 0.10
    assert current.futures_tick("BANKNIFTY") == 0.20

    older = regime_for("2025-01-01")
    assert older.lot_size("NIFTY") == 75
    assert older.futures_tick("NIFTY") == 0.05


def test_a_window_that_crosses_a_rule_change_is_flagged():
    assert spans_a_break("2026-07-15", "2026-08-15") == ["2026-08-03"]
    assert spans_a_break("2026-08-04", "2026-08-15") == []


def test_the_live_desk_accumulates_ofi_from_the_ticks_it_already_receives():
    """OFITracker existed for weeks and was constructed nowhere in the running
    engine, so the Auction page could only draw book pressure for sessions
    already five days cold. MPEngine.on_tick now feeds it."""
    with tempfile.TemporaryDirectory() as folder:
        desk = _desk(folder)
        symbol = "NSE:NIFTY26SEP24000CE"
        desk.footprints.watch(symbol)            # someone has the chart open
        asyncio.run(desk.on_tick(_live_tick(symbol, 100.0, 10, 570, 99.9, 100.1, 500, 500)))
        asyncio.run(desk.on_tick(_live_tick(symbol, 100.0, 20, 571, 99.9, 100.1, 700, 500)))

        # Size added at an unchanged bid is buying pressure of exactly that size.
        assert desk.ofi.states[symbol].cumulative == 200
        view = desk.ofi_view(symbol, 60)
        assert view["cumulative"] == 200
        assert view["series"][-1]["cum"] == 200


def test_ofi_is_kept_only_for_contracts_someone_is_reading():
    """An OFIState is ~1 MB at saturation against 208 KB for an order-flow
    state, and with MP_SYMBOLS_CSV unset the desk's universe is the whole ~670
    -name feed including the analysis-only option ladder -- ~600 MB of book
    pressure for curves nothing draws. Scope is the footprint's detail set
    (bounded at MAX_DETAIL_SYMBOLS) plus the directional scope."""
    with tempfile.TemporaryDirectory() as folder:
        desk = _desk(folder)
        watched, ignored = "NSE:NIFTY26SEP24000CE", "NSE:NIFTY26SEP26000PE"
        desk.footprints.watch(watched)
        for symbol in (watched, ignored):
            asyncio.run(desk.on_tick(_live_tick(symbol, 100.0, 10, 570, 99.9, 100.1, 500, 500)))
            asyncio.run(desk.on_tick(_live_tick(symbol, 100.0, 20, 571, 99.9, 100.1, 700, 500)))

        assert set(desk.ofi.states) == {watched}
        # Both still get order flow: it is the OFI book state that is scoped.
        assert set(desk.flow.states) == {watched, ignored}


def test_the_directional_scope_also_earns_a_book_state():
    with tempfile.TemporaryDirectory() as folder:
        desk = _desk(folder)
        symbol = "NSE:NIFTY50-INDEX"
        desk.set_directional_scope([symbol])
        asyncio.run(desk.on_tick(_live_tick(symbol, 100.0, 10, 570, 99.9, 100.1, 500, 500)))
        asyncio.run(desk.on_tick(_live_tick(symbol, 100.0, 20, 571, 99.9, 100.1, 700, 500)))

        assert desk.ofi.states[symbol].cumulative == 200


def test_a_session_roll_clears_the_books_cumulative_ofi():
    """NSE has no overnight book, so yesterday's accumulation is not a starting
    level for today; carrying it would bias every reading of the new session."""
    with tempfile.TemporaryDirectory() as folder:
        desk = _desk(folder)
        symbol = "NSE:NIFTY26SEP24000CE"
        desk.footprints.watch(symbol)
        asyncio.run(desk.on_tick(_live_tick(symbol, 100.0, 10, 570, 99.9, 100.1, 500, 500)))
        asyncio.run(desk.on_tick(_live_tick(symbol, 100.0, 20, 571, 99.9, 100.1, 700, 500)))
        assert desk.ofi.states[symbol].cumulative == 200

        desk.session_day = "1999-01-01"          # force the roll on the next tick
        asyncio.run(desk.on_tick(_live_tick(symbol, 100.0, 30, 572, 99.9, 100.1, 700, 500)))

        assert desk.ofi.states.get(symbol, OFIState(symbol=symbol)).cumulative == 0


def test_a_reset_drops_states_rather_than_zeroing_them_in_place():
    """Expired strikes and rolled futures series never trade again. Resetting
    in place kept an entry for every one of them for the life of the process --
    OrderFlowTracker.reset() clears, and this must match it."""
    tracker = OFITracker()
    tracker.state("NSE:NIFTY26SEP24000CE").update(1.0, 100.0, 100.1, 500, 500)

    tracker.reset()

    assert tracker.states == {}


def test_pruning_keeps_only_the_symbols_it_is_given():
    tracker = OFITracker()
    for symbol in ("A", "B", "C"):
        tracker.state(symbol).update(1.0, 100.0, 100.1, 500, 500)

    assert tracker.prune({"B"}) == 2
    assert set(tracker.states) == {"B"}


def test_the_ofi_series_groups_minutes_onto_the_bars_own_bucket_starts():
    """The line is drawn beside footprint columns, so its buckets must be the
    columns' own starts — joining the two by index would slide the whole curve
    across a session that has a bar with no book events in it."""
    state = OFIState(symbol="S")
    start = 1_788_241_500                        # 09:15 IST, a bucket boundary
    for index in range(5):
        state.update(start + index * 60, 100.0, 100.1, 500 + 100 * (index + 1), 500)

    # The first frame only establishes the book; four events follow it.
    minutes = state.series(60)
    assert [row["t"] for row in minutes] == [start + index * 60 for index in range(1, 5)]
    assert minutes[-1]["cum"] == state.cumulative

    five = state.series(300)
    assert len(five) == 1
    assert five[0]["t"] == start
    assert five[0]["ofi"] == sum(row["ofi"] for row in minutes)
    assert five[0]["cum"] == state.cumulative
    assert five[0]["events"] == sum(row["events"] for row in minutes)


def test_the_desk_hands_the_footprint_the_method_and_confidence_it_classified_with():
    """on_print was called with five arguments, so every live bar published
    methods=None and confidence=None: the zero-tick suppression gate could
    never fire on a bar the desk had actually captured, and no bar could be
    shaded weak however thin its evidence."""
    with tempfile.TemporaryDirectory() as folder:
        desk = _desk(folder)
        symbol = "NSE:NIFTY26SEP24000CE"
        desk.footprints.watch(symbol, tick_size=0.05)
        asyncio.run(desk.on_tick(_live_tick(symbol, 100.0, 1000, 570, 99.9, 100.1, 500, 500)))
        asyncio.run(desk.on_tick(_live_tick(symbol, 100.1, 1100, 571, 99.9, 100.1, 500, 500)))
        bar = desk.footprints.payload(symbol, 5)["bars"][-1]

        assert bar["methods"]["quote"] == 1
        assert bar["confidence"] is not None


def test_a_symbol_that_never_quoted_reports_no_ofi_at_all():
    """None, not a zeroed block: a contract whose book was never seen has not
    been measured as balanced, it has not been measured at all."""
    tracker = OFITracker()
    tracker.states["QUIET"] = OFIState(symbol="QUIET")

    assert tracker.snapshot("QUIET", 1_788_241_500, 60) is None


def test_days_group_into_their_regimes():
    grouped = group_by_regime(["2026-08-01", "2026-08-04", "2026-09-01"])

    assert grouped == {"2026-07-freeze": ["2026-08-01"],
                       "2026-08-session": ["2026-08-04", "2026-09-01"]}


def test_expiry_weekday_follows_the_regime_not_a_constant():
    # NIFTY weeklies moved to Tuesday on 1 Sep 2025.
    assert is_expiry_day("2026-09-01") is True        # Tuesday
    assert is_expiry_day("2026-09-03") is False       # Thursday
    assert is_expiry_day("2025-08-28") is True        # Thursday, older regime
    # BANKNIFTY has no weeklies: only the month's last Tuesday.
    assert is_expiry_day("2026-09-01", "BANKNIFTY") is False
    assert is_expiry_day("2026-09-29", "BANKNIFTY") is True
