"""The lazy ratio backfill must ask for prior-day history, not for any rows.

LiveCandleWriter stores a bar a minute for every subscribed contract, so an
analysis-only ITM/OTM leg selected this morning already has rows the first time
the ratio chart is opened. The old "any rows at all" guard read that as
"already downloaded" and skipped the 90-day fetch forever; because
build_ratio_history intersects the legs' timestamps, one such leg pinned the
whole premium ratio to today. Measured on the live desk on 4 Sep 2026:
NSE:NIFTY2690823950CE and NSE:NIFTY2690824000CE each held 176 minute bars,
every one of them dated that day.
"""
import asyncio
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from macd_trader.candle_store import prior_session_candle_count, store_historical_candles
from macd_trader.contracts import OptionContract
from macd_trader.engine import (
    RATIO_BACKFILL_RETRY_SECONDS,
    TradingEngine,
    session_open_epoch,
)
from macd_trader.models import Candle


IST = ZoneInfo("Asia/Kolkata")


def _today_open() -> datetime:
    return datetime.now(IST).replace(hour=9, minute=15, second=0, microsecond=0)


def _bars(symbol: str, start: datetime, count: int, price: float = 100.0) -> list[Candle]:
    return [
        Candle(symbol, int((start + timedelta(minutes=index)).timestamp()),
               price, price, price, price, 10, True)
        for index in range(count)
    ]


def _ladder() -> list[OptionContract]:
    ladder = []
    for side in ("CE", "PE"):
        for role, strike in (("ITM", 90.0), ("ATM", 100.0), ("OTM", 110.0)):
            ladder.append(OptionContract(
                underlying="TEST", spot_symbol="NSE:TEST-EQ", option_type=side,
                symbol=f"NSE:TEST26SEP{side}{role}", strike=strike, expiry="2026-09-29",
                selection_price=100.0, moneyness=role, analysis_only=role != "ATM",
            ))
    return ladder


class _Broker:
    """Records every history request so a retry loop cannot hide."""

    def __init__(self, rows_for: dict[str, list[Candle]] | None = None, error: str | None = None):
        self.rows_for = rows_for or {}
        self.error = error
        self.requested: list[str] = []

    async def history_range(self, symbol, timeframe_seconds, start, end):
        self.requested.append(symbol)
        if self.error:
            raise RuntimeError(self.error)
        return self.rows_for.get(symbol, [])


def _engine(database: str, contracts: list[OptionContract], broker: _Broker):
    obj = SimpleNamespace(
        broker=broker,
        history={},
        _ratio_locks={},
        _ratio_errors={},
        _ratio_backfill_retry={},
        settings=SimpleNamespace(
            research_database_path=database,
            timeframe_seconds=60,
            fast_period=3, slow_period=6, signal_period=2,
            bb_period=5, bb_deviations=2.0, kama_period=3,
            kama_fast=2, kama_slow=5, kama_rsi_period=3, kama_roc_period=2,
        ),
    )
    obj.ratio_contracts = lambda _spot: contracts
    obj.ratio_history = TradingEngine.ratio_history.__get__(obj)
    return obj


def _seed(database: str, contracts: list[OptionContract], start: datetime, count: int) -> None:
    for contract in contracts:
        store_historical_candles(
            database,
            _bars(contract.symbol, start, count, 120.0 if contract.moneyness == "ITM" else 40.0),
        )


class TestPriorSessionCount:
    def test_today_only_rows_do_not_count_as_history(self, tmp_path):
        database = str(tmp_path / "history.sqlite3")
        store_historical_candles(database, _bars("NSE:TEST26SEP24000CE", _today_open(), 176))

        assert prior_session_candle_count(
            database, "NSE:TEST26SEP24000CE", session_open_epoch()) == 0

    def test_an_earlier_session_counts(self, tmp_path):
        database = str(tmp_path / "history.sqlite3")
        store_historical_candles(
            database, _bars("NSE:TEST26SEP24000CE", _today_open() - timedelta(days=1), 20))

        assert prior_session_candle_count(
            database, "NSE:TEST26SEP24000CE", session_open_epoch()) == 20

    def test_missing_table_reads_as_no_coverage(self, tmp_path):
        assert prior_session_candle_count(str(tmp_path / "empty.sqlite3"), "NSE:X", 0) == 0


class TestLazyBackfill:
    def test_a_leg_holding_only_todays_rows_is_downloaded(self, tmp_path):
        database = str(tmp_path / "history.sqlite3")
        contracts = _ladder()
        _seed(database, contracts, _today_open(), 176)
        prior_open = _today_open() - timedelta(days=3)
        broker = _Broker({
            contract.symbol: _bars(contract.symbol, prior_open, 30,
                                   120.0 if contract.moneyness == "ITM" else 40.0)
            for contract in contracts
        })
        engine = _engine(database, contracts, broker)

        payload = asyncio.run(engine.ratio_history("NSE:TEST-EQ", 300))

        assert sorted(broker.requested) == sorted(row.symbol for row in contracts)
        assert payload["download_errors"] == {}
        assert all(row["points"] for row in payload["ratios"])
        # The download landed before this session, which is the whole point.
        assert all(
            prior_session_candle_count(database, row.symbol, session_open_epoch()) == 30
            for row in contracts
        )

    def test_a_leg_with_prior_day_rows_is_not_redownloaded(self, tmp_path):
        database = str(tmp_path / "history.sqlite3")
        contracts = _ladder()
        _seed(database, contracts, _today_open() - timedelta(days=3), 30)
        broker = _Broker()
        engine = _engine(database, contracts, broker)

        payload = asyncio.run(engine.ratio_history("NSE:TEST-EQ", 300))

        assert broker.requested == []
        assert payload["download_errors"] == {}

    def test_a_strike_listed_today_is_not_re_requested_on_the_next_poll(self, tmp_path):
        """Fyers rate-limits per endpoint, and this backfill runs off a browser
        poll — an unremembered failure would re-request on every refresh."""
        database = str(tmp_path / "history.sqlite3")
        contracts = _ladder()
        _seed(database, contracts, _today_open(), 176)
        broker = _Broker()  # every leg comes back empty
        engine = _engine(database, contracts, broker)

        asyncio.run(engine.ratio_history("NSE:TEST-EQ", 300))
        first_pass = list(broker.requested)
        payload = asyncio.run(engine.ratio_history("NSE:TEST-EQ", 300))

        assert sorted(first_pass) == sorted(row.symbol for row in contracts)
        assert broker.requested == first_pass
        assert set(payload["download_errors"]) == {row.symbol for row in contracts}

    def test_a_download_that_only_reaches_today_is_not_repeated_every_poll(self, tmp_path):
        """The common shape of "listed today" is not an empty range.

        Fyers answers the 90-day request for a strike listed this morning with
        that morning's bars: non-empty, so the empty-range branch never fires,
        and the store succeeds — but every stored row is still on or after
        09:15, so coverage is unchanged. Treating that as success re-downloads
        six legs into a rate-limited endpoint and rewrites the live research
        database once a minute for as long as the Ratio page is open.
        """
        database = str(tmp_path / "history.sqlite3")
        contracts = _ladder()
        _seed(database, contracts, _today_open(), 176)
        broker = _Broker({
            contract.symbol: _bars(contract.symbol, _today_open(), 176,
                                   120.0 if contract.moneyness == "ITM" else 40.0)
            for contract in contracts
        })
        engine = _engine(database, contracts, broker)

        asyncio.run(engine.ratio_history("NSE:TEST-EQ", 300))
        first_pass = list(broker.requested)
        payload = asyncio.run(engine.ratio_history("NSE:TEST-EQ", 300))
        asyncio.run(engine.ratio_history("NSE:TEST-EQ", 300))

        assert sorted(first_pass) == sorted(row.symbol for row in contracts)
        assert broker.requested == first_pass
        # The operator is told why the chart is still pinned to today.
        assert set(payload["download_errors"]) == {row.symbol for row in contracts}
        assert all(
            engine._ratio_backfill_retry[row.symbol] - time.monotonic()
            > RATIO_BACKFILL_RETRY_SECONDS
            for row in contracts
        )

    def test_a_transient_failure_stands_down_for_less_time_than_an_empty_range(self, tmp_path):
        database = str(tmp_path / "history.sqlite3")
        contracts = _ladder()
        _seed(database, contracts, _today_open(), 176)
        limited = _engine(database, contracts, _Broker(error="429 Too Many Requests"))
        empty = _engine(database, contracts, _Broker())

        asyncio.run(limited.ratio_history("NSE:TEST-EQ", 300))
        asyncio.run(empty.ratio_history("NSE:TEST-EQ", 300))

        symbol = contracts[0].symbol
        assert "429" in limited._ratio_errors[symbol]
        transient = limited._ratio_backfill_retry[symbol] - time.monotonic()
        listed_today = empty._ratio_backfill_retry[symbol] - time.monotonic()
        assert 0 < transient <= RATIO_BACKFILL_RETRY_SECONDS < listed_today
