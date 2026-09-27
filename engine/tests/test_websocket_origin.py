"""The order-taking stream socket must refuse other websites' pages."""

import asyncio
import importlib
from types import SimpleNamespace

import pytest

from macd_trader.config import Settings


class FakeSocket:
    def __init__(self, origin=None, token=""):
        self.headers = {"origin": origin} if origin else {}
        self.query_params = {"token": token} if token else {}
        self.closed_with = None
        self.accepted = False

    async def close(self, code):
        self.closed_with = code

    async def accept(self):
        self.accepted = True


@pytest.fixture
def app_module(monkeypatch):
    module = importlib.import_module("macd_trader.app")
    fake = SimpleNamespace(settings=Settings(allowed_origins_csv="https://desk.example.ts.net/"))
    monkeypatch.setattr(module, "engine", fake)
    return module, fake


@pytest.mark.parametrize("origin", [
    None,                              # curl, scripts: governed by the token
    "http://localhost:3200",           # the parallel terminal through nginx
    "http://127.0.0.1:3100",
    "http://[::1]:5173",
    "https://desk.example.ts.net",     # configured extra origin
])
def test_trusted_origins_connect(app_module, origin):
    module, _ = app_module
    socket = FakeSocket(origin)
    assert asyncio.run(module.websocket_authorize(socket)) is True
    assert socket.accepted


@pytest.mark.parametrize("origin", [
    "https://evil.example",
    "http://localhost.evil.example:3200",
    "http://rebound.attacker.test:3200",  # DNS rebinding keeps the attacker's name
    "null",
])
def test_foreign_origins_are_refused(app_module, origin):
    module, _ = app_module
    socket = FakeSocket(origin)
    assert asyncio.run(module.websocket_authorize(socket)) is False
    assert socket.closed_with == 4403 and not socket.accepted


def test_token_is_still_checked_first(app_module):
    module, fake = app_module
    fake.settings = Settings(api_token="secret")
    socket = FakeSocket("http://localhost:3200", token="wrong")
    assert asyncio.run(module.websocket_authorize(socket)) is False
    assert socket.closed_with == 4401
