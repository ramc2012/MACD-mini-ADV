import os, sys, json, importlib
from datetime import datetime, timezone, timedelta
import tempfile; S = tempfile.mkdtemp()
os.environ.update(STATE_DIR=S + "/state", UPSTOX_TOKEN_FILE="/nonexistent")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lane
sent = []
now = datetime.now(timezone.utc)
sig = lambda i, sym, price, ago=5: {"signal_id": f"s{i}", "symbol": sym, "side": "BUY", "kind": "PREMIUM_MACD_ZERO_CROSS_UP", "price": price,
                                    "timestamp": (now - timedelta(seconds=ago)).isoformat()}
world = {"signals": [], "macd": [], "trades": [], "orders": [], "positions": [], "margin": 30000.0}
lane.state["started_at"] = (now - timedelta(minutes=1)).isoformat()
def engine(path):
    return world["signals"] if path.startswith("/api/signals") else {"positions": [{"symbol": s} for s in world["macd"]]}
def upstox(method, host, path, body=None, query=None):
    if method == "POST":
        oid = f"OID{len(sent)+1}"; sent.append((path, body)); return {"data": {"order_ids": [oid]}}
    if method == "DELETE": sent.append((path, query)); return {"data": {}}
    if "funds" in path: return {"data": {"equity": {"available_margin": world["margin"]}}}
    if "retrieve-all" in path: return {"data": world["orders"]}
    if "trades" in path: return {"data": world["trades"]}
    return {"data": world["positions"]}
lots = {"NSE:CHEAP26NOV100CE": (500, "NSE_FO|1"), "NSE:DEAR26NOV900CE": (300, "NSE_FO|2")}
lane.engine, lane.upstox = engine, upstox
lane.PREFER_WAIT = 0       # sections 1-7 test entry mechanics; the weekly-index preference is tested in 8
lane.resolve = lambda s: {"upstox": lots[s][1], "lot_size": lots[s][0], "tick_size": 0.05}
# 1. not armed: offered option signals are journalled as skipped, nothing is sent and no would-be order is kept;
#    spot, SELL and re-entry signals are not this lane's and leave no record at all
world["signals"] = [sig(1, "NSE:DEAR26NOV900CE", 120.0, 8), sig(2, "NSE:CHEAP26NOV100CE", 20.0, 4),
                    {**sig(90, "NSE:CHEAP-EQ", 20.0, 4)}, {**sig(91, "NSE:CHEAP26NOV100CE", 20.0, 4), "side": "SELL"},
                    {**sig(92, "NSE:CHEAP26NOV100CE", 20.0, 3), "kind": "PREMIUM_MACD_REENTRY"}]
lane.read_upstox(); lane.step()
print("not armed:", [(d["kind"], d.get("symbol"), d.get("reason")) for d in lane.state["decisions"]], "sent", sent)
assert not sent and not lane.state["orders"]
assert [(d["kind"], d["symbol"]) for d in lane.state["decisions"]] == [("SKIP", "NSE:DEAR26NOV900CE"), ("SKIP", "NSE:CHEAP26NOV100CE")]
assert all(d["reason"].startswith("lane not armed") for d in lane.state["decisions"])
# 2. armed: new cheap signal -> real order (fake), fill, MACD holds, then MACD closes -> sell
os.makedirs(S + "/state", exist_ok=True); open(lane.ARM_FILE, "w").write("yes")
lane.state["orders"] = {}
world["signals"].append(sig(3, "NSE:CHEAP26NOV100CE", 21.0, 2))
lane.step(); print("armed entry sent:", sent[-1]); assert sent[-1][1]["transaction_type"] == "BUY" and sent[-1][1]["quantity"] == 500 and sent[-1][1]["price"] == 21.45
world["orders"] = [{"order_id": "OID1", "status": "complete", "filled_quantity": 500}]
world["trades"] = [{"order_id": "OID1", "trade_id": "T1", "quantity": 500, "average_price": 21.4, "transaction_type": "BUY"}]
world["positions"] = [{"instrument_token": "NSE_FO|1", "last_price": 25.0, "quantity": 500}]
world["macd"] = ["NSE:CHEAP26NOV100CE"]
lane.read_upstox(); lane.step()
snap = lane.snapshot(); print("held:", [(p["symbol"], p["quantity"], p["average_price"], p["unrealized_pnl"]) for p in snap["positions"]], "free", snap["free"])
world["macd"] = []
lane.step(); print("exit sent:", sent[-1]); assert sent[-1][1]["transaction_type"] == "SELL" and sent[-1][1]["quantity"] == 500 and sent[-1][1]["price"] == 24.25
world["orders"].append({"order_id": "OID2", "status": "complete", "filled_quantity": 500})
world["trades"].append({"order_id": "OID2", "trade_id": "T2", "quantity": 500, "average_price": 24.3, "transaction_type": "SELL"})
lane.read_upstox(); lane.step()
snap = lane.snapshot(); print("after exit: realized", snap["realized"], "positions", snap["positions"], "closed", len(snap["closed"]))
assert abs(snap["realized"] - 1450) < 0.01 and not snap["positions"]
# 2b. armed: a re-entry signal is still not taken (MACD paper lane only until promoted)
n = len(sent); k = len(lane.state["decisions"])
world["signals"].append({**sig(93, "NSE:DEAR26NOV900CE", 20.0, 1), "kind": "PREMIUM_MACD_REENTRY"})
lane.step(); assert len(sent) == n and len(lane.state["decisions"]) == k
# 3. old signal (before start) and stale ones ignored; a second identical signal while ordering skipped
world["signals"].append(sig(4, "NSE:CHEAP26NOV100CE", 20.0, 600))
n = len(sent); lane.step(); assert len(sent) == n
print("OK, decisions:", [d["kind"] for d in lane.state["decisions"]])
# 4. owner controls: kill cancels a pending real entry and disarms; arm refused while the order API is blocked;
#    exit-all sells a held position even while disarmed; HTTP refuses other origins.
world["signals"].append(sig(5, "NSE:CHEAP26NOV100CE", 20.0, 1))
lane.step(); pending = [o for o in lane.state["orders"].values() if o["side"] == "BUY" and o.get("open")]
assert pending, "entry should be pending"
r = lane.set_armed(False, "test"); assert not lane.armed() and r["cancelled_entries"] == [pending[0]["order_id"]] and sent[-1][0] == "/v3/order/cancel"
lane.state["positions"]["NSE:CHEAP26NOV100CE"] = {"symbol": "NSE:CHEAP26NOV100CE", "instrument": "NSE_FO|1", "quantity": 500, "average_price": 21.4,
                                                  "lot_size": 500, "tick": 0.05, "opened_at": now.isoformat(), "macd_seen": True, "realized": 0.0}
r = lane.exit_all("test"); assert r["sent"] == ["NSE:CHEAP26NOV100CE"] and sent[-1][1]["transaction_type"] == "SELL" and sent[-1][1]["price"] == 24.25
import threading, urllib.request, urllib.error
from http.server import ThreadingHTTPServer
srv = ThreadingHTTPServer(("127.0.0.1", 0), lane.Handler); threading.Thread(target=srv.serve_forever, daemon=True).start()
def post(path, body, origin):
    req = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/api/upstox-live{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **({"Origin": origin} if origin else {})})
    try:
        with urllib.request.urlopen(req) as resp: return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())
assert post("/arm", {"confirm": "ARM"}, "https://evil.example")[0] == 403
assert post("/arm", {"confirm": "ARM"}, None)[0] == 403
lane.cache["order_api"] = {"ok": False, "detail": "read-only token"}
assert post("/arm", {"confirm": "ARM"}, "http://localhost:3200")[0] == 409 and not lane.armed()
assert post("/arm", {}, "http://localhost:3200")[0] == 400
lane.cache["order_api"] = {"ok": True, "detail": "ok"}
assert post("/arm", {"confirm": "ARM"}, "http://localhost:3200")[0] == 200 and lane.armed()
assert post("/kill", {}, "http://127.0.0.1:3200")[0] == 200 and not lane.armed()
lane.ALLOWED_ORIGINS.add("http://100.119.170.94:3200")
assert post("/arm", {"confirm": "ARM"}, "http://100.119.170.94:3200")[0] == 200 and lane.armed()
assert post("/kill", {}, "http://100.119.170.94:3200")[0] == 200 and not lane.armed()
assert post("/arm", {"confirm": "ARM"}, "http://100.119.170.95:3200")[0] == 403
srv.shutdown()
print("controls OK")
# 5. the fund is Upstox's available margin: a top-up is used at the next read, entries sent since the read are
#    held back, a stale read blocks entries, and the optional cap still limits.
assert lane.CAPITAL == 0, "default: no lane cap"
lane.state["orders"], lane.state["positions"] = {}, {}
open(lane.ARM_FILE, "w").write("yes"); lane.cache["order_api"] = {"ok": True}
world.update(orders=[], trades=[], positions=[], macd=[], margin=30000.0)
lane.read_upstox()
world["signals"].append(sig(6, "NSE:DEAR26NOV900CE", 120.0, 1))     # 1 lot = 122.4 x 300 = 36,720
n = len(sent); lane.step(); assert len(sent) == n and lane.state["decisions"][-1]["kind"] == "SKIP_FUNDS"
world["margin"] = 50000.0; lane.read_upstox()                        # owner topped up
world["signals"].append(sig(7, "NSE:DEAR26NOV900CE", 120.0, 1))
lane.step(); assert sent[-1][1]["transaction_type"] == "BUY" and sent[-1][1]["quantity"] == 300, "top-up must be used"
assert abs(lane.free_funds() - (50000 - 36720)) < 0.01, lane.fund_status()
world["signals"].append(sig(8, "NSE:CHEAP26NOV100CE", 30.0, 1))     # 30.6 x 500 = 15,300 > 13,280 left
n = len(sent); lane.step(); assert len(sent) == n and lane.state["decisions"][-1]["kind"] == "SKIP_FUNDS"
world["margin"] = 13280.0; lane.read_upstox()                         # Upstox now shows the blocked margin
assert abs(lane.free_funds() - 13280) < 0.01, lane.fund_status()      # not subtracted twice
lane.cache["funds_at"] -= 1000
assert lane.fund_status()[0] == 0 and "old" in lane.fund_status()[1]
lane.read_upstox(); lane.CAPITAL = 20000.0
assert lane.fund_status()[1].startswith("lane cap"), lane.fund_status()
lane.CAPITAL = 0.0
# 6. arming is one-time: steps, day changes and a restart (re-import) leave it armed; only Kill removes it
for _ in range(3): lane.step()
lane.state["orders_today"] = {"day": "2000-01-01", "count": 0}; lane.step()
assert lane.armed()
del sys.modules["lane"]; import lane as lane2
lane2.recover_armed_since() if lane2.armed() else None
assert lane2.armed(), "restart must not disarm"
lane2.set_armed(False, "test"); assert not lane2.armed()
print("funds + arming OK")
# 7. not armed with a held position: the MACD exit is NOT sent, journalled once (no would-be order is kept)
lane2.state["positions"]["NSE:CHEAP26NOV100CE"] = {"symbol": "NSE:CHEAP26NOV100CE", "instrument": "NSE_FO|1", "quantity": 500,
    "average_price": 21.4, "lot_size": 500, "tick": 0.05, "opened_at": now.isoformat(), "macd_seen": True, "realized": 0.0}
lane2.engine, lane2.upstox = engine, upstox
lane2.cache["positions"] = [{"instrument_token": "NSE_FO|1", "last_price": 25.0}]
world["macd"] = []; n = len(sent); lane2.state["orders"] = {}
lane2.step(); lane2.step()
assert len(sent) == n and [d["kind"] for d in lane2.state["decisions"]].count("EXIT_NOT_SENT") == 1
assert not any(o.get("dry") for o in lane2.state["orders"].values())
print("not-armed exit OK")

# 8. owner settings: side filter (calls only / puts only) and weekly-index preference
L = lane2
lots.update({"NSE:NIFTY26N1024000CE": (65, "NSE_FO|9"), "NSE:NIFTY26N1024000PE": (65, "NSE_FO|10"), "NSE:HAL26NOV4950PE": (150, "NSE_FO|11")})
L.resolve = lambda s: {"upstox": lots[s][1], "lot_size": lots[s][0], "tick_size": 0.05}
open(L.ARM_FILE, "w").write("yes"); L.cache["order_api"] = {"ok": True}
L.state.update(orders={}, positions={}, decisions=[], started_at=(now - timedelta(minutes=5)).isoformat())
world.update(macd=[], margin=30000.0); L.read_upstox()
fresh_now = lambda i, sym, price, ago: {**sig(i, sym, price, 0), "timestamp": (datetime.now(timezone.utc) - timedelta(seconds=ago)).isoformat()}
assert L.weekly_index("NSE:NIFTY26N1024000CE") and L.weekly_index("NSE:NIFTY26NOV24000CE") and L.weekly_index("BSE:SENSEX26O0172500PE")
assert not L.weekly_index("NSE:BANKNIFTY26NOV54500CE") and not L.weekly_index("NSE:HAL26NOV4950CE")
# calls only: a put signal leaves no record, a call is taken
L.save_lane_settings("CE", "test")
world["signals"] = [fresh_now(100, "NSE:HAL26NOV4950PE", 50.0, 30), fresh_now(101, "NSE:CHEAP26NOV100CE", 20.0, 30)]
n = len(sent); L.step()
assert [b["instrument_token"] for _, b in sent[n:]] == ["NSE_FO|1"], sent[n:]
assert not [d for d in L.state["decisions"] if d.get("symbol") == "NSE:HAL26NOV4950PE"]
# puts only
L.save_lane_settings("PE", "test"); L.state["orders"] = {}
world["signals"] = [fresh_now(102, "NSE:CHEAP26NOV100CE", 20.0, 30), fresh_now(103, "NSE:HAL26NOV4950PE", 50.0, 30)]
n = len(sent); L.step(); assert [b["instrument_token"] for _, b in sent[n:]] == ["NSE_FO|11"]
try:
    L.save_lane_settings("ALL", "test"); raise AssertionError("bad value accepted")
except ValueError:
    pass
# weekly index first: an OLDER stock signal and a NIFTY weekly signal compete for money that fits one lot
L.save_lane_settings("BOTH", "test"); L.state["orders"] = {}; L.PREFER_WAIT = 10
world["margin"] = 12000.0; L.read_upstox()
world["signals"] = [fresh_now(104, "NSE:CHEAP26NOV100CE", 20.0, 3)]              # 20.4 x 500 = 10,200
n = len(sent); L.step(); assert len(sent) == n, "a stock signal waits PREFER_WAIT for a weekly-index one"
world["signals"].append(fresh_now(105, "NSE:NIFTY26N1024000CE", 150.0, 1))     # 153 x 65 = 9,945
L.step(); assert [b["instrument_token"] for _, b in sent[n:]] == ["NSE_FO|9"], "weekly index first"
world["signals"] = [{**s, "timestamp": (datetime.now(timezone.utc) - timedelta(seconds=12)).isoformat()} if s["signal_id"] == "s104" else s
                    for s in world["signals"]]
L.step()
assert len(sent) == n + 1 and L.state["decisions"][-1]["kind"] == "SKIP_FUNDS" and L.state["decisions"][-1]["symbol"] == "NSE:CHEAP26NOV100CE"
# the HTTP switch
srv = ThreadingHTTPServer(("127.0.0.1", 0), L.Handler); threading.Thread(target=srv.serve_forever, daemon=True).start()
def post2(path, body, origin):
    req = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/api/upstox-live{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **({"Origin": origin} if origin else {})})
    try:
        with urllib.request.urlopen(req) as resp: return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())
assert post2("/settings", {"sides": "CE"}, "https://evil.example")[0] == 403
assert post2("/settings", {"sides": "XX"}, "http://localhost:3200")[0] == 400
code, body = post2("/settings", {"sides": "CE"}, "http://localhost:3200")
assert code == 200 and body["snapshot"]["settings"]["sides"] == "CE" and "calls only" in body["snapshot"]["rules"]["signals"]
srv.shutdown(); L.set_armed(False, "test")
print("side filter + weekly-index preference OK")
