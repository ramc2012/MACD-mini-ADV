"""Live features (live_features.py): closed minutes published once, snapshots for changed symbols, profiles each
minute, rows stored, latency summarised per minute; the engine reads the desk's snapshot before its own tracker."""
import asyncio
import sqlite3
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import orjson

from macd_trader import bus
from macd_trader.entry_context import EntryFlow
from macd_trader.live_features import FlowSnapshots, LatencyMeter, LiveFeatures, _quantiles
from macd_trader.models import Tick
from macd_trader.orderflow import FlowState

IST = ZoneInfo("Asia/Kolkata")
T0 = int(datetime(2026, 10, 26, 10, 0, tzinfo=IST).timestamp())


class _Conn:
    def __init__(self):
        self.sent = []
        self.is_connected = True

    async def publish(self, subject, payload, headers=None):
        self.sent.append((subject, orjson.loads(payload), headers))


def _state(symbol, rows, trades=5):
    state = FlowState(symbol)
    state.trades, state.cumulative_delta, state.buy_volume, state.sell_volume, state.total_volume = trades, 40, 70, 30, 110
    for row in rows:
        state.minutes.append(list(row))
    return state


def _features(tmp_path, states, profiles=None, universe=()):
    tracker = SimpleNamespace(states=states, burst=lambda symbol: {"side": 0})
    features = LiveFeatures("nats://unused", str(tmp_path / "ticks.sqlite3"), tracker, profiles, universe=lambda: universe)
    features._connection = _Conn()
    return features


def test_only_closed_minutes_are_published_and_each_once(tmp_path):
    rows = [(T0, 100.0, 10, 6, 4, 2), (T0 + 60, 101.0, 20, 15, 5, 12), (T0 + 120, 102.0, 5, 5, 0, 17)]
    features = _features(tmp_path, {"NSE:A-EQ": _state("NSE:A-EQ", rows)})
    sent = asyncio.run(features.publish_once(now=T0 + 150))           # the 10:02 minute is still forming
    flow = [m for subject, m, _ in features._connection.sent if subject.startswith("ft.flow.1m.")]
    assert sent["minutes"] == 2 and [m["minute_ts"] for m in flow] == [T0, T0 + 60]
    assert flow[1]["delta"] == 10 and flow[1]["cvd"] == 12 and flow[1]["v"] == 1
    headers = [h for subject, _, h in features._connection.sent if subject.startswith("ft.flow.1m.")]
    assert headers[0] == {"Nats-Msg-Id": f"flow:NSE:A-EQ:{T0}"}
    features._connection.sent.clear()
    again = asyncio.run(features.publish_once(now=T0 + 190))          # now 10:02 has closed; nothing repeats
    assert again["minutes"] == 1 and again["snapshots"] == 0           # no new trades: no snapshot
    db = sqlite3.connect(tmp_path / "ticks.sqlite3")
    assert [r[0] for r in db.execute("SELECT minute_ts FROM live_flow_minutes ORDER BY minute_ts")] == [T0, T0 + 60, T0 + 120]


def test_snapshots_go_to_the_subject_and_the_kv_bucket(tmp_path):
    features = _features(tmp_path, {"NSE:NIFTY26OCTFUT": _state("NSE:NIFTY26OCTFUT", [(T0, 1, 1, 1, 0, 1)])})
    asyncio.run(features.publish_once(now=T0 + 30))
    subjects = [subject for subject, _, _ in features._connection.sent]
    assert "fts.flow.NSE:NIFTY26OCTFUT" in subjects and "$KV.ft_latest.flow.NSE:NIFTY26OCTFUT" in subjects
    snap = next(m for s, m, _ in features._connection.sent if s.startswith("fts.flow."))
    assert snap["session_cvd"] == 40 and snap["session_classified_volume"] == 100 and snap["day"] == "2026-10-26"


def test_profiles_are_published_once_a_minute_for_the_universe(tmp_path):
    profile = SimpleNamespace(day="2026-10-26", poc=lambda: 100.0, value_area=lambda: (101.0, 99.0), ib_high=102.0,
                              ib_low=98.0, high=103.0, low=97.0, open_price=100.0, last_price=100.5,
                              open_type=lambda: "OA", day_type=lambda: "normal", brackets_seen={0, 1})
    profiles = SimpleNamespace(get=lambda symbol: profile if symbol == "NSE:A-EQ" else None)
    features = _features(tmp_path, {}, profiles, universe=("NSE:A-EQ", "NSE:B-EQ"))
    assert asyncio.run(features.publish_once(now=T0 + 5))["profiles"] == 1
    assert asyncio.run(features.publish_once(now=T0 + 30))["profiles"] == 0
    message = next(m for s, m, _ in features._connection.sent if s.startswith("ft.profile.1m."))
    assert (message["poc"], message["vah"], message["val"], message["open_type"]) == (100.0, 101.0, 99.0, "OA")


def test_latency_is_summarised_per_minute():
    meter = LatencyMeter()
    for i in range(100):
        meter.add("bus", float(i), now=T0 + 1)
    meter.add("bus", 5.0, now=T0 + 61)                                 # rolls the minute
    assert meter.closed[0][:3] == (T0, "bus", 100)
    assert meter.last_minute["bus"]["p95"] == 94.0 and meter.last_minute["bus"]["max"] == 99.0
    assert _quantiles([]) == {"n": 0, "p50": None, "p95": None, "p99": None, "max": None}


def test_bus_messages_carry_the_engine_receipt_time_to_the_meter():
    tick = Tick("NSE:A-EQ", 100.0, 10, timestamp=datetime(2026, 10, 26, 10, 0, tzinfo=IST))
    received = datetime(2026, 10, 26, 10, 0, 0, 250_000, tzinfo=IST)
    payload = bus.encode_tick(tick, seq=1, analysis_only=False, received_at=received)
    decoded, _, seq, (exchange_ms, received_ms) = bus.decode_tick_stamped(payload)
    assert decoded.symbol == "NSE:A-EQ" and seq == 1 and received_ms - exchange_ms == 250
    stamps, delivered = [], []

    async def handler(t, a):
        delivered.append(t.symbol)

    subscriber = bus.TickBusSubscriber("nats://unused", handler, on_stamps=lambda *s: stamps.append(s))
    asyncio.run(subscriber._on_message(SimpleNamespace(data=payload)))
    assert delivered == ["NSE:A-EQ"] and stamps[0][:2] == (exchange_ms, received_ms)


def test_entry_flow_reads_the_desk_snapshot_first_and_falls_back_to_its_own():
    snapshots = FlowSnapshots("nats://unused")
    flow = EntryFlow(snapshots)
    flow.day = "2026-10-26"
    now_ms = T0 * 1000
    snapshots.latest["NSE:RELIANCE-EQ"] = {"symbol": "NSE:RELIANCE-EQ", "session_cvd": 50.0,
                                           "session_classified_volume": 200.0, "session_total_volume": 250.0,
                                           "trades": 9, "last_price": 1190.0, "day": "2026-10-26",
                                           "computed_at_ms": now_ms}
    snapshots.get = lambda symbol, now=None: FlowSnapshots.get(snapshots, symbol, now_ms + 1000)
    context = flow.context("NSE:RELIANCE26OCT1200CE")
    assert context["source"] == "desk" and context["session_cvd_share_signed"] == 0.25
    snapshots.get = lambda symbol, now=None: FlowSnapshots.get(snapshots, symbol, now_ms + 600_000)   # stale
    assert flow.context("NSE:RELIANCE26OCT1200CE")["source"] == "local"
    assert flow.health()["reads"] == {"desk": 1, "local": 1}


def test_published_minutes_are_read_back_in_tracker_form(tmp_path):
    from macd_trader.live_features import read_live_minutes
    features = _features(tmp_path, {"NSE:NIFTY26OCTFUT": _state("NSE:NIFTY26OCTFUT",
                                                                [(T0, 100.0, 10, 6, 4, 2), (T0 + 60, 101.0, 5, 1, 4, -1)])})
    asyncio.run(features.publish_once(now=T0 + 130))
    rows = read_live_minutes(str(tmp_path / "ticks.sqlite3"), "NSE:NIFTY26OCTFUT", "2026-10-26")
    assert rows == [[T0, 100.0, 10.0, 6.0, 4.0, 2.0], [T0 + 60, 101.0, 5.0, 1.0, 4.0, -1.0]]
    assert read_live_minutes(str(tmp_path / "missing.sqlite3"), "X", "2026-10-26") is None
