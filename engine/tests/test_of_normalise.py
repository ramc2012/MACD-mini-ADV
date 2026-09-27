"""Per-symbol order-flow normalisation.

The doctrine under test is mostly about what is NOT published: no baseline,
too small a sample, a stale baseline or a badly classified reading must yield
None rather than a number that reads as a measurement.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime

import pytest

from macd_trader.of_normalise import (
    BASELINE_COLUMNS,
    IST,
    Baseline,
    BaselineStore,
    FLOW_SCORE_RANGE,
    MIN_BASELINE_BARS,
    Reading,
    normalise,
    normalised_flow,
    reading_from_prints,
)
from macd_trader.orderflow import OrderFlowTracker


@dataclass
class Tick:
    symbol: str
    ltp: float
    volume: int | None = None
    last_qty: int | None = None
    bid: float | None = None
    ask: float | None = None
    timestamp: object = None


@dataclass
class Stamp:
    """Minimal stand-in for datetime, carrying only what on_tick reads."""
    epoch: float

    def timestamp(self) -> float:
        return self.epoch


@dataclass
class FakePrint:
    timestamp: float
    price: float
    size: int
    side: int
    method: str = "quote"


def make_baseline(**overrides) -> Baseline:
    base = dict(
        symbol="NSE:NIFTY26SEPFUT",
        as_of="2026-08-25",
        instrument="FUT",
        sessions=1,
        bars=386,
        volume_median=5000.0,
        volume_p25=4225.0,
        volume_p75=6630.0,
        abs_delta_median=1300.0,
        nd_median=0.17,
        nd_p25=-0.058,
        nd_p75=0.366,
        trades_median=60.0,
        delta_mean=900.0,
        delta_sd=2100.0,
        ret_sd=0.0007,
        spread_bps_median=0.5,
        price_median=24800.0,
        quote_share=0.735,
        classified_share=0.991,
        estimator="quote_rule_zero_tick",
    )
    base.update(overrides)
    return Baseline(**base)


def prints_over_window(
    n: int = 60,
    size: int = 100,
    side: int = 1,
    start: float = 1_000_000.0,
    span: float = 60.0,
    method: str = "quote",
) -> list[FakePrint]:
    step = span / max(n - 1, 1)
    return [
        FakePrint(start + i * step, 100.0 + i * 0.05, size, side, method)
        for i in range(n)
    ]


TODAY = date(2026, 8, 26)


# --- the reading ------------------------------------------------------------

def test_reading_conserves_the_tape():
    rows = (
        [FakePrint(1_000_000.0 + i, 100.0, 100, 1) for i in range(20)]
        + [FakePrint(1_000_020.0 + i, 100.0, 50, -1) for i in range(10)]
        + [FakePrint(1_000_030.0 + i, 100.0, 25, 0, "tick") for i in range(4)]
    )
    reading = reading_from_prints("SYM", rows)
    assert reading is not None
    assert reading.buy_volume + reading.sell_volume + reading.unclassified_volume == reading.volume
    assert reading.volume == 20 * 100 + 10 * 50 + 4 * 25
    assert reading.classified_share == pytest.approx(2500 / 2600)


def test_reading_is_anchored_to_the_last_print_not_the_wall_clock():
    rows = prints_over_window(start=5_000.0)
    reading = reading_from_prints("SYM", rows)
    assert reading.end_ts == pytest.approx(5_060.0)
    assert reading.covered_seconds == 60.0
    assert reading.partial is False


def test_reading_excludes_prints_older_than_the_window():
    old = [FakePrint(0.0 + i, 100.0, 999, 1) for i in range(5)]
    recent = prints_over_window(n=30, size=10, start=10_000.0)
    reading = reading_from_prints("SYM", old + recent)
    # The stale block is outside the window, so it must not inflate volume...
    assert reading.volume == 30 * 10
    # ...but its presence means the deque DOES reach back a full window.
    assert reading.partial is False


def test_short_deque_is_flagged_partial_and_rated_on_what_it_covers():
    rows = prints_over_window(n=30, size=10, start=10_000.0, span=30.0)
    reading = reading_from_prints("SYM", rows)
    assert reading.partial is True
    assert reading.covered_seconds == pytest.approx(30.0)
    # 300 units over 30s is a rate of 600/min, not 300/min.
    assert reading.volume_per_minute == pytest.approx(600.0)


def test_a_window_too_short_to_support_a_rate_returns_none():
    rows = prints_over_window(n=10, size=10, start=10_000.0, span=5.0)
    assert reading_from_prints("SYM", rows) is None


def test_no_prints_returns_none():
    assert reading_from_prints("SYM", []) is None


# --- the floors: None, never a default --------------------------------------

def test_missing_baseline_returns_none():
    reading = reading_from_prints("SYM", prints_over_window())
    assert normalise(reading, None, now=reading.end_ts, today=TODAY) is None


def test_baseline_with_too_small_a_sample_returns_none():
    reading = reading_from_prints("SYM", prints_over_window())
    thin = make_baseline(bars=MIN_BASELINE_BARS - 1)
    assert normalise(reading, thin, now=reading.end_ts, today=TODAY) is None


def test_baseline_from_a_different_regime_returns_none():
    reading = reading_from_prints("SYM", prints_over_window())
    ancient = make_baseline(as_of="2026-01-02")
    assert normalise(reading, ancient, now=reading.end_ts, today=TODAY) is None


def test_reading_from_a_dead_tape_returns_none():
    reading = reading_from_prints("SYM", prints_over_window())
    out = normalise(reading, make_baseline(), now=reading.end_ts + 7200, today=TODAY)
    assert out is None


def test_too_few_prints_returns_none():
    rows = [FakePrint(10_000.0 + i * 15, 100.0, 10, 1) for i in range(4)]
    reading = reading_from_prints("SYM", rows)
    assert reading is not None and reading.trades == 4
    assert normalise(reading, make_baseline(), now=reading.end_ts, today=TODAY) is None


def test_poorly_classified_reading_withholds_direction_but_keeps_rvol():
    # 80% of the window's volume is unclassifiable.
    rows = (
        [FakePrint(10_000.0 + i, 100.0, 100, 0, "tick") for i in range(48)]
        + [FakePrint(10_050.0 + i, 100.0, 100, 1) for i in range(12)]
    )
    reading = reading_from_prints("SYM", rows)
    out = normalise(reading, make_baseline(), now=reading.end_ts, today=TODAY)
    assert out is not None
    assert out["rvol"] is not None          # volume is published by NSE
    assert out["nd_score"] is None          # the aggressor is not
    assert out["delta_z"] is None
    assert out["flow_score"] is None
    assert out["confidence"]["directional_withheld"] is True
    assert "reading_classification_poor" in out["confidence"]["grade_reasons"]
    assert out["confidence"]["grade"] == "low"


def test_no_usable_baseline_statistic_returns_none():
    reading = reading_from_prints("SYM", prints_over_window())
    empty = make_baseline(
        volume_median=None, trades_median=None,
        nd_median=None, nd_p25=None, nd_p75=None,
        delta_mean=None, delta_sd=None,
    )
    assert normalise(reading, empty, now=reading.end_ts, today=TODAY) is None


# --- the numbers ------------------------------------------------------------

def test_rvol_is_volume_rate_over_the_symbols_own_median():
    rows = prints_over_window(n=100, size=100)     # 10,000 over 60s
    reading = reading_from_prints("SYM", rows)
    out = normalise(reading, make_baseline(volume_median=5000.0),
                    now=reading.end_ts, today=TODAY)
    assert out["rvol"] == pytest.approx(2.0)
    assert out["trade_rvol"] == pytest.approx(100 / 60.0, abs=1e-3)


def test_identical_raw_deltas_normalise_differently_per_symbol():
    """The whole point: one raw number, two symbols, two readings."""
    rows = prints_over_window(n=60, size=100)      # delta +6000/min
    reading = reading_from_prints("SYM", rows)
    big = normalise(reading, make_baseline(delta_mean=900.0, delta_sd=2100.0),
                    now=reading.end_ts, today=TODAY)
    small = normalise(reading, make_baseline(delta_mean=50.0, delta_sd=120.0),
                      now=reading.end_ts, today=TODAY)
    assert big["delta_z"] == pytest.approx((6000 - 900) / 2100, rel=1e-3)
    assert small["delta_z"] > big["delta_z"] * 5
    assert big["reading"]["delta_per_minute"] == small["reading"]["delta_per_minute"]


def test_flow_score_is_bounded_and_signed():
    buys = reading_from_prints("SYM", prints_over_window(side=1, size=1000))
    sells = reading_from_prints("SYM", prints_over_window(side=-1, size=1000))
    hot = normalise(buys, make_baseline(), now=buys.end_ts, today=TODAY)
    cold = normalise(sells, make_baseline(), now=sells.end_ts, today=TODAY)
    assert FLOW_SCORE_RANGE[0] <= cold["flow_score"] < 0 < hot["flow_score"] <= FLOW_SCORE_RANGE[1]
    assert hot["flow_score_range"] == [-100.0, 100.0]


def test_flow_score_saturates_rather_than_running_away():
    absurd = reading_from_prints("SYM", prints_over_window(size=10_000_000))
    out = normalise(absurd, make_baseline(), now=absurd.end_ts, today=TODAY)
    assert out["flow_score"] <= FLOW_SCORE_RANGE[1]
    assert out["flow_score"] > 90


def test_thin_participation_attenuates_the_score_and_never_inflates_it():
    rows = prints_over_window(n=60, size=100)
    reading = reading_from_prints("SYM", rows)
    normal = normalise(reading, make_baseline(volume_median=6000.0),
                       now=reading.end_ts, today=TODAY)
    thin = normalise(reading, make_baseline(volume_median=60_000.0),
                     now=reading.end_ts, today=TODAY)
    assert normal["participation"] == 1.0
    assert thin["participation"] == pytest.approx(0.1, abs=0.01)
    assert abs(thin["flow_score"]) < abs(normal["flow_score"])
    # A symbol trading at 10x its usual volume still cannot exceed weight 1.
    loud = normalise(reading, make_baseline(volume_median=600.0),
                     now=reading.end_ts, today=TODAY)
    assert loud["participation"] == 1.0


def test_ingredients_explain_the_score():
    rows = prints_over_window(n=60, size=500)
    reading = reading_from_prints("SYM", rows)
    out = normalise(reading, make_baseline(), now=reading.end_ts, today=TODAY)
    names = [item["name"] for item in out["ingredients"]]
    assert names == ["nd_score", "delta_z"]
    assert all(item["basis"] == "inferred" for item in out["ingredients"])
    # The decomposition is ADDITIVE and lands on combined_z, the quantity the
    # ingredients actually build. The previous assertion (contributions summing
    # to flow_score) held for ANY set of values whatsoever: contribution was
    # flow_score * weight/weight_total, and the weights sum to 1 by
    # construction, so the test passed identically on garbage ingredients.
    z_sum = sum(item["z_contribution"] for item in out["ingredients"])
    assert z_sum == pytest.approx(out["combined_z"], abs=0.01)


def test_a_contribution_carries_its_own_ingredients_sign_and_size():
    """The real content of the old assertion, which it could not check: an
    ingredient that is strongly negative must report a strongly negative
    contribution, not the same share of the score as a flat one."""
    reading = reading_from_prints("SYM", prints_over_window(n=60, size=500))
    out = normalise(reading, make_baseline(), now=reading.end_ts, today=TODAY)
    by_name = {item["name"]: item for item in out["ingredients"]}
    for name, item in by_name.items():
        # sign agrees with the underlying z, and magnitude scales with it
        assert (item["z_contribution"] >= 0) == (item["value"] >= 0), name
        assert abs(item["z_contribution"]) <= abs(item["value"]) + 1e-9, name


def test_basis_separates_measured_volume_from_inferred_direction():
    reading = reading_from_prints("SYM", prints_over_window())
    out = normalise(reading, make_baseline(), now=reading.end_ts, today=TODAY)
    assert "rvol" in out["basis"]["measured"]
    assert "flow_score" in out["basis"]["inferred"]
    assert "delta_z" in out["basis"]["inferred"]
    assert not set(out["basis"]["measured"]) & set(out["basis"]["inferred"])


# --- confidence -------------------------------------------------------------

def test_every_output_carries_its_sample_size_and_both_classification_shares():
    reading = reading_from_prints("SYM", prints_over_window())
    out = normalise(reading, make_baseline(), now=reading.end_ts, today=TODAY)
    confidence = out["confidence"]
    assert confidence["baseline_bars"] == 386
    assert confidence["baseline_sessions"] == 1
    assert confidence["reading_classified_share"] == 1.0
    assert confidence["reading_quote_share"] == 1.0
    assert confidence["reading_trades"] == 60
    assert confidence["baseline_classified_share"] == pytest.approx(0.991)
    assert confidence["baseline_quote_share"] == pytest.approx(0.735)


def test_legacy_estimator_is_named_and_downgraded_not_hidden():
    reading = reading_from_prints("SYM", prints_over_window())
    out = normalise(reading, make_baseline(estimator="legacy_pre_2026-08-27"),
                    now=reading.end_ts, today=TODAY)
    assert out["baseline"]["estimator_current"] is False
    assert "baseline_estimator_legacy" in out["confidence"]["grade_reasons"]
    assert out["confidence"]["grade"] == "medium"
    assert out["flow_score"] is not None


def test_stale_baseline_downgrades_then_disqualifies():
    reading = reading_from_prints("SYM", prints_over_window())
    fresh = normalise(reading, make_baseline(as_of="2026-08-25"),
                      now=reading.end_ts, today=TODAY)
    assert fresh["confidence"]["grade"] == "high"
    stale = normalise(reading, make_baseline(as_of="2026-08-15"),
                      now=reading.end_ts, today=TODAY)
    assert stale["confidence"]["grade"] == "medium"
    older = normalise(reading, make_baseline(as_of="2026-08-05"),
                      now=reading.end_ts, today=TODAY)
    assert older["confidence"]["grade"] == "low"
    assert normalise(reading, make_baseline(as_of="2026-06-01"),
                     now=reading.end_ts, today=TODAY) is None


def test_stale_reading_is_flagged_with_its_age():
    reading = reading_from_prints("SYM", prints_over_window())
    out = normalise(reading, make_baseline(), now=reading.end_ts + 600, today=TODAY)
    assert out["stale"] is True
    assert out["reading_age_seconds"] == pytest.approx(600.0)
    assert "reading_stale" in out["confidence"]["grade_reasons"]


def test_partial_window_is_flagged():
    rows = prints_over_window(n=40, span=30.0)
    reading = reading_from_prints("SYM", rows)
    out = normalise(reading, make_baseline(), now=reading.end_ts, today=TODAY)
    assert out["window_partial"] is True
    assert "window_partial" in out["confidence"]["grade_reasons"]


# --- the store --------------------------------------------------------------

def write_baseline_db(path, rows, view: bool = True) -> None:
    connection = sqlite3.connect(str(path))
    columns = ", ".join(f"{name} TEXT" if name in ("symbol", "as_of", "instrument", "estimator")
                        else f"{name} REAL" for name in BASELINE_COLUMNS)
    connection.execute(f"CREATE TABLE of_symbol_baseline ({columns})")
    placeholders = ", ".join("?" for _ in BASELINE_COLUMNS)
    connection.executemany(
        f"INSERT INTO of_symbol_baseline VALUES ({placeholders})", rows
    )
    if view:
        connection.execute(
            "CREATE VIEW of_symbol_baseline_latest AS SELECT * FROM of_symbol_baseline"
        )
    connection.commit()
    connection.close()


def baseline_row(symbol: str, **overrides) -> tuple:
    baseline = make_baseline(symbol=symbol, **overrides)
    return tuple(getattr(baseline, name) for name in BASELINE_COLUMNS)


def test_store_reads_the_view(tmp_path):
    db = tmp_path / "ticks.sqlite3"
    write_baseline_db(db, [baseline_row("NSE:NIFTY26SEPFUT")])
    store = BaselineStore(str(db))
    assert len(store) == 1
    assert store.get("NSE:NIFTY26SEPFUT").bars == 386
    assert store.get("NSE:UNKNOWN") is None


def test_store_falls_back_to_the_table_when_the_view_is_absent(tmp_path):
    db = tmp_path / "ticks.sqlite3"
    write_baseline_db(db, [baseline_row("NSE:NIFTY26SEPFUT")], view=False)
    store = BaselineStore(str(db))
    assert store.get("NSE:NIFTY26SEPFUT") is not None


def test_missing_database_degrades_to_no_baselines_not_an_exception(tmp_path):
    store = BaselineStore(str(tmp_path / "nope.sqlite3"))
    assert store.get("NSE:NIFTY26SEPFUT") is None
    assert store.error is not None


def test_database_without_the_baseline_table_degrades_quietly(tmp_path):
    db = tmp_path / "ticks.sqlite3"
    connection = sqlite3.connect(str(db))
    connection.execute("CREATE TABLE ticks (symbol_id INTEGER)")
    connection.commit()
    connection.close()
    store = BaselineStore(str(db))
    assert store.get("NSE:NIFTY26SEPFUT") is None


def test_normalised_flow_end_to_end(tmp_path):
    db = tmp_path / "ticks.sqlite3"
    write_baseline_db(db, [baseline_row("NSE:NIFTY26SEPFUT")])
    store = BaselineStore(str(db))
    rows = prints_over_window(n=60, size=100)
    out = normalised_flow("NSE:NIFTY26SEPFUT", rows, store=store,
                          now=rows[-1].timestamp, today=TODAY)
    assert out["symbol"] == "NSE:NIFTY26SEPFUT"
    assert out["rvol"] is not None and out["flow_score"] is not None
    assert normalised_flow("NSE:NOTINTHETABLE", rows, store=store,
                           now=rows[-1].timestamp, today=TODAY) is None


# --- the snapshot surface ---------------------------------------------------

def feed(tracker: OrderFlowTracker, symbol: str, n: int = 80) -> None:
    """Drive a tracker with a minute of quote-classified prints ending now."""
    base = time.time() - 60.0
    cumulative = 0
    for i in range(n):
        cumulative += 100
        tracker.on_tick(Tick(
            symbol=symbol, ltp=100.0 + i * 0.05, volume=cumulative,
            bid=99.9 + i * 0.05, ask=100.0 + i * 0.05,
            timestamp=Stamp(base + i * 0.75),
        ))


def test_snapshot_publishes_the_key_absent_when_there_is_no_baseline():
    tracker = OrderFlowTracker()
    feed(tracker, "NSE:NOSUCHSYMBOL")
    snapshot = tracker.snapshot("NSE:NOSUCHSYMBOL")
    assert "normalised" in snapshot
    # No baseline exists for this invented symbol, so the desk is told nothing
    # rather than told zero.
    assert snapshot["normalised"] is None


def test_snapshot_never_breaks_when_normalisation_does(monkeypatch):
    import macd_trader.of_normalise as of_normalise

    def explode(*args, **kwargs):
        raise RuntimeError("baseline store on fire")

    monkeypatch.setattr(of_normalise, "normalised_flow", explode)
    tracker = OrderFlowTracker()
    feed(tracker, "NSE:SOMETHING")
    snapshot = tracker.snapshot("NSE:SOMETHING")
    assert snapshot["normalised"] is None
    assert snapshot["trades"] > 0          # the rest of the snapshot survives


def test_snapshot_carries_the_normalised_block_when_a_baseline_exists(tmp_path, monkeypatch):
    db = tmp_path / "ticks.sqlite3"
    # Dated today, because this test feeds wall-clock timestamps: a fixture
    # pinned to a literal date would quietly start failing a month from now.
    today = datetime.now(IST).date().isoformat()
    write_baseline_db(db, [baseline_row("NSE:NIFTY26SEPFUT", as_of=today,
                                        volume_median=5000.0)])
    import macd_trader.of_normalise as of_normalise

    store = BaselineStore(str(db))
    monkeypatch.setattr(of_normalise, "default_store", lambda: store)
    tracker = OrderFlowTracker()
    feed(tracker, "NSE:NIFTY26SEPFUT")
    snapshot = tracker.snapshot("NSE:NIFTY26SEPFUT")
    block = snapshot["normalised"]
    assert block is not None
    assert block["rvol"] is not None
    assert set(block) >= {"rvol", "flow_score", "ingredients", "basis",
                          "baseline", "confidence"}
    assert block["confidence"]["baseline_bars"] == 386


def test_participation_is_absent_when_rvol_could_not_be_measured():
    """The attenuation applied internally is 1.0 (RVOL only ever attenuates, so
    an unmeasurable one must not penalise the score). What must NOT happen is
    publishing that 1.0: on the wire it is indistinguishable from a symbol
    measured to be trading at exactly its usual size, which is a claim about a
    number nobody computed.
    """
    reading = reading_from_prints("SYM", prints_over_window(n=60, size=500))
    baseline = make_baseline(volume_median=None)
    out = normalise(reading, baseline, now=reading.end_ts, today=TODAY)
    assert out["rvol"] is None
    assert out["participation"] is None
    # ...and the score is still produced, unattenuated, from what WAS measured.
    assert out["flow_score"] is not None
    unattenuated = normalise(reading, make_baseline(), now=reading.end_ts, today=TODAY)
    assert out["combined_z"] == pytest.approx(unattenuated["combined_z"], abs=1e-9)
