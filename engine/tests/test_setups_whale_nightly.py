"""The setup journal, whale Layer A, and the nightly job that runs both."""
from __future__ import annotations

import sqlite3
from datetime import datetime, time, timedelta, timezone

from macd_trader import nightly, setups, whale
from macd_trader.candle_store import store_historical_candles
from macd_trader.models import Candle

IST = timezone(timedelta(hours=5, minutes=30))


def _bar(day: str, minute: int) -> int:
    opened = datetime.combine(datetime.fromisoformat(day), time(9, 15), IST)
    return int((opened + timedelta(minutes=minute)).timestamp())


# ---------------------------------------------------------------------------
# Setup journal
# ---------------------------------------------------------------------------

def _measurement(**kw) -> dict:
    row = {"symbol": "S", "day": "2026-09-01", "regime_id": "2026-08-session", "expiry_day": 0,
           "ib_width": 50.0, "break_side": "up", "first_break_bracket": "C",
           "extension_ratio": 0.8, "opened_outside_prior_value": 1, "rule80_triggered": 1,
           "rule80_completed": 0, "gap": 1, "gap_filled": 0, "gap_fill_bracket": None,
           "returned_to_prior_value": 0}
    row.update(kw)
    return row


def _session(**kw) -> dict:
    row = {"open": 24100.0, "close": 24180.0, "ib_high": 24150.0, "ib_low": 24100.0,
           "vah": 24050.0, "val": 23950.0, "day_type": "normal_variation_day"}
    row.update(kw)
    return row


def test_every_setup_gets_a_row_and_unmeasurable_ones_say_so():
    rows = setups.evaluate(_measurement(), _session())

    assert [row["setup"] for row in rows] == [s.setup_id for s in setups.SETUPS]
    unmeasurable = {row["setup"] for row in rows if not row["measurable"]}
    assert unmeasurable == {"S4", "S6", "S7", "S9", "S10"}
    assert all(row["context"] is None for row in rows if not row["measurable"])


def test_the_80_percent_rule_row_chains_context_trigger_outcome():
    by = {row["setup"]: row for row in setups.evaluate(_measurement(), _session())}

    assert (by["S1"]["context"], by["S1"]["triggered"], by["S1"]["outcome"]) == (1, 1, 0)

    # No context: trigger and outcome are null, never zero -- they were not
    # observable, which is different from having failed.
    quiet = {row["setup"]: row for row in setups.evaluate(
        _measurement(opened_outside_prior_value=0, rule80_triggered=0), _session())}
    assert (quiet["S1"]["context"], quiet["S1"]["triggered"], quiet["S1"]["outcome"]) == (0, None, None)


def test_ib_breakout_outcome_is_the_first_target():
    by = {row["setup"]: row for row in setups.evaluate(_measurement(extension_ratio=0.3), _session())}
    assert (by["S2"]["triggered"], by["S2"]["outcome"], by["S2"]["direction"]) == (1, 0, "bullish")

    by = {row["setup"]: row for row in setups.evaluate(_measurement(extension_ratio=0.6), _session())}
    assert by["S2"]["outcome"] == 1


def test_gap_rule_needs_the_gap_to_survive_bracket_c():
    early = {row["setup"]: row for row in setups.evaluate(
        _measurement(gap=1, gap_filled=1, gap_fill_bracket="B"), _session())}
    assert (early["S11"]["context"], early["S11"]["triggered"]) == (1, 0)

    held = {row["setup"]: row for row in setups.evaluate(
        _measurement(gap=1, gap_filled=0), _session())}
    assert (held["S11"]["triggered"], held["S11"]["outcome"]) == (1, 1)


def test_trend_day_continuation_checks_the_close_held():
    by = {row["setup"]: row for row in setups.evaluate(
        _measurement(extension_ratio=1.2), _session(day_type="trend_day", close=24250.0))}
    assert (by["S8"]["context"], by["S8"]["triggered"], by["S8"]["outcome"]) == (1, 1, 1)

    faded = {row["setup"]: row for row in setups.evaluate(
        _measurement(extension_ratio=1.2), _session(day_type="trend_day", close=24120.0))}
    assert faded["S8"]["outcome"] == 0


def test_the_day_type_matrix_shortlists_and_expiry_narrows_it():
    assert setups.eligible("trend_day", False) == ["S2", "S4", "S7", "S8", "S9", "S11"]
    assert "S8" not in setups.eligible("balanced_day", False)
    assert setups.eligible("balanced_day", True) == ["S3", "S5", "S12"]
    assert setups.eligible(None, False) == []


def test_journal_summary_reports_rates_with_denominators(tmp_path):
    connection = sqlite3.connect(str(tmp_path / "h.sqlite3"))
    rows = setups.evaluate(_measurement(), _session())
    rows += setups.evaluate(_measurement(day="2026-09-02", rule80_completed=1), _session())
    setups.save(connection, rows)

    summary = {row["setup"]: row for row in setups.journal_summary(connection, "S")}

    assert summary["S1"]["context"] == [2, 2]
    assert summary["S1"]["triggered"] == [2, 2]
    assert summary["S1"]["outcome"] == [1, 2]
    assert summary["S4"]["measurable"] is False
    assert setups.journal_summary(connection, "S", regime_id="nope") == []


# ---------------------------------------------------------------------------
# Whale Layer A
# ---------------------------------------------------------------------------

def _prints(n: int, qty: int = 65, step_ms: int = 1000, start: int = 1_000_000):
    return [(start + i * step_ms, 24000.0, qty, 1) for i in range(n)]


def test_a_freeze_size_print_is_flagged_and_three_in_a_minute_is_a_slicer():
    freeze = whale.freeze_quantity("NSE:NIFTY26SEPFUT", "2026-09-01")
    assert freeze == 1800
    base = _prints(40)                         # ordinary lots
    t = base[-1][0]
    base += [(t + 5000, 24001.0, freeze, 1), (t + 20000, 24001.0, freeze, 1),
             (t + 40000, 24002.0, freeze, 1)]

    events = whale.detect("NSE:NIFTY26SEPFUT", "2026-09-01", base)

    kinds = [e.kind for e in events]
    assert kinds.count("freeze_print") == 3
    assert "slicer" in kinds
    assert kinds.count("large_print") >= 1   # 1800 against a 65 median


def test_the_rolling_median_evicts_what_leaves_the_thirty_minute_window():
    """The median is kept incrementally rather than re-sorted on every print;
    it still has to be the median of exactly the last thirty minutes."""
    prints = ([(i * 1000, 24000.0, 650, 1) for i in range(60)]
              + [(1_900_000 + i * 1000, 24000.0, 65, 1) for i in range(40)]
              + [(1_960_000, 24000.0, 650, 1)])

    events = whale.detect("NSE:NIFTY26SEPFUT", "2026-09-01", prints)

    large = [e for e in events if e.kind == "large_print"]
    # Nothing while the window is all 650s; one once they have aged out.
    assert [e.ts_ms for e in large] == [1_960_000]
    assert large[0].evidence == "650 vs 30m median 65"


def test_opposite_sided_freeze_prints_do_not_make_a_slicer():
    freeze = 1800
    rows = _prints(40) + [(2_000_000, 1.0, freeze, 1), (2_010_000, 1.0, freeze, -1),
                          (2_020_000, 1.0, freeze, 1)]
    events = whale.detect("NSE:NIFTY26SEPFUT", "2026-09-01", rows)
    assert "slicer" not in [e.kind for e in events]


def test_constant_child_size_needs_regular_spacing():
    regular = _prints(40) + [(3_000_000 + i * 10_000, 1.0, 650, 1) for i in range(5)]
    assert "constant_child" in [e.kind for e in whale.detect("NSE:NIFTY26SEPFUT", "2026-09-01", regular)]

    irregular = _prints(40) + [(3_000_000 + t, 1.0, 650, 1) for t in (0, 1000, 30000, 31000, 90000)]
    assert "constant_child" not in [e.kind for e in whale.detect("NSE:NIFTY26SEPFUT", "2026-09-01", irregular)]


def test_a_single_lot_repeated_is_not_a_child_order():
    rows = _prints(60, qty=65, step_ms=10_000)
    assert "constant_child" not in [e.kind for e in whale.detect("NSE:NIFTY26SEPFUT", "2026-09-01", rows)]


def test_aggression_decays_with_a_fifteen_minute_half_life():
    events = [whale.WhaleEvent("S", 0, "freeze_print", 1, 1800, 1.0, 1.0, "")]
    assert whale.aggression(events, 0)["buy"] == 1.0
    assert abs(whale.aggression(events, 900_000)["buy"] - 0.5) < 1e-6
    assert whale.aggression([whale.WhaleEvent("S", 0, "x", -1, 1, 1.0, 2.0, "")], 0)["net"] == -2.0


def test_events_round_trip_and_rank_by_score(tmp_path):
    connection = sqlite3.connect(str(tmp_path / "h.sqlite3"))
    events = [whale.WhaleEvent("S", 1, "large_print", 1, 700, 1.0, 2.3, "a"),
              whale.WhaleEvent("S", 2, "freeze_print", -1, 1800, 1.0, 1.0, "b")]

    assert whale.save_events(connection, "2026-09-01", events) == 2
    stored = whale.events_for(connection, "2026-09-01")
    assert [row["kind"] for row in stored] == ["large_print", "freeze_print"]
    assert whale.events_for(connection, "2026-08-31") == []


class _Entry:
    def __init__(self, strike, kind, oi, volume, ltp=10.0):
        self.strike, self.option_type, self.oi, self.volume, self.ltp = strike, kind, oi, volume, ltp
        self.symbol = f"NSE:NIFTY26SEP{int(strike)}{kind}"


def test_chain_window_reports_oi_change_pcr_and_walls(tmp_path):
    connection = sqlite3.connect(str(tmp_path / "h.sqlite3"))
    whale.save_chain(connection, 1000, "NIFTY", "2026-09-08", 24000.0,
                     [_Entry(24000, "CE", 1000, 10), _Entry(24000, "PE", 2000, 30)])
    whale.save_chain(connection, 1200, "NIFTY", "2026-09-08", 24010.0,
                     [_Entry(24000, "CE", 1500, 40), _Entry(24000, "PE", 2000, 50)])

    window = whale.chain_window(connection, "NIFTY", seconds=180)

    assert window["snapshots"] == 2
    assert window["pcr_oi"] == round(2000 / 1500, 3)
    top = window["strikes"][0]
    assert (top["type"], top["d_oi"], top["d_volume"]) == ("CE", 500, 30)
    assert window["walls"][0]["type"] == "PE"
    assert whale.chain_window(connection, "BANKNIFTY")["snapshots"] == 0


# ---------------------------------------------------------------------------
# Nightly
# ---------------------------------------------------------------------------

def _seed_history(path: str, days: list[str]) -> None:
    for day in days:
        store_historical_candles(path, [
            Candle("NSE:TEST-EQ", _bar(day, i), 100.0 + i * 0.1, 100.5 + i * 0.1,
                   99.5 + i * 0.1, 100.2 + i * 0.1, 50, True) for i in range(120)
        ], None)


_TICK_SCHEMA = """
    CREATE TABLE tick_symbols (id INTEGER PRIMARY KEY, symbol TEXT UNIQUE);
    CREATE TABLE ticks (symbol_id INTEGER, ts_ms INTEGER, ltp REAL, cum_volume INTEGER,
      last_qty INTEGER, bid REAL, ask REAL, bid_qty INTEGER, ask_qty INTEGER, oi INTEGER,
      tbq INTEGER, tsq INTEGER);
    CREATE TABLE tick_session_ladder (symbol_id INTEGER, day TEXT, price REAL,
      buy_volume INTEGER, sell_volume INTEGER);
    CREATE TABLE tick_minute_flow (symbol_id INTEGER, minute_ts INTEGER, open REAL,
      high REAL, low REAL, close REAL, volume INTEGER, buy_volume INTEGER,
      sell_volume INTEGER, trades INTEGER);"""


def test_nightly_writes_every_tier_and_is_idempotent(tmp_path):
    history = str(tmp_path / "history.sqlite3")
    ticks = str(tmp_path / "ticks.sqlite3")
    sqlite3.connect(ticks).executescript(_TICK_SCHEMA)
    days = ["2026-08-31", "2026-09-01"]
    _seed_history(history, days)

    first = nightly.run(ticks, history, "2026-08-31", ["NSE:TEST-EQ", "NSE:TEST26SEP100CE"])
    second = nightly.run(ticks, history, "2026-09-01", ["NSE:TEST-EQ"], whale_symbols=["NSE:TEST-EQ"])
    again = nightly.run(ticks, history, "2026-09-01", ["NSE:TEST-EQ"])

    assert first["symbols"] == 1                     # the option was excluded
    assert second["measurements"] == 1
    assert second["journal_rows"] == len(setups.SETUPS)
    assert second["whale_events"] == 0               # no raw ticks seeded
    assert second["whale_windows"] == 0              # no chain snapshots either
    connection = sqlite3.connect(history)
    assert connection.execute("SELECT COUNT(*) FROM session_profiles").fetchone()[0] == 2
    assert connection.execute("SELECT COUNT(*) FROM setup_journal").fetchone()[0] == 2 * len(setups.SETUPS)
    assert connection.execute("SELECT COUNT(*) FROM positional_evaluations").fetchone()[0] == 2
    assert again["journal_rows"] == second["journal_rows"]
    assert connection.execute("SELECT COUNT(*) FROM setup_journal").fetchone()[0] == 2 * len(setups.SETUPS)


def test_nightly_rebuilds_windows_and_the_close_when_snapshots_exist(tmp_path):
    history = str(tmp_path / "history.sqlite3")
    ticks = str(tmp_path / "ticks.sqlite3")
    sqlite3.connect(ticks).executescript(_TICK_SCHEMA)
    day = "2026-09-01"
    _seed_history(history, [day])
    connection = sqlite3.connect(history)
    # Three windows can close (09:19, 09:22, 09:25); the first snapshot has no
    # earlier one and the 15:29 snapshot's earlier one is hours old.
    for minute, oi in ((1, 10_000), (4, 10_400), (7, 11_000), (10, 11_300), (374, 12_000)):
        whale.save_chain(connection, _bar(day, minute), "NIFTY", "2026-09-08", 24000.0,
                         [_Entry(24000, "CE", oi, 100 * minute, 120.0), _Entry(24000, "PE", 9_000, 50 * minute, 110.0)])
    connection.close()

    report = nightly.run(ticks, history, day, ["NSE:TEST-EQ"], whale_symbols=["NSE:NIFTY26SEPFUT", "NSE:TEST-EQ"])
    again = nightly.run(ticks, history, day, ["NSE:TEST-EQ"], whale_symbols=["NSE:NIFTY26SEPFUT"])

    assert report["whale_windows"] == again["whale_windows"] == 3
    assert report["whale_eod"] == {"NIFTY": "ok"} and report["whale_outcomes"] == 0
    connection = sqlite3.connect(history)
    assert connection.execute("SELECT COUNT(*), MIN(source) FROM whale_windows").fetchone() == (3, "nightly")
    assert connection.execute("SELECT COUNT(*) FROM whale_strike_windows").fetchone()[0] == 6
    # These entries carry no prior close, so there is no day-over-day build to
    # report — not one the size of the whole book. Both strikes are skipped.
    assert connection.execute(
        "SELECT d_call_oi, history_days, d_oi_skipped FROM whale_eod").fetchone() == (0, 0, 2)
