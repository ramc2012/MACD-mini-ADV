from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .market_calendar import next_trading_day
from .models import ClosedPosition, Position, Trade

# engine.py owns the desk's IST helpers but imports this module, so the
# visibility rule keeps its own zone.
IST = ZoneInfo("Asia/Kolkata")
CLOSED_VISIBLE_UNTIL = time(8, 0)


def closed_visible_until(exit_time: datetime) -> datetime:
    """08:00 IST on the next TRADING day after the IST date of the exit.

    A Friday 15:20 close therefore stays on the desk through the weekend -- and
    through a Monday exchange holiday -- and disappears at 08:00 on the next
    session's morning. Counting weekdays instead rolled Friday's round trips
    off at 08:00 on 14 Sep 2026 (Ganesh Chaturthi), a day with no session,
    so they were gone before anyone read them on the Tuesday.

    08:00 rather than 06:00 because the day rolls off the desk when the
    session state rolls, and that roll fires on the first tick of the new
    day -- Fyers republishes the prior close well before the bell, so a
    07:0x roll was pruning the previous day's round trips before anyone had
    read them. Both lanes share this rule; the auction desk used to stamp
    the next CALENDAR day instead, which quietly dropped every Friday trade
    on Saturday morning while the MACD lane kept its own until Monday.
    """
    local = exit_time.astimezone(IST)
    day = next_trading_day(local.date())
    return datetime.combine(day, CLOSED_VISIBLE_UNTIL, tzinfo=IST)


def breakeven_exit_price(average_price: float, quantity: int,
                         slippage_bps: float = 0.0,
                         brokerage_per_leg: float = 0.0) -> float:
    """Market price at which closing the position nets exactly zero.

    ``average_price`` already carries the entry slippage — fills are slipped on
    the way in — and one leg of brokerage has been paid. Selling pays slippage
    again on the way out, plus the second leg.

    This exists because a trailing stop placed below this level converts a
    winning trade into a guaranteed loss the moment it triggers. With
    activation 0.20 and trail 0.20, the stop armed at 1.20x entry sat at
    1.20 * 0.80 = 0.96x entry, i.e. 4% underwater before costs.
    """
    qty = max(1, int(quantity))
    slip = min(max(0.0, slippage_bps) / 10_000.0, 0.99)
    gross = average_price * qty + 2.0 * max(0.0, brokerage_per_leg)
    return gross / (qty * (1.0 - slip))


class Portfolio:
    def __init__(self, initial_capital: float, lane: str = "macd"):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.realized_pnl = 0.0
        self.positions: dict[str, Position] = {}
        self.lane = lane

    def apply_trade(self, trade: Trade) -> ClosedPosition | None:
        """Book a fill. Returns the closing slice's record, if it closed any.

        The record is returned rather than kept here because the engine
        replays every historical trade through this method on restart and
        would otherwise re-create months of records; the ExecutionManager
        owns the list and loads it from the repository.
        """
        signed_quantity = trade.quantity if trade.side == "BUY" else -trade.quantity
        position = self.positions.get(trade.symbol)
        if position is None:
            position = Position(
                trade.symbol, signed_quantity, trade.price, trade.price,
                trade.timestamp, trade.price, lot_size=trade.lot_size,
            )
            position.entry_fees = trade.fees
            position.track_excursion(trade.price)
            self.positions[trade.symbol] = position
            self.cash -= signed_quantity * trade.price + trade.fees
            self.realized_pnl -= trade.fees
            return None

        old_quantity = position.quantity
        if position.lot_size != trade.lot_size:
            raise ValueError(f"Lot size changed for open position {trade.symbol}")
        new_quantity = old_quantity + signed_quantity
        closes = old_quantity != 0 and (old_quantity > 0) != (signed_quantity > 0)
        closing_quantity = min(abs(old_quantity), abs(signed_quantity)) if closes else 0
        closed: ClosedPosition | None = None
        exit_fee_share = 0.0
        if closing_quantity:
            direction = 1 if old_quantity > 0 else -1
            gross = direction * (trade.price - position.average_price) * closing_quantity
            self.realized_pnl += gross
            # Fees are cash-basis in realized_pnl (charged in full at each
            # fill, below). The record carries the slice's own share so that
            # sum(records.realized_pnl) + open entry fees == realized_pnl.
            entry_fee_share = position.entry_fees * closing_quantity / abs(old_quantity)
            exit_fee_share = trade.fees * closing_quantity / abs(signed_quantity)
            # The exit print is part of the path.
            position.track_excursion(trade.price)
            remaining = abs(old_quantity) - closing_quantity
            closed = ClosedPosition(
                position_id=position.position_id,
                symbol=trade.symbol,
                lane=self.lane,
                side="LONG" if direction > 0 else "SHORT",
                quantity=closing_quantity,
                lots=closing_quantity // position.lot_size if position.lot_size > 0 else 0,
                lot_size=position.lot_size,
                entry_time=position.opened_at,
                entry_price=position.average_price,
                exit_time=trade.timestamp,
                exit_price=trade.price,
                gross_pnl=gross,
                fees=entry_fee_share + exit_fee_share,
                realized_pnl=gross - entry_fee_share - exit_fee_share,
                return_pct=(direction * (trade.price / position.average_price - 1.0) * 100.0
                            if position.average_price else 0.0),
                max_price=position.max_price,
                min_price=position.min_price,
                max_return_pct=position.max_return_pct,
                min_return_pct=position.min_return_pct,
                exit_reason=None,
                partial=remaining > 0,
                remaining_quantity=remaining,
                exit_trade_id=trade.trade_id,
                visible_until=closed_visible_until(trade.timestamp),
            )
            position.entry_fees -= entry_fee_share

        if new_quantity == 0:
            del self.positions[trade.symbol]
        elif (old_quantity > 0) == (new_quantity > 0) and abs(new_quantity) > abs(old_quantity):
            added = abs(signed_quantity)
            position.average_price = (
                position.average_price * abs(old_quantity) + trade.price * added
            ) / abs(new_quantity)
            position.quantity = new_quantity
            position.last_price = trade.price
            position.entry_fees += trade.fees
            position.track_excursion(trade.price)
        elif (old_quantity > 0) == (new_quantity > 0):
            # Partial close: average, opened_at and excursion carry on.
            position.quantity = new_quantity
            position.last_price = trade.price
        else:
            # Reversal: a new position begins at this fill.
            position.quantity = new_quantity
            position.average_price = trade.price
            position.last_price = trade.price
            position.opened_at = trade.timestamp
            position.entry_fees = trade.fees - exit_fee_share
            position.reset_excursion(trade.price)
        self.cash -= signed_quantity * trade.price + trade.fees
        self.realized_pnl -= trade.fees
        return closed

    def mark(self, symbol: str, price: float) -> bool:
        position = self.positions.get(symbol)
        if position is None or price <= 0:
            return False
        position.track_excursion(price)
        if position.last_price == price:
            return False
        position.last_price = price
        return True

    def snapshot(self) -> dict:
        unrealized = sum(position.unrealized_pnl for position in self.positions.values())
        market_value = sum(position.last_price * position.quantity for position in self.positions.values())
        return {
            "initial_capital": self.initial_capital,
            "cash": self.cash,
            "market_value": market_value,
            "equity": self.cash + market_value,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": unrealized,
            "positions": [position.payload() for position in self.positions.values()],
        }
