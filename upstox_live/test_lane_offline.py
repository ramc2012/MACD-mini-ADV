import os, sys, json, importlib
from datetime import datetime, timezone, timedelta
import tempfile; S = tempfile.mkdtemp()
from pathlib import Path
trade_token_path = Path(S) / "trading-token"
analytics_token_path = Path(S) / "analytics-token"
os.environ.update(STATE_DIR=S + "/state", UPSTOX_ORDER_TOKEN_FILE=str(trade_token_path),
                  UPSTOX_TOKEN_FILE=str(analytics_token_path), ORDER_WINDOW="")  # window gate: section 9
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lane

# Account reads and order calls use the same daily token, including after rotation.
# If it is missing, there is no network request and no fallback to the analytics token.
analytics_token_path.write_text("mock-analytics-token")
original_http = lane.http
original_cache = lane.cache.copy()
auth_calls = []
def intercept_http(method, url, headers, body=None):
    auth_calls.append((method, url, headers["Authorization"]))
    return {"data": {"equity": {"available_margin": 1000}}} if "funds" in url else {"data": []}

lane.http = intercept_http
lane.read_upstox()
lane.probe_order_api()
assert lane.cache["read_error"].startswith("Upstox trading token is unavailable")
assert lane.cache["order_api"]["ok"] is False and not auth_calls
trade_token_path.write_text("mock-trading-token")
lane.read_upstox()
assert lane.cache["read_error"] is None
assert len(auth_calls) == 4 and all(row[0] == "GET" and row[2] == "Bearer mock-trading-token" for row in auth_calls)
trade_token_path.with_suffix(".new").write_text("mock-trading-token-rotated")
trade_token_path.with_suffix(".new").replace(trade_token_path)
lane.upstox("POST", lane.ORDER_HOST, "/v3/order/place")
assert auth_calls[-1][0] == "POST" and auth_calls[-1][2] == "Bearer mock-trading-token-rotated"
# A changed public IP: Upstox answers UDAPI1154 with both addresses. The probe must report "blocked" (it used to
# count any answered request as reachable), and the reason must name the IP to register.
MISMATCH = ('POST /v3/order/place -> 403: {"status":"error","errors":[{"errorCode":"UDAPI1154","message":"Access to this API '
            'is blocked due to static IP restrictions. Reason: The configured static IP (198.51.100.7) does not match the '
            'request origin IP (203.0.113.9)."}]}')
def refuse_http(method, url, headers, body=None):
    raise RuntimeError(MISMATCH)
lane.http = refuse_http
lane.cache.pop("order_api", None)
lane.probe_order_api()
assert lane.cache["order_api"]["ok"] is False, lane.cache["order_api"]
assert "203.0.113.9" in lane.cache["order_api"]["detail"] and "198.51.100.7" in lane.cache["order_api"]["detail"]
assert lane.ip_block("-> 401: UDAPI1221 not registered").endswith("(UDAPI1221)") and lane.ip_block("order not found") is None
lane.http = original_http
lane.cache.clear()
lane.cache.update(original_cache)

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

# 9. (9 Nov 2026) Upstox's zone-less fill time must not crash the loop; a position the MACD lane never held is sold
#    after MACD_ADOPT_SECONDS; nothing is sent outside the order window; a failing step still saves state
L.set_armed(True, "test"); L.state["orders"] = {}; L.state["positions"] = {}
assert L.parse_time("2026-11-09 14:45:16").tzinfo is not None
L.state["positions"]["NSE:ORPHAN26NOV970CE"] = {"symbol": "NSE:ORPHAN26NOV970CE", "instrument": "NSE_FO|77", "quantity": 700,
    "average_price": 17.0, "lot_size": 700, "tick": 0.05, "opened_at": "2026-11-09 19:16:30", "macd_seen": False,
    "realized": 0.0, "bought": 700}
L.state["orders"]["o77"] = {"order_id": "o77", "symbol": "NSE:ORPHAN26NOV970CE", "instrument": "NSE_FO|77", "side": "BUY",
    "quantity": 700, "price": 17.25, "placed_at": (datetime.now(timezone.utc) - timedelta(minutes=30)).astimezone(L.IST).isoformat(timespec="seconds"),
    "open": False, "status": "complete"}
L.save_state(L.state); L.state.update(L.load_state())       # restart: the naive stamp is replaced by the BUY's placed_at
assert "+05:30" in L.state["positions"]["NSE:ORPHAN26NOV970CE"]["opened_at"]
L.cache["positions"] = [{"instrument_token": "NSE_FO|77", "last_price": 15.4}]
world["signals"] = []
L.ORDER_WINDOW = ("00:00", "00:00") if datetime.now(L.IST).strftime("%H:%M") != "00:00" else ("23:59", "23:59")
n = len(sent); L.step(); assert len(sent) == n, "nothing is sent outside the order window"
L.ORDER_WINDOW = None
L.step(); assert [(b["instrument_token"], b["transaction_type"]) for _, b in sent[n:]] == [("NSE_FO|77", "SELL")]
saved = []
orig_step, orig_save = L.step, L.save_state
L.step = lambda: (_ for _ in ()).throw(RuntimeError("boom")); L.save_state = lambda s: saved.append(1)
L.LOOP_SECONDS = 0; L.READ_SECONDS = 1e9
class OnePass:                      # the loop waits on the stop event between steps (10 Nov 2026): end it after one
    def is_set(self): return False
    def wait(self, _): raise KeyboardInterrupt
orig_stopping, L.stopping = L.stopping, OnePass()
try:
    L.loop()
except KeyboardInterrupt:
    pass
L.stopping, L.step, L.save_state = orig_stopping, orig_step, orig_save
assert saved, "state is saved even when step() fails"
# a fill recovered by hand ("recovered|<order id>") is not counted again when Upstox's trade list is read
L.state["orders"]["o88"] = {"order_id": "o88", "symbol": "NSE:REC26NOV100CE", "instrument": "NSE_FO|88", "side": "BUY",
    "quantity": 100, "price": 10.0, "placed_at": L.iso(), "open": False, "status": "complete"}
L.state["fills"]["recovered|o88"] = {"order_id": "o88", "symbol": "NSE:REC26NOV100CE", "side": "BUY", "quantity": 100, "price": 10.0}
L.state["positions"]["NSE:REC26NOV100CE"] = {"symbol": "NSE:REC26NOV100CE", "instrument": "NSE_FO|88", "quantity": 100,
    "average_price": 10.0, "opened_at": L.iso(), "macd_seen": False, "realized": 0.0, "bought": 100}
L.cache["trades"] = [{"order_id": "o88", "trade_id": "T88", "quantity": 100, "average_price": 10.0, "transaction_type": "BUY"}]
L.record_fills()
assert L.state["positions"]["NSE:REC26NOV100CE"]["quantity"] == 100 and "T88|o88" in L.state["fills"]
# owner-managed: handed over, the lane never exits it, never re-enters it, and a re-read trade does not rebuild it
L.state["orders"]["o99"] = {"order_id": "o99", "symbol": "NSE:OWN26NOV970CE", "instrument": "NSE_FO|99", "side": "BUY",
    "quantity": 700, "price": 17.25, "placed_at": L.iso(), "open": False, "status": "complete"}
L.state["positions"]["NSE:OWN26NOV970CE"] = {"symbol": "NSE:OWN26NOV970CE", "instrument": "NSE_FO|99", "quantity": 700,
    "average_price": 17.0, "opened_at": "2026-11-01T10:00:00+05:30", "macd_seen": False, "realized": 0.0, "bought": 700}
L.hand_to_owner("NSE:OWN26NOV970CE", "test")
L.cache["trades"] = [{"order_id": "o99", "trade_id": "T99", "quantity": 700, "average_price": 17.0, "transaction_type": "BUY"}]
L.record_fills(); assert "NSE:OWN26NOV970CE" not in L.state["positions"]
L.cache["positions"] = [{"instrument_token": "NSE_FO|99", "last_price": 15.4}]
L.PREFER_WAIT = 0
world["signals"] = [fresh_now(199, "NSE:OWN26NOV970CE", 15.0, 1)]
n = len(sent); L.step(); assert len(sent) == n, "owner-managed contract: no exit, no entry"
assert L.state["decisions"][-1]["reason"] == "owner manages this contract"
assert L.snapshot()["owner_managed"][0]["symbol"] == "NSE:OWN26NOV970CE"
L.set_armed(False, "test")
print("naive fill time + order window + save-on-failure + recovered fill + owner-managed OK")
lane = L
# 12. docker stop (SIGTERM): the running step finishes and saves, then the process leaves with 0; no new step starts
import threading, time as _time
left = []
lane.stopping.clear(); os.remove(lane.STATE_FILE) if os.path.exists(lane.STATE_FILE) else None
lane.lock.acquire()                                    # a step is in progress
lane.stop_cleanly(leave=left.append); _time.sleep(0.3)
assert lane.stopping.is_set() and not left and not os.path.exists(lane.STATE_FILE), "must wait for the running step"
lane.lock.release(); _time.sleep(0.5)
assert left == [0] and os.path.exists(lane.STATE_FILE), left
lane.stop_cleanly(leave=left.append); _time.sleep(0.2); assert left == [0], "a second signal does nothing"
calls = []; real_step = lane.step; lane.step = lambda: calls.append(1); lane.loop(); lane.step = real_step
assert not calls, "no step after the stop signal"
lane.stopping.clear(); lane.STOP_WAIT_SECONDS = 0.3; left.clear(); os.remove(lane.STATE_FILE)
holder = threading.Thread(target=lambda: (lane.lock.acquire(), _time.sleep(1.2), lane.lock.release())); holder.start(); _time.sleep(0.1)
lane.stop_cleanly(leave=left.append); _time.sleep(0.7)
assert left == [1] and not os.path.exists(lane.STATE_FILE), "a stuck step: exit 1, no save over it"
holder.join(); lane.stopping.clear()
print("stop: waits for the running step, saves, exits 0; stuck step exits 1 without saving")


# 13. (17 Nov 2026) owner trades: an option bought by hand is adopted after two reads and managed with the MACD
#     lane's price exits (or follows the MACD paper lane if it holds it); a lane position sold by hand is recorded as
#     closed by the owner and no SELL is sent; units added by hand join the position; lane fills are never adopted.
L = lane
L.lock = threading.RLock()   # section 12's stop test leaves the old lock held by its finished stop thread (os._exit in production)
L.set_armed(False, "test")
L.state.update(orders={}, positions={}, fills={}, decisions=[], realized=0.0, closed=[])
L.cache.pop("reconcile_pending", None)
L.ORDER_WINDOW = None
world.update(signals=[], macd=[], orders=[], trades=[], margin=30000.0)
lots.update({"NSE:VOLT26NOV1160CE": (375, "NSE_FO|500")})
L.resolve = lambda s: ({"upstox": lots[s][1], "lot_size": lots[s][0], "tick_size": 0.05,
                        "expiry_ms": (datetime.now(timezone.utc) + timedelta(days=8)).timestamp() * 1000} if s in lots else None)
volt = {"instrument_token": "NSE_FO|500", "exchange": "NFO", "trading_symbol": "VOLT26NOV1160CE", "product": "D",
        "quantity": 1500, "average_price": 19.44, "last_price": 18.0}
weekly = {"instrument_token": "NSE_FO|600", "exchange": "NFO", "trading_symbol": "NIFTY2611824000CE", "product": "I",
          "quantity": 65, "average_price": 100.0, "last_price": 100.0}
fut = {"instrument_token": "NSE_FO|700", "exchange": "NFO", "trading_symbol": "VOLT26NOVFUT", "product": "D", "quantity": 375,
       "average_price": 1150.0, "last_price": 1150.0}
handed = {"instrument_token": "NSE_FO|119232", "exchange": "NFO", "trading_symbol": "HIND26NOV970CE", "product": "D",
          "quantity": 700, "average_price": 17.0, "last_price": 17.0}
L.state["owner_managed"] = {"NSE:HIND26NOV970CE": {"instrument": "NSE_FO|119232", "since": L.iso(), "by": "test"}}
world["positions"] = [dict(volt), dict(weekly), dict(fut), dict(handed)]
L.read_upstox(); assert not L.state["positions"], "one read is not enough"
L.read_upstox()
assert set(L.state["positions"]) == {"NSE:VOLT26NOV1160CE", "NSE:NIFTY2611824000CE"}, L.state["positions"].keys()
v = L.state["positions"]["NSE:VOLT26NOV1160CE"]
assert v["origin"] == "owner" and v["quantity"] == 1500 and v["average_price"] == 19.44 and v["lot_size"] == 375
assert L.state["positions"]["NSE:NIFTY2611824000CE"]["lot_size"] is None, "Upstox name is not a Fyers name: label only"
assert [d["kind"] for d in L.state["decisions"]].count("ADOPTED") == 2
# a signal on the adopted contract does not buy more
open(L.ARM_FILE, "w").write("yes"); L.cache["order_api"] = {"ok": True}; L.PREFER_WAIT = 0
world["signals"] = [fresh_now(300, "NSE:VOLT26NOV1160CE", 18.0, 1)]
n = len(sent); L.step(); assert len(sent) == n and L.state["decisions"][-1]["reason"].startswith("already held")
# above the stop: nothing; at the 30% stop: SELL of the whole quantity with the position's product
world["signals"] = []
L.step(); assert len(sent) == n
world["positions"][0]["last_price"] = 13.6; L.read_upstox(); L.step()
assert sent[-1][1]["transaction_type"] == "SELL" and sent[-1][1]["quantity"] == 1500 and sent[-1][1]["product"] == "D"
assert sent[-1][1]["instrument_token"] == "NSE_FO|500" and "hard stop" in L.state["orders"][f"OID{len(sent)}"]["reason"]
# the weekly (intraday product): trail arms at +30%, sells when 25% off the peak, with product I
world["positions"][1]["last_price"] = 140.0; L.read_upstox(); n = len(sent); L.step(); assert len(sent) == n
world["positions"][1]["last_price"] = 105.0; L.read_upstox(); L.step()
assert sent[-1][1]["instrument_token"] == "NSE_FO|600" and sent[-1][1]["product"] == "I" and sent[-1][1]["quantity"] == 65
# the lane's own SELL fills: recorded as a lane fill (not as an owner close), the position closes
oid = f"OID{len(sent)}"
world["orders"] = [{"order_id": oid, "status": "complete", "filled_quantity": 65}]
world["trades"] = [{"order_id": oid, "trade_id": "TW", "instrument_token": "NSE_FO|600", "quantity": 65, "average_price": 104.9, "transaction_type": "SELL"}]
world["positions"][1]["quantity"] = 0
L.read_upstox(); L.read_upstox()
assert "NSE:NIFTY2611824000CE" not in L.state["positions"] and not [d for d in L.state["decisions"] if d["kind"] == "CLOSED_BY_OWNER"]
assert abs(L.state["closed"][-1]["exit_price"] - 104.9) < 1e-9
# follows the MACD paper lane when it holds the contract: no price exit while it holds, out when it closes
L.state["orders"] = {}; L.state["positions"]["NSE:VOLT26NOV1160CE"]["peak"] = 19.44
world["positions"][0]["last_price"] = 13.0; world["macd"] = ["NSE:VOLT26NOV1160CE"]
L.read_upstox(); n = len(sent); L.step(); assert len(sent) == n, "MACD lane holds it: its exits rule"
world["macd"] = []; L.step(); assert sent[-1][1]["instrument_token"] == "NSE_FO|500" and L.state["orders"][f"OID{len(sent)}"]["reason"] == "MACD lane closed it"
# the owner sells it by hand meanwhile: the lane's SELL is cancelled by timeout elsewhere; here the account goes flat
L.state["orders"] = {}
world.update(orders=[], trades=[{"order_id": "MANUAL1", "trade_id": "M1", "instrument_token": "NSE_FO|500", "quantity": 1500,
                                 "average_price": 14.2, "transaction_type": "SELL"}])
world["positions"][0].update(quantity=0, day_sell_price=14.1)
L.read_upstox(); assert "NSE:VOLT26NOV1160CE" in L.state["positions"]
L.read_upstox(); assert "NSE:VOLT26NOV1160CE" not in L.state["positions"]
row = [d for d in L.state["decisions"] if d["kind"] == "CLOSED_BY_OWNER"][-1]
assert row["quantity"] == 1500 and row["price"] == 14.2 and row["price_from"] == "the owner's trades today"
assert L.state["closed"][-1]["closed_by"] == "owner"
n = len(sent); L.step(); assert len(sent) == n, "no SELL for a position the owner closed"
# a lane position: the owner adds units, then sells part by hand
L.state["positions"]["NSE:CHEAP26NOV100CE"] = {"symbol": "NSE:CHEAP26NOV100CE", "instrument": "NSE_FO|1", "quantity": 500,
    "average_price": 20.0, "lot_size": 500, "tick": 0.05, "opened_at": L.iso(), "macd_seen": True, "realized": 0.0, "bought": 500}
world["macd"] = ["NSE:CHEAP26NOV100CE"]
world["trades"] = [{"order_id": "MANUAL2", "trade_id": "M2", "instrument_token": "NSE_FO|1", "quantity": 500, "average_price": 22.0, "transaction_type": "BUY"}]
world["positions"] = [{"instrument_token": "NSE_FO|1", "exchange": "NFO", "trading_symbol": "CHEAP26NOV100CE", "product": "D",
                       "quantity": 1000, "average_price": 21.0, "last_price": 23.0}]
L.read_upstox(); L.read_upstox()
c = L.state["positions"]["NSE:CHEAP26NOV100CE"]
assert c["quantity"] == 1000 and abs(c["average_price"] - 21.0) < 1e-9 and c["owner_added"] == 500
world["trades"].append({"order_id": "MANUAL3", "trade_id": "M3", "instrument_token": "NSE_FO|1", "quantity": 500, "average_price": 25.0, "transaction_type": "SELL"})
world["positions"][0]["quantity"] = 500
L.read_upstox(); L.read_upstox()
assert L.state["positions"]["NSE:CHEAP26NOV100CE"]["quantity"] == 500 and abs(L.state["realized"] - ((14.2 - 19.44) * 1500 + (104.9 - 100) * 65 + (25 - 21) * 500)) < 0.01, L.state["realized"]
# a lane BUY whose fill reaches the positions list before the trade list is never adopted as the owner's
L.state["positions"] = {}
L.state["orders"]["LANEBUY"] = {"order_id": "LANEBUY", "symbol": "NSE:VOLT26NOV1160CE", "instrument": "NSE_FO|500", "side": "BUY",
    "quantity": 375, "price": 18.0, "placed_at": "2026-11-17T09:20:00+05:30", "placed_ts": 1.0, "open": False, "status": "complete", "filled": 375}
world.update(trades=[], orders=[{"order_id": "LANEBUY", "status": "complete", "filled_quantity": 375}])
world["positions"] = [{**volt, "quantity": 375}]
for _ in range(3): L.read_upstox()
assert not L.state["positions"], "unrecorded lane fill: not adopted"
world["trades"] = [{"order_id": "LANEBUY", "trade_id": "LB", "instrument_token": "NSE_FO|500", "quantity": 375, "average_price": 18.0, "transaction_type": "BUY"}]
for _ in range(3): L.read_upstox()
assert list(L.state["positions"]) == ["NSE:VOLT26NOV1160CE"] and L.state["positions"]["NSE:VOLT26NOV1160CE"].get("origin") is None
# no row at all in the account: three reads before it counts as closed
world.update(positions=[], trades=[])
L.read_upstox(); L.read_upstox(); assert L.state["positions"]
L.read_upstox(); assert not L.state["positions"] and L.state["decisions"][-1]["price_from"] in ("the lane's last mark", "last price")
# expiry day: an adopted position is sold from the flatten time
lots["NSE:VOLT26NOV1160CE"] = (375, "NSE_FO|500")
L.resolve = lambda s: {"upstox": "NSE_FO|500", "lot_size": 375, "tick_size": 0.05, "expiry_ms": datetime.now(timezone.utc).timestamp() * 1000}
L.state["orders"] = {}; world["positions"] = [dict(volt)]
L.read_upstox(); L.read_upstox(); assert L.state["positions"]["NSE:VOLT26NOV1160CE"]["origin"] == "owner"
L.EXPIRY_FLATTEN_IST = "23:59" if datetime.now(L.IST).strftime("%H:%M") < "23:59" else "23:59"
n = len(sent); L.step()
if datetime.now(L.IST).strftime("%H:%M") < "23:59":
    assert len(sent) == n, "before the flatten time: held"
L.EXPIRY_FLATTEN_IST = "00:00"; L.step(); assert "expiry day" in L.state["orders"][f"OID{len(sent)}"]["reason"]
L.set_armed(False, "test")
print("owner trades: adopt + manage, owner closes/adds acknowledged, lane fills never adopted OK")
