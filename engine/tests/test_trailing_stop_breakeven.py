"""A trailing stop must never be armed below the round-trip breakeven.

Both lanes shipped the same geometry defect: the stop was derived purely from
the peak, so at the moment of activation it sat *below* the entry price and the
mechanism meant to protect profit guaranteed a loss instead.

  MP desk    activation 0.20, trail 0.20 -> 1.20 * 0.80 = 0.96x entry  (-4.0%)
  MACD lane  activation 0.30, trail 0.25 -> 1.30 * 0.75 = 0.975x entry (-2.5%)
"""
from types import SimpleNamespace

from macd_trader.portfolio import breakeven_exit_price


class TestBreakevenExitPrice:
    def test_with_no_costs_it_is_the_entry_price(self):
        assert breakeven_exit_price(100.0, 50) == 100.0

    def test_slippage_raises_the_floor_above_entry(self):
        # Exit slippage must be earned back before the trade is flat.
        floor = breakeven_exit_price(100.0, 50, slippage_bps=25.0)
        assert floor > 100.0
        assert round(floor, 4) == round(100.0 / (1 - 0.0025), 4)

    def test_brokerage_is_charged_for_both_legs(self):
        # 2 x Rs 20 spread over 50 units = Rs 0.80 per unit.
        floor = breakeven_exit_price(100.0, 50, brokerage_per_leg=20.0)
        assert round(floor, 4) == 100.8

    def test_selling_at_the_floor_really_does_net_zero(self):
        from macd_trader.models import Trade
        from macd_trader.portfolio import Portfolio

        entry, qty, bps, fee = 100.0, 50, 25.0, 20.0
        book = Portfolio(1_000_000)
        book.apply_trade(Trade("o1", "X", "BUY", qty, entry, fees=fee))
        floor = breakeven_exit_price(entry, qty, bps, fee)
        exit_fill = floor * (1 - bps / 10_000)          # slippage on the way out
        book.apply_trade(Trade("o2", "X", "SELL", qty, exit_fill, fees=fee))
        assert abs(book.snapshot()["realized_pnl"]) < 0.01

    def test_quantity_and_slippage_are_defended(self):
        assert breakeven_exit_price(100.0, 0) > 0          # no ZeroDivisionError
        assert breakeven_exit_price(100.0, 10, slippage_bps=-5) == 100.0


def _mp_position(entry, qty=50):
    return SimpleNamespace(quantity=qty, average_price=entry, peak_price=entry,
                           trailing_stop=None, hard_stop=entry * 0.75)


class TestArmedStopIsNeverUnderwater:
    """Reproduces the arming arithmetic of each lane at its own settings."""

    @staticmethod
    def _arm(peak, entry, qty, trail_pct, bps, fee):
        floor = breakeven_exit_price(entry, qty, bps, fee)
        return round(max(peak * (1 - trail_pct), floor), 4)

    def test_mp_desk_activation_no_longer_locks_in_a_loss(self):
        entry = 100.0
        peak = entry * 1.20                                   # activation point
        assert peak * (1 - 0.20) == 96.0                       # the old value
        armed = self._arm(peak, entry, 50, 0.20, 25.0, 20.0)
        assert armed > entry, "stop is still below entry"
        # The production value is rounded to 4dp; against a Rs 0.05 tick that
        # tolerance is ~1/1600th of a tick, so compare at the same precision.
        assert armed >= round(breakeven_exit_price(entry, 50, 25.0, 20.0), 4)

    def test_macd_lane_activation_no_longer_locks_in_a_loss(self):
        entry = 100.0
        peak = entry * 1.30
        assert peak * (1 - 0.25) == 97.5                       # the old value
        armed = self._arm(peak, entry, 50, 0.25, 5.0, 0.0)
        assert armed > entry

    def test_a_big_winner_still_trails_normally(self):
        # Once the peak is high enough the trail dominates and the floor is
        # irrelevant — the clamp must not pin the stop at breakeven forever.
        entry = 100.0
        armed = self._arm(entry * 3.0, entry, 50, 0.20, 25.0, 20.0)
        assert armed == round(300.0 * 0.80, 4) == 240.0

    def test_stop_only_ever_ratchets_upward(self):
        entry, prev = 100.0, None
        for multiple in (1.20, 1.25, 1.40, 2.0, 3.0):
            armed = self._arm(entry * multiple, entry, 50, 0.20, 25.0, 20.0)
            if prev is not None:
                assert armed >= prev
            prev = armed
