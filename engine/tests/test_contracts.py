import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from macd_trader.brokers import OptionChainEntry, OptionContractMetadata
from macd_trader.contracts import ContractSelector, select_contract_ladder, select_liquid_contract


def test_contract_selection_rounds_ce_up_and_pe_down():
    rows = [
        OptionChainEntry(99, "PE", "PE99", volume=100),
        OptionChainEntry(100, "PE", "PE100", volume=100),
        OptionChainEntry(100, "CE", "CE100", volume=100),
        OptionChainEntry(101, "CE", "CE101", volume=100),
    ]
    assert select_liquid_contract(rows, 100.4, "CE").symbol == "CE101"
    assert select_liquid_contract(rows, 100.4, "PE").symbol == "PE100"


def test_neighbour_replaces_anchor_only_when_materially_more_liquid():
    rows = [
        OptionChainEntry(100, "CE", "CE100", volume=100),
        OptionChainEntry(101, "CE", "CE101", volume=100),
        OptionChainEntry(102, "CE", "CE102", volume=160),
    ]
    assert select_liquid_contract(rows, 100.4, "CE").symbol == "CE102"


def test_contract_ladder_assigns_call_and_put_moneyness_in_opposite_directions():
    rows = [
        OptionChainEntry(strike, side, f"{side}{strike}", volume=100)
        for side in ("CE", "PE") for strike in (99, 100, 101)
    ]
    assert [(role, row.symbol) for role, row in select_contract_ladder(rows, 100, "CE")] == [
        ("ITM", "CE99"), ("ATM", "CE100"), ("OTM", "CE101"),
    ]
    assert [(role, row.symbol) for role, row in select_contract_ladder(rows, 100, "PE")] == [
        ("ITM", "PE101"), ("ATM", "PE100"), ("OTM", "PE99"),
    ]


def test_same_day_contract_selection_survives_restart(tmp_path):
    class BrokerMustNotBeCalled:
        async def option_chain(self, _symbol, _expiry_token=None):
            raise AssertionError("same-day selection was recalculated")

        async def lot_sizes(self, symbols):
            return {symbol: 750 for symbol in symbols}

    contracts = [
        {
            "underlying": "SBIN", "spot_symbol": "NSE:SBIN-EQ", "option_type": side,
            "symbol": f"NSE:SBIN2099{side}{role}", "strike": strike, "expiry": "2099-08-27",
            "selection_price": 1065.0, "volume": 1000, "oi": 5000, "retained": False,
            "moneyness": role, "analysis_only": role != "ATM",
        }
        for side, strikes in (("CE", (1060, 1070, 1080)), ("PE", (1080, 1070, 1060)))
        for role, strike in zip(("ITM", "ATM", "OTM"), strikes)
    ]
    path = tmp_path / "contracts.json"
    path.write_text(json.dumps({"date": datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat(), "spots": {}, "contracts": contracts}))
    selector = ContractSelector(BrokerMustNotBeCalled(), str(path))
    rows = asyncio.run(selector.build(["NSE:SBIN-EQ"]))
    assert {row.symbol for row in rows} == {row["symbol"] for row in contracts}
    assert {row.lot_size for row in rows} == {750}


def test_open_contract_is_retained_across_selection_day(tmp_path):
    class BrokerMustNotBeCalled:
        async def option_chain(self, _symbol, _expiry_token=None):
            raise AssertionError("no fresh chain is needed for a retained position")

        async def lot_sizes(self, symbols):
            return {symbol: 750 for symbol in symbols}

    contract = {
        "underlying": "SBIN", "spot_symbol": "NSE:SBIN-EQ", "option_type": "CE",
        "symbol": "NSE:SBIN26AUG1070CE", "strike": 1070.0, "expiry": "2026-08-25",
        "selection_price": 1065.0, "volume": 1000, "oi": 5000, "retained": False,
    }
    path = tmp_path / "contracts.json"
    path.write_text(json.dumps({"date": "2026-08-01", "spots": {}, "contracts": [contract]}))
    selector = ContractSelector(BrokerMustNotBeCalled(), str(path))

    restored = selector.retained_contracts({contract["symbol"]})
    rows = asyncio.run(selector.build([], restored))

    assert [row.symbol for row in rows] == [contract["symbol"]]
    assert rows[0].retained is True
    assert rows[0].lot_size == 750
    assert selector.new_symbols == set()


def test_missing_held_contract_is_rehydrated_from_instrument_master(tmp_path):
    class MetadataBroker:
        async def option_contract_metadata(self, symbols):
            assert symbols == ["NSE:POWERINDIA26AUG35000CE"]
            return {
                symbols[0]: OptionContractMetadata(
                    symbol=symbols[0], underlying="POWERINDIA", option_type="CE",
                    strike=35_000.0, expiry="2026-08-25", lot_size=25,
                )
            }

    selector = ContractSelector(MetadataBroker(), str(tmp_path / "missing.json"))
    rows = asyncio.run(selector.restore_held_contracts({"NSE:POWERINDIA26AUG35000CE": 25}))

    assert len(rows) == 1
    assert rows[0].spot_symbol == "NSE:POWERINDIA-EQ"
    assert rows[0].retained is True
    assert rows[0].lot_size == 25


def test_expired_contracts_are_never_reused_from_the_saved_selection():
    """The saved-selection fast path had no expiry check.

    On 21 Aug 2026 the live desk still held NSE:NIFTY2681824300CE/PE (expired
    18 Aug) and BSE:SENSEX2682077800CE/PE (expired 20 Aug) in its ATM universe,
    so NIFTY and SENSEX had no tradable ATM option at all.
    """
    import datetime

    from macd_trader.contracts import contract_is_live

    today = datetime.date(2026, 8, 21)
    assert contract_is_live("2026-08-26", today) is True     # still live
    assert contract_is_live("2026-08-21", today) is True     # expires today
    assert contract_is_live("2026-08-20", today) is False    # the SENSEX pair
    assert contract_is_live("2026-08-18", today) is False    # the NIFTY pair


def test_an_unusable_expiry_is_treated_as_dead_not_trusted():
    import datetime

    from macd_trader.contracts import contract_is_live

    today = datetime.date(2026, 8, 21)
    for value in (None, "", "not-a-date", "26AUG"):
        assert contract_is_live(value, today) is False


def test_full_iso_timestamps_are_accepted():
    import datetime

    from macd_trader.contracts import contract_is_live

    today = datetime.date(2026, 8, 21)
    assert contract_is_live("2026-08-26T15:30:00+05:30", today) is True
    assert contract_is_live("2026-08-18T15:30:00+05:30", today) is False


def test_selection_rolls_past_an_expiry_that_is_hours_away():
    """The front chain on an expiry day is not a tradable selection.

    Fyers answers options-chain-v3 with the FRONT expiry unless one is named,
    so on 25 Aug 2026 the selector picked 427 contracts that settled at 15:30
    that same day. The roll asks for the next listed series instead.
    """
    from macd_trader.brokers import OptionChain
    from macd_trader.rollover import Expiry

    front = OptionChain("2026-08-25", 1000.0, [
        OptionChainEntry(strike, side, f"NSE:SBIN26AUG{strike}{side}", volume=10)
        for side in ("CE", "PE") for strike in (990, 1000, 1010)
    ], [Expiry("2026-08-25", "1787652600"), Expiry("2026-09-29", "1790676600")])
    back = OptionChain("2026-09-29", 1000.0, [
        OptionChainEntry(strike, side, f"NSE:SBIN26SEP{strike}{side}", volume=10)
        for side in ("CE", "PE") for strike in (990, 1000, 1010)
    ], [Expiry("2026-08-25", "1787652600"), Expiry("2026-09-29", "1790676600")])

    asked: list[str | None] = []

    class RollingBroker:
        async def option_chain(self, _symbol, expiry_token=None):
            asked.append(expiry_token)
            return back if expiry_token == "1790676600" else front

        async def preopen_price(self, _symbol):
            return 1000.0

        async def lot_sizes(self, symbols):
            return {symbol: 750 for symbol in symbols}

    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        selector = ContractSelector(RollingBroker(), f"{directory}/contracts.json")
        rows = asyncio.run(selector.build(["NSE:SBIN-EQ"]))

    assert asked == [None, "1790676600"]
    assert len(rows) == 6
    assert {row.moneyness for row in rows} == {"ITM", "ATM", "OTM"}
    assert {row.expiry for row in rows} == {"2026-09-29"}
    assert selector.rolled == {"NSE:SBIN-EQ": "2026-08-25 -> 2026-09-29"}


def test_selection_keeps_the_front_series_when_it_is_not_expiring():
    from macd_trader.brokers import OptionChain
    from macd_trader.rollover import Expiry

    chain = OptionChain("2099-09-29", 1000.0, [
        OptionChainEntry(strike, side, f"{side}{strike}", volume=10)
        for side in ("CE", "PE") for strike in (990, 1000, 1010)
    ], [Expiry("2099-09-29", "1"), Expiry("2099-10-27", "2")])

    calls = 0

    class Broker:
        async def option_chain(self, _symbol, expiry_token=None):
            nonlocal calls
            calls += 1
            return chain

        async def preopen_price(self, _symbol):
            return 1000.0

        async def lot_sizes(self, symbols):
            return {symbol: 750 for symbol in symbols}

    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        selector = ContractSelector(Broker(), f"{directory}/contracts.json")
        rows = asyncio.run(selector.build(["NSE:SBIN-EQ"]))

    assert calls == 1, "a chain that is not expiring must not cost a second request"
    assert len(rows) == 6
    assert selector.rolled == {}


def test_a_selection_saved_before_the_roll_window_is_not_reused_inside_it():
    """atm_contracts.json is rewritten daily, but only checked for liveness.

    A snapshot written the day before expiry names contracts that are still
    unexpired the next morning, so the fast path would hand back exactly the
    expiry-day selection the roll exists to avoid.
    """
    import datetime
    import tempfile

    from macd_trader.rollover import is_tradable

    today = datetime.date(2026, 8, 25)
    assert is_tradable("2026-08-25", 1, today) is False
    assert is_tradable("2026-08-26", 1, today) is True
    assert is_tradable("2026-08-25", 0, today) is True

    refetched = []

    class Broker:
        async def option_chain(self, symbol, expiry_token=None):
            refetched.append(symbol)
            raise RuntimeError("chain unavailable")

        async def lot_sizes(self, symbols):
            return {symbol: 750 for symbol in symbols}

    stale = {
        "underlying": "SBIN", "spot_symbol": "NSE:SBIN-EQ", "option_type": "CE",
        "symbol": "NSE:SBIN26AUG1070CE", "strike": 1070.0,
        "expiry": datetime.datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat(),
        "selection_price": 1065.0, "volume": 1000, "oi": 5000, "retained": False,
    }
    with tempfile.TemporaryDirectory() as directory:
        path = f"{directory}/contracts.json"
        with open(path, "w") as handle:
            json.dump({"date": datetime.datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat(),
                       "spots": {}, "contracts": [stale]}, handle)
        selector = ContractSelector(Broker(), path)
        rows = asyncio.run(selector.build(["NSE:SBIN-EQ"]))

    assert refetched == ["NSE:SBIN-EQ"], "the expiry-day snapshot must not be reused"
    assert rows == []
