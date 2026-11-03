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
    trim = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_trim_shadow_journal"
            or isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == "SHADOW_DETAIL_MAX_BYTES"]
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setenv("MACD_SHADOW_LANES_URL", "http://shadow-lanes:8430/")
    import os
    import json
    namespace = dict(os=os, json=json, HTTPException=HTTPException,
                     engine=SimpleNamespace(settings=SimpleNamespace(api_token="test-only")))
    exec(compile(ast.Module(body=[*trim, function], type_ignores=[]), str(path), "exec"), namespace)
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
    for name in ("shadow_lane_snapshot", "shadow_lane_archive", "retained_desk_read", "local_decision_status"):
        route = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
        assert any("Depends(authorize)" in ast.unparse(d) and "app.get" in ast.unparse(d) for d in route.decorator_list)


def test_retained_proxy_forwards_only_authenticated_reads_and_scope(monkeypatch):
    import os
    from fastapi import Request, Response
    path=Path(__file__).resolve().parents[1]/'src/macd_trader/app.py'
    function=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.AsyncFunctionDef) and n.name=='retained_desk_read')
    function.decorator_list=[]
    seen=[]
    def handler(request):
        seen.append(request)
        return httpx.Response(503,json={'detail':'Retained dataset unavailable'})
    real_client=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real_client(transport=httpx.MockTransport(handler),**kw))
    monkeypatch.setenv('MACD_SHADOW_LANES_URL','http://shadow-lanes:8430')
    namespace=dict(os=os,HTTPException=HTTPException,Request=Request,Response=Response,
                   engine=SimpleNamespace(settings=SimpleNamespace(api_token='isolated-test-token')))
    exec(compile(ast.Module(body=[function],type_ignores=[]),str(path),'exec'),namespace)
    request=Request({'type':'http','query_string':b'scope=retained&limit=20'})
    read=namespace['retained_desk_read']
    response=asyncio.run(read('vanguard/paper',request))
    assert response.status_code==503
    assert seen[0].method=='GET' and seen[0].headers['X-Macd-Token']=='isolated-test-token'
    assert seen[0].url.params['scope']=='retained'
    for invalid in ('orders','https://other.example/api','vanguard/../orders'):
        with pytest.raises(HTTPException):asyncio.run(read(invalid,request))
    assert len(seen)==1


def test_an_oversized_journal_detail_is_trimmed_for_the_live_view_only(monkeypatch):
    big = {"reason": "readiness", "blockers": [f"b{k}" for k in range(50)], "universe": ["x" * 100] * 1000, "ok": True}
    body = {"journal": [{"at": "t1", "kind": "readiness", "detail": big}, {"at": "t2", "kind": "x", "detail": {"reason": "r"}}]}
    read = proxy(monkeypatch, lambda request: httpx.Response(200, json=body))
    live = asyncio.run(read("cbe"))
    detail = live["journal"][0]["detail"]
    assert detail["reason"] == "readiness" and detail["ok"] is True and len(detail["blockers"]) == 20
    assert "universe" not in detail and detail["detail_truncated_bytes"] > 100_000
    assert live["journal"][1]["detail"] == {"reason": "r"}
    assert "universe" in asyncio.run(read("cbe", archive=True))["journal"][0]["detail"]
