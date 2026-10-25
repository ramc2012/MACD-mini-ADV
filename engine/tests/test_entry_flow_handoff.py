"""Desk/local handoffs must preserve the session date and never invent gap volume or full-session CVD."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from macd_trader.entry_context import EntryFlow
from macd_trader.live_features import FlowSnapshots
from macd_trader.models import Tick


IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 26, 10, 0, tzinfo=IST)
SPOT, OPTION = "NSE:SBIN-EQ", "NSE:SBIN26OCT800CE"


def _snapshot(symbol, now=NOW, volume=1000):
    return {"symbol": symbol, "day": now.date().isoformat(), "computed_at_ms": now.timestamp() * 1000,
            "session_cvd": 50.0, "session_classified_volume": 200.0,
            "session_total_volume": volume, "last_price": 800.1, "trades": 9}


def _tick(symbol=SPOT, now=NOW, volume=1000):
    return Tick(symbol, 800.1, volume=volume, bid=799.9, ask=800.1, last_qty=25, timestamp=now)


def _flow(**snapshots):
    desk = FlowSnapshots("nats://unused")
    desk.latest.update(snapshots)
    desk.received = len(snapshots)
    return EntryFlow(desk), desk


def test_fresh_snapshot_initializes_context_without_any_local_ticks():
    flow, _ = _flow(**{SPOT: _snapshot(SPOT)})
    out = flow.context(OPTION, now=NOW)
    assert out["source"] == "desk" and out["flow_day"] == "2026-10-26"
    assert out["session_cvd_share_signed"] == .25
    assert flow.tracker.states == {}


def test_day_rollover_works_while_local_tracking_is_suppressed():
    flow, desk = _flow(**{SPOT: _snapshot(SPOT)})
    flow.on_tick(_tick(), now=NOW)
    tomorrow = NOW + timedelta(days=1)
    desk.latest[SPOT] = _snapshot(SPOT, tomorrow)
    flow.on_tick(_tick(now=tomorrow), now=tomorrow)
    assert flow.day == "2026-10-27" and flow.tracker.states == {}
    assert flow.context(OPTION, now=tomorrow)["source"] == "desk"


def test_index_future_is_selected_from_fresh_snapshots_with_no_local_state():
    future = "NSE:NIFTY26OCTFUT"
    flow, _ = _flow(**{future: _snapshot(future),
                      "NSE:NIFTY26NOVFUT": _snapshot("NSE:NIFTY26NOVFUT", volume=100),
                      "NSE:NIFTYBANK26OCTFUT": _snapshot("NSE:NIFTYBANK26OCTFUT", volume=100000)})
    out = flow.context("NSE:NIFTY26OCT24000PE", now=NOW)
    assert out["underlying"] == future and out["source"] == "desk"
    assert out["session_cvd_share_signed"] == -.25


def test_fallback_is_per_symbol_and_rebaselines_after_a_desk_gap():
    other = "NSE:RELIANCE-EQ"
    flow, desk = _flow(**{SPOT: _snapshot(SPOT), other: _snapshot(other)})
    flow.on_tick(_tick(), now=NOW)
    flow.on_tick(_tick(other), now=NOW)
    late = NOW + timedelta(seconds=121)
    desk.latest[other] = _snapshot(other, late)
    flow.on_tick(_tick(other, late, 50000), now=late)
    flow.on_tick(_tick(now=late, volume=50000), now=late)
    flow.on_tick(_tick(now=late + timedelta(seconds=1), volume=50025), now=late + timedelta(seconds=1))
    assert other not in flow.tracker.states
    assert flow.tracker.states[SPOT].total_volume == 25  # the 49,000-volume gap was never classified
    out = flow.context(OPTION, now=late + timedelta(seconds=1))
    assert out["source"] == "local" and out["flow_scope"] == "fallback_interval"
    assert out["session_cvd"] is None and out["session_cvd_share_signed"] is None
    assert out["interval_total_volume"] == 25 and out["interval_cvd_share_signed"] > 0
    assert out["interval_started_at"] == late.isoformat()


def test_desk_recovery_discards_the_partial_interval_before_a_second_outage():
    flow, desk = _flow(**{SPOT: _snapshot(SPOT)})
    flow.on_tick(_tick(), now=NOW)
    late = NOW + timedelta(seconds=121)
    flow.on_tick(_tick(now=late, volume=50000), now=late)
    flow.on_tick(_tick(now=late, volume=50025), now=late)
    desk.latest[SPOT] = _snapshot(SPOT, late, volume=50025)
    assert flow.context(OPTION, now=late)["source"] == "desk"
    assert SPOT not in flow.tracker.states
    later = late + timedelta(seconds=121)
    flow.on_tick(_tick(now=later, volume=90000), now=later)
    out = flow.context(OPTION, now=later)
    assert out["flow_scope"] == "fallback_interval" and out["interval_total_volume"] == 0
    assert out["session_cvd"] is None


def test_explicit_local_mode_ignores_desk_snapshots():
    flow, _ = _flow(**{SPOT: _snapshot(SPOT)})
    flow.on_tick(_tick(), now=NOW, use_snapshots=False)
    flow.on_tick(_tick(volume=1025), now=NOW, use_snapshots=False)
    out = flow.context(OPTION, now=NOW, use_snapshots=False)
    assert out["source"] == "local" and out["session_total_volume"] == 25


def test_previous_day_snapshots_do_not_suppress_current_day_local_ticks():
    yesterday = NOW - timedelta(days=1)
    flow, _ = _flow(**{SPOT: _snapshot(SPOT, yesterday)})
    flow.on_tick(_tick(), now=NOW)
    flow.on_tick(_tick(volume=1025), now=NOW)
    out = flow.context(OPTION, now=NOW)
    assert out["flow_day"] == "2026-10-26" and out["source"] == "local"
    assert out["session_total_volume"] == 25
