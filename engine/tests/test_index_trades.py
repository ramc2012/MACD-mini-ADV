"""Index trades: side and contract, no-trade, the plan, walk-forward, the loop."""
from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta

import pytest

from macd_trader import index_trades as it
from macd_trader import option_agent as oa
from test_option_agent import _db


def _row(day, root, side, moneyness, expiry="2026-10-27", y=None, trend=0.0, symbol=None):
    features = {name: 0.0 for name in oa.FEATURES}
    features.update({"u_ret_5d": trend * side, "o_moneyness": moneyness, "o_dte": 20.0,
                     "is_index": 1.0 if root in it.INDEX_SPOTS else 0.0})
    return {"day": day, "symbol": symbol or f"NSE:{root}{side}{moneyness}", "side": side, "underlying": root,
            "strike": 25000.0, "expiry": expiry, "entry_price": 100.0, "features": features, "y": y}


def test_the_better_side_is_traded_and_a_negative_view_is_no_trade():
    model = {"features": ["u_ret_5d"], "weights": [0.1], "mean": [0.0], "std": [0.01], "intercept": 0.0}
    up = [_row("d", "NIFTY", 1, 0.0, trend=0.02), _row("d", "NIFTY", -1, 0.0, trend=0.02),
          _row("d", "NIFTY", 1, 0.05, trend=0.02)]                      # 5% ITM: outside the band
    choice = it.decide(up, model)
    assert choice["NIFTY"]["side"] == 1 and choice["NIFTY"]["row"]["features"]["o_moneyness"] == 0.0
    assert choice["NIFTY"]["conviction"] > 0 and choice["NIFTY"]["alternative"]["symbol"].endswith("-10.0")
    assert choice["BANKNIFTY"] == {"side": 0, "reason": "no streamed contract that outlives a 3-session hold"}
    gloomy = {**model, "intercept": -1.0}
    assert it.decide(up, gloomy)["NIFTY"]["side"] == 0


def test_view_exposes_scorecard_vote_units_separately_from_return_models():
    history = sqlite3.connect(":memory:")
    it.ensure_schema(history)
    history.execute("INSERT INTO agent_index_models (version,trained_at,config,status) VALUES (1,'test',?, 'champion')",
                    ('{"name":"scorecard"}',))
    history.execute("INSERT INTO agent_index_trades (run_id,day,underlying,side,predicted,model_version) VALUES (1,'2026-10-13','NIFTY',0,1,1)")
    result = it.view(history, 1)[0]
    assert result["prediction_kind"] == "scorecard" and result["predicted"] == 1
    history.execute("UPDATE agent_index_models SET config=?", ('{"name":"pooled-underlying-l50"}',))
    assert it.view(history, 1)[0]["prediction_kind"] == "return"
    history.close()


def test_fit_carries_the_mean_as_intercept_and_pools_stock_rows():
    rows = [_row("d1", "NIFTY", 1, 0.0, y=0.3), _row("d1", "SBIN", 1, 0.0, y=0.1)]
    assert it.fit(rows, it.CONFIGS["pooled-underlying-l50"])["intercept"] == pytest.approx(0.2)
    assert it.fit(rows, it.CONFIGS["index-only-l100"])["intercept"] == pytest.approx(0.3)
    # A lottery ticket is clipped before it sets the level.
    assert it.fit([_row("d1", "NIFTY", 1, 0.0, y=12.0)], it.CONFIGS["index-only-l100"])["intercept"] == 2.0


def test_plan_prices_stop_and_target_off_the_index_atr():
    row = {"side": 1, "strike": 25000.0, "expiry": "2026-10-27", "entry_price": 300.0}
    p = it.plan(row, 25000.0, 250.0, "2026-09-29")
    assert (p["stop_spot"], p["target_spot"]) == (24750.0, 25375.0)
    assert p["premium_stop"] < 300.0 < p["premium_target"] and 0.05 < p["iv"] < 1.0
    put = it.plan({**row, "side": -1}, 25000.0, 250.0, "2026-09-29")
    assert (put["stop_spot"], put["target_spot"]) == (25250.0, 24625.0)


def test_walk_forward_learns_a_planted_direction_and_beats_the_coin_flip():
    rng = random.Random(5)
    rows = []
    for d in range(26):
        day = (date(2026, 8, 3) + timedelta(days=d)).isoformat()
        for root in ("NIFTY", "BANKNIFTY", "SBIN", "ITC"):
            trend = rng.gauss(0, 0.02)
            for side in (1, -1):
                y = 0.6 * trend * side / 0.02 * 0.2 + rng.gauss(0, 0.05)       # the trend's side wins
                rows.append(_row(day, root, side, 0.0, y=y, trend=trend, symbol=f"{root}{d}{side}"))
    days = sorted({r["day"] for r in rows})
    result = it.walk_forward(rows, it.CONFIGS["pooled-underlying-l50"], days[12:])
    assert result["trades"] > 0 and result["mean_excess"] > 0 and result["win_rate"] > 0.7
    assert all(t["underlying"] in it.INDEX_SPOTS for t in result["log"])


def test_loop_emits_index_trades_with_plan_and_tracks_them(tmp_path):
    history = _db(tmp_path / "historical.sqlite3")
    oa.bootstrap(history, "2026-08-17", "2026-09-25")
    trained = it.train(history)
    assert trained["status"] == "promoted" and trained["champion"] in it.CONFIGS
    assert it.train(history)["status"] == "kept"
    run = oa.emit(history, "2026-09-02", source="manual", lots={"NSE:NIFTY26OCT25000CE": 75, "NSE:NIFTY26OCT25000PE": 75})
    assert set(run["index"]) == set(it.INDEX_SPOTS)
    view = oa.picks_view(history, "2026-09-02")["index"]
    nifty = next(t for t in view if t["underlying"] == "NIFTY")
    if nifty["side"]:
        assert nifty["symbol"].startswith("NSE:NIFTY") and nifty["lot"] == {"NSE:NIFTY26OCT25000CE": 75, "NSE:NIFTY26OCT25000PE": 75}.get(nifty["symbol"])
        assert nifty["atr"] > 0 and nifty["y"] is not None and nifty["spot_path"]
    else:
        assert nifty["reason"]
    assert next(t for t in view if t["underlying"] == "SENSEX")["side"] == 0
    perf = oa.performance(history, days=400)["index"]
    assert perf["summary"]["opportunities"] >= 1
    models = oa.models_view(history)
    assert models["index_models"][0]["status"] == "champion"
    learned = oa.learn(history)
    assert learned["index"]["status"] in ("kept", "promoted")
    history.close()


def test_the_nearest_series_that_outlives_the_hold_is_used_never_a_far_month():
    model = {"features": ["u_ret_5d", "o_dte"], "weights": [0.1, 0.5], "mean": [0.0, 0.0], "std": [0.01, 1.0], "intercept": 0.0}
    near = _row("d", "NIFTY", 1, 0.0, expiry="2026-10-06", trend=0.02, symbol="NSE:NIFTY26O0625000CE")
    far = _row("d", "NIFTY", 1, 0.0, expiry="2026-10-27", trend=0.02, symbol="NSE:NIFTY26OCT25000CE")
    far["features"]["o_dte"] = 28.0                                       # the far month would score higher
    choice = it.decide([near, far], model, front={"NIFTY": "2026-10-01"})["NIFTY"]
    assert choice["row"]["symbol"] == "NSE:NIFTY26O0625000CE"
    # The front (1 Oct) expires inside the hold, so this is the next series -- and it says so.
    assert (choice["series"], choice["front_expiry"]) == ("next", "2026-10-01")
    assert it.decide([near], model, front={"NIFTY": "2026-10-06"})["NIFTY"]["series"] == "front"


def test_no_next_series_is_explained_not_silently_skipped():
    out = it.decide([], {"rule": "trend", "features": ["u_ret_5d"]}, front={"SENSEX": "2026-10-01"})
    assert out["SENSEX"]["side"] == 0 and "front series (2026-10-01) expires inside" in out["SENSEX"]["reason"]
    assert out["NIFTY"]["reason"] == "no streamed contract that outlives a 3-session hold"


def test_front_expiries_come_from_the_contracts_that_traded():
    symbols = ["BSE:SENSEX26O0172500CE", "BSE:SENSEX26O0872500CE", "NSE:NIFTY26O0622800PE", "NSE:SBIN26OCT1000CE",
               "NSE:NIFTY50-INDEX"]
    assert it.front_expiries(symbols, "2026-09-29") == {"SENSEX": "2026-10-01", "NIFTY": "2026-10-06"}


def test_refitting_the_same_index_policy_keeps_historical_version_weights(monkeypatch):
    history = sqlite3.connect(":memory:")
    oa.ensure_schema(history)
    rows = []
    for d in range(17):
        day = (date(2026, 8, 3) + timedelta(days=d)).isoformat()
        for side in (1, -1):
            rows.append(_row(day, "NIFTY", side, 0.0, y=0.01 * (d + 1) * side,
                             trend=0.002 * (d + 1), symbol=f"NIFTY{d}{side}"))
    extra = rows[-2:]
    del rows[-2:]
    monkeypatch.setattr(oa, "load_candidates", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(it, "CONFIGS", {"pooled-underlying-l50": it.CONFIGS["pooled-underlying-l50"]})
    first = it.train(history)
    columns = "weights, train_to, samples, trained_at, stats, metrics"
    original = history.execute(f"SELECT {columns} FROM agent_index_models WHERE version = ?", (first["version"],)).fetchone()
    history.execute("INSERT INTO agent_index_trades (run_id, day, underlying, side, model_version) "
                    "VALUES (1, '2026-08-19', 'NIFTY', 0, ?)", (first["version"],))
    history.commit()
    rows.extend(extra)
    again = it.train(history)
    assert again["status"] == "kept" and again["version"] != first["version"]
    assert history.execute(f"SELECT {columns} FROM agent_index_models WHERE version = ?", (first["version"],)).fetchone() == original
    assert it.active_model(history)[0] == again["version"]
    assert history.execute("SELECT model_version FROM agent_index_trades WHERE run_id = 1").fetchone()[0] == first["version"]
    history.close()
