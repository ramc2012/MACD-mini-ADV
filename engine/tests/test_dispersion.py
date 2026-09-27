"""Tests for the durable CE/PE breadth series and its offline reconstruction.

``research/dispersion_reconstruct.py`` lives outside src/ and is loaded by
path, matching test_of_validation.py.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from macd_trader.candle_store import store_historical_candles
from macd_trader.dispersion import (
    LIVE, RECONSTRUCTED, breadth_series, coverage, latest_breadth, load, record,
)
from macd_trader.models import Candle

IST = timezone(timedelta(hours=5, minutes=30))
_PATH = Path(__file__).resolve().parents[1] / "research" / "dispersion_reconstruct.py"


def _load():
    spec = importlib.util.spec_from_file_location("dispersion_reconstruct", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dr = _load()


def _bar(day: str, minute: int) -> int:
    opened = datetime.combine(datetime.fromisoformat(day), time(9, 15), IST)
    return int((opened + timedelta(minutes=minute)).timestamp())


# ---------------------------------------------------------------------------
# Breadth counting
# ---------------------------------------------------------------------------

def test_breadth_counts_each_bar_and_keeps_the_cohort_size():
    rows = [
        ("CE", 100, 0.5), ("CE", 100, -0.2), ("PE", 100, 0.1),
        ("CE", 200, 0.5), ("PE", 200, -0.4), ("PE", 200, -0.1),
    ]

    series = breadth_series(rows, total=8)

    assert [point.timestamp for point in series] == [100, 200]
    assert (series[0].ce_above, series[0].ce_eligible) == (1, 2)
    assert (series[0].pe_above, series[0].pe_eligible) == (1, 1)
    assert (series[1].ce_above, series[1].pe_above) == (1, 0)
    # Contracts that never printed stay visible as a coverage gap.
    assert all(point.total == 8 for point in series)


def test_breadth_drops_contracts_without_a_finite_macd():
    series = breadth_series([("CE", 100, 0.5), ("PE", 100, float("nan")), ("PE", 100, None)], total=3)

    assert len(series) == 1
    assert (series[0].ce_eligible, series[0].pe_eligible) == (1, 0)


def test_latest_breadth_uses_only_the_newest_cohort():
    """A dormant contract keeps yesterday's MACD; it must not join today's count."""
    rows = [("CE", 100, 5.0), ("CE", 200, 1.0), ("PE", 200, -1.0)]

    point = latest_breadth(rows, total=3)

    assert point.timestamp == 200
    assert (point.ce_eligible, point.pe_eligible) == (1, 1)
    assert (point.ce_above, point.pe_above) == (1, 0)


def test_latest_breadth_without_any_usable_point():
    assert latest_breadth([], total=0) is None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def test_record_round_trips_and_reports_coverage(tmp_path):
    database = str(tmp_path / "history.sqlite3")
    points = breadth_series([("CE", _bar("2026-08-24", 0), 0.4), ("PE", _bar("2026-08-24", 0), -0.4)], total=2)

    assert record(database, 300, points, RECONSTRUCTED) == 1

    rows = load(database, 300)
    assert rows[0]["ceAbove"] == 1 and rows[0]["peAbove"] == 0
    assert rows[0]["source"] == RECONSTRUCTED
    assert coverage(database) == [
        {"day": "2026-08-24", "timeframe_seconds": 300, "bars": 1, "sources": RECONSTRUCTED},
    ]


def test_a_rerun_of_the_backfill_never_displaces_a_live_bar(tmp_path):
    """The live row is the cohort the desk actually watched; the reconstruction
    only infers which contracts would have been chosen."""
    database = str(tmp_path / "history.sqlite3")
    live = breadth_series([("CE", 100, 1.0), ("PE", 100, 1.0)], total=2)
    record(database, 300, live, LIVE)

    guess = breadth_series([("CE", 100, -1.0), ("PE", 100, -1.0)], total=99)
    record(database, 300, guess, RECONSTRUCTED)

    row = load(database, 300)[0]
    assert (row["ceAbove"], row["peAbove"], row["total"], row["source"]) == (1, 1, 2, LIVE)


def test_a_live_bar_replaces_an_earlier_reconstruction(tmp_path):
    database = str(tmp_path / "history.sqlite3")
    record(database, 300, breadth_series([("CE", 100, -1.0)], total=9), RECONSTRUCTED)

    record(database, 300, breadth_series([("CE", 100, 1.0), ("PE", 100, 1.0)], total=2), LIVE)

    row = load(database, 300)[0]
    assert (row["ceAbove"], row["total"], row["source"]) == (1, 2, LIVE)


def test_load_filters_by_timeframe_and_start(tmp_path):
    database = str(tmp_path / "history.sqlite3")
    record(database, 300, breadth_series([("CE", 100, 1.0), ("CE", 300, 1.0)], total=1), LIVE)
    record(database, 1800, breadth_series([("CE", 100, 1.0)], total=1), LIVE)

    assert [row["time"] for row in load(database, 300)] == [100, 300]
    assert [row["time"] for row in load(database, 300, since=200)] == [300]
    assert [row["time"] for row in load(database, 1800)] == [100]


# ---------------------------------------------------------------------------
# Symbol parsing
# ---------------------------------------------------------------------------

def test_parse_option_reads_monthly_weekly_and_digit_bearing_roots():
    assert dr.parse_option("NSE:LICI26SEP425CE", "2026-09-29") == {
        "exchange": "NSE", "root": "LICI", "strike": 425.0, "side": "CE", "symbol": "NSE:LICI26SEP425CE"}
    # Weekly code is YY M DD, so 2026-09-08 becomes 26908.
    weekly = dr.parse_option("NSE:NIFTY2690824050CE", "2026-09-08")
    assert (weekly["root"], weekly["strike"]) == ("NIFTY", 24050.0)
    # A root that begins with digits must not be mistaken for the expiry code.
    assert dr.parse_option("NSE:360ONE26SEP1200PE", "2026-09-29")["root"] == "360ONE"
    assert dr.parse_option("BSE:SENSEX2690176900PE", "2026-09-01")["root"] == "SENSEX"


def test_parse_option_rejects_symbols_that_do_not_split_cleanly():
    assert dr.parse_option("NSE:RELIANCE-EQ", "2026-09-29") is None
    assert dr.parse_option("NSE:LICI26SEP425CE", "2026-08-27") is None


def test_index_roots_map_to_their_spot_series():
    assert dr.spot_symbol("NSE", "NIFTY") == "NSE:NIFTY50-INDEX"
    assert dr.spot_symbol("NSE", "LICI") == "NSE:LICI-EQ"


# ---------------------------------------------------------------------------
# End-to-end reconstruction against stored minute bars
# ---------------------------------------------------------------------------

def _seed(database: str, day: str) -> None:
    """One underlying opening at 1000 with strikes 950/1000/1050 quoted.

    The call premium rises through the session and the put premium falls, so
    the ATM cohort must end with the call above zero and the put below it.
    """
    legs = [("NSE:TEST-EQ", None, lambda i: 1000.0 + i)]
    for strike in (950, 1000, 1050):
        legs.append((f"NSE:TEST26SEP{strike}CE", "2026-09-29", lambda i: 40.0 + 2 * i))
        legs.append((f"NSE:TEST26SEP{strike}PE", "2026-09-29", lambda i: 40.0 - 0.5 * i))
    for symbol, expiry, price in legs:
        store_historical_candles(database, [
            Candle(symbol, _bar(day, index), price(index), price(index), price(index), price(index), 100, True)
            for index in range(120)
        ], expiry)


def test_reconstruction_picks_the_atm_strike_and_scores_both_sides(tmp_path):
    database = str(tmp_path / "history.sqlite3")
    day = "2026-09-01"
    _seed(database, day)
    connection = sqlite3.connect(database)
    bars, meta = dr.day_minutes(connection, day)
    connection.close()

    cohort = dr.select_cohort(bars, meta)

    # Spot opens at 1000, so the 1000 strike is ATM — one CE and one PE only.
    assert sorted(row["symbol"] for row in cohort) == ["NSE:TEST26SEP1000CE", "NSE:TEST26SEP1000PE"]

    points = dr.reconstruct_day(bars, cohort, 300, {"fast": 3, "slow": 6, "signal": 2})

    assert points and all(point.total == 2 for point in points)
    assert all(point.ce_eligible == 1 and point.pe_eligible == 1 for point in points)
    last = points[-1]
    assert (last.ce_above, last.pe_above) == (1, 0)


def test_reconstruction_skips_an_underlying_missing_one_side(tmp_path):
    database = str(tmp_path / "history.sqlite3")
    day = "2026-09-01"
    _seed(database, day)
    connection = sqlite3.connect(database)
    connection.execute("DELETE FROM historical_candles WHERE symbol='NSE:TEST26SEP1000PE'")
    connection.commit()
    bars, meta = dr.day_minutes(connection, day)
    connection.close()

    # Without a matching put the two sides would describe different strikes,
    # so the underlying drops out rather than contributing a one-sided count.
    assert dr.select_cohort(bars, meta) == []


# ---------------------------------------------------------------------------
# The live producer
# ---------------------------------------------------------------------------

def _contract(symbol: str, side: str, moneyness: str):
    from macd_trader.contracts import OptionContract
    return OptionContract(
        underlying="TEST", spot_symbol="NSE:TEST-EQ", option_type=side, symbol=symbol,
        strike=100.0, expiry="2026-09-29", selection_price=100.0, moneyness=moneyness,
        analysis_only=moneyness != "ATM",
    )


def test_engine_scores_only_the_atm_legs_of_its_own_ladder():
    """The ITM/OTM legs exist to draw the ratio chart. Counting them here would
    triple the cohort and double-count each underlying's direction."""
    from macd_trader.config import Settings
    from macd_trader.engine import TradingEngine
    from macd_trader.models import IndicatorPoint

    engine = TradingEngine(Settings(symbols_csv="NSE:TEST-EQ", feed_mode="simulation"))
    ladder = {
        "ce_atm": _contract("NSE:TEST26SEP100CE", "CE", "ATM"),
        "pe_atm": _contract("NSE:TEST26SEP100PE", "PE", "ATM"),
        "ce_itm": _contract("NSE:TEST26SEP95CE", "CE", "ITM"),
        "pe_otm": _contract("NSE:TEST26SEP95PE", "PE", "OTM"),
    }
    engine.contract_selector.contracts = {row.symbol: row for row in ladder.values()}
    engine.strategy.points = {
        "NSE:TEST26SEP100CE": IndicatorPoint("NSE:TEST26SEP100CE", 200, 0.8, 0.1, 0.7),
        "NSE:TEST26SEP100PE": IndicatorPoint("NSE:TEST26SEP100PE", 200, -0.3, 0.1, -0.4),
        # Analysis-only legs are positive and must still be ignored.
        "NSE:TEST26SEP95CE": IndicatorPoint("NSE:TEST26SEP95CE", 200, 9.0, 0.1, 8.0),
        "NSE:TEST26SEP95PE": IndicatorPoint("NSE:TEST26SEP95PE", 200, 9.0, 0.1, 8.0),
    }

    point = engine.dispersion_point()

    assert (point.ce_above, point.pe_above) == (1, 0)
    assert (point.ce_eligible, point.pe_eligible, point.total) == (1, 1, 2)


def test_engine_reports_no_breadth_before_any_indicator_exists():
    from macd_trader.config import Settings
    from macd_trader.engine import TradingEngine

    engine = TradingEngine(Settings(symbols_csv="NSE:TEST-EQ", feed_mode="simulation"))
    engine.contract_selector.contracts = {"NSE:TEST26SEP100CE": _contract("NSE:TEST26SEP100CE", "CE", "ATM")}
    engine.strategy.points = {}

    assert engine.dispersion_point() is None
