"""Exercise the real GET proxy without importing the trading application's global engine."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
import httpx
import pytest


def proxy(monkeypatch, handler):
    # Load only this transport function: no app import can touch runtime files or credentials.
    path = Path(__file__).resolve().parents[1] / "src/macd_trader/app.py"
    tree = ast.parse(path.read_text())
    function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_shadow_lane_read")
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setenv("MACD_SHADOW_LANES_URL", "http://shadow-lanes:8430/")
    import os
    namespace = dict(os=os, HTTPException=HTTPException, engine=SimpleNamespace(settings=SimpleNamespace(api_token="test-only")))
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["_shadow_lane_read"]


def test_snapshot_and_history_are_fixed_authenticated_gets(monkeypatch):
    seen = []
    def handler(request):
        seen.append((request.method, request.url.path, request.headers.get("X-Macd-Token")))
        return httpx.Response(200, json={"shadow_only": True, "execution_enabled": False})
    read = proxy(monkeypatch, handler)
    assert asyncio.run(read("vanguard"))["execution_enabled"] is False
    asyncio.run(read("cbe", archive=True))
    assert seen == [("GET", "/api/shadow-lanes/vanguard", "test-only"), ("GET", "/api/shadow-lanes/cbe/archive", "test-only")]
    with pytest.raises(HTTPException) as error:
        asyncio.run(read("../orders"))
    assert error.value.status_code == 404 and len(seen) == 2


def test_upstream_error_body_is_not_exposed(monkeypatch):
    read = proxy(monkeypatch, lambda request: httpx.Response(500, text="private upstream detail"))
    with pytest.raises(HTTPException) as error:
        asyncio.run(read("vanguard"))
    assert error.value.status_code == 502 and "private" not in error.value.detail


def test_both_routes_require_the_existing_app_auth():
    path = Path(__file__).resolve().parents[1] / "src/macd_trader/app.py"
    tree = ast.parse(path.read_text())
    for name in ("shadow_lane_snapshot", "shadow_lane_archive"):
        route = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
        assert any("Depends(authorize)" in ast.unparse(d) and "app.get" in ast.unparse(d) for d in route.decorator_list)
