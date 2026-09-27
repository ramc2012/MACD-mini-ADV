"""Read models behind the auction page."""
from __future__ import annotations

import json
import sqlite3

from macd_trader import auction_views
from macd_trader.base_rates import compare_session, grouped_summaries, summarise
from macd_trader.profile_history import ensure_tables


def _history(path: str) -> None:
    connection = sqlite3.connect(path)
    ensure_tables(connection)
    with connection:
        connection.executemany(
            """INSERT INTO session_profiles
               (symbol, day, open, high, low, close, poc, vah, val, ib_high, ib_low,
                volume, buy_volume, sell_volume, cumulative_delta, imbalance, trades,
                day_type, single_prints, value_migration, levels, source, built_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'ladder','')""",
            [("S", "2026-08-31", 100, 104, 98, 103, 101, 102, 100, 103, 99,
              1000, 600, 400, 200, 0.2, 50, "trend_day", "[]", "higher", 9),
             ("S", "2026-09-01", 103, 108, 102, 107, 105, 106, 104, 106, 103,
              1200, 700, 500, 200, 0.17, 60, "balanced_day", "[]", "higher", 11)])
        connection.execute(
            """INSERT INTO period_profiles
               (symbol, period, period_start, period_end, sessions, open, high, low, close,
                poc, vah, val, volume, buy_volume, sell_volume, cumulative_delta,
                naked_pocs, built_at)
               VALUES ('S','week','2026-08-31','2026-09-01',2,100,108,98,107,
                       104,106,100,2200,1300,900,400,?,'')""",
            (json.dumps([95.0, 99.5]),))
    connection.close()


def _ticks(path: str) -> None:
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE tick_symbols (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT UNIQUE);
        CREATE TABLE tick_minute_flow (symbol_id INTEGER, minute_ts INTEGER, open REAL,
            high REAL, low REAL, close REAL, volume INTEGER, buy_volume INTEGER,
            sell_volume INTEGER, delta INTEGER, trades INTEGER, ticks INTEGER, vwap REAL,
            quote_n INTEGER, mid_n INTEGER, tick_n INTEGER, zero_tick_n INTEGER,
            pending_n INTEGER, conflict_n INTEGER, unclassified INTEGER, avg_spread REAL,
            ofi REAL, ofi_events INTEGER, avg_confidence REAL);
    """)
    connection.execute("INSERT INTO tick_symbols (symbol) VALUES ('S')")
    # 09:15 and 09:16 IST on 2026-09-01. Delta rises while OFI falls.
    base = 1788241500
    connection.executemany(
        "INSERT INTO tick_minute_flow VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(base, 100, 101, 99, 100.5, 500, 300, 200, 100, 20, 40, 100.2,
          30, 2, 5, 3, 0, 0, 0, 0.1, -800.0, 120, 0.72),
         (base + 60, 100.5, 102, 100, 101.5, 400, 260, 140, 120, 18, 36, 101.1,
          28, 1, 4, 3, 0, 0, 0, 0.1, -600.0, 110, 0.55)])
    connection.commit()
    connection.close()


def test_context_reports_location_against_every_timeframe(tmp_path):
    path = str(tmp_path / "history.sqlite3")
    _history(path)

    out = auction_views.context(path, "S", as_of="2026-09-02", price=110.0)

    assert out["prior_day"]["day"] == "2026-09-01"
    assert out["week"]["period_start"] == "2026-08-31"
    assert out["location"] == {"day": "above_value", "week": "above_value", "month": "unknown"}
    assert out["alignment"] == "aligned_above_value"
    assert out["naked_pocs"] == [101.0]
    assert out["levels"]["pd_poc"] == 105.0
    assert out["regime"]["regime_id"] == "2026-08-session"


def test_context_falls_back_to_the_prior_close_without_a_live_mark(tmp_path):
    path = str(tmp_path / "history.sqlite3")
    _history(path)

    out = auction_views.context(path, "S", as_of="2026-09-02")

    assert out["price"] == 107.0


def test_every_view_returns_empty_before_a_backfill_has_run(tmp_path):
    """A missing table is an empty panel, not a 500."""
    path = str(tmp_path / "blank.sqlite3")
    sqlite3.connect(path).close()

    assert auction_views.symbols(path) == []
    assert auction_views.sessions(path, "S") == []
    assert auction_views.periods(path, "S") == {"week": [], "month": []}
    assert auction_views.base_rates(path, "S")["groups"] == []
    assert auction_views.flow(path, "S", "2026-09-01")["series"] == []


def test_sessions_and_periods_decode_their_json_columns(tmp_path):
    path = str(tmp_path / "history.sqlite3")
    _history(path)

    assert auction_views.sessions(path, "S")[0]["single_prints"] == []
    assert auction_views.periods(path, "S")["week"][0]["naked_pocs"] == [95.0, 99.5]
    assert auction_views.symbols(path) == ["S"]


def test_flow_accumulates_both_series_and_flags_a_sign_disagreement(tmp_path):
    path = str(tmp_path / "ticks.sqlite3")
    _ticks(path)

    out = auction_views.flow(path, "S", "2026-09-01")

    assert out["minutes"] == 2
    assert out["cumulative_delta"] == 220
    assert out["cumulative_ofi"] == -1400
    # Book pressure and inferred delta point opposite ways: the flag exists so
    # the page can say so rather than blending them into one line.
    assert out["diverged"] is True
    assert out["mean_confidence"] == 0.635
    assert [row["cvd"] for row in out["series"]] == [100, 220]
    assert [row["cumulative_ofi"] for row in out["series"]] == [-800, -1400]


def test_flow_days_lists_recorded_sessions_newest_first(tmp_path):
    path = str(tmp_path / "ticks.sqlite3")
    _ticks(path)

    assert auction_views.flow_days(path, "S") == ["2026-09-01"]


# ---------------------------------------------------------------------------
# Base-rate summarising
# ---------------------------------------------------------------------------

def _measurement(day: str, **kwargs) -> dict:
    row = {"symbol": "S", "day": day, "regime_id": "2026-08-session", "ib_width": 10.0,
           "break_side": "up", "extension_ratio": 0.5, "range_ib_ratio": 1.5,
           "first_break_bracket": "C", "opened_outside_prior_value": 0, "gap": 0}
    row.update(kwargs)
    return row


def test_rates_are_returned_with_their_denominator():
    rows = [_measurement("2026-09-01"), _measurement("2026-09-02", break_side="none")]

    summary = summarise(rows)

    assert summary["ib_broken"] == (1, 2)
    assert summary["extension_over_25pct"] == (2, 2)
    assert summary["extension_over_100pct"] == (0, 2)
    assert summary["median_extension_ratio"] == 0.5


def test_conditional_rates_use_only_the_sessions_that_qualified():
    """The 80% rule's denominator is sessions that opened outside value, not
    every session — otherwise it looks far rarer than it is."""
    rows = [
        _measurement("2026-09-01", opened_outside_prior_value=1, rule80_triggered=1,
                     rule80_completed=1),
        _measurement("2026-09-02", opened_outside_prior_value=1, rule80_triggered=0),
        _measurement("2026-09-03", opened_outside_prior_value=0),
    ]

    summary = summarise(rows)

    assert summary["opened_outside_value"] == (2, 3)
    assert summary["rule80_triggered"] == (1, 2)
    assert summary["rule80_completed"] == (1, 1)


def test_grouping_flags_a_span_that_crosses_a_rule_change():
    rows = [_measurement("2026-07-15", regime_id="2026-07-freeze"),
            _measurement("2026-08-15", regime_id="2026-08-session")]

    grouped = grouped_summaries(rows)

    assert grouped["crosses"] == ["2026-08-03"]
    assert [group["key"] for group in grouped["groups"]] == [
        "all", "2026-07-freeze", "2026-08-session"]
    assert grouped["groups"][1]["sessions"] == 1


def test_a_session_is_compared_against_its_own_regime():
    rows = [_measurement(f"2026-09-0{i}", extension_ratio=0.1) for i in range(1, 5)]
    summary = summarise(rows)

    comparisons = compare_session(_measurement("2026-09-05", extension_ratio=0.6), summary)

    by_label = {row["label"]: row for row in comparisons}
    assert by_label["extension ≥ 50% of IB"]["today"] is True
    # None of the four reference sessions reached 50%, so today is unusual.
    assert by_label["extension ≥ 50% of IB"]["base_rate"] == 0.0
    assert by_label["extension ≥ 50% of IB"]["sample"] == 4


def test_base_rates_view_pairs_today_with_its_regime_table(tmp_path):
    path = str(tmp_path / "history.sqlite3")
    connection = sqlite3.connect(path)
    from macd_trader.profile_history import MEASUREMENT_SCHEMA, save_measurements
    connection.executescript(MEASUREMENT_SCHEMA)
    save_measurements(connection, [_measurement("2026-08-31"), _measurement("2026-09-01")])
    connection.close()

    out = auction_views.base_rates(path, "S")

    assert out["today"]["day"] == "2026-09-01"
    assert out["today"]["regime_id"] == "2026-08-session"
    assert out["today"]["regime_sessions"] == 2
    assert any(row["label"] == "IB broken" for row in out["today"]["comparisons"])
