"""Daily login without a browser: the refresh token Fyers returns at login, exchanged with the PIN."""
import asyncio
import hashlib

import httpx
import pytest

from macd_trader.brokers import FyersBroker
from macd_trader.config import Settings


def _broker(**kwargs):
    return FyersBroker(Settings(feed_mode="fyers", fyers_client_id="APP-100", fyers_secret="sec", execution_mode="paper",
                                allow_live_orders=False, **kwargs))


def test_refresh_posts_the_app_hash_refresh_token_and_pin(monkeypatch):
    seen = {}

    async def post(self, url, json=None, **_):
        seen.update(url=url, body=json)
        return httpx.Response(200, json={"s": "ok", "access_token": "NEW"}, headers={"content-type": "application/json"})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    out = asyncio.run(_broker(fyers_refresh_token="R1", fyers_pin="1234").refresh_access_token())
    assert out == {"access_token": "NEW"} and seen["url"].endswith("/api/v3/validate-refresh-token")
    assert seen["body"] == {"grant_type": "refresh_token", "appIdHash": hashlib.sha256(b"APP-100:sec").hexdigest(),
                            "refresh_token": "R1", "pin": "1234"}


def test_refresh_without_saved_material_or_with_a_refusal_says_why(monkeypatch):
    with pytest.raises(RuntimeError, match="No refresh token and PIN"):
        asyncio.run(_broker().refresh_access_token())

    async def post(self, url, json=None, **_):
        return httpx.Response(400, json={"s": "error", "message": "invalid pin"}, headers={"content-type": "application/json"})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    with pytest.raises(RuntimeError, match="invalid pin"):
        asyncio.run(_broker(fyers_refresh_token="R1", fyers_pin="9999").refresh_access_token())
