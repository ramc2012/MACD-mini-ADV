"""The option agent: contracts, no look-ahead, labels, the model, the loop."""
from __future__ import annotations

import math
import json
import random
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from macd_trader import market_calendar
from macd_trader import option_agent as oa

IST = ZoneInfo("Asia/Kolkata")
SPOTS = {"NSE:NIFTY50-INDEX": 25000.0, "NSE:SBIN-EQ": 1000.0, "NSE:ITC-EQ": 400.0, "NSE:INFY-EQ": 1500.0}
OPTIONS = {
    "NSE:SBIN26OCT1000CE": ("NSE:SBIN-EQ", 1000, 1), "NSE:SBIN26OCT1000PE": ("NSE:SBIN-EQ", 1000, -1),
    "NSE:SBIN26OCT1020CE": ("NSE:SBIN-EQ", 1020, 1), "NSE:SBIN26OCT980PE": ("NSE:SBIN-EQ", 980, -1),
    "NSE:ITC26OCT400CE": ("NSE:ITC-EQ", 400, 1), "NSE:ITC26OCT400PE": ("NSE:ITC-EQ", 400, -1),
    "NSE:INFY26OCT1500CE": ("NSE:INFY-EQ", 1500, 1), "NSE:INFY26OCT1500PE": ("NSE:INFY-EQ", 1500, -1),
    "NSE:NIFTY26OCT25000CE": ("NSE:NIFTY50-INDEX", 25000, 1), "NSE:NIFTY26OCT25000PE": ("NSE:NIFTY50-INDEX", 25000, -1),
    # A weekly expiring 15 Sep: never a candidate within three sessions of it.
    "NSE:NIFTY2691525000CE": ("NSE:NIFTY50-INDEX", 25000, 1),
}
EXPIRY = {"NSE:NIFTY2691525000CE": date(2026, 9, 15)}


def _days(first: date, last: date) -> list[date]:
    out, day = [], first
    while day <= last:
        if market_calendar.is_trading_day(day):
            out.append(day)
        day += timedelta(days=1)
    return out


def _db(path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("""CREATE TABLE historical_candles (
        symbol TEXT NOT NULL, timeframe_seconds INTEGER NOT NULL, timestamp INTEGER NOT NULL,
        open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
        volume INTEGER NOT NULL, asset_type TEXT NOT NULL, expiry TEXT, downloaded_at TEXT NOT NULL,
        PRIMARY KEY(symbol, timeframe_seconds, timestamp))""")
    rng = random.Random(7)
    level = dict(SPOTS)
    rows = []
    for day in _days(date(2026, 8, 3), date(2026, 9, 25)):
        start = int(datetime.combine(day, time(9, 15), IST).timestamp())
        drift = {s: rng.gauss(0, 0.006) for s in SPOTS}
        # Feed each segment through its own continuous close. A 15:29 option
        # print is incomplete when the configured F&O session ends at 15:40.
        end = oa.session_bounds(None, day.isoformat())[1]
        for minute in range((end - start) // 60):
            ts = start + minute * 60
            for spot, base in SPOTS.items():
                if ts >= oa.session_bounds(spot, day.isoformat())[1]:
                    continue
                prev = level[spot]
                # Mean-reverting walk, so the strikes stay near the money.
                level[spot] = prev * math.exp(drift[spot] / 375 + rng.gauss(0, 0.0006) - 0.002 * math.log(prev / base))
                rows.append((spot, ts, prev, max(prev, level[spot]), min(prev, level[spot]), level[spot], 5000, "spot", None))
            for symbol, (spot, strike, side) in OPTIONS.items():
                if ts >= oa.session_bounds(symbol, day.isoformat())[1]:
                    continue
                expiry = EXPIRY.get(symbol, date(2026, 10, 27))
                if day > expiry:
                    continue
                s = level[spot]
                dte = max(1, (expiry - day).days)
                price = max(0.0, side * (s - strike)) + 0.02 * s * math.sqrt(dte / 30)
                rows.append((symbol, ts, price, price * 1.002, price * 0.998, price, 1000, "option",
                             expiry.isoformat()))
    connection.executemany(
        "INSERT INTO historical_candles VALUES (?, 60, ?, ?, ?, ?, ?, ?, ?, ?, 'test')", rows)
    connection.commit()
    return connection


@pytest.fixture(scope="module")
def history(tmp_path_factory):
    connection = _db(tmp_path_factory.mktemp("agent") / "historical.sqlite3")
    yield connection
    connection.close()


def test_contracts_parse_with_exchange_expiry_rules():
    monthly = oa.parse_option("NSE:SBIN26SEP1000CE")
    assert monthly == {"root": "SBIN", "spot": "NSE:SBIN-EQ", "strike": 1000.0, "side": 1, "expiry": "2026-09-29"}
    weekly = oa.parse_option("BSE:SENSEX2690376800PE")
    assert (weekly["spot"], weekly["side"], weekly["expiry"], weekly["strike"]) == ("BSE:SENSEX-INDEX", -1, "2026-09-03", 76800.0)
    assert oa.parse_option("NSE:NIFTY26O0625000CE")["expiry"] == "2026-10-06"
    assert oa.parse_option("NSE:SBIN26OCTFUT") is None
    # A stored expiry wins over the ticker's.
    assert oa.parse_option("NSE:SBIN26SEP1000CE", "2026-09-28")["expiry"] == "2026-09-28"


def test_third_session_skips_the_weekend():
    assert oa.third_session_after("2026-09-24", []) == "2026-09-29"       # Thu -> Fri, Mon, Tue
    assert oa.third_session_after("2026-09-24", ["2026-09-25"]) == "2026-09-29"


def test_features_do_not_see_past_1445(history, tmp_path):
    day = "2026-09-10"
    hist, days = oa.build_history(history, day, day)
    before = {r["symbol"]: r["features"] for r in oa.build_candidates(history, day, hist, [])}
    assert before
    # Rewrite everything after 14:45 on that day; the 14:45 view must not move.
    cut = oa._as_of_epoch(day)
    copy = sqlite3.connect(tmp_path / "copy.sqlite3")
    history.backup(copy)
    copy.execute("UPDATE historical_candles SET close = close * 1.5, high = high * 1.5, volume = volume * 9 "
                 "WHERE timestamp >= ? AND timestamp < ?", (cut, cut + 3600))
    hist2, _ = oa.build_history(copy, day, day)
    after = {r["symbol"]: r["features"] for r in oa.build_candidates(copy, day, hist2, [])}
    copy.close()
    assert before == after


def test_candidates_never_cross_expiry_and_are_signed_by_side(history):
    hist, days = oa.build_history(history, "2026-09-08", "2026-09-25")
    near = oa.build_candidates(history, "2026-09-10", hist, days[days.index("2026-09-10") + 1:][:3])
    early, _ = oa.build_history(history, "2026-09-02", "2026-09-02")
    far = oa.build_candidates(history, "2026-09-02", early, [])
    assert "NSE:NIFTY2691525000CE" not in {r["symbol"] for r in near}   # expires 15 Sep, inside the hold
    assert "NSE:NIFTY2691525000CE" in {r["symbol"] for r in far}
    ce = next(r for r in near if r["symbol"] == "NSE:SBIN26OCT1000CE")
    pe = next(r for r in near if r["symbol"] == "NSE:SBIN26OCT1000PE")
    for name in oa.DIRECTIONAL:
        assert ce["features"][name] == pytest.approx(-pe["features"][name])


def test_labels_are_session_closes_and_ranked_within_day_and_side(history):
    report = oa.bootstrap(history, "2026-08-17", "2026-09-25")
    assert report["candidates"] > 0 and report["labelled"] > 0
    row = history.execute("""SELECT day, symbol, entry_price, r1, y, y_pct FROM agent_candidates
                             WHERE symbol = 'NSE:SBIN26OCT1000CE' AND y IS NOT NULL ORDER BY day LIMIT 1""").fetchone()
    day, symbol, entry, r1, y, y_pct = row
    nxt = market_calendar.next_trading_day(date.fromisoformat(day))
    close = history.execute(
        """SELECT close FROM historical_candles WHERE symbol = ? AND timestamp < ? ORDER BY timestamp DESC LIMIT 1""",
        (symbol, int(datetime.combine(nxt + timedelta(days=1), time(0), IST).timestamp()))).fetchone()[0]
    assert r1 == pytest.approx(close / entry - 1)
    assert 0 < y_pct < 1
    # The last sessions have no three closes after them yet.
    assert history.execute("SELECT COUNT(*) FROM agent_candidates WHERE day = '2026-09-25' AND y IS NOT NULL").fetchone()[0] == 0


def _synthetic_rows(days=24, per_side=40, seed=3):
    rng = random.Random(seed)
    rows = []
    for d in range(days):
        day = (date(2026, 8, 3) + timedelta(days=d)).isoformat()
        for side in (1, -1):
            for i in range(per_side):
                features = {name: rng.gauss(0, 1) for name in oa.FEATURES}
                rows.append({"day": day, "symbol": f"S{i}{side}", "side": side, "underlying": f"U{i}",
                             "features": features, "y": 0.05 * features["u_ret_1d"] + rng.gauss(0, 0.05)})
    oa.rank_within(rows)
    return rows


def test_ridge_finds_a_planted_signal_walk_forward():
    rows = _synthetic_rows()
    model = oa.fit(rows, oa.CONFIGS["ridge-all-l5"])
    weights = dict(zip(model["features"], model["weights"]))
    assert max(weights, key=lambda n: abs(weights[n])) == "u_ret_1d" and weights["u_ret_1d"] > 0
    result = oa.walk_forward(rows, oa.CONFIGS["ridge-all-l5"])
    assert result["test_days"] > 0 and result["excess"] > 0 and result["ce_ic"] > 0.3


def test_walk_forward_trains_only_on_closed_outcomes(monkeypatch):
    rows = _synthetic_rows(days=16)
    seen = []
    real_fit = oa.fit

    def spy(train_rows, config):
        seen.append(sorted({r["day"] for r in train_rows}))
        return real_fit(train_rows, config)

    monkeypatch.setattr(oa, "fit", spy)
    result = oa.walk_forward(rows, oa.CONFIGS["ridge-all-l5"])
    days = sorted({r["day"] for r in rows})
    for tested, trained in zip(result["days"], seen):
        # Day t's model knows only days whose three-session outcome closed before t.
        assert days.index(trained[-1]) <= days.index(tested["day"]) - 4


def test_picks_are_top_n_with_one_contract_per_underlying():
    rows = [{"day": "d", "symbol": f"X{i}", "side": 1, "underlying": f"U{i % 4}",
             "features": {"u_ret_1d": i, "o_ret_intraday": 0}} for i in range(12)]
    picks = oa.pick(rows, oa.fit([], oa.CONFIGS["momentum-rule"]), top_n=10)
    assert [p["symbol"] for p in picks[1]] == ["X11", "X10", "X9", "X8"]
    assert picks[-1] == []


def _two_sided(underlying, ce_signal, pe_signal):
    # momentum-rule scores u_ret_1d, which the candidates carry signed by side.
    return [{"day": "d", "symbol": f"{underlying}CE", "side": 1, "underlying": underlying,
             "features": {"u_ret_1d": ce_signal, "o_ret_intraday": 0}},
            {"day": "d", "symbol": f"{underlying}PE", "side": -1, "underlying": underlying,
             "features": {"u_ret_1d": pe_signal, "o_ret_intraday": 0}}]


def test_an_underlying_is_listed_on_one_side_only():
    rows = [*_two_sided("HDFCBANK", 0.73, 0.63), *_two_sided("SBIN", 0.1, 0.5)]
    picks = oa.pick(rows, oa.fit([], oa.CONFIGS["momentum-rule"]))
    assert [p["symbol"] for p in picks[1]] == ["HDFCBANKCE"]
    assert [p["symbol"] for p in picks[-1]] == ["SBINPE"]


def test_indices_are_left_to_the_index_lane():
    rows = [*_two_sided("BANKNIFTY", 0.73, 0.63), *_two_sided("NIFTY", 0.2, 0.9),
            *_two_sided("SENSEX", 0.1, 0.8), *_two_sided("MIDCPNIFTY", 0.9, 0.1), *_two_sided("SBIN", 0.6, 0.1)]
    picks = oa.pick(rows, oa.fit([], oa.CONFIGS["momentum-rule"]))
    assert [p["symbol"] for p in picks[1]] == ["SBINCE"]
    assert picks[-1] == []


def test_loop_trains_emits_once_and_tracks(history):
    first = oa.train(history)
    assert first["status"] == "promoted" and first["champion"] in oa.CONFIGS
    again = oa.train(history)
    assert again["status"] == "kept"
    assert history.execute("SELECT COUNT(*) FROM agent_models WHERE status = 'champion'").fetchone()[0] == 1

    run = oa.emit(history, "2026-09-25")
    assert run["status"] == "emitted" and 0 < len(run["ce"]) + len(run["pe"]) and max(len(run["ce"]), len(run["pe"])) <= oa.TOP_N
    assert oa.emit(history, "2026-09-25")["status"] == "already_emitted"
    view = oa.picks_view(history, "2026-09-25", {"NSE:SBIN26OCT1000CE": 750, "NSE:SBIN26OCT1000PE": 750})
    listed = view["ce"] + view["pe"]
    assert view["run"]["run_id"] == run["run_id"] and listed[0]["entry_price"] > 0
    # One contract per underlying, and each underlying on one side only.
    assert len({p["underlying"] for p in listed}) == len(listed)
    assert all(eq_min <= p["size"]["notional"] <= eq_max for p in listed for eq_min, eq_max in [(2_000, 25_000)])
    sbin = [p for p in listed if p["symbol"] in ("NSE:SBIN26OCT1000CE", "NSE:SBIN26OCT1000PE")]
    assert not sbin or sbin[0]["size"]["lot_size"] == 750

    oa.emit(history, "2026-09-02", source="manual")
    perf = oa.performance(history, days=400)
    past = next(r for r in perf["runs"] if r["day"] == "2026-09-02")
    side = "ce" if past["ce"]["picks"] else "pe"
    assert past[side]["labelled"] == past[side]["picks"] > 0 and past[side]["y"] is not None
    assert perf["basket"]
    models = oa.models_view(history)
    assert models["models"][0]["status"] in ("champion", "challenger") and models["constants"]["top_n"] == oa.TOP_N


def test_relabel_fills_outcomes_once_sessions_close(history):
    history.execute("UPDATE agent_candidates SET y = NULL, y_pct = NULL, r1 = NULL WHERE day = '2026-09-17'")
    history.commit()
    assert oa.relabel(history, today="2026-09-25") > 0
    assert history.execute(
        "SELECT COUNT(*) FROM agent_candidates WHERE day = '2026-09-17' AND y IS NULL").fetchone()[0] == 0


def test_relabel_persists_closed_d1_without_waiting_for_d3(history, tmp_path):
    copy = sqlite3.connect(tmp_path / "partial.sqlite3")
    history.backup(copy)
    oa.ensure_schema(copy)
    day = "2026-09-17"
    copy.execute("UPDATE agent_candidates SET y=NULL,y_pct=NULL,r1=NULL,r2=NULL,r3=NULL WHERE day=?", (day,))
    copy.commit()
    oa.relabel(copy, today="2026-09-18")
    count, d1, d2, d3, complete = copy.execute(
        "SELECT COUNT(*),SUM(r1 IS NOT NULL),SUM(r2 IS NOT NULL),SUM(r3 IS NOT NULL),SUM(y IS NOT NULL) "
        "FROM agent_candidates WHERE day=?", (day,)).fetchone()
    assert count > 0 and d1 == count
    assert d2 == d3 == complete == 0
    copy.close()


def test_the_fast_emission_path_computes_the_same_features_as_training(history):
    """emit() reads prepared prior sessions plus today; bootstrap rebuilds everything. Same 14:45 view."""
    day = "2026-09-18"
    before = {r["symbol"]: r["features"] for r in oa.load_candidates(history, day) if r["day"] == day}
    oa._prepared.clear()
    prepared = oa.prepare(history, day)
    assert day not in prepared.days and prepared.days[-1] < day
    assert oa.prepare(history, day) is prepared                     # cached for the day
    oa.emit(history, day, source="manual")
    after = {r["symbol"]: r["features"] for r in oa.load_candidates(history, day) if r["day"] == day}
    assert before and set(after) >= set(before)
    for symbol, features in before.items():
        assert after[symbol] == pytest.approx(features)


def test_stale_option_and_underlying_snapshots_are_not_candidates(history, tmp_path):
    day, symbol, spot = "2026-09-10", "NSE:SBIN26OCT1000CE", "NSE:SBIN-EQ"
    copy = sqlite3.connect(tmp_path / "stale.sqlite3")
    history.backup(copy)
    cut = oa._as_of_epoch(day)
    hist, _ = oa.build_history(copy, day, day)
    assert symbol in {r["symbol"] for r in oa.build_candidates(copy, day, hist, [])}
    # A thin option that last printed two minutes before the cutoff keeps its price ...
    copy.execute("DELETE FROM historical_candles WHERE symbol = ? AND timestamp >= ? AND timestamp < ?",
                 (symbol, cut - 120, cut))
    hist, _ = oa.build_history(copy, day, day)
    assert symbol in {r["symbol"] for r in oa.build_candidates(copy, day, hist, [])}
    # ... but one silent for more than OPTION_SNAPSHOT_LAG_SECONDS is stale.
    copy.execute("DELETE FROM historical_candles WHERE symbol = ? AND timestamp >= ? AND timestamp < ?",
                 (symbol, cut - oa.OPTION_SNAPSHOT_LAG_SECONDS - 120, cut))
    hist, _ = oa.build_history(copy, day, day)
    candidates = oa.build_candidates(copy, day, hist, [])
    assert symbol not in {r["symbol"] for r in candidates}
    assert any(r["underlying"] == "SBIN" for r in candidates)
    copy.execute("DELETE FROM historical_candles WHERE symbol = ? AND timestamp >= ? AND timestamp < ?",
                 (spot, cut - 120, cut))
    hist, _ = oa.build_history(copy, day, day)
    assert all(r["underlying"] != "SBIN" for r in oa.build_candidates(copy, day, hist, []))
    copy.close()


def test_missing_session_close_stays_unlabelled_and_missing_benchmark_does_not_shift_horizon(history, tmp_path):
    day, symbol = "2026-09-10", "NSE:SBIN26OCT1000CE"
    after = oa._sessions_after(day)
    copy = sqlite3.connect(tmp_path / "incomplete.sqlite3")
    history.backup(copy)
    hist, _ = oa.build_history(copy, day, day)
    row = next(r for r in oa.build_candidates(copy, day, hist, []) if r["symbol"] == symbol)
    incomplete = after[1]
    end = oa.session_bounds(symbol, incomplete)[1]
    copy.execute("DELETE FROM historical_candles WHERE symbol = ? AND timestamp >= ? AND timestamp < ?",
                 (symbol, end - 180, end))
    summaries = {d: oa.summarise_day(copy, d, {symbol}, options=False) for d in [day, *after]}
    # The caller's observed benchmark sessions omit day two. They must not
    # silently turn the fourth calendar session into r3.
    shifted = [after[0], after[2], market_calendar.next_trading_day(date.fromisoformat(after[2])).isoformat()]
    oa.label([row], shifted, summaries)
    assert row["r1"] is not None and row["r2"] is None and row["r3"] is not None and row["y"] is None
    assert oa.third_session_after(day, shifted) == after[-1]
    copy.close()


def test_a_final_minute_present_before_the_session_ends_is_not_a_closed_outcome(history, monkeypatch):
    day, symbol = "2026-09-10", "NSE:SBIN26OCT1000CE"
    after = oa._sessions_after(day)
    hist, _ = oa.build_history(history, day, day)
    row = next(r for r in oa.build_candidates(history, day, hist, []) if r["symbol"] == symbol)
    summaries = {d: oa.summarise_day(history, d, {symbol}, options=False) for d in [day, *after]}

    class DuringSession(datetime):
        @classmethod
        def now(cls, tz=None):
            moment = datetime.combine(date.fromisoformat(after[-1]), time(12), IST)
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    monkeypatch.setattr(oa, "datetime", DuringSession)
    oa.label([row], after, summaries)
    assert row["r1"] is not None and row["r2"] is not None and row["r3"] is None and row["y"] is None


def test_relabel_reads_the_calendar_horizon_even_when_a_benchmark_session_is_missing(history, tmp_path):
    day, symbol = "2026-09-10", "NSE:INFY26OCT1500CE"
    after = oa._sessions_after(day)
    copy = sqlite3.connect(tmp_path / "missing-benchmark.sqlite3")
    history.backup(copy)
    start, end = oa.session_bounds(oa.BENCHMARK, after[1])
    copy.execute("DELETE FROM historical_candles WHERE symbol = ? AND timestamp >= ? AND timestamp < ?",
                 (oa.BENCHMARK, start, end))
    copy.execute("UPDATE agent_candidates SET y = NULL, y_pct = NULL, r1 = NULL, r2 = NULL, r3 = NULL "
                 "WHERE day = ? AND symbol = ?", (day, symbol))
    copy.commit()
    assert oa.relabel(copy, today=after[-1]) > 0
    entry, r2, r3, y = copy.execute("SELECT entry_price, r2, r3, y FROM agent_candidates WHERE day = ? AND symbol = ?",
                                    (day, symbol)).fetchone()
    for horizon, actual in ((after[1], r2), (after[2], r3)):
        close = oa.summarise_day(copy, horizon, {symbol}, options=False)[symbol]["close"]
        assert actual == pytest.approx(close / entry - 1)
    assert y is not None
    copy.close()


def test_manual_emit_cannot_freeze_an_early_or_future_decision(monkeypatch):
    history = sqlite3.connect(":memory:")

    class BeforeCutoff(datetime):
        @classmethod
        def now(cls, tz=None):
            moment = datetime(2026, 9, 10, 14, 44, 30, tzinfo=IST)
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    monkeypatch.setattr(oa, "datetime", BeforeCutoff)
    for day in ("2026-09-10", "2026-09-11"):
        assert oa.emit(history, day, source="manual")["status"] == "cutoff_not_reached"
    assert history.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 0
    assert history.execute("SELECT COUNT(*) FROM agent_models").fetchone()[0] == 0
    history.close()


def test_every_refit_preserves_the_model_version_referenced_by_old_runs(monkeypatch):
    history = sqlite3.connect(":memory:")
    rows = _synthetic_rows(days=16, per_side=8)
    monkeypatch.setattr(oa, "load_candidates", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(oa, "CONFIGS", {"ridge-all-l5": oa.CONFIGS["ridge-all-l5"]})
    first = oa.train(history)
    columns = "weights, train_to, samples, trained_at, stats, metrics"
    original = history.execute(f"SELECT {columns} FROM agent_models WHERE version = ?", (first["version"],)).fetchone()
    history.execute("INSERT INTO agent_runs (day, emitted_at, model_version) VALUES ('2026-08-19', 'test', ?)",
                    (first["version"],))
    history.commit()
    rows[:] = _synthetic_rows(days=17, per_side=8)
    again = oa.train(history)
    assert again["status"] == "kept" and again["version"] != first["version"]
    assert history.execute(f"SELECT {columns} FROM agent_models WHERE version = ?", (first["version"],)).fetchone() == original
    assert oa.active_model(history)[0] == again["version"]
    assert history.execute("SELECT model_version FROM agent_runs").fetchone()[0] == first["version"]
    assert history.execute("SELECT COUNT(*) FROM agent_models WHERE status = 'champion'").fetchone()[0] == 1
    history.close()


def test_emissions_freeze_candidates_prices_features_and_benchmarks(history, tmp_path):
    day = "2026-09-18"
    copy = sqlite3.connect(tmp_path / "frozen.sqlite3")
    history.backup(copy)
    oa._prepared.clear()
    emitted = oa.emit(copy, day, source="manual")
    assert emitted["status"] == "emitted"
    originals = {r["symbol"]: r for r in oa.load_candidates(copy, day) if r["day"] == day}
    assert originals
    symbol, original = next(iter(originals.items()))
    cut = oa._as_of_epoch(day)
    copy.execute("UPDATE historical_candles SET close = close * 1.5, high = high * 1.5, volume = volume * 9 "
                 "WHERE timestamp >= ? AND timestamp < ?", (cut - 600, cut))
    copy.commit()
    oa._prepared.clear()
    oa.emit(copy, day, source="manual")
    oa.bootstrap(copy, day, day)
    frozen = {r["symbol"]: r for r in oa.load_candidates(copy, day) if r["day"] == day}
    assert frozen.keys() == originals.keys()
    for candidate, before in originals.items():
        assert (frozen[candidate]["entry_price"], frozen[candidate]["entry_ts"], frozen[candidate]["features"]) == (
            before["entry_price"], before["entry_ts"], before["features"])
    # An upsert from another rebuild cannot attach returns computed from a
    # different entry to an already frozen decision.
    altered = {**original, "entry_price": original["entry_price"] * 2, "y": 99.0, "r1": 99.0}
    oa._store_candidates(copy, [altered, {**altered, "symbol": "NSE:NEW26OCT1000CE"}], "history")
    assert copy.execute("SELECT y FROM agent_candidates WHERE day = ? AND symbol = ?", (day, symbol)).fetchone()[0] != 99.0
    assert copy.execute("SELECT COUNT(*) FROM agent_candidates WHERE symbol = 'NSE:NEW26OCT1000CE'").fetchone()[0] == 0
    assert copy.execute("SELECT entry_price FROM agent_picks WHERE run_id = ? AND symbol = ?",
                        (emitted["run_id"], symbol)).fetchone() in (None, (original["entry_price"],))
    copy.close()


@pytest.mark.parametrize("fails", [False, True])
def test_a_missing_or_failed_index_decision_excludes_indices_and_keeps_stocks(history, tmp_path, monkeypatch, fails):
    from macd_trader import index_trades
    day = "2026-09-18"
    copy = sqlite3.connect(tmp_path / "index-gate.sqlite3")
    history.backup(copy)
    oa._prepared.clear()
    hist, _ = oa.build_history(copy, day, day)
    candidates = oa.build_candidates(copy, day, hist, [])
    assert any(r["underlying"] in oa.INDEX_SPOTS for r in candidates)

    def unavailable(*_args, **_kwargs):
        if fails:
            raise RuntimeError("index scoring unavailable")
        return []

    monkeypatch.setattr(index_trades, "emit", unavailable)
    run = oa.emit(copy, day, source="manual")
    assert run["status"] == "emitted" and run["ce"] + run["pe"]
    picked = copy.execute("SELECT underlying FROM agent_picks WHERE run_id = ?", (run["run_id"],)).fetchall()
    assert all(root not in oa.INDEX_SPOTS for (root,) in picked)
    assert (run["index_error"] is not None) == fails
    copy.close()


def _capture_candidate(day, premium):
    features = {name: 0.0 for name in oa.FEATURES}
    features.update({"u_ret_1d": premium / 100, "o_ret_intraday": 0.01, "o_premium_pct": premium / 1000})
    return {"day": day, "symbol": "NSE:SBIN26OCT1000CE", "side": 1, "underlying": "SBIN", "spot": "NSE:SBIN-EQ",
            "strike": 1000.0, "expiry": "2026-10-27", "entry_ts": oa._as_of_epoch(day) - 60,
            "entry_price": premium, "features": features}


def test_cold_start_captures_fresh_candidates_without_a_trained_champion(tmp_path,monkeypatch):
    from macd_trader import index_trades
    day='2026-09-18';h=sqlite3.connect(tmp_path/'cold-start.sqlite3')
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            t=datetime(2026,9,18,14,47,tzinfo=oa.IST)
            return t.astimezone(tz) if tz else t.replace(tzinfo=None)
    monkeypatch.setattr(oa,'datetime',Clock)
    oa.ensure_schema(h)
    monkeypatch.setattr(oa,'train',lambda *a,**kw:pytest.fail('Live cold start must not block on training'))
    monkeypatch.setattr(oa,'trading_days',lambda *a:[day])
    monkeypatch.setattr(oa,'prepare',lambda *a:oa.History())
    monkeypatch.setattr(oa,'summarise_day',lambda *a,**kw:{})
    monkeypatch.setattr(oa,'build_candidates',lambda *a:[_capture_candidate(day,10)])
    monkeypatch.setattr(index_trades,'emit',lambda *a,**kw:[])
    r=oa.emit(h,day,source='scheduled')
    assert r['status']=='emitted' and r['ce']
    assert h.execute("select count(*) from agent_models where status='baseline' and samples=0").fetchone()[0]==1
    assert oa.active_model(h)==(None,None,None)
    assert h.execute('select count(*) from agent_candidates where y is null').fetchone()[0]==1
    assert oa.emit(h,day,source='scheduled')['status']=='already_emitted'
    assert h.execute('select count(*) from agent_runs').fetchone()[0]==1
    h.close()


def test_cold_start_after_the_window_does_not_create_a_retrospective_run(tmp_path,monkeypatch):
    h=sqlite3.connect(tmp_path/'missed-window.sqlite3')
    monkeypatch.setattr(oa,'train',lambda *a,**kw:pytest.fail('No historical training in live admission'))
    out=oa.emit(h,'2026-09-18',source='scheduled')
    assert out['status']=='awaiting_live_window'
    assert h.execute('select count(*) from agent_runs').fetchone()[0]==0
    assert h.execute('select count(*) from agent_picks').fetchone()[0]==0
    h.close()


def test_index_cold_start_baseline_is_not_a_trained_champion(tmp_path):
    from macd_trader import index_trades
    h=sqlite3.connect(tmp_path/'index-baseline.sqlite3')
    a=index_trades.baseline_model(h);b=index_trades.baseline_model(h)
    assert a==b and a[1]['rule']=='trend' and index_trades.active_model(h)==(None,None,None)
    assert h.execute('select samples,status from agent_index_models').fetchone()==(0,'baseline')
    h.close()


def _stub_capture_context(monkeypatch, day):
    from macd_trader import index_trades
    monkeypatch.setattr(oa, "active_model", lambda _h: (1, {"rule": True, "features": ["u_ret_1d", "o_ret_intraday"]}, "momentum-rule"))
    monkeypatch.setattr(oa, "trading_days", lambda *_a: [day])
    monkeypatch.setattr(oa, "prepare", lambda *_a: oa.History())
    monkeypatch.setattr(oa, "summarise_day", lambda *_a, **_k: {})
    monkeypatch.setattr(index_trades, "emit", lambda *_a, **_k: [])


@pytest.mark.parametrize("sources", [("manual", "scheduled"), ("scheduled", "scheduled")])
def test_two_connections_cannot_publish_different_prepared_snapshots_for_a_frozen_day(tmp_path, monkeypatch, sources):
    day = "2026-09-18"
    path = tmp_path / "concurrent-emissions.sqlite3"
    setup = sqlite3.connect(path)
    oa.ensure_schema(setup)
    setup.close()
    _stub_capture_context(monkeypatch, day)
    staged = threading.Barrier(2)
    local = threading.local()

    def build(*_args):
        row = _capture_candidate(day, local.premium)
        # Both SQLite connections have already built divergent local views
        # before either may capture the day. The losing view must be discarded.
        staged.wait(timeout=5)
        return [row]

    monkeypatch.setattr(oa, "build_candidates", build)

    def emit(premium, source):
        local.premium = premium
        connection = sqlite3.connect(path, timeout=10)
        try:
            return oa.emit(connection, day, source=source)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(emit, premium, source) for premium, source in zip((10.0, 20.0), sources)]
        results = [f.result(timeout=15) for f in futures]
    history = sqlite3.connect(path)
    captured = oa.load_candidates(history, day, day)
    assert len(captured) == 1 and captured[0]["entry_price"] in (10.0, 20.0)
    expected = round(oa.score({"rule": True, "features": ["u_ret_1d", "o_ret_intraday"]}, captured[0]["features"])[0], 5)
    picks = history.execute("SELECT p.entry_price, p.score, c.entry_price, c.features FROM agent_picks p "
                            "JOIN agent_candidates c ON c.day = p.day AND c.symbol = p.symbol").fetchall()
    assert picks and all(entry == stored and value == expected for entry, value, stored, _features in picks)
    assert all(json.loads(features) == captured[0]["features"] for _entry, _value, _stored, features in picks)
    if sources == ("scheduled", "scheduled"):
        assert sorted(r["status"] for r in results) == ["already_emitted", "emitted"]
        assert len(picks) == 1
    else:
        assert all(r["status"] == "emitted" for r in results) and len(picks) == 2
    history.close()


def test_missing_cutoff_bars_do_not_freeze_an_empty_day_before_late_data_arrives(tmp_path, monkeypatch):
    day = "2026-09-18"
    history = sqlite3.connect(tmp_path / "late-cutoff.sqlite3")
    _stub_capture_context(monkeypatch, day)
    available = []
    monkeypatch.setattr(oa, "build_candidates", lambda *_a: list(available))
    assert oa.emit(history, day)["status"] == "no_eligible_candidates"
    assert history.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 0
    available.append(_capture_candidate(day, 10.0))
    recovered = oa.emit(history, day)
    assert recovered["status"] == "emitted" and recovered["candidates"] == 1 and recovered["ce"]
    assert oa.emit(history, day)["status"] == "already_emitted"
    history.close()


def test_first_capture_removes_unemitted_exclusions_and_outcomes_from_a_different_entry(tmp_path, monkeypatch):
    day = "2026-09-18"
    history = sqlite3.connect(tmp_path / "unemitted-rebuild.sqlite3")
    oa.ensure_schema(history)
    _stub_capture_context(monkeypatch, day)
    previous = {**_capture_candidate(day, 10.0), "r1": 0.2, "r2": 0.3, "r3": 0.4, "y": 0.3,
                "y_pct": 0.8, "max_ret": 0.5, "min_ret": -0.1}
    excluded = {**previous, "symbol": "NSE:ITC26OCT400CE", "underlying": "ITC"}
    archived = {**excluded, "day": "2026-09-17"}
    oa._store_candidates(history, [previous, excluded, archived], "history")
    history.execute("INSERT INTO agent_runs (day, emitted_at, model_version, candidates, source) "
                    "VALUES (?, 'test', 1, 1, 'manual')", (archived["day"],))
    history.commit()
    late = _capture_candidate(day, 20.0)
    monkeypatch.setattr(oa, "build_candidates", lambda *_a: [late])
    first = oa.emit(history, day, source="manual")
    second = oa.emit(history, day, source="manual")
    assert first["candidates"] == second["candidates"] == 1
    captured = oa.load_candidates(history, day, day)
    assert len(captured) == 1 and captured[0]["symbol"] == late["symbol"] and captured[0]["entry_price"] == 20.0
    for field in ("r1", "r2", "r3", "y", "y_pct", "max_ret", "min_ret"):
        assert captured[0][field] is None
    assert history.execute("SELECT labelled_at FROM agent_candidates WHERE day = ?", (day,)).fetchone()[0] is None
    assert history.execute("SELECT candidates FROM agent_runs WHERE day = ?", (day,)).fetchall() == [(1,), (1,)]
    assert history.execute("SELECT entry_price FROM agent_picks WHERE day = ?", (day,)).fetchall() == [(20.0,), (20.0,)]
    archived_after = oa.load_candidates(history, archived["day"], archived["day"])
    assert len(archived_after) == 1 and archived_after[0]["entry_price"] == archived["entry_price"]
    assert archived_after[0]["y"] == archived["y"] and archived_after[0]["features"] == archived["features"]
    history.close()


def test_past_lists_are_listed_openable_by_id_and_open_ones_gathered(history, monkeypatch):
    from datetime import datetime as real_datetime

    class Frozen(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 9, 24, 12, 0, tzinfo=tz or oa.IST)

    monkeypatch.setattr(oa, "datetime", Frozen)
    early = oa.emit(history, "2026-09-18", source="manual")
    late = oa.emit(history, "2026-09-23", source="manual")
    runs = oa.runs_view(history, days=30)
    by_id = {r["run_id"]: r for r in runs}
    assert by_id[late["run_id"]]["open"] and not by_id[early["run_id"]]["open"]
    assert by_id[early["run_id"]]["hold_until"] == "2026-09-23"          # 18 Sep + 3 sessions (21, 22, 23)
    assert by_id[late["run_id"]]["sessions_elapsed"] == 1
    assert oa.picks_view(history, run_id=early["run_id"])["run"]["day"] == "2026-09-18"
    active = oa.active_view(history)
    open_days = [l["run"]["day"] for l in active["lists"]]
    assert "2026-09-23" in open_days and "2026-09-18" not in open_days


def test_a_list_is_compared_with_the_actual_top_performers_of_its_day(history):
    run = oa.emit(history, "2026-09-11", source="manual")
    view = oa.compare_view(history, run["run_id"], "y", top=3)
    for key in ("ce", "pe"):
        side = view[key]
        best = side["actual_top"]
        assert len(best) <= 3 and [r["rank"] for r in best] == list(range(1, len(best) + 1))
        assert all(best[i]["ret"] >= best[i + 1]["ret"] for i in range(len(best) - 1))
        if side["picks"]:
            assert side["overlap"] == sum(1 for p in side["picks"] if p["rank"] <= len(best))
            assert 0 < side["picks_percentile"] <= 1
            # capture: 0 = the average candidate, 1 = the actual top
            assert side["universe_mean"] <= side["top_mean"]
    summary = oa.compare_summary(history, days=400)
    assert any(r["run_id"] == run["run_id"] for r in summary["rows"])
