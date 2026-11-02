"""2 Nov 2026: the morning snapshot and pre-open quotes carry the previous session's trade time, and bucketed by
that stamp they rewrote Friday's last stored minute with Monday's price and the day's whole volume."""
import asyncio
from datetime import UTC, datetime, timedelta

from macd_trader.config import Settings
from macd_trader.engine import TradingEngine
from macd_trader.models import Tick

SYMBOL = "NSE:RELIANCE26NOV1160PE"


def test_a_print_stamped_on_an_earlier_day_builds_no_minute_bar(tmp_path):
    engine = TradingEngine(Settings(
        symbols_csv="NSE:RELIANCE-EQ", feed_mode="simulation", execution_mode="paper", tick_capture_enabled=False,
        database_path=str(tmp_path / "macd.sqlite3"), research_database_path=str(tmp_path / "historical.sqlite3"),
        mp_database_path=str(tmp_path / "mp.sqlite3"), blast_database_path=str(tmp_path / "blast.sqlite3"),
        tick_database_path=str(tmp_path / "ticks.sqlite3"), runtime_settings_path=str(tmp_path / "settings.json")))
    bucketed = []
    engine.minute_candles.on_tick = lambda tick: (bucketed.append(tick.timestamp), (None, None))[1]

    now = datetime.now(UTC)
    asyncio.run(engine.on_tick(Tick(SYMBOL, 7.5, volume=7_083_000, timestamp=now - timedelta(days=3))))
    assert bucketed == []
    assert engine.latest_ticks[SYMBOL].ltp == 7.5          # still the visible quote

    asyncio.run(engine.on_tick(Tick(SYMBOL, 7.0, volume=11_500, timestamp=now)))
    assert bucketed == [now]
