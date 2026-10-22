"""Agentic lane: the model proposes, code validates and executes in a paper book, guards act without waiting."""
import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from macd_trader import agentic_brain as brain
from macd_trader import agentic_lane as agentic_lane_module
from macd_trader.agentic_lane import AgenticLane
from macd_trader.config import Settings
from macd_trader.events import EventHub
from macd_trader.models import Tick

IST = ZoneInfo("Asia/Kolkata")
CE = "NSE:SBIN26OCT800CE"
PE = "NSE:NIFTY26OCT23000PE"
NOW = datetime(2026, 10, 5, 11, 0, tzinfo=IST)          # a Monday session


class NoOrders:
    name = "fyers"

    async def place_order(self, _order):
        raise AssertionError("paper lanes must never reach the broker's order API")


class FakeClient:
    """Stands in for GrokClient: returns queued decisions and records the prompts it was shown."""

    def __init__(self, *decisions):
        self.decisions = list(decisions)
        self.prompts = []
        self.model = "grok-test"
        self.base_url = ""

    configured = True
    key_hint = "…test"
    reasoning_effort = None

    async def decide(self, system, user, max_tokens=2500):
        self.prompts.append(user)
        return self.decisions.pop(0), {"latency_ms": 5, "model": "grok-test", "prompt_tokens": 100, "completion_tokens": 50}


def build(tmp_path: Path, *decisions, risk_pct: float = 0.1) -> AgenticLane:
    # risk_pct 10%: these tests exercise the book mechanics; the decision matrix's own sizing is tested in
    # test_agentic_matrix.py with the default 1.5%.
    settings = Settings(execution_mode="paper", slippage_bps=0, database_path=str(tmp_path / "macd.sqlite3"),
                        research_database_path=str(tmp_path / "hist.sqlite3"), agentic_capital=1_000_000,
                        agentic_risk_per_trade_pct=risk_pct)
    engine = SimpleNamespace(latest_ticks={
        "NSE:NIFTY50-INDEX": Tick("NSE:NIFTY50-INDEX", 23000.0, change_pct=-0.5),
        "NSE:SBIN-EQ": Tick("NSE:SBIN-EQ", 805.0, change_pct=1.2),
        CE: Tick(CE, 20.0), PE: Tick(PE, 100.0)})
    lane = AgenticLane(settings, NoOrders(), EventHub(), str(tmp_path / "agentic.sqlite3"), engine=engine)
    lane.set_tradable_contracts({CE: 750, PE: 75})
    lane.set_contract_context({
        CE: {"spot_symbol": "NSE:SBIN-EQ", "option_type": "CE", "strike": 800, "expiry": "2099-12-30", "volume": 1, "oi": 1},
        PE: {"spot_symbol": "NSE:NIFTY50-INDEX", "option_type": "PE", "strike": 23000, "expiry": "2099-12-30", "volume": 1, "oi": 1}})
    lane.execution.set_quote(CE, 20.0)
    lane.execution.set_quote(PE, 100.0)
    lane.runtime_dir = str(tmp_path / "runtime")
    lane.update_brain(universe="all")                 # these tests trade a stock option too
    lane.client = FakeClient(*decisions)
    return lane


def decision(*actions, view="Trend intact."):
    return {"market_view": view, "regime": "neutral", "confidence": 0.6, "actions": list(actions),
            "next_check_minutes": 15, "notes_for_next_cycle": "watch 23,000"}


def act(action, contract_id=None, position_id=None, lots=None, stop=None, target=None, reason="because"):
    return {"action": action, "contract_id": contract_id, "position_id": position_id, "lots": lots,
            "stop_premium": stop, "target_premium": target, "reason": reason}


def candidate_id(lane, symbol):
    _, validation = lane.build_context(NOW)
    return next(cid for cid, row in validation["candidates"].items() if row["symbol"] == symbol)


# ----------------------------------------------------------------------------------- validation

def limits(**kw):
    return brain.Limits(capital=1_000_000, **kw)


def ctx(**kw):
    base = {"candidates": {"C01": {"symbol": CE, "ltp": 20.0, "lot_size": 750, "expiry": "2099-12-30"}},
            "positions": {}, "book": {"cash": 1_000_000, "deployed": 0, "day_pnl": 0}, "clock": "11:00",
            "today": "2026-10-05", "market_open": True}
    base.update(kw)
    return base


def test_validate_trims_to_the_position_cap_and_rejects_unknown_contracts():
    out = brain.validate(decision(act("open", "C01", lots=50, stop=12, target=40), act("open", "C99", lots=1)), ctx(), limits())
    assert len(out.accepted) == 1 and out.accepted[0]["lots"] == 10          # 15% of 10 L / (20 x 750)
    assert "trimmed" in out.accepted[0]["note"] and out.accepted[0]["stop_premium"] == 12
    assert out.rejected[0]["rejected"].startswith("contract_id is not")


def test_validate_blocks_new_risk_after_the_loss_stop_and_late_in_the_day():
    stopped = brain.validate(decision(act("open", "C01", lots=1)), ctx(book={"cash": 1e6, "deployed": 0, "day_pnl": -31_000}), limits())
    late = brain.validate(decision(act("open", "C01", lots=1)), ctx(clock="15:12"), limits())
    assert not stopped.accepted and "loss stop" in stopped.rejected[0]["rejected"]
    assert not late.accepted and "after 15:10" in late.rejected[0]["rejected"]


def test_validate_turns_an_oversized_reduce_into_a_close_and_drops_levels_on_the_wrong_side():
    pos = {"P01": {"symbol": CE, "lots": 2, "lot_size": 750, "ltp": 25.0, "value": 37_500, "return_pct": 25, "expiry": "2099-12-30"}}
    out = brain.validate(decision(act("reduce", position_id="P01", lots=5), act("set_levels", position_id="P01", stop=30, target=20)),
                         ctx(positions=pos), limits())
    close, levels = out.accepted
    assert close["action"] == "close" and close["lots"] == 2
    assert levels["stop_premium"] is None and levels["target_premium"] is None


def test_rules_fallback_cuts_losers_books_half_of_winners_and_leaves_expiry_day():
    positions = {"P01": {"symbol": CE, "lots": 4, "return_pct": 75, "expiry": "2099-12-30"},
                 "P02": {"symbol": PE, "lots": 1, "return_pct": -50, "expiry": "2099-12-30"},
                 "P03": {"symbol": "X", "lots": 1, "return_pct": 5, "expiry": "2026-10-05"}}
    d = brain.rules_decision({"positions": positions, "clock": "14:50", "today": "2026-10-05"}, "test")
    kinds = {a["position_id"]: (a["action"], a["lots"]) for a in d["actions"]}
    assert kinds == {"P01": ("reduce", 2), "P02": ("close", None), "P03": ("close", None)}


# ----------------------------------------------------------------------------------- the Grok client

def test_grok_client_sends_the_schema_and_never_echoes_the_key():
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"model": "grok-4", "usage": {"prompt_tokens": 9, "completion_tokens": 3},
                                         "choices": [{"message": {"content": json.dumps(decision())}}]})

    client = brain.GrokClient("secret-key", "grok-4", transport=httpx.MockTransport(handler))
    out, meta = asyncio.run(client.decide("sys", "user"))
    assert out["market_view"] == "Trend intact." and meta["prompt_tokens"] == 9
    assert seen["auth"] == "Bearer secret-key"
    assert seen["body"]["response_format"]["type"] == "json_schema" and "max_completion_tokens" in seen["body"]

    failing = brain.GrokClient("secret-key", "grok-4", transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom")))
    with pytest.raises(brain.BrainError) as err:
        asyncio.run(failing.decide("sys", "user"))
    assert "secret-key" not in str(err.value)


def test_grok_client_falls_back_to_json_mode_when_the_schema_is_refused():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body["response_format"]["type"])
        if body["response_format"]["type"] == "json_schema":
            return httpx.Response(400, text="response_format json_schema not supported")
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(decision())}}]})

    client = brain.GrokClient("k", "grok-old", transport=httpx.MockTransport(handler))
    asyncio.run(client.decide("sys", "user"))
    assert calls == ["json_schema", "json_object"] and client.json_schema_supported is False


# ----------------------------------------------------------------------------------- the lane

def test_cycle_opens_with_thesis_then_books_half_then_closes(tmp_path):
    lane = build(tmp_path)
    cid = candidate_id(lane, CE)
    lane.client = FakeClient(
        decision(act("open", cid, lots=4, stop=12, target=60, reason="SBIN RS60 leader, NIFTY above 50dma")),
        decision(act("reduce", position_id="P01", lots=2, reason="+50%: book half")),
        decision(act("close", position_id="P01", reason="thesis done")))
    cycle = asyncio.run(lane.think("test", "prefer liquid names", NOW))
    position = lane.portfolio.positions[CE]
    assert position.lots == 4 and cycle["mode"] == "grok" and cycle["executed"][0]["status"] == "filled"
    assert lane.meta[CE]["thesis"].startswith("SBIN RS60") and lane.meta[CE]["stop_premium"] == 12
    assert "OPERATOR NOTE" in lane.client.prompts[0] and '"candidates"' in lane.client.prompts[0]

    lane.execution.set_quote(CE, 30.0)
    asyncio.run(lane.think("test", None, NOW))
    assert lane.portfolio.positions[CE].lots == 2 and lane.meta[CE]["booked"] == 1

    asyncio.run(lane.think("test", None, NOW))
    assert CE not in lane.held_symbols() and CE not in lane.meta
    cycles = lane.recent_cycles()
    assert len(cycles) == 3 and cycles[-1]["market_view"] == "Trend intact."
    assert [a["action"] for a in reversed(lane.actions())] == ["open", "reduce", "close"]


def test_guards_close_at_the_agents_stop_without_waiting_for_a_cycle(tmp_path, monkeypatch):
    monkeypatch.setattr(agentic_lane_module, "INTRADAY_FLATTEN_AT", __import__("datetime").time(23, 59))   # wall clock may be past 15:15
    lane = build(tmp_path)
    lane.client = FakeClient(decision(act("open", candidate_id(lane, PE), lots=2, stop=80, reason="index put")))
    asyncio.run(lane.think("test", None, NOW))
    assert lane.portfolio.positions[PE].lots == 2
    asyncio.run(lane.execution.on_tick(PE, 79.0))
    assert PE not in lane.held_symbols()
    assert lane.actions()[0]["reason"] == "guard: AGENT_STOP" and lane._triggers


def test_an_entry_is_skipped_when_the_premium_ran_away_while_the_model_was_thinking(tmp_path):
    lane = build(tmp_path)
    cid = candidate_id(lane, PE)

    class SlowClient(FakeClient):
        async def decide(self, system, user, max_tokens=2500):
            lane.engine.latest_ticks[PE] = Tick(PE, 112.0)        # a newer tick lands during the call
            return await super().decide(system, user, max_tokens)

    lane.client = SlowClient(decision(act("open", cid, lots=1, reason="index put")))
    cycle = asyncio.run(lane.think("test", None, NOW))
    assert not lane.held_symbols()
    assert cycle["executed"][0]["status"] == "failed" and "premium moved from 100.00 to 112.00" in cycle["executed"][0]["error"]


def test_no_key_means_rules_only_and_nothing_new(tmp_path):
    lane = build(tmp_path)
    lane.client = brain.GrokClient("", "grok-4")
    cycle = asyncio.run(lane.think("test", None, NOW))
    assert cycle["mode"] == "rules" and not lane.held_symbols() and "no XAI_API_KEY" in cycle["market_view"]


def test_outside_the_session_the_model_may_comment_but_not_trade(tmp_path):
    lane = build(tmp_path)
    lane.client = FakeClient(decision(act("open", candidate_id(lane, CE), lots=1)))
    evening = datetime(2026, 10, 5, 19, 0, tzinfo=IST)
    cycle = asyncio.run(lane.think("operator request", None, evening))
    assert not lane.held_symbols() and cycle["rejected"][0]["rejected"] == "market closed"


def test_restart_restores_positions_and_their_thesis(tmp_path):
    lane = build(tmp_path)
    lane.client = FakeClient(decision(act("open", candidate_id(lane, CE), lots=1, target=50, reason="keep me")))
    asyncio.run(lane.think("test", None, NOW))
    again = build(tmp_path)
    assert again.portfolio.positions[CE].lots == 1 and again.meta[CE]["thesis"] == "keep me"
    assert again.meta[CE]["target_premium"] == 50


def test_engine_wiring_includes_the_agentic_lane():
    from macd_trader.engine import _smart_lanes
    stand_in = SimpleNamespace(smart_lanes=SimpleNamespace(lanes=["a", "b"]), agentic="agentic")
    assert _smart_lanes(stand_in) == ["a", "b", "agentic"]
    assert _smart_lanes(SimpleNamespace()) == []


# ----------------------------------------------------------------------------------- the connection panel

def test_panel_key_wins_over_the_environment_and_is_never_returned(tmp_path, monkeypatch):
    import os
    lane = build(tmp_path)
    lane.runtime_dir = str(tmp_path / "runtime")
    monkeypatch.setenv("XAI_API_KEY", "env-key-000011112222")
    status = lane.update_brain(api_key="panel-key-33334444abcd", model="grok-4.7", reasoning_effort="high")
    assert status["configured"] and status["key_source"] == "panel" and status["key_hint"] == "…abcd"
    assert status["model"] == "grok-4.7" and status["reasoning_effort"] == "high"
    assert "panel-key" not in json.dumps(status) and "env-key" not in json.dumps(status)
    stored = tmp_path / "runtime" / brain.BRAIN_FILE
    assert oct(os.stat(stored).st_mode & 0o777) == "0o600"
    status = lane.update_brain(clear_key=True)
    assert status["key_source"] == "environment" and status["key_hint"] == "…2222"
    monkeypatch.delenv("XAI_API_KEY")
    assert lane.update_brain(model="")["configured"] is False and lane.client.model == lane.settings.agentic_model


def test_index_universe_offers_only_index_contracts_and_cycles_hourly(tmp_path):
    lane = build(tmp_path)
    lane.update_brain(universe="index", cycle_minutes=60)
    prompt, validation = lane.build_context(NOW)
    assert [row["symbol"] for row in validation["candidates"].values()] == [PE]
    assert "INDEX OPTIONS ONLY" in prompt and "option_agent_picks" not in prompt
    lane.client = FakeClient(decision(act("hold", reason="nothing yet")) | {"next_check_minutes": 5})
    asyncio.run(lane.think("test", None, NOW))
    assert lane.next_cycle_at == NOW + timedelta(minutes=60)      # the model may not shorten the hour
    with pytest.raises(ValueError):
        lane.update_brain(cycle_minutes=7)
    assert lane.brain_status()["universe"] == "index" and lane.health()["cycle_minutes"] == 60


def test_index_universe_extras_are_tradable_and_carry_risk_fields(tmp_path):
    lane = build(tmp_path)
    nxt = "NSE:NIFTY26NOV23500PE"
    lane.set_index_universe({nxt: {"spot_symbol": "NSE:NIFTY50-INDEX", "option_type": "PE", "strike": 23500,
                                   "expiry": "2100-01-26", "lot_size": 75, "moneyness": "NEXT-OTM", "volume": 0, "oi": 0}})
    lane.engine.latest_ticks[nxt] = Tick(nxt, 150.0)
    lane.set_tradable_contracts({CE: 750, PE: 75})             # the engine's next refresh must not drop it
    lane.update_brain(universe="index")
    prompt, validation = lane.build_context(NOW)
    rows = {r["symbol"] for r in validation["candidates"].values()}
    assert rows == {PE, nxt} and lane.execution.lot_sizes[nxt] == 75
    data = json.loads(prompt)
    # labels come from the live spot (23,000), not the selection's stale tag: a 23,500 put is in the money
    labels = {c["strike"]: c["moneyness"] for c in data["candidates"]}
    assert labels == {23000: "ATM", 23500: "next-expiry ITM"}
    extra = next(c for c in data["candidates"] if c["moneyness"] == "next-expiry ITM")
    assert {"breakeven", "move_needed_pct", "delta", "theta_rs_per_lot_day", "iv_pct"} <= set(extra)
    assert "data_quality" in data and "your_track_record" in data and "spot" not in extra


def arm(cid, op, level, until="14:00", confirm=None, lots=1, horizon="intraday", stop=60.0, target=160.0):
    action = act("arm", cid, lots=lots, stop=stop, target=target, reason="break of the first-hour low")
    action.update({"horizon": horizon, "trigger_index": "NIFTY", "trigger_op": op, "trigger_level": level,
                   "confirm_index": None, "confirm_op": None, "confirm_level": None, "valid_until": until})
    if confirm:
        action.update({"confirm_index": confirm[0], "confirm_op": confirm[1], "confirm_level": confirm[2]})
    return action


def test_a_trigger_is_armed_then_fires_between_cycles_when_the_index_breaks(tmp_path):
    lane = build(tmp_path)
    cid = candidate_id(lane, PE)
    lane.client = FakeClient(decision(arm(cid, "below", 22950), arm(cid, "below", 23100), arm(cid, "below", 22900, until="16:00")))
    cycle = asyncio.run(lane.think("test", None, NOW))
    assert [a["action"] for a in cycle["accepted"]] == ["arm"] and cycle["executed"][0]["status"] == "armed"
    reasons = sorted(r["rejected"] for r in cycle["rejected"])
    assert "already below" in reasons[1] or "already below" in reasons[0]
    assert any("valid_until" in r for r in reasons)
    assert list(lane.armed) == ["T01"] and not lane.held_symbols()
    asyncio.run(lane.check_triggers(NOW.replace(minute=5)))           # NIFTY 23000: nothing yet
    assert not lane.held_symbols()
    lane.engine.latest_ticks["NSE:NIFTY50-INDEX"] = Tick("NSE:NIFTY50-INDEX", 22940.0)
    fired = asyncio.run(lane.check_triggers(NOW.replace(minute=10)))
    assert fired[0]["status"] == "filled" and lane.portfolio.positions[PE].lots == 1 and not lane.armed
    assert lane.meta[PE]["stop_premium"] == 60.0 and lane.meta[PE]["horizon"] == "intraday"
    assert "trigger T01 fired (NIFTY below 22950" in lane.meta[PE]["thesis"]
    assert any("trigger T01 fired" in t for t in lane._triggers)        # the model is asked to review the fill


def test_triggers_need_their_confirmation_and_expire_unfired(tmp_path):
    lane = build(tmp_path)
    cid = candidate_id(lane, PE)
    lane.engine.latest_ticks["NSE:NIFTYBANK-INDEX"] = Tick("NSE:NIFTYBANK-INDEX", 55000.0)
    lane.client = FakeClient(decision(arm(cid, "below", 22950, until="11:30", confirm=("BANKNIFTY", "below", 54800))))
    asyncio.run(lane.think("test", None, NOW))
    lane.engine.latest_ticks["NSE:NIFTY50-INDEX"] = Tick("NSE:NIFTY50-INDEX", 22900.0)
    asyncio.run(lane.check_triggers(NOW.replace(minute=20)))
    assert not lane.held_symbols() and "T01" in lane.armed                # BANKNIFTY has not confirmed
    asyncio.run(lane.check_triggers(NOW.replace(minute=31, hour=11)))
    assert not lane.armed and lane.actions()[0]["status"] == "expired"


def test_intraday_positions_are_flattened_at_1515(tmp_path):
    lane = build(tmp_path)
    lane.client = FakeClient(decision(act("open", candidate_id(lane, PE), lots=1, reason="trend day")))
    asyncio.run(lane.think("test", None, NOW))
    assert lane.meta[PE]["horizon"] == "intraday"
    asyncio.run(lane.flatten_intraday(NOW.replace(hour=15, minute=10)))
    assert lane.held_symbols()
    asyncio.run(lane.flatten_intraday(NOW.replace(hour=15, minute=16)))
    assert not lane.held_symbols() and lane.actions()[0]["reason"] == "guard: INTRADAY_FLATTEN"


def test_a_level_break_asks_for_a_cycle_once(tmp_path):
    lane = build(tmp_path)
    lane._levels = {"day": NOW.date().isoformat(), "first_hour": True,
                    "NIFTY": {"first_hour_low": 22950.0, "first_hour_high": 23100.0, "prev_low": 22800.0, "prev_high": 23200.0},
                    "BANKNIFTY": {}}
    lane._level_events()
    assert not lane._triggers
    lane.engine.latest_ticks["NSE:NIFTY50-INDEX"] = Tick("NSE:NIFTY50-INDEX", 22940.0)
    lane._level_events()
    lane._level_events()
    assert lane._triggers == ["NIFTY broke below its first hour low 22950"]


def test_the_key_is_only_ever_sent_to_x_ai(tmp_path):
    lane = build(tmp_path)
    lane.runtime_dir = str(tmp_path / "runtime")
    with pytest.raises(ValueError):
        lane.update_brain(api_base="https://example.com/v1")
    with pytest.raises(ValueError):
        lane.update_brain(api_base="http://api.x.ai/v1")
    assert lane.update_brain(api_base="https://api.x.ai/v1/")["api_base"] == "https://api.x.ai/v1"


def test_connection_test_lists_models_and_checks_the_chosen_one():
    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "grok-4.7"}, {"id": "grok-3-mini"}]})
        body = json.loads(request.content)
        assert body["model"] == "grok-4.7" and body["reasoning_effort"] == "low"
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    client = brain.GrokClient("k" * 20, "grok-4.7", transport=httpx.MockTransport(handler), reasoning_effort="low")
    result = asyncio.run(client.test())
    assert result["ok"] and result["model_listed"] and result["reply"] == "OK" and "grok-3-mini" in result["models"]

    denied = brain.GrokClient("k" * 20, "grok-4.7", transport=httpx.MockTransport(
        lambda r: httpx.Response(401, json={"error": "bad key"})))
    result = asyncio.run(denied.test())
    assert not result["ok"] and "HTTP 401" in result["error"]
