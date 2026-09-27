"""Order-flow classification is an ESTIMATE. These tests pin the difference
between the irreducible part of that estimate and three defects that made it
worse than the method warrants.

NSE publishes price, quantity, time and the best bid/ask — never the aggressor.
So a footprint can never tie out to exchange volume, and no test here pretends
otherwise. What the desk CAN do is (a) classify with the full Lee-Ready tick
test rather than a truncated one, (b) feed that test real trade prices rather
than its own echo, and (c) report the residual instead of deleting it.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from macd_trader.orderflow import OrderFlowTracker, classify


class _Tick:
    """Minimal stand-in for the broker tick the tracker consumes."""
    def __init__(self, symbol, ltp, volume, bid=None, ask=None, last_qty=None, minute=0):
        self.symbol, self.ltp, self.volume = symbol, ltp, volume
        self.bid, self.ask, self.last_qty = bid, ask, last_qty
        self.timestamp = datetime(2026, 8, 27, 9, 15) + timedelta(seconds=minute)


# ── the zero-tick rule ──────────────────────────────────────────────────────

def test_unchanged_price_carries_the_previous_aggressor():
    """Lee-Ready's tick test is three-branch: up, down, and UNCHANGED, where
    the prior classification carries. Returning 0 there discarded exactly the
    prints that cluster where price is not moving."""
    assert classify(100.0, None, None, 100.0, last_side=1) == (1, "zero_tick")
    assert classify(100.0, None, None, 100.0, last_side=-1) == (-1, "zero_tick")


def test_unchanged_price_with_no_prior_side_is_still_unknown():
    """The carry-forward needs something to carry. The first print of a session
    at an unchanged price genuinely has no evidence behind it."""
    assert classify(100.0, None, None, 100.0, last_side=0) == (0, "tick")


def test_the_quote_rule_still_wins_over_the_carry_forward():
    """A print at the ask is buyer-initiated on direct evidence, whatever the
    previous print did."""
    assert classify(101.0, 100.0, 101.0, 101.0, last_side=-1) == (1, "quote")
    assert classify(100.0, 100.0, 101.0, 100.0, last_side=1) == (-1, "quote")


def test_up_and_down_ticks_are_unaffected_by_the_carry_forward():
    assert classify(101.0, None, None, 100.0, last_side=-1) == (1, "tick")
    assert classify(99.0, None, None, 100.0, last_side=1) == (-1, "tick")


# ── the tick rule must not be fed its own echo ─────────────────────────────

def test_quote_only_ticks_do_not_advance_the_tick_rule_reference():
    """Fyers SymbolUpdate also fires on quote/OI changes. Those carry no new
    cumulative volume, so they are not prints — and must not move the price the
    NEXT real print is compared against."""
    tracker = OrderFlowTracker()
    s = "TEST-CE"
    tracker.on_tick(_Tick(s, 100.0, volume=1000, minute=0))          # baseline
    tracker.on_tick(_Tick(s, 105.0, volume=1100, minute=1))          # real print, +5 -> buy
    state = tracker.states[s]
    assert state.last_trade_price == 105.0

    # A quote-only update at a different LTP, no volume change.
    tracker.on_tick(_Tick(s, 107.0, volume=1100, minute=2))
    assert state.last_trade_price == 105.0, "a non-trade tick moved the trade reference"

    # The next real print must be judged against 105, not 107.
    tracker.on_tick(_Tick(s, 106.0, volume=1200, minute=3))
    assert state.last_side == 1, "106 vs the true previous trade 105 is an UPTICK"


# ── volume conservation ────────────────────────────────────────────────────

def test_buy_plus_sell_plus_unclassified_equals_total():
    """The identity the desk needs in order to state its own coverage. Before
    this, unclassified size was added to no counter and was unrecoverable."""
    tracker = OrderFlowTracker()
    s = "TEST-PE"
    # A NON-ZERO baseline: `if tick.volume:` treats 0 as absent, so a zero-volume
    # tick cannot establish the session baseline and the next tick would become
    # it instead — silently consuming the first real print.
    cum = 1000
    tracker.on_tick(_Tick(s, 50.0, volume=cum, minute=0))
    for i, (px, size) in enumerate([(50.0, 100), (51.0, 200), (51.0, 150), (49.0, 300)], start=1):
        cum += size
        tracker.on_tick(_Tick(s, px, volume=cum, minute=i))
    st = tracker.states[s]
    assert st.total_volume == 750
    assert st.buy_volume + st.sell_volume + st.unclassified_volume == st.total_volume


def test_an_entirely_unclassifiable_tape_reports_zero_coverage_not_zero_volume():
    """Every print at one price with no quotes and no prior side: nothing can be
    classified. The old code reported total silence; the tape still happened."""
    tracker = OrderFlowTracker()
    s = "FLAT-CE"
    tracker.on_tick(_Tick(s, 10.0, volume=500, minute=0))   # baseline
    for i in range(1, 5):
        tracker.on_tick(_Tick(s, 10.0, volume=500 + i * 100, minute=i))
    st = tracker.states[s]
    assert st.total_volume == 400
    assert st.buy_volume == 0 and st.sell_volume == 0
    assert st.unclassified_volume == 400, "the tape must survive even when the estimate cannot"


def test_classified_volume_never_exceeds_total():
    tracker = OrderFlowTracker()
    s = "X-CE"
    cum = 500
    tracker.on_tick(_Tick(s, 20.0, volume=cum, bid=19.9, ask=20.1, minute=0))
    for i, (px, size) in enumerate([(20.1, 50), (19.9, 70), (20.1, 40), (20.0, 60)], start=1):
        cum += size
        tracker.on_tick(_Tick(s, px, volume=cum, bid=19.9, ask=20.1, minute=i))
    st = tracker.states[s]
    assert st.buy_volume + st.sell_volume <= st.total_volume


def test_the_first_tick_is_a_baseline_and_contributes_no_volume():
    """A cumulative session total is not a single print. This is deliberate —
    the test exists so the behaviour is a documented choice, and so the
    resulting shortfall against exchange volume is understood rather than
    rediscovered as a bug."""
    tracker = OrderFlowTracker()
    tracker.on_tick(_Tick("B-CE", 30.0, volume=99_999, minute=0))
    st = tracker.states["B-CE"]
    assert st.total_volume == 0
    assert st.trades == 0


# ── one classifier, not two ────────────────────────────────────────────────

def test_the_condensed_history_path_uses_the_same_classifier_object():
    """tick_store carried its own copy whose docstring claimed to mirror this
    one and did not (no MID_TOLERANCE deadband), so replaying a session
    disagreed with the live footprint for reasons unrelated to the
    quote-vs-tick question a replay is meant to measure."""
    from macd_trader import tick_store
    assert tick_store.classify is classify


# ── the three-vote classifier, live ────────────────────────────────────────

def test_the_book_a_print_is_judged_against_is_the_one_it_traded_into():
    """Fyers batches: the tick that reports a trade already carries the book
    AFTER it. Classifying a lift at the ask against the quote it just moved
    reads the buy as a hit on the bid, so the prior book is remembered and the
    memory advances on EVERY update, quote-only ones included."""
    tracker = OrderFlowTracker()
    s = "BOOK-CE"
    tracker.on_tick(_Tick(s, 100.0, volume=1000, bid=99.9, ask=100.1, minute=0))
    state = tracker.states[s]
    assert (state.prior_bid, state.prior_ask) == (99.9, 100.1)

    # Quote-only: no volume change, so no print — but the book memory moves.
    assert tracker.on_tick(_Tick(s, 100.0, volume=1000, bid=100.0, ask=100.2, minute=1)) is None
    assert (state.prior_bid, state.prior_ask) == (100.0, 100.2)
    assert state.trades == 0

    # A lift at 100.2 reported alongside a book that has already stepped up.
    row = tracker.on_tick(_Tick(s, 100.2, volume=1100, bid=100.1, ask=100.3, minute=2))
    assert row.side == 1 and row.method == "quote"


def test_conservation_survives_the_switch_to_the_three_vote_classifier():
    """The identity that lets the desk state its own coverage does not depend on
    which rule assigned the side, so it must still hold now that three rules
    vote and a confidence is attached to the verdict."""
    tracker = OrderFlowTracker()
    s = "VOTE-PE"
    cum = 1000
    tracker.on_tick(_Tick(s, 50.0, volume=cum, bid=49.9, ask=50.1, minute=0))
    for i, (px, size) in enumerate([(50.1, 100), (50.0, 200), (50.0, 150), (49.9, 300)], start=1):
        cum += size
        tracker.on_tick(_Tick(s, px, volume=cum, bid=px - 0.1, ask=px + 0.1, minute=i))
    state = tracker.states[s]

    assert state.total_volume == 750
    assert state.buy_volume + state.sell_volume + state.unclassified_volume == state.total_volume


def test_the_vote_verdicts_fold_onto_the_method_counters_the_health_metric_reads():
    """classification_health and the "quote-rule coverage below 50%" readiness
    blocker index these counters by name. A verdict name that never appears as
    a key would KeyError the health metric or silently drop out of the share."""
    tracker = OrderFlowTracker()
    s = "KEYS-CE"
    cum = 1000
    tracker.on_tick(_Tick(s, 50.0, volume=cum, bid=49.9, ask=50.1, minute=0))
    for i, (px, size, bid, ask) in enumerate(
            [(50.1, 100, 49.9, 50.1), (50.0, 200, None, None),
             (50.0, 150, None, None), (49.9, 300, 49.9, 50.1)], start=1):
        cum += size
        tracker.on_tick(_Tick(s, px, volume=cum, bid=bid, ask=ask, minute=i))
    state = tracker.states[s]

    assert set(state.methods) <= {"quote", "mid", "tick", "zero_tick", "pending", "conflict"}
    assert sum(state.methods.values()) == state.trades
    assert state.methods["quote"] >= 1


def test_the_weighted_delta_never_outruns_the_raw_one():
    """Every confidence is a fraction, so weighting each print by it can only
    shrink the delta's magnitude. A weighted figure larger than the raw one
    would mean a print counted more than the size that traded."""
    tracker = OrderFlowTracker()
    s = "W-CE"
    cum = 1000
    tracker.on_tick(_Tick(s, 50.0, volume=cum, bid=49.9, ask=50.1, minute=0))
    for i, (px, size) in enumerate([(50.1, 100), (50.1, 200), (50.1, 150)], start=1):
        cum += size
        tracker.on_tick(_Tick(s, px, volume=cum, bid=49.9, ask=50.1, minute=i))
    snap = tracker.snapshot(s)

    assert snap["cumulative_delta"] == 450
    assert 0 < snap["weighted_cumulative_delta"] <= snap["cumulative_delta"]
    assert 0 < snap["mean_confidence"] <= 1.0


def test_a_bar_of_prints_nobody_scored_reports_no_mean_confidence():
    """A checkpoint written before the score existed restores prints with
    confidence None. Averaging those in as zero would report a tape that failed
    every vote, which is a claim about prints nobody voted on."""
    from macd_trader.orderflow import FlowState
    tracker = OrderFlowTracker()
    state = FlowState("OLD-PE")
    state.total_volume = 0.0
    tracker.states["OLD-PE"] = state

    assert tracker.snapshot("OLD-PE")["mean_confidence"] is None


# ── speed of tape ──────────────────────────────────────────────────────────

def test_speed_of_tape_counts_every_update_not_only_the_prints():
    """A book being re-quoted twenty times a second with nothing trading IS a
    fast tape — it is the shape that precedes the trade. Counting prints alone
    would call that contract quiet."""
    tracker = OrderFlowTracker()
    s = "SPEED-CE"
    for i in range(25):
        tracker.on_tick(_Tick(s, 100.0, volume=1000, bid=99.9, ask=100.1, minute=i * 0.4))
    speed = tracker.tape_speed(s)

    assert speed["window_seconds"] == 10.0
    assert speed["updates_per_s"] == 2.5
    assert speed["qty_per_s"] == 0.0, "quote-only updates carry no traded quantity"


def test_the_tape_speed_percentile_is_withheld_until_the_day_has_windows_to_rank_against():
    """30 updates a second is a dead index future and a frantic weekly option,
    so the absolute figure alone says nothing; the reading is its rank against
    THIS contract's own day. Ranking against five windows would say more about
    the sample than the tape."""
    tracker = OrderFlowTracker()
    s = "PCT-CE"
    tracker.on_tick(_Tick(s, 100.0, volume=1000, bid=99.9, ask=100.1, minute=0))
    assert tracker.tape_speed(s)["updates_pct"] is None

    # One update per closed ten-second window, then a burst inside the last one.
    for i in range(1, 14):
        tracker.on_tick(_Tick(s, 100.0, volume=1000, bid=99.9, ask=100.1, minute=i * 10))
    assert tracker.tape_speed(s)["samples"] >= 12
    for i in range(20):
        tracker.on_tick(_Tick(s, 100.0, volume=1000, bid=99.9, ask=100.1, minute=140 + i * 0.2))

    assert tracker.tape_speed(s)["updates_pct"] == 100.0


def test_coverage_is_none_when_it_was_never_measured():
    """A session restored from a payload written before the coverage fields
    existed has no measurement. Reporting 100% (by synthesising total from
    buy+sell) or 0% would both be claims about a tape nobody counted; None is
    the only honest answer, and the UI hides the stat on it."""
    from macd_trader.orderflow import OrderFlowTracker, FlowState
    tracker = OrderFlowTracker()
    state = FlowState("OLD-CE")
    state.buy_volume, state.sell_volume = 1000.0, 500.0
    state.total_volume = 0.0            # never measured
    state.trades = 40
    tracker.states["OLD-CE"] = state
    snap = tracker.snapshot("OLD-CE")
    assert snap["classified_share"] is None
    assert snap["buy_volume"] == 1000.0, "the sides that WERE measured still report"


# ── coverage and agreement are two readings, not one ───────────────────────

def test_the_mean_confidence_is_taken_over_the_prints_that_got_a_side():
    """combine_votes returns confidence 0.0 for the "unknown" verdict, so
    averaging over TOTAL volume folded every unsided print in at zero -- and
    published a second, worse statement of coverage under the name agreement.
    A tape half of which is sided by the uncontested quote rule (0.7, the
    strongest single-rule verdict) must not report 0.35."""
    from macd_trader.orderflow import FlowState, OrderFlowTracker

    tracker = OrderFlowTracker()
    state = FlowState("CONF-CE")
    state.trades = 2
    state.buy_volume, state.sell_volume = 500.0, 0.0
    state.total_volume, state.unclassified_volume = 1000.0, 500.0
    state.conf_volume = 0.7 * 500.0          # only the sided 500 scored
    tracker.states["CONF-CE"] = state

    snap = tracker.snapshot("CONF-CE")
    assert snap["mean_confidence"] == 0.7
    # Coverage is the other half of the reading, and it is unchanged.
    assert snap["classified_share"] == 0.5


def test_a_depthless_update_clears_the_prior_book_instead_of_holding_a_stale_one():
    """tick_store's offline reconstruction writes (bid, ask) unconditionally,
    so a depth-less update leaves it (None, None) and the next print falls to
    the tick rule. The live path used to keep the last quote it ever saw, so a
    run of depth-less updates got quote-rule verdicts (0.7, or 0.9 with pending
    agreement) against an arbitrarily stale book -- inflating quote_share,
    which feeds the confidence grade, the stacked-imbalance gate and the
    readiness blocker, and making the live and replayed classifications of one
    session disagree."""
    tracker = OrderFlowTracker()
    s = "STALE-CE"
    # A real book, a print against it, then depth stops arriving entirely.
    tracker.on_tick(_Tick(s, 100.0, volume=1000, bid=99.9, ask=100.1, minute=0))
    tracker.on_tick(_Tick(s, 100.1, volume=1100, bid=99.9, ask=100.1, minute=1))
    assert tracker.states[s].recent[-1].method == "quote"

    tracker.on_tick(_Tick(s, 100.1, volume=1100, minute=2))          # no depth
    assert (tracker.states[s].prior_bid, tracker.states[s].prior_ask) == (None, None)

    tracker.on_tick(_Tick(s, 100.3, volume=1200, minute=3))          # a print
    verdict = tracker.states[s].recent[-1]
    assert verdict.method == "tick", "judged against a quote two updates old"
    assert verdict.side == 1


def test_the_very_first_print_may_still_read_its_own_book():
    """The fallback exists for the print immediately after the volume baseline,
    which otherwise has no book at all and is permanently sideless. It applies
    once -- when nothing has been classified yet -- not on every later print
    that happens to arrive without depth."""
    tracker = OrderFlowTracker()
    s = "FIRST-CE"
    tracker.on_tick(_Tick(s, 100.0, volume=1000, minute=0))          # baseline
    tracker.on_tick(_Tick(s, 100.1, volume=1100, bid=100.0, ask=100.1, minute=1))

    assert tracker.states[s].recent[-1].method == "quote"
    assert tracker.states[s].recent[-1].side == 1
