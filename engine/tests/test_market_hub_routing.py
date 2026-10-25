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
