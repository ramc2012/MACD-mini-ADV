"""FyersBroker data calls: straight to Fyers by default, through the market-intelligence hub when configured."""
import asyncio
import json

import httpx
import pytest

from macd_trader.brokers import FyersBroker
from macd_trader.config import Settings


def _broker(hub=""):
    return FyersBroker(Settings(feed_mode="simulation", fyers_client_id="APP-100", fyers_access_token="tok",
                                market_hub_url=hub))


def _run(broker, handler, call):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await call(client)
    return asyncio.run(go())


def test_without_a_hub_the_call_goes_to_fyers_with_the_session_header():
    seen = {}

    def handler(request):
        seen.update(url=str(request.url), auth=request.headers.get("authorization"), method=request.method)
        return httpx.Response(200, json={"s": "ok"})

    broker = _broker()
    response = _run(broker, handler, lambda c: broker._data_get(c, "depth", {"symbol": "NSE:X"}, priority="high"))
    assert response.status_code == 200 and seen["method"] == "GET"
    assert seen["url"].startswith("https://api-t1.fyers.in/data/depth?symbol=NSE%3AX") and seen["auth"] == "APP-100:tok"


def test_with_a_hub_the_call_is_posted_to_it_without_the_token():
    seen = {}

    def handler(request):
        seen.update(url=str(request.url), auth=request.headers.get("authorization"), body=json.loads(request.content))
        return httpx.Response(200, json={"s": "ok", "d": {}}, headers={"x-mi-served": "cached"})

    broker = _broker("http://host.docker.internal:8400/")
    response = _run(broker, handler, lambda c: broker._data_get(c, "quotes", {"symbols": "NSE:A,NSE:B"},
                                                               priority="critical", ttl_ms=0))
    assert response.json()["s"] == "ok" and seen["url"] == "http://host.docker.internal:8400/v1/request"
    assert seen["auth"] is None                                     # the hub holds the session, not the caller
    assert seen["body"] == {"broker": "fyers", "endpoint": "quotes", "query": {"symbols": "NSE:A,NSE:B"},
                            "priority": "critical", "caller": "macd-mini-adv", "ttl_ms": 0}


def test_local_recovery_never_requests_historical_broker_data(monkeypatch):
    monkeypatch.setenv("MACD_HISTORY_DOWNLOADS_ENABLED", "false")
    def forbidden(request): pytest.fail("Historical recovery must stay offline")
    for hub in ("", "http://hub:8400"):
        broker = _broker(hub)
        with pytest.raises(RuntimeError, match="disabled"):
            _run(broker, forbidden, lambda c: broker._data_get(c, "history", {}, priority="bulk"))


def test_a_hub_that_does_not_answer_falls_back_to_fyers_and_is_skipped_for_a_minute():
    seen = []

    def handler(request):
        seen.append(request.url.host)
        if request.url.host == "hub":
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json={"s": "ok"})

    broker = _broker("http://hub:8400")
    for _ in range(2):
        response = _run(broker, handler, lambda c: broker._data_get(c, "quotes", {"symbols": "NSE:A"}, priority="high"))
        assert response.json() == {"s": "ok"}
    assert seen == ["hub", "api-t1.fyers.in", "api-t1.fyers.in"]       # the second call skipped the hub
    status = broker.hub_status()
    assert status["bypassed"] and status["fallbacks"] == 1 and "ConnectError" in status["last_error"]
    broker._hub_skip_until = 0                                          # a minute later the hub is tried again
    _run(broker, handler, lambda c: broker._data_get(c, "quotes", {"symbols": "NSE:A"}, priority="high"))
    assert seen[-2:] == ["hub", "api-t1.fyers.in"] and broker.hub_fallbacks == 2


def test_the_hubs_own_error_falls_back_but_a_fyers_answer_through_the_hub_does_not():
    def answer(status, served):
        def handler(request):
            if request.url.host == "hub":
                return httpx.Response(status, json={"error": "no_session"} if not served else {"s": "error", "code": -16},
                                      headers={"x-mi-served": "fetched"} if served else {})
            return httpx.Response(200, json={"s": "direct"})
        return handler

    broker = _broker("http://hub:8400")
    assert _run(broker, answer(503, False), lambda c: broker._data_get(c, "depth", {}, priority="high")).json() == {"s": "direct"}
    broker = _broker("http://hub:8400")
    response = _run(broker, answer(401, True), lambda c: broker._data_get(c, "depth", {}, priority="high"))
    assert response.status_code == 401 and broker.hub_fallbacks == 0   # Fyers' own 401 still reaches the caller
    broker = _broker("http://hub:8400")
    response = _run(broker, answer(429, False), lambda c: broker._data_get(c, "depth", {}, priority="high"))
    assert response.status_code == 429 and broker.hub_fallbacks == 0   # the shared day budget is not routed around


def test_the_hub_url_comes_from_the_environment_never_settings_json():
    from macd_trader.config import ENVIRONMENT_ONLY
    assert "market_hub_url" in ENVIRONMENT_ONLY
