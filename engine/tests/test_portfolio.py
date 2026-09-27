from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from pytest import approx

from macd_trader.models import Trade
from macd_trader.portfolio import Portfolio, closed_visible_until

IST = ZoneInfo("Asia/Kolkata")
SYMBOL = "NSE:TEST"


def test_round_trip_books_realized_pnl():
    portfolio = Portfolio(100_000)
    portfolio.apply_trade(Trade("o1", "NSE:TEST", "BUY", 10, 100))
    portfolio.apply_trade(Trade("o2", "NSE:TEST", "SELL", 10, 110))
    snapshot = portfolio.snapshot()
    assert snapshot["realized_pnl"] == 100
    assert snapshot["equity"] == 100_100
    assert snapshot["positions"] == []


def test_trade_fees_reduce_cash_equity_and_realized_pnl():
    portfolio = Portfolio(100_000)
    portfolio.apply_trade(Trade("o1", "NSE:TEST", "BUY", 10, 100, fees=20))
    portfolio.apply_trade(Trade("o2", "NSE:TEST", "SELL", 10, 110, fees=20))
    snapshot = portfolio.snapshot()
    assert snapshot["realized_pnl"] == 60
    assert snapshot["equity"] == 100_060


def test_mark_tracks_mfe_and_mae():
    portfolio = Portfolio(100_000)
    portfolio.apply_trade(Trade("o1", SYMBOL, "BUY", 10, 100))
    for price in (120, 90, 110):
        assert portfolio.mark(SYMBOL, price) is True
    position = portfolio.positions[SYMBOL]
    assert position.max_price == 120
    assert position.min_price == 90
    assert position.max_return_pct == approx(20)
    assert position.min_return_pct == approx(-10)
    assert portfolio.mark(SYMBOL, 110) is False
    assert portfolio.mark(SYMBOL, 0) is False
    assert (position.max_price, position.min_price) == (120, 90)
    assert position.return_pct == approx(10)
    assert position.payload()["position_id"] == f"{SYMBOL}|{position.opened_at.isoformat()}"


def test_full_close_returns_closed_record():
    portfolio = Portfolio(100_000)
    portfolio.apply_trade(Trade("o1", SYMBOL, "BUY", 10, 100))
    opened_at = portfolio.positions[SYMBOL].opened_at
    portfolio.mark(SYMBOL, 130)
    exit_trade = Trade("o2", SYMBOL, "SELL", 10, 110)
    closed = portfolio.apply_trade(exit_trade)

    assert closed is not None
    assert closed.position_id == f"{SYMBOL}|{opened_at.isoformat()}"
    assert closed.side == "LONG"
    assert closed.lane == "macd"
    assert (closed.quantity, closed.entry_price, closed.exit_price) == (10, 100, 110)
    assert (closed.gross_pnl, closed.fees, closed.realized_pnl) == (100, 0, 100)
    assert closed.return_pct == approx(10)
    assert closed.max_return_pct == approx(30)
    assert closed.min_return_pct == 0
    assert (closed.max_price, closed.min_price) == (130, 100)
    assert closed.partial is False
    assert closed.remaining_quantity == 0
    assert closed.exit_trade_id == exit_trade.trade_id
    assert closed.exit_reason is None
    assert portfolio.positions == {}
    assert portfolio.realized_pnl == 100


def test_partial_close_keeps_average_and_excursion():
    portfolio = Portfolio(100_000)
    assert portfolio.apply_trade(Trade("o1", SYMBOL, "BUY", 20, 100)) is None
    portfolio.mark(SYMBOL, 150)
    first = portfolio.apply_trade(Trade("o2", SYMBOL, "SELL", 10, 140))
    assert first.quantity == 10
    assert first.partial is True
    assert first.remaining_quantity == 10
    assert first.max_return_pct == 50
    remaining = portfolio.positions[SYMBOL]
    assert remaining.average_price == 100
    assert remaining.max_price == 150
    assert remaining.quantity == 10
    second = portfolio.apply_trade(Trade("o3", SYMBOL, "SELL", 10, 120))
    assert second.partial is False
    assert second.remaining_quantity == 0
    assert second.position_id == first.position_id
    assert SYMBOL not in portfolio.positions


def test_fees_are_attributed_pro_rata_and_bridge_to_realized():
    portfolio = Portfolio(100_000)
    portfolio.apply_trade(Trade("o1", SYMBOL, "BUY", 20, 100, fees=20))
    closed = portfolio.apply_trade(Trade("o2", SYMBOL, "SELL", 10, 110, fees=20))
    assert closed.fees == 30
    assert closed.realized_pnl == 70
    position = portfolio.positions[SYMBOL]
    assert position.entry_fees == 10
    # Cash-basis book equals the record net of the entry fees still parked in
    # the open half, the same bridge MP statistics() documents.
    assert portfolio.realized_pnl == 60
    assert portfolio.realized_pnl == closed.realized_pnl - position.entry_fees


def test_pyramid_does_not_rewrite_excursion():
    portfolio = Portfolio(100_000)
    portfolio.apply_trade(Trade("o1", SYMBOL, "BUY", 10, 100))
    portfolio.mark(SYMBOL, 120)
    portfolio.apply_trade(Trade("o2", SYMBOL, "BUY", 10, 120))
    position = portfolio.positions[SYMBOL]
    assert position.average_price == 110
    assert position.max_return_pct == approx(20)
    assert position.max_price == 120
    portfolio.mark(SYMBOL, 121)
    assert position.max_return_pct == approx(20)
    assert position.max_price == 121


def test_reversal_resets_excursion_and_opened_at():
    portfolio = Portfolio(100_000)
    portfolio.apply_trade(Trade("o1", SYMBOL, "BUY", 10, 100))
    first_open = portfolio.positions[SYMBOL].opened_at
    portfolio.mark(SYMBOL, 120)
    reversal = Trade("o2", SYMBOL, "SELL", 20, 90)
    closed = portfolio.apply_trade(reversal)
    assert closed.quantity == 10
    assert closed.max_return_pct == approx(20)
    assert closed.partial is False
    short = portfolio.positions[SYMBOL]
    assert short.quantity == -10
    assert short.opened_at == reversal.timestamp != first_open
    assert short.max_price == short.min_price == 90
    assert (short.max_return_pct, short.min_return_pct) == (0, 0)
    portfolio.mark(SYMBOL, 81)
    assert short.max_return_pct == approx(10)
    assert short.max_price == 90


def test_closed_visible_until_is_0800_ist_next_weekday():
    # 08:00, not 06:00: the session roll fires on the first tick of the new
    # day and Fyers republishes the prior close well before the bell, so a
    # 06:00 boundary pruned the previous day's round trips at ~07:0x.
    from macd_trader.portfolio import CLOSED_VISIBLE_UNTIL
    assert CLOSED_VISIBLE_UNTIL.hour == 8
    tuesday_close = datetime(2026, 9, 1, 9, 50, tzinfo=UTC)
    assert closed_visible_until(tuesday_close) == datetime(2026, 9, 2, 8, 0, tzinfo=IST)
    friday_close = datetime(2026, 9, 4, 9, 50, tzinfo=UTC)
    assert closed_visible_until(friday_close) == datetime(2026, 9, 7, 8, 0, tzinfo=IST)
    saturday = datetime(2026, 9, 5, 4, 0, tzinfo=UTC)
    assert closed_visible_until(saturday) == datetime(2026, 9, 7, 8, 0, tzinfo=IST)
    # A close after 18:30 UTC is already the next IST date.
    late_utc = datetime(2026, 9, 1, 19, 0, tzinfo=UTC)
    assert closed_visible_until(late_utc) == datetime(2026, 9, 3, 8, 0, tzinfo=IST)
