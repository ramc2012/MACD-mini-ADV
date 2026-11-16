"""The MCX lane: the MACD lane's rules on MCX commodity options.

What these protect: the MCX clock (09:00 to 23:30/23:55, evening-only days),
contract selection from the MCX instrument master (exact roots, the series
roll, the underlying futures, the MACD lane's ATM and liquidity rule), lot
units for valuation, the same zero-cross entry and stop/trail exits on the
lane's own paper book, expiry flattening, the REST fallback, and that MCX
prints never reach the NSE lanes.
"""
import asyncio
import json
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from macd_trader import execution, mcx_lane, models, rollover
from macd_trader.config import Settings
from macd_trader.events import EventHub
from macd_trader.mcx_lane import (
    ALREADY_HELD, AUTO_TRADE_OFF, NOT_SELECTED, SESSION_CLOSED, TAKEN,
    McxCalendar, McxContract, McxLane, aggregate_minutes, mcx_bucket, parse_mcx_master, plan_selection,
)
from macd_trader.models import Candle, Signal, Tick

IST = ZoneInfo("Asia/Kolkata")


class Clock(datetime):
    moment = datetime(2026, 11, 18, 11, 0, tzinfo=IST)

    @classmethod
    def now(cls, tz=None):
        moment = cls.moment
        return moment.astimezone(tz) if tz else moment.astimezone(IST).replace(tzinfo=None)


@pytest.fixture(autouse=True)
def mcx_clock(monkeypatch):
    """A Wednesday in the winter schedule (US standard time, close 23:55), 11:00 IST."""
    Clock.moment = datetime(2026, 11, 18, 11, 0, tzinfo=IST)
    for module in (mcx_lane, execution, models, rollover):
        monkeypatch.setattr(module, "datetime", Clock)
    monkeypatch.setattr(sys.modules[__name__], "datetime", Clock)
    return Clock


def at(hour: int, minute: int = 0, second: int = 0, day: date = date(2026, 11, 18)) -> datetime:
    return datetime.combine(day, time(hour, minute, second), IST)


# ------------------------------------------------------------------ master

def master_row(token: str, ticker: str, expiry: date, underlying: str, *, lot: int = 1,
               strike: float = -1, side: str = "XX", underlying_token: str = "") -> list[str]:
    row = [""] * 21
    row[0] = token
    row[1] = ticker
    row[3] = str(lot)
    row[8] = str(int(datetime.combine(expiry, time(23, 30), IST).timestamp()))
    row[9] = ticker
    row[10] = "11"
    row[13] = underlying
    row[15] = str(strike)
    row[16] = side
    row[17] = underlying_token
    return row


NOV_OPT = date(2026, 11, 18)     # expires today: must be rolled past
DEC_OPT = date(2026, 12, 16)
NOV_FUT = date(2026, 11, 19)
DEC_FUT = date(2026, 12, 18)


def crude_rows(strikes=range(5600, 6101, 50), *, lot: int = 1, underlying: str = "CRUDEOIL") -> list[list[str]]:
    rows = [
        master_row("F-NOV", "MCX:CRUDEOIL26NOVFUT", NOV_FUT, underlying, lot=lot),
        master_row("F-DEC", "MCX:CRUDEOIL26DECFUT", DEC_FUT, underlying, lot=lot),
    ]
    for strike in strikes:
        for side in ("CE", "PE"):
            rows.append(master_row(f"N{strike}{side}", f"MCX:CRUDEOIL26NOV{strike}{side}", NOV_OPT, underlying,
                                   lot=lot, strike=strike, side=side, underlying_token="F-NOV"))
            rows.append(master_row(f"D{strike}{side}", f"MCX:CRUDEOIL26DEC{strike}{side}", DEC_OPT, underlying,
                                   lot=lot, strike=strike, side=side, underlying_token="F-DEC"))
    return rows


def gold_rows() -> list[list[str]]:
    rows = [master_row("G-FEB", "MCX:GOLD27FEBFUT", date(2027, 2, 5), "GOLD"),
            master_row("GM-DEC", "MCX:GOLDM26DECFUT", date(2026, 12, 4), "GOLDM")]
    for strike in range(128000, 132001, 1000):
        for side in ("CE", "PE"):
            # Gold options on the Feb futures, monthly; no underlying token, so
            # the nearest futures on or after the option's expiry is used.
            rows.append(master_row(f"G{strike}{side}", f"MCX:GOLD26DEC{strike}{side}", date(2026, 11, 25), "GOLD",
                                   strike=strike, side=side))
            rows.append(master_row(f"GM{strike}{side}", f"MCX:GOLDM26DEC{strike}{side}", date(2026, 11, 25), "GOLDM",
                                   strike=strike, side=side))
    return rows


class FakeBroker:
    name = "fyers"

    def __init__(self, rows=None, quotes=None, history=None):
        self.rows = rows or []
        self.quote_map: dict[str, Tick] = dict(quotes or {})
        self.history = history or {}
        self.quote_requests: list[list[str]] = []
        self.master_reads = 0

    async def instrument_rows(self, exchange):
        assert exchange == "MCX"
        self.master_reads += 1
        return self.rows

    async def quotes(self, symbols):
        self.quote_requests.append(list(symbols))
        return {symbol: self.quote_map[symbol] for symbol in symbols if symbol in self.quote_map}

    async def history_range(self, symbol, timeframe_seconds, start, end):
        assert timeframe_seconds == 60
        return self.history.get(symbol, [])

    async def place_order(self, _order):
        raise AssertionError("the MCX lane is paper-only")


def quote(symbol: str, ltp: float, *, volume: int = 0, oi: int = 0, moment: datetime | None = None) -> Tick:
    return Tick(symbol, ltp, volume, (moment or Clock.moment).astimezone(UTC), open_interest=oi)


def build(tmp_path: Path, broker=None, **overrides) -> McxLane:
    settings = Settings(**{
        "execution_mode": "paper",
        "slippage_bps": 0,
        "paper_cross_spread": False,
        "mcx_auto_trade": True,
        "mcx_roots_csv": "CRUDEOIL",
        "mcx_timeframe_seconds": 60,
        "mcx_target_notional": 100_000,
        "mcx_database_path": str(tmp_path / "mcx.sqlite3"),
        "mcx_contract_snapshot_path": str(tmp_path / "mcx_contracts.json"),
        "fast_period": 3, "slow_period": 6, "signal_period": 3,
        **overrides,
    })
    return McxLane(settings, broker or FakeBroker(), EventHub())


def crude_broker(**liquidity) -> FakeBroker:
    quotes = {"MCX:CRUDEOIL26DECFUT": quote("MCX:CRUDEOIL26DECFUT", 5823.0)}
    for symbol, (volume, oi) in liquidity.items():
        quotes[symbol] = quote(symbol, 100.0, volume=volume, oi=oi)
    return FakeBroker(crude_rows(), quotes)


# ----------------------------------------------------------------- calendar

class TestCalendar:
    def test_winter_close_is_2355_and_summer_close_is_2330(self):
        # MCX closes at 23:30 while the US is on daylight time and at 23:55 from early November to mid March.
        calendar = McxCalendar()
        assert calendar.closes_at(date(2026, 11, 18)) == time(23, 55)
        assert calendar.closes_at(date(2026, 7, 15)) == time(23, 30)
        assert calendar.in_session(at(23, 50)) and not calendar.in_session(at(23, 56))
        july = date(2026, 7, 15)
        assert calendar.in_session(at(23, 29, day=july)) and not calendar.in_session(at(23, 31, day=july))

    def test_opens_at_nine_not_nine_fifteen(self):
        calendar = McxCalendar()
        assert calendar.in_session(at(9, 0)) and not calendar.in_session(at(8, 59))
        assert calendar.phase(at(8, 59)) == "pre-open"
        assert calendar.phase(at(16, 0)) == "morning" and calendar.phase(at(18, 0)) == "evening"

    def test_evening_only_days_and_closures(self):
        calendar = McxCalendar(extra_closed_csv="2026-12-31")
        guru_nanak = date(2026, 11, 24)
        assert not calendar.in_session(at(10, 0, day=guru_nanak))
        assert calendar.in_session(at(17, 30, day=guru_nanak))
        assert not calendar.in_session(at(12, 0, day=date(2026, 12, 25)))
        assert not calendar.in_session(at(12, 0, day=date(2026, 12, 31)))
        assert not calendar.in_session(at(12, 0, day=date(2026, 11, 21)))  # Saturday
        assert calendar.next_open(at(23, 57)) == at(9, 0, day=date(2026, 11, 19))

    def test_bars_anchor_at_0900_and_evening_bars_share_the_grid(self):
        calendar = McxCalendar()
        assert mcx_bucket(int(at(9, 14).timestamp()), 1800, calendar) == int(at(9, 0).timestamp())
        assert mcx_bucket(int(at(17, 5).timestamp()), 1800, calendar) == int(at(17, 0).timestamp())
        assert mcx_bucket(int(at(23, 57).timestamp()), 1800, calendar) is None
        evening_only = date(2026, 11, 24)
        assert mcx_bucket(int(at(17, 44, day=evening_only).timestamp()), 1800, calendar) == int(
            at(17, 30, day=evening_only).timestamp())

    def test_minute_history_folds_onto_the_live_grid_and_drops_off_session_minutes(self):
        calendar = McxCalendar()
        minutes = [Candle("S", int(at(9, minute).timestamp()), 10 + minute, 11 + minute, 9, 10 + minute, 5, True)
                   for minute in range(0, 4)]
        minutes.append(Candle("S", int(at(8, 59).timestamp()), 1, 1, 1, 1, 5, True))
        bars = aggregate_minutes(minutes, 120, calendar)
        assert [bar.timestamp for bar in bars] == [int(at(9, 0).timestamp()), int(at(9, 2).timestamp())]
        assert (bars[0].open, bars[0].high, bars[0].close, bars[0].volume) == (10, 12, 11, 10)


# --------------------------------------------------------------- selection

class TestSelection:
    def test_master_parse_keeps_exact_roots_only(self):
        listed = parse_mcx_master(gold_rows() + crude_rows(), {"GOLD", "CRUDEOIL"})
        assert {row.root for row in listed} == {"GOLD", "CRUDEOIL"}
        assert not any("GOLDM" in row.symbol for row in listed)
        assert any(row.kind == "FUT" and row.symbol == "MCX:GOLD27FEBFUT" for row in listed)

    def test_root_is_read_from_the_ticker_when_the_underlying_column_is_a_futures_symbol(self):
        listed = parse_mcx_master(crude_rows(underlying="CRUDEOIL26DECFUT"), {"CRUDEOIL"})
        assert listed and {row.root for row in listed} == {"CRUDEOIL"}

    def test_series_expiring_today_is_rolled_and_its_own_futures_prices_it(self):
        listed = parse_mcx_master(crude_rows(), {"CRUDEOIL"})
        plans, errors = plan_selection(listed, ["CRUDEOIL"], 1, date(2026, 11, 18))
        assert not errors
        assert plans["CRUDEOIL"]["expiry"] == DEC_OPT.isoformat() and plans["CRUDEOIL"]["rolled"]
        assert plans["CRUDEOIL"]["futures"].symbol == "MCX:CRUDEOIL26DECFUT"

    def test_without_an_underlying_token_the_next_futures_on_or_after_expiry_is_used(self):
        listed = parse_mcx_master(gold_rows(), {"GOLD"})
        plans, _ = plan_selection(listed, ["GOLD"], 1, date(2026, 11, 18))
        assert plans["GOLD"]["futures"].symbol == "MCX:GOLD27FEBFUT"

    def test_missing_root_is_reported_not_raised(self):
        listed = parse_mcx_master(crude_rows(), {"CRUDEOIL", "COPPER"})
        plans, errors = plan_selection(listed, ["CRUDEOIL", "COPPER"], 1, date(2026, 11, 18))
        assert set(plans) == {"CRUDEOIL"} and "COPPER" in errors

    def test_prepare_selects_side_rounded_atm_with_the_liquidity_tie_break(self, tmp_path):
        broker = crude_broker(**{
            "MCX:CRUDEOIL26DEC5850CE": (1000, 0),
            "MCX:CRUDEOIL26DEC5800PE": (1000, 0),
            "MCX:CRUDEOIL26DEC5750PE": (3000, 0),   # >= 1.5x the anchor: preferred, as on the MACD lane
        })
        lane = build(tmp_path, broker)
        symbols = asyncio.run(lane.prepare())
        legs = {row.option_type: row for row in lane.contracts.values()}
        assert legs["CE"].symbol == "MCX:CRUDEOIL26DEC5850CE"
        assert legs["PE"].symbol == "MCX:CRUDEOIL26DEC5750PE"
        assert legs["CE"].lot_size == 100 and legs["CE"].exchange_lot == 1
        assert set(symbols) == {"MCX:CRUDEOIL26DEC5850CE", "MCX:CRUDEOIL26DEC5750PE", "MCX:CRUDEOIL26DECFUT"}
        assert lane.execution.lot_sizes == {"MCX:CRUDEOIL26DEC5850CE": 100, "MCX:CRUDEOIL26DEC5750PE": 100}
        assert lane.known_expiries()["MCX:CRUDEOIL26DEC5850CE"] == DEC_OPT.isoformat()

    def test_no_liquidity_falls_back_to_the_side_rounded_anchor(self, tmp_path):
        lane = build(tmp_path, crude_broker())
        asyncio.run(lane.prepare())
        assert {row.strike for row in lane.contracts.values()} == {5850.0, 5800.0}

    def test_same_day_restart_reuses_the_morning_selection(self, tmp_path):
        asyncio.run(build(tmp_path, crude_broker()).prepare())
        broker = crude_broker()
        broker.quote_map["MCX:CRUDEOIL26DECFUT"] = quote("MCX:CRUDEOIL26DECFUT", 6050.0)
        lane = build(tmp_path, broker)
        asyncio.run(lane.prepare())
        assert broker.master_reads == 0
        assert {row.strike for row in lane.contracts.values()} == {5850.0, 5800.0}
        assert lane.selection["source"] == "saved"

    def test_a_master_lot_that_is_neither_one_nor_the_units_is_flagged(self, tmp_path):
        broker = crude_broker()
        broker.rows = crude_rows(lot=10)
        lane = build(tmp_path, broker)
        asyncio.run(lane.prepare())
        assert "CRUDEOIL" in lane.multiplier_warnings
        assert all(row.lot_size == 100 for row in lane.contracts.values())

    def test_selection_failure_never_raises_and_keeps_only_holdings(self, tmp_path):
        class Broken(FakeBroker):
            async def instrument_rows(self, exchange):
                raise RuntimeError("master down")

        lane = build(tmp_path, Broken())
        assert asyncio.run(lane.prepare()) == []
        assert "master down" in lane.error


# ---------------------------------------------------------- entries, exits

def ready_lane(tmp_path, **overrides) -> tuple[McxLane, McxContract]:
    lane = build(tmp_path, crude_broker(), **overrides)
    asyncio.run(lane.prepare())
    asyncio.run(lane.warm())
    call = next(row for row in lane.contracts.values() if row.option_type == "CE")
    return lane, call


def feed(lane: McxLane, symbol: str, price: float, moment: datetime, *, source: str = "ws") -> None:
    Clock.moment = moment
    asyncio.run(lane.on_tick(Tick(symbol, price, 10, moment.astimezone(UTC)), moment.astimezone(UTC), source=source))


class TestTrading:
    def test_premium_zero_cross_enters_with_lot_units(self, tmp_path):
        lane, call = ready_lane(tmp_path)
        start = at(11, 0)
        path = [100, 96, 92, 88, 85, 83, 82, 82.5, 84, 88, 94, 101]
        for minute, price in enumerate(path):
            feed(lane, call.symbol, price, start + timedelta(minutes=minute, seconds=5))
            feed(lane, call.symbol, price, start + timedelta(minutes=minute, seconds=50))
        taken = [row for row in lane.decisions if row["verdict"] == TAKEN]
        assert len(taken) == 1
        position = lane.portfolio.positions[call.symbol]
        assert position.lot_size == 100
        # Whole lots nearest 1 lakh at the entry premium (premium x 100 units per lot), capped at 10.
        assert position.lots == min(10, round(100_000 / (position.average_price * 100)))
        signals = lane.repository.rows("signals", 10)
        assert signals and signals[0]["kind"].startswith("PREMIUM_MACD_ZERO_CROSS_UP")

    def test_auto_trade_off_records_the_signal_and_buys_nothing(self, tmp_path):
        lane, call = ready_lane(tmp_path, mcx_auto_trade=False)
        signal = Signal(call.symbol, "BUY", "PREMIUM_MACD_ZERO_CROSS_UP", 100.0, 0.5, 0.1, 0.4,
                        evaluated_candle_timestamp=int(Clock.moment.timestamp()))
        asyncio.run(lane._on_signal(signal))
        assert lane.decisions[0]["verdict"] == AUTO_TRADE_OFF
        assert not lane.portfolio.positions
        assert lane.repository.rows("signals", 5)

    def test_signal_after_the_close_is_not_traded(self, tmp_path):
        lane, call = ready_lane(tmp_path)
        Clock.moment = at(23, 56)
        signal = Signal(call.symbol, "BUY", "PREMIUM_MACD_ZERO_CROSS_UP", 100.0, 0.5, 0.1, 0.4,
                        evaluated_candle_timestamp=int(at(23, 54).timestamp()))
        asyncio.run(lane._on_signal(signal))
        assert lane.decisions[0]["verdict"] == SESSION_CLOSED

    def test_inherited_hard_stop_exits_and_a_held_contract_is_not_bought_again(self, tmp_path):
        lane, call = ready_lane(tmp_path)
        feed(lane, call.symbol, 100.0, at(11, 0, 5))
        signal = Signal(call.symbol, "BUY", "PREMIUM_MACD_ZERO_CROSS_UP", 100.0, 0.5, 0.1, 0.4,
                        evaluated_candle_timestamp=int(at(11, 0).timestamp()))
        asyncio.run(lane._on_signal(signal))
        assert lane.decisions[0]["verdict"] == TAKEN
        again = Signal(call.symbol, "BUY", "PREMIUM_MACD_ZERO_CROSS_UP", 100.0, 0.5, 0.1, 0.4,
                       evaluated_candle_timestamp=int(at(11, 1).timestamp()))
        Clock.moment = at(11, 1, 10)
        asyncio.run(lane._on_signal(again))
        assert lane.decisions[0]["verdict"] == ALREADY_HELD
        feed(lane, call.symbol, 69.0, at(11, 2, 0))   # MACD lane hard stop: -30%
        assert call.symbol not in lane.portfolio.positions
        assert lane.execution.exit_reasons[call.symbol] == "HARD_STOP_30_PCT"

    def test_off_session_print_marks_but_never_stops_out(self, tmp_path):
        lane, call = ready_lane(tmp_path)
        feed(lane, call.symbol, 100.0, at(11, 0, 5))
        asyncio.run(lane._on_signal(Signal(call.symbol, "BUY", "K", 100.0, 0.5, 0.1, 0.4,
                                           evaluated_candle_timestamp=int(at(11, 0).timestamp()))))
        assert call.symbol in lane.portfolio.positions
        feed(lane, call.symbol, 50.0, at(23, 57))
        position = lane.portfolio.positions[call.symbol]
        assert position.last_price == 50.0

    def test_contracts_outside_todays_selection_are_never_entered(self, tmp_path):
        lane, call = ready_lane(tmp_path)
        call.retained = True
        asyncio.run(lane._on_signal(Signal(call.symbol, "BUY", "K", 100.0, 0.5, 0.1, 0.4,
                                           evaluated_candle_timestamp=int(Clock.moment.timestamp()))))
        assert lane.decisions[0]["verdict"] == NOT_SELECTED

    def test_holding_expiring_today_is_flattened_from_2300(self, tmp_path):
        lane, call = ready_lane(tmp_path)
        feed(lane, call.symbol, 100.0, at(11, 0, 5))
        asyncio.run(lane._on_signal(Signal(call.symbol, "BUY", "K", 100.0, 0.5, 0.1, 0.4,
                                           evaluated_candle_timestamp=int(at(11, 0).timestamp()))))
        call.expiry = "2026-11-18"
        feed(lane, call.symbol, 101.0, at(22, 59, 50))
        assert call.symbol in lane.portfolio.positions
        feed(lane, call.symbol, 101.0, at(23, 0, 5))
        assert call.symbol not in lane.portfolio.positions
        assert lane.execution.exit_reasons[call.symbol] == "MCX_EXPIRY_FLATTEN_2300_IST"

    def test_maintenance_flattens_expiring_holdings_without_a_new_tick(self, tmp_path):
        lane, call = ready_lane(tmp_path)
        feed(lane, call.symbol, 100.0, at(11, 0, 5))
        asyncio.run(lane._on_signal(Signal(call.symbol, "BUY", "K", 100.0, 0.5, 0.1, 0.4,
                                           evaluated_candle_timestamp=int(at(11, 0).timestamp()))))
        call.expiry = "2026-11-18"
        feed(lane, call.symbol, 102.0, at(22, 59, 58))
        Clock.moment = at(23, 0, 4)
        asyncio.run(lane.maintain(Clock.moment.astimezone(UTC)))
        assert call.symbol not in lane.portfolio.positions

    def test_the_closing_bar_is_evaluated_at_the_mcx_close(self, tmp_path):
        lane, call = ready_lane(tmp_path)
        feed(lane, call.symbol, 100.0, at(23, 54, 10))
        assert not lane.history[call.symbol]
        Clock.moment = at(23, 56)
        asyncio.run(lane.maintain(Clock.moment.astimezone(UTC)))
        assert [bar.timestamp for bar in lane.history[call.symbol]] == [int(at(23, 54).timestamp())]
        asyncio.run(lane.maintain(Clock.moment.astimezone(UTC)))   # once a day
        assert len(lane.history[call.symbol]) == 1

    def test_a_new_bar_length_resets_and_rewarms(self, tmp_path):
        lane, call = ready_lane(tmp_path)
        needs = lane.apply_settings(lane.settings.model_copy(update={"mcx_timeframe_seconds": 1800}))
        assert needs == {"reselect": False, "rewarm": True}
        assert lane.timeframe_seconds == 1800 and call.symbol in lane._warming
        needs = lane.apply_settings(lane.settings.model_copy(update={"mcx_roots_csv": "CRUDEOIL,GOLD"}))
        assert needs["reselect"]

    def test_lane_never_reaches_a_live_order_api(self, tmp_path):
        lane = build(tmp_path, execution_mode="live", allow_live_orders=True)
        assert lane.execution.settings.execution_mode == "paper"
        assert not lane.execution.settings.allow_live_orders

    def test_manual_order_is_scoped_to_mcx(self, tmp_path):
        lane, _call = ready_lane(tmp_path)
        with pytest.raises(ValueError):
            asyncio.run(lane.manual_order("NSE:SBIN-EQ", "BUY", 1))


# ------------------------------------------------------------- restarts

class TestRestart:
    def test_holding_survives_a_restart_with_its_contract_record(self, tmp_path):
        lane, call = ready_lane(tmp_path)
        feed(lane, call.symbol, 100.0, at(11, 0, 5))
        asyncio.run(lane._on_signal(Signal(call.symbol, "BUY", "K", 100.0, 0.5, 0.1, 0.4,
                                           evaluated_candle_timestamp=int(at(11, 0).timestamp()))))
        lane.repository.close()
        again = build(tmp_path, crude_broker())
        assert call.symbol in again.portfolio.positions
        assert again.contracts[call.symbol].expiry == DEC_OPT.isoformat()
        assert again.execution.lot_sizes[call.symbol] == 100

    def test_expired_while_offline_settles_at_the_last_mark(self, tmp_path):
        lane, call = ready_lane(tmp_path)
        feed(lane, call.symbol, 100.0, at(11, 0, 5))
        asyncio.run(lane._on_signal(Signal(call.symbol, "BUY", "K", 100.0, 0.5, 0.1, 0.4,
                                           evaluated_candle_timestamp=int(at(11, 0).timestamp()))))
        lane.portfolio.mark(call.symbol, 120.0)
        lane.repository.save_position_excursions(lane.portfolio.positions.values())
        lane._save_snapshot()
        lane.repository.close()
        Clock.moment = datetime(2026, 12, 17, 8, 50, tzinfo=IST)
        later = build(tmp_path, FakeBroker())
        assert call.symbol in later.portfolio.positions
        asyncio.run(later.prepare())
        assert call.symbol not in later.portfolio.positions
        closed = later.repository.closed_positions(lane="mcx")
        assert closed and closed[0]["exit_reason"] == "EXPIRED_WHILE_OFFLINE_LAST_MARK"


# --------------------------------------------------------- warm-up, REST

class TestData:
    def test_warmup_replays_history_and_excludes_the_forming_bar(self, tmp_path):
        broker = crude_broker()
        lane = build(tmp_path, broker)
        asyncio.run(lane.prepare())
        call = next(row for row in lane.contracts.values() if row.option_type == "CE")
        Clock.moment = at(11, 30, 20)
        broker.history[call.symbol] = [
            Candle(call.symbol, int((at(10, 0) + timedelta(minutes=m)).timestamp()), 90 + m, 91 + m, 89 + m, 90 + m, 3, True)
            for m in range(0, 91)   # through 11:30, the forming minute
        ]
        report = asyncio.run(lane.warm())
        assert report["loaded"] == 1   # only the call has history here
        bars = lane.history[call.symbol]
        assert bars[-1].timestamp == int(at(11, 29).timestamp())
        assert call.symbol not in lane._warming

    def test_live_bars_closed_during_warmup_extend_history_without_trading(self, tmp_path):
        lane = build(tmp_path, crude_broker())
        asyncio.run(lane.prepare())
        call = next(row for row in lane.contracts.values() if row.option_type == "CE")
        feed(lane, call.symbol, 100.0, at(11, 0, 5))
        feed(lane, call.symbol, 101.0, at(11, 1, 5))   # closes the 11:00 bar while warming
        assert lane._warm_buffer[call.symbol]
        asyncio.run(lane.warm())
        assert [bar.timestamp for bar in lane.history[call.symbol]] == [int(at(11, 0).timestamp())]
        assert not lane.decisions

    def test_rest_fallback_polls_silent_contracts_and_respects_quote_age(self, tmp_path):
        lane, call = ready_lane(tmp_path)   # subscribed at 11:00:00
        broker = lane.broker
        # The socket gets the fallback window (60 s) to speak before any REST call.
        Clock.moment = at(11, 0, 30)
        assert asyncio.run(lane.poll_stale_quotes(Clock.moment.astimezone(UTC))) == 0
        assert broker.quote_requests[-1] != [call.symbol] and lane.rest_polls == 0
        broker.quote_map[call.symbol] = quote(call.symbol, 100.0, moment=at(11, 1, 25))
        Clock.moment = at(11, 1, 30)
        fed = asyncio.run(lane.poll_stale_quotes(Clock.moment.astimezone(UTC)))
        assert fed >= 1 and lane.rest_ticks >= 1
        assert lane.execution.last_prices[call.symbol] == 100.0
        # An old last trade is a mark, not an executable price.
        broker.quote_map[call.symbol] = quote(call.symbol, 99.0, moment=at(10, 50))
        lane.latest.pop(call.symbol)
        Clock.moment = at(11, 2, 0)
        asyncio.run(lane.poll_stale_quotes(Clock.moment.astimezone(UTC)))
        with pytest.raises(RuntimeError):
            lane.execution._fresh_price(call.symbol)

    def test_feed_expected_only_after_a_websocket_tick_today(self, tmp_path):
        lane, call = ready_lane(tmp_path)
        assert not lane.feed_expected(at(18, 0).astimezone(UTC))
        feed(lane, call.symbol, 100.0, at(18, 0, 1))
        assert lane.feed_expected(at(18, 0, 30).astimezone(UTC))
        assert not lane.feed_expected(at(23, 57).astimezone(UTC))

    def test_snapshot_is_json_ready(self, tmp_path):
        lane, call = ready_lane(tmp_path)
        feed(lane, call.symbol, 100.0, at(11, 0, 5))
        payload = lane.snapshot()
        json.dumps(payload)
        assert payload["health"]["execution"] == "paper only"
        assert payload["risk"]["inherited_from"] == "MACD lane"
        assert payload["contracts"][0]["root"] == "CRUDEOIL"


# ---------------------------------------------------------------- engine

class TestEngineRouting:
    def _engine(self, tmp_path):
        from macd_trader.engine import TradingEngine

        settings = Settings(
            feed_mode="simulation", symbols_csv="NSE:SBIN-EQ", tick_capture_enabled=False,
            database_path=str(tmp_path / "macd.sqlite3"), mp_database_path=str(tmp_path / "mp.sqlite3"),
            blast_database_path=str(tmp_path / "blast.sqlite3"), tick_database_path=str(tmp_path / "ticks.sqlite3"),
            contract_snapshot_path=str(tmp_path / "contracts.json"),
            mcx_database_path=str(tmp_path / "mcx.sqlite3"),
            mcx_contract_snapshot_path=str(tmp_path / "mcx_contracts.json"),
        )
        return TradingEngine(settings)

    def test_mcx_prints_reach_only_the_mcx_lane(self, tmp_path):
        engine = self._engine(tmp_path)
        seen = []

        async def capture(tick, received_at=None, **_):
            seen.append(tick.symbol)

        engine.mcx.on_tick = capture
        import datetime as real

        symbol = "MCX:CRUDEOIL26DEC5850CE"
        # The engine compares the print with its own wall clock, which this test does not pin.
        tick = Tick(symbol, 100.0, 10, real.datetime.now(real.UTC))
        asyncio.run(engine.on_tick(tick))
        assert seen == [symbol]
        assert symbol not in engine.latest_ticks
        assert symbol not in engine.candles.current

    def test_mcx_contracts_are_not_dropped_by_the_nse_expiry_rule(self):
        from macd_trader.engine import _drop_expired

        # parse_option would infer an NSE-style expiry for this ticker.
        kept = _drop_expired(["MCX:CRUDEOIL26NOV5850CE", "NSE:SBIN26OCT800CE"], {}, date(2026, 11, 18))
        assert kept == ["MCX:CRUDEOIL26NOV5850CE"]

    def test_health_reports_the_lane(self, tmp_path):
        engine = self._engine(tmp_path)
        health = engine.mcx.health()
        assert health["roots"] == ["GOLD", "SILVER", "CRUDEOIL", "COPPER", "NATURALGAS"]
        assert health["execution"] == "paper only"


def test_evening_feed_time_is_not_clamped_to_the_nse_close():
    """Fyers republishes a feed time without a trade time; only past the instrument's own close is it clamped."""
    from macd_trader.brokers import FyersBroker

    evening = int(at(20, 15).timestamp())
    mcx = FyersBroker.exchange_timestamp({"symbol": "MCX:CRUDEOIL26DEC5850CE", "exch_feed_time": evening})
    assert mcx == at(20, 15).astimezone(UTC)
    late = FyersBroker.exchange_timestamp({"symbol": "MCX:CRUDEOIL26DEC5850CE", "exch_feed_time": int(at(23, 58).timestamp())})
    assert late == at(23, 55).astimezone(UTC)
    nse = FyersBroker.exchange_timestamp({"symbol": "NSE:SBIN26NOV800CE", "exch_feed_time": evening})
    assert nse.astimezone(IST).time() < time(16, 0)


def test_switching_history_downloads_on_tells_the_broker_and_restarts_the_warmup(tmp_path):
    """Under the engine-wide hold the warm-up is refused; turning the lane's switch on must run it again from empty."""
    lane, call = ready_lane(tmp_path)
    assert lane.broker.mcx_history_allowed is False
    assert not lane._warming                                    # the first warm-up has finished
    needs = lane.apply_settings(lane.settings.model_copy(update={"mcx_history_downloads": True}))
    assert lane.broker.mcx_history_allowed is True
    assert needs["rewarm"] and not needs["reselect"]
    assert call.symbol in lane._warming
    assert lane.settings_view()["history_downloads"] is True
    needs = lane.apply_settings(lane.settings.model_copy(update={"mcx_history_downloads": False}))
    assert lane.broker.mcx_history_allowed is False and not needs["rewarm"]
