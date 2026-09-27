"""Absorption's "failed to move" must mean the same thing on every instrument.

The gate was `span_pct <= 0.6` -- a percent of price. Measured over 179,534
minute bars on 2026-08-20 that passed 99.9% of EQUITY bars (always true, so
absorption collapsed to "one-sided flow" with no failed-to-move requirement)
and only 18.4% of OPTION bars, where one Rs 0.05 tick on a Rs 1 premium is
already a 5% move. The threshold is now expressed in ticks.
"""
from datetime import UTC, datetime

from macd_trader.orderflow import (
    ABSORPTION_MAX_SPAN_TICKS,
    ABSORPTION_MIN_PRESSURE,
    OrderFlowTracker,
)


def _load(tracker, symbol, prices, side=1, size=100):
    """Push classified prints directly so the gate is tested, not classify()."""
    from macd_trader.orderflow import FlowState, Print

    state = FlowState(symbol)
    now = datetime.now(UTC).timestamp()
    for i, price in enumerate(prices):
        state.recent.append(Print(now + i, price, size, side, "quote"))
    tracker.states[symbol] = state
    return state


class TestTickGate:
    def test_pinned_price_with_one_sided_flow_is_absorption(self):
        tracker = OrderFlowTracker()
        _load(tracker, "X", [100.0] * 30)
        result = tracker.absorption("X")
        assert result["detected"] is True
        assert result["span_ticks"] == 0.0

    def test_within_the_tick_budget_still_counts(self):
        tracker = OrderFlowTracker()          # 4 ticks = Rs 0.20
        _load(tracker, "X", [100.0, 100.05, 100.10, 100.15, 100.20] * 6)
        result = tracker.absorption("X")
        assert result["span_ticks"] == 4.0
        assert result["detected"] is True

    def test_one_tick_beyond_the_budget_is_not(self):
        tracker = OrderFlowTracker()
        _load(tracker, "X", [100.0, 100.05, 100.10, 100.15, 100.20, 100.25] * 5)
        result = tracker.absorption("X")
        assert result["span_ticks"] == 5.0
        assert result["detected"] is False

    def test_a_cheap_option_is_no_longer_judged_by_percent(self):
        # Rs 1.00 premium moving 3 ticks is a 15% move -- the old percent gate
        # rejected it outright. In ticks it is a quiet auction, as intended.
        tracker = OrderFlowTracker()
        _load(tracker, "OPT", [1.00, 1.05, 1.10, 1.15] * 8)
        result = tracker.absorption("OPT")
        assert result["range_pct"] > 0.6, "this is the case the old gate refused"
        assert result["detected"] is True

    def test_a_liquid_equity_no_longer_passes_trivially(self):
        # Rs 3400 stock ranging Rs 2.40 is 0.07% -- far under the old 0.6% gate,
        # so it was flagged as "failed to move" while moving 48 ticks.
        tracker = OrderFlowTracker()
        prices = [3400.0 + i * 0.05 for i in range(49)]
        _load(tracker, "EQ", prices)
        result = tracker.absorption("EQ")
        assert result["range_pct"] < 0.6, "the old gate would have passed this"
        assert result["span_ticks"] == 48.0
        assert result["detected"] is False

    def test_pressure_is_still_required(self):
        tracker = OrderFlowTracker()
        state = _load(tracker, "X", [100.0] * 30)
        for i, row in enumerate(list(state.recent)):   # alternate the aggressor
            state.recent[i] = row._replace(side=1 if i % 2 else -1) \
                if hasattr(row, "_replace") else row
        result = tracker.absorption("X")
        if abs(result["pressure"]) < ABSORPTION_MIN_PRESSURE:
            assert result["detected"] is False

    def test_side_is_reported_from_the_pressure_direction(self):
        tracker = OrderFlowTracker()
        _load(tracker, "SELLERS", [50.0] * 30, side=-1)
        assert tracker.absorption("SELLERS")["side"] == "sellers_absorbed"
        _load(tracker, "BUYERS", [50.0] * 30, side=1)
        assert tracker.absorption("BUYERS")["side"] == "buyers_absorbed"

    def test_threshold_is_a_named_constant(self):
        assert ABSORPTION_MAX_SPAN_TICKS > 0
        assert 0 < ABSORPTION_MIN_PRESSURE <= 1

    def test_too_few_prints_is_never_absorption(self):
        tracker = OrderFlowTracker()
        _load(tracker, "X", [100.0] * 5)
        assert tracker.absorption("X")["detected"] is False
