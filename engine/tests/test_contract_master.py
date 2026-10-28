"""Contract selection from the instrument master plus batched quotes.

The chain path cost ~430 paced REST calls (about five minutes) every morning.
On 1 Oct 2026 the master path chose 426 of the same 428 ATM legs (the other two
were a strike list with zero volume everywhere), every expiry, the SENSEX roll
and the NEXT series, in nine seconds.
"""
import asyncio
import json
from datetime import date, timedelta

from macd_trader.brokers import OptionChain, OptionChainEntry, OptionContractMetadata
from macd_trader.contracts import ContractSelector, OptionContract
from macd_trader.models import Tick

TODAY = date.today()
FRONT = (TODAY + timedelta(days=7)).isoformat()
NEXT = (TODAY + timedelta(days=14)).isoformat()
EXPIRING = TODAY.isoformat()
SBIN = "NSE:SBIN-EQ"
NIFTY = "NSE:NIFTY50-INDEX"


def _master(root, exchange, expiry, strikes, lot):
    return [
        OptionContractMetadata(f"{exchange}:{root}{expiry}{strike}{side}", root, side, float(strike), expiry, lot)
        for side in ("CE", "PE") for strike in strikes
    ]


class MasterBroker:
    def __init__(self, master, spots, volumes=None, quotes_fail=False):
        self.master = master
        self.spots = spots
        self.volumes = volumes or {}
        self.quotes_fail = quotes_fail
        self.quote_calls = []
        self.chain_calls = []

    async def option_master(self, exchanges):
        return [row for row in self.master if row.symbol.split(":")[0] in exchanges]

    async def quotes(self, symbols):
        self.quote_calls.append(list(symbols))
        if self.quotes_fail:
            raise RuntimeError("429 Too Many Requests")
        out = {}
        for symbol in symbols:
            if symbol in self.spots:
                out[symbol] = Tick(symbol, self.spots[symbol])
            else:
                out[symbol] = Tick(symbol, 1.0, self.volumes.get(symbol, 100))
        return out

    async def option_chain(self, symbol, expiry_token=None):
        self.chain_calls.append(symbol)
        entries = [OptionChainEntry(row.strike, row.option_type, row.symbol, volume=100)
                   for row in self.master if row.expiry == FRONT and row.underlying == "SBIN"]
        return OptionChain(FRONT, self.spots[symbol], entries)

    async def preopen_price(self, _symbol):
        return None

    async def lot_sizes(self, symbols):
        return {}


def _ladder(rows, prefix=""):
    return {(row.option_type, row.moneyness): row.strike for row in rows if row.moneyness.startswith(prefix)}


def test_master_ladder_matches_the_chain_rules_without_a_chain_call(tmp_path):
    master = _master("SBIN", "NSE", FRONT, range(1000, 1110, 10), 750)
    broker = MasterBroker(master, {SBIN: 1053.0})
    selector = ContractSelector(broker, str(tmp_path / "contracts.json"))

    rows = asyncio.run(selector.build([SBIN]))

    assert broker.chain_calls == []
    # Side-rounded ATM: calls round up, puts down; ITM/OTM are the neighbours.
    assert _ladder(rows) == {
        ("CE", "ITM"): 1050, ("CE", "ATM"): 1060, ("CE", "OTM"): 1070,
        ("PE", "ITM"): 1060, ("PE", "ATM"): 1050, ("PE", "OTM"): 1040,
    }
    assert {row.lot_size for row in rows} == {750} and {row.expiry for row in rows} == {FRONT}
    assert all(row.selection_price == 1053.0 for row in rows)
    assert [row.analysis_only for row in rows if row.moneyness == "ATM"] == [False, False]
    # Liquidity is quoted only for the five strikes per side the ATM rule reads.
    assert len(broker.quote_calls) == 2 and len(broker.quote_calls[1]) == 10
    assert selector.selection_source["master"] == 1 and selector.selection_source["chain"] == 0


def test_master_path_keeps_the_liquidity_tie_break(tmp_path):
    master = _master("SBIN", "NSE", FRONT, range(1000, 1110, 10), 750)
    busy = next(row.symbol for row in master if row.option_type == "CE" and row.strike == 1080)
    broker = MasterBroker(master, {SBIN: 1053.0}, volumes={busy: 1000})

    rows = asyncio.run(ContractSelector(broker, str(tmp_path / "c.json")).build([SBIN]))

    assert _ladder(rows)[("CE", "ATM")] == 1080
    assert _ladder(rows)[("CE", "OTM")] == 1090


def test_master_path_rolls_past_an_expiring_series_and_adds_next_for_indices(tmp_path):
    master = [
        *_master("NIFTY", "NSE", EXPIRING, range(24900, 25150, 50), 75),
        *_master("NIFTY", "NSE", FRONT, range(24900, 25150, 50), 75),
        *_master("NIFTY", "NSE", NEXT, range(24900, 25150, 50), 75),
    ]
    broker = MasterBroker(master, {NIFTY: 25010.0})
    selector = ContractSelector(broker, str(tmp_path / "c.json"))

    rows = asyncio.run(selector.build([NIFTY]))

    front = [row for row in rows if not row.moneyness.startswith("NEXT-")]
    nxt = [row for row in rows if row.moneyness.startswith("NEXT-")]
    assert {row.expiry for row in front} == {FRONT}
    assert {row.expiry for row in nxt} == {NEXT} and all(row.analysis_only for row in nxt)
    assert _ladder(nxt, "NEXT-")[("CE", "NEXT-ATM")] == 25050
    assert selector.rolled[NIFTY] == f"{EXPIRING} -> {FRONT}"


def test_failed_quotes_fall_back_to_the_chain(tmp_path):
    master = _master("SBIN", "NSE", FRONT, range(1000, 1110, 10), 750)
    broker = MasterBroker(master, {SBIN: 1053.0}, quotes_fail=True)
    selector = ContractSelector(broker, str(tmp_path / "c.json"))

    rows = asyncio.run(selector.build([SBIN]))

    assert broker.chain_calls == [SBIN]
    assert _ladder(rows)[("CE", "ATM")] == 1060
    assert selector.selection_source["chain"] == 1 and "quotes" in selector.selection_source["fallbacks"]["*"]


def test_an_underlying_missing_from_the_master_alone_uses_the_chain(tmp_path):
    master = _master("NIFTY", "NSE", FRONT, range(24900, 25150, 50), 75)
    broker = MasterBroker(master, {SBIN: 1053.0, NIFTY: 25010.0})
    selector = ContractSelector(broker, str(tmp_path / "c.json"))

    asyncio.run(selector.build([SBIN, NIFTY]))

    assert broker.chain_calls == [SBIN]
    assert selector.master_errors[SBIN] == "not in the instrument master"


def test_snapshot_reference_comes_from_todays_ladder_not_a_held_contract(tmp_path):
    path = tmp_path / "c.json"
    selector = ContractSelector(MasterBroker([], {}), str(path))
    today = OptionContract("SBIN", SBIN, "CE", "NSE:SBIN-NEW", 1060, FRONT, 1053.0, moneyness="ATM")
    held = OptionContract("SBIN", SBIN, "CE", "NSE:SBIN-OLD", 1000, FRONT, 998.0, retained=True)

    selector._save([today, held])

    assert json.loads(path.read_text())["spots"][SBIN]["selection_price"] == 1053.0


# --- 28 Oct 2026: stale carried selections and the intraday "active contract" refresh -------------------------

def test_a_spot_with_only_next_series_legs_is_selected_again(tmp_path):
    # NIFTY's front weekly expired, leaving only its NEXT- ladder in today's file; SBIN is complete. The pooled
    # role check used to count NIFTY as covered by SBIN's ITM/ATM/OTM legs and never select it again.
    master = [*_master("SBIN", "NSE", FRONT, range(1000, 1110, 10), 750),
              *_master("NIFTY", "NSE", FRONT, range(22000, 23100, 50), 75),
              *_master("NIFTY", "NSE", NEXT, range(22000, 23100, 50), 75)]
    broker = MasterBroker(master, {SBIN: 1053.0, NIFTY: 22610.0})
    path = tmp_path / "contracts.json"
    selector = ContractSelector(broker, str(path))
    rows = asyncio.run(selector.build([SBIN, NIFTY]))
    payload = json.loads(path.read_text())
    payload["contracts"] = [row for row in payload["contracts"]
                            if row["spot_symbol"] != NIFTY or row["moneyness"].startswith("NEXT-")]
    path.write_text(json.dumps(payload))
    again = ContractSelector(broker, str(path))
    rows = asyncio.run(again.build([SBIN, NIFTY]))
    assert {("CE", "ATM"), ("PE", "ATM")} <= {(r.option_type, r.moneyness) for r in rows if r.spot_symbol == NIFTY}


def test_a_held_tick_keeps_the_selection_date(tmp_path):
    master = _master("SBIN", "NSE", FRONT, range(1000, 1110, 10), 750)
    path = tmp_path / "contracts.json"
    selector = ContractSelector(MasterBroker(master, {SBIN: 1053.0}), str(path))
    rows = asyncio.run(selector.build([SBIN]))
    selector.selection_date = (TODAY - timedelta(days=2)).isoformat()   # as if selected two days ago
    atm = next(r for r in rows if r.moneyness == "ATM")
    selector.retain(atm.symbol)
    assert json.loads(path.read_text())["date"] == selector.selection_date   # not re-dated as today
    before = path.stat().st_mtime_ns
    selector.retain(atm.symbol)                                         # already retained: no rewrite
    assert path.stat().st_mtime_ns == before


def _now():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.combine(TODAY, datetime.min.time(), ZoneInfo("Asia/Kolkata")).replace(hour=11)


def test_refresh_moves_an_idle_or_distant_atm_to_the_active_strike_near_spot(tmp_path):
    master = _master("SBIN", "NSE", FRONT, range(1000, 1210, 10), 750)
    broker = MasterBroker(master, {SBIN: 1053.0})
    selector = ContractSelector(broker, str(tmp_path / "contracts.json"))
    rows = asyncio.run(selector.build([SBIN]))
    atm = {r.option_type: r for r in rows if r.moneyness == "ATM"}
    now = _now()
    active = {r.symbol: now - timedelta(minutes=1) for r in rows}
    # Spot still next to the ATM strike and both legs trading: nothing changes.
    assert asyncio.run(selector.refresh({SBIN: 1055.0}, active, now)) == {}
    # Spot has run 4 strikes away: the ladder is chosen again around 1094.
    changed = asyncio.run(selector.refresh({SBIN: 1094.0}, active, now))
    assert changed[SBIN]["to"] == {"CE": "NSE:SBIN" + FRONT + "1100CE", "PE": "NSE:SBIN" + FRONT + "1090PE"}
    assert "strikes from spot" in changed[SBIN]["reasons"][0]
    old = selector.contracts[atm["CE"].symbol]                          # the replaced leg keeps streaming ...
    assert old.moneyness == "PREV-ATM" and old.analysis_only             # ... as context only, no new entries
    assert "NSE:SBIN" + FRONT + "1100CE" in selector.new_symbols
    saved = json.loads((tmp_path / "contracts.json").read_text())
    assert saved["refreshes"][-1]["spot"] == SBIN
    assert saved["spots"][SBIN]["selection_price"] == 1094.0


def test_refresh_keeps_a_held_contract_and_replaces_an_idle_atm(tmp_path):
    master = _master("SBIN", "NSE", FRONT, range(1000, 1210, 10), 750)
    ce = lambda strike: f"NSE:SBIN{FRONT}{strike}CE"
    broker = MasterBroker(master, {SBIN: 1053.0}, {ce(1060): 5000})    # morning: 1060 is the busy call
    selector = ContractSelector(broker, str(tmp_path / "contracts.json"))
    rows = asyncio.run(selector.build([SBIN]))
    assert next(r for r in rows if r.moneyness == "ATM" and r.option_type == "CE").symbol == ce(1060)
    selector.retain(ce(1060))                                           # an open position in it
    broker.volumes = {ce(1060): 0, ce(1050): 5000}                      # by 11:00 only 1050 trades
    now = _now()
    last = {r.symbol: now - timedelta(minutes=1) for r in rows if r.symbol != ce(1060)}
    changed = asyncio.run(selector.refresh({SBIN: 1053.0}, last, now))
    assert "no trade today" in changed[SBIN]["reasons"][0]
    assert changed[SBIN]["to"]["CE"] == ce(1050) and ce(1050) in selector.contracts
    assert ce(1060) in selector.contracts                               # held: stays subscribed until it exits


def test_a_held_leg_chosen_today_keeps_its_spot_covered_on_restart(tmp_path):
    master = _master("SBIN", "NSE", FRONT, range(1000, 1110, 10), 750)
    broker = MasterBroker(master, {SBIN: 1053.0})
    path = tmp_path / "contracts.json"
    selector = ContractSelector(broker, str(path))
    rows = asyncio.run(selector.build([SBIN]))
    selector.retain(next(r for r in rows if r.moneyness == "ATM").symbol)
    calls = len(broker.quote_calls)
    restarted = ContractSelector(broker, str(path))
    asyncio.run(restarted.build([SBIN]))
    assert len(broker.quote_calls) == calls                             # no re-selection, ladder unchanged


def test_a_streamed_strike_with_comparable_liquidity_keeps_the_atm_slot():
    from macd_trader.contracts import select_liquid_contract
    entries = [OptionChainEntry(k, "CE", f"S{k}", volume=v) for k, v in ((1040, 900), (1050, 1100), (1060, 1600), (1070, 100))]
    assert select_liquid_contract(entries, 1053.0, "CE").symbol == "S1060"                       # the side-rounded ATM
    assert select_liquid_contract(entries, 1053.0, "CE", prefer={"S1050"}).symbol == "S1050"     # 1100 >= 2/3 of 1600
    assert select_liquid_contract(entries, 1053.0, "CE", prefer={"S1070"}).symbol == "S1060"     # too thin to keep
