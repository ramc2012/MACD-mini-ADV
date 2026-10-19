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
