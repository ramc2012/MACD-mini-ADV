"""Regression tests for the six session-integrity fixes."""
from datetime import datetime, timedelta, timezone

from macd_trader.candles import IST, CandleAggregator, session_bucket
from macd_trader.chart_history import aggregate_session_candles
from macd_trader.models import Candle, Signal, Tick
from macd_trader.repository import TradeRepository


def _tick(symbol, ltp, hh, mm, ss=0, volume=100, day=None):
    base = day or datetime.now(IST).date()
    moment = datetime(base.year, base.month, base.day, hh, mm, ss, tzinfo=IST)
    return Tick(symbol, ltp, volume, timestamp=moment.astimezone(timezone.utc))


def test_live_buckets_match_the_stored_history_buckets():
    """The bug that made chart setups and generated signals disagree: live
    bucketed on epoch % timeframe (09:00/09:30) while history anchored to the
    09:15 open (09:15/09:45)."""
    aggregator = CandleAggregator(1800)
    day = datetime.now(IST).date()
    live_buckets = []
    for hh, mm in [(9, 20), (9, 50), (10, 20)]:
        current, _ = aggregator.on_tick(_tick("X", 10.0, hh, mm))
        assert current is not None
        live_buckets.append(current.timestamp)

    minutes = []
    for hh, mm in [(9, 20), (9, 50), (10, 20)]:
        moment = datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)
        minutes.append(Candle("X", int(moment.timestamp()), 10, 10, 10, 10, 100, True))
    history_buckets = sorted(c.timestamp for c in aggregate_session_candles(minutes, 1800))

    assert live_buckets == history_buckets
    labels = [datetime.fromtimestamp(b, IST).strftime("%H:%M") for b in live_buckets]
    assert labels == ["09:15", "09:45", "10:15"]


def test_pre_open_and_post_close_ticks_build_no_candle():
    """A pre-open re-broadcast of yesterday's close evaluated as a live bar —
    an INDIGO alert fired on a candle stamped 15:30 the previous day."""
    aggregator = CandleAggregator(1800)
    assert aggregator.on_tick(_tick("X", 10.0, 9, 0)) == (None, None)
    assert aggregator.on_tick(_tick("X", 10.0, 15, 45)) == (None, None)
    assert aggregator.rejected_off_session == 2
    current, _ = aggregator.on_tick(_tick("X", 10.0, 9, 16))
    assert current is not None


def test_out_of_order_ticks_never_rewrite_a_committed_bar():
    aggregator = CandleAggregator(1800)
    aggregator.on_tick(_tick("X", 10.0, 10, 20))
    current, closed = aggregator.on_tick(_tick("X", 99.0, 9, 20))   # late arrival
    assert closed is None
    assert aggregator.rejected_out_of_order == 1
    assert current is not None and current.high == 10.0             # untouched


def test_flush_closes_the_final_bar_of_the_session():
    """No tick from a later bucket exists after 15:30, so the 15:15 bar was
    built and never evaluated — MANAPPURAM's qualifying close was lost."""
    aggregator = CandleAggregator(1800)
    aggregator.on_tick(_tick("X", 10.0, 15, 20))
    aggregator.on_tick(_tick("X", 11.0, 15, 25))
    closed = aggregator.flush()
    assert len(closed) == 1
    assert closed[0].closed is True and closed[0].high == 11.0
    assert datetime.fromtimestamp(closed[0].timestamp, IST).strftime("%H:%M") == "15:15"
    assert aggregator.flush() == []          # idempotent


def test_signal_dedup_survives_a_reconnect(tmp_path):
    """The strategy's in-memory guard is rebuilt on every reconnect; the
    durable index is what actually enforces one signal per bar."""
    repository = TradeRepository(str(tmp_path / "book.sqlite3"))
    try:
        bar = 1_755_000_000
        first = Signal("NSE:X26AUG100CE", "BUY", "MACD_ZERO_CROSS", 10.0, 1, 0, 1,
                       evaluated_candle_timestamp=bar)
        again = Signal("NSE:X26AUG100CE", "BUY", "MACD_ZERO_CROSS", 10.5, 1, 0, 1,
                       evaluated_candle_timestamp=bar)      # different signal_id, same bar
        later = Signal("NSE:X26AUG100CE", "BUY", "MACD_ZERO_CROSS", 11.0, 1, 0, 1,
                       evaluated_candle_timestamp=bar + 1800)
        assert repository.save_signal(first) is True
        assert repository.save_signal(again) is False       # duplicate bar rejected
        assert repository.save_signal(later) is True        # next bar allowed
        assert len(repository.rows("signals", 10)) == 2
    finally:
        repository.close()


def test_minimum_warmup_bars_is_indicator_driven_not_five_hundred():
    from macd_trader.config import Settings
    from macd_trader.engine import TradingEngine

    engine = TradingEngine(Settings(symbols_csv="NSE:SBIN-EQ", feed_mode="simulation"))
    needed = engine.minimum_warmup_bars()
    assert needed < 500, "a 500-bar demand forced a broker fetch for already-warm contracts"
    assert needed >= engine.settings.slow_period + engine.settings.signal_period


def test_feed_liveness_is_measured_from_ticks_not_the_last_connect():
    """The SDK abandons its socket after five retries and never raises, so the
    engine reported "connected" while no tick had arrived for hours. Liveness
    must come from tick arrivals."""
    from datetime import UTC
    from macd_trader.config import Settings
    from macd_trader.engine import FEED_STALE_SECONDS, TradingEngine

    engine = TradingEngine(Settings(symbols_csv="NSE:SBIN-EQ", feed_mode="simulation"))
    engine.status = "connected"

    # Never received a tick during the session -> not alive.
    engine.last_tick_at = None
    assert engine.seconds_since_last_tick() is None

    fresh = datetime.now(timezone.utc)
    engine.last_tick_at = fresh
    assert engine.seconds_since_last_tick() < 5

    engine.last_tick_at = fresh - timedelta(seconds=FEED_STALE_SECONDS + 60)
    assert engine.seconds_since_last_tick() > FEED_STALE_SECONDS
    # Outside market hours silence is normal, so only assert the arithmetic
    # here; the session-gated verdict is exercised by feed_alive() in service.
    assert engine.broker_status()["status"] in {"connected", "stale"}


def test_expired_token_is_never_reported_as_a_live_feed():
    """The header showed green FYERS·CONNECTED at 07:05 with a token that died
    at 06:00. Quiet outside market hours is normal; a dead session is not."""
    from datetime import UTC
    from macd_trader.brokers import FyersBroker
    from macd_trader.config import Settings
    from macd_trader.engine import TradingEngine

    settings = Settings(symbols_csv="NSE:SBIN-EQ", feed_mode="simulation")
    engine = TradingEngine(settings)
    engine.status = "connected"

    class DeadToken:
        name = "fyers"
        def token_expiry(self):
            return datetime.now(timezone.utc) - timedelta(hours=1)
        def token_expired(self, now=None):
            return True

    class LiveToken(DeadToken):
        def token_expiry(self):
            return datetime.now(timezone.utc) + timedelta(hours=8)
        def token_expired(self, now=None):
            return False

    engine.broker = LiveToken()
    engine.last_tick_at = datetime.now(timezone.utc)
    assert engine.token_expired() is False
    assert engine.feed_alive() is True

    engine.broker = DeadToken()
    assert engine.token_expired() is True
    assert engine.feed_alive() is False, "an expired token must not read as a live feed"
    status = engine.broker_status()
    assert status["status"] == "token_expired"
    assert status["token_expired"] is True


def test_token_expiry_is_read_from_the_jwt_claim():
    import base64, json as _json
    from macd_trader.brokers import FyersBroker
    from macd_trader.config import Settings

    expiry = int((datetime.now(timezone.utc) + timedelta(hours=5)).timestamp())
    claims = base64.urlsafe_b64encode(_json.dumps({"exp": expiry}).encode()).decode().rstrip("=")
    broker = FyersBroker(Settings(fyers_access_token=f"header.{claims}.signature"))
    parsed = broker.token_expiry()
    assert parsed is not None and abs(parsed.timestamp() - expiry) < 2
    assert broker.token_expired() is False

    broker_none = FyersBroker(Settings(fyers_access_token="not-a-jwt"))
    assert broker_none.token_expiry() is None
    assert broker_none.token_expired() is False      # unknown is not expired
