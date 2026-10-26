"""The MACD lane's entry gate: study features live, skip the weakest, spend cash best-first, journal all."""
import asyncio
import math
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from macd_trader import entry_scorer as es
from macd_trader.entry_model import MODEL

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 9, 30, 11, 15, tzinfo=IST)


def feats(**over):
    base = dict(price=20.0, side=-1, strike=1000.0, expiry="2026-10-27", now=NOW, is_index=False,
                spot=980.0, spot_prev_close=1000.0, spot_open=995.0, spot_close_5d=1020.0, spot_vwap=990.0,
                nifty=24_800.0, nifty_prev_close=25_000.0, day_open=16.0, day_high=22.0, day_low=15.0,
                option_prev_close=17.0, volume_today=50_000, prior_day_volume=100_000,
                macd_history=[0.4, -0.1, -0.3, -0.2], macd_preview=0.05, crowd_same=4, crowd_other=1)
    base.update(over)
    return es.build_features(**base)


def test_features_follow_the_study_definitions():
    f = feats()
    assert set(f) == set(MODEL["features"]) | {"dte", "dte_frac"}          # dte is journalled, no longer scored
    assert f["dte"] == 27 and f["minutes_since_open"] == 120
    assert f["dte_frac"] == pytest.approx(27 + 4.25 / 24)                    # to the 15:30 expiry
    assert f["theta_share"] == pytest.approx(1 - math.sqrt((f["dte_frac"] - 2) / f["dte_frac"]))
    assert f["moneyness"] == pytest.approx(-math.log(980 / 1000))          # a put 2% in the money is positive
    assert f["u_ret_prev"] == pytest.approx(0.02)                           # spot -2% helps a put
    assert f["nifty_ret_prev"] == pytest.approx(0.008)
    assert f["o_range_pos"] == pytest.approx((20 - 15) / (22 - 15))
    assert (f["dip_bars"], f["dip_depth_pct"]) == (3, pytest.approx(-0.3 / 20))
    assert f["macd_slope_pct"] == pytest.approx((0.05 - -0.2) / 20)
    assert f["o_vol_ratio"] == pytest.approx(math.log1p(50_000 / 100_001))


def test_expiry_is_judged_against_the_expected_hold_and_the_cut_is_a_study_percentile():
    # 27 and 13 days out both lose little time value over a 2-day hold; one inside the hold loses all of it.
    assert es.theta_share(27.0, 2.0) < 0.05 and es.theta_share(13.0, 2.0) < 0.1 and es.theta_share(1.5, 2.0) == 1.0
    assert abs(es.probability(feats(expiry="2026-10-27")) - es.probability(feats(expiry="2026-10-13"))) < 0.01
    assert es.threshold(0.3) == MODEL["percentiles"]["30"]
    assert es.threshold(0.0) == MODEL["percentiles"]["5"]                  # nearest tabulated percentile


def test_the_cut_follows_the_day_s_own_scores_once_there_are_enough(tmp_path):
    settings = SimpleNamespace(macd_entry_filter=True, macd_entry_skip_fraction=0.3, macd_entry_rank_seconds=0.0)
    gate = es.EntryGate(settings, str(tmp_path / "h.sqlite3"))
    assert gate.cut() == es.threshold(0.3)                                  # too few scores yet
    for k in range(es.MIN_DAY_SCORES):
        gate.admit(SimpleNamespace(symbol=f"NSE:X{k}26OCT100CE", signal_id=str(k)), feats(crowd_same=k))
    today = gate._day_scores[datetime.now(IST).date().isoformat()]
    assert gate.cut() == pytest.approx(es.quantile(today, 0.3)) and gate.status()["cut_basis"] == "day"


class Book:
    """One entry's worth of cash: whoever is asked first gets it."""
    def __init__(self):
        self.positions, self.asked = {}, []

    async def on_signal(self, signal):
        self.asked.append(signal.symbol)
        if not self.positions:
            self.positions[signal.symbol] = object()


def test_weak_signals_are_skipped_and_cash_goes_to_the_best_first(tmp_path):
    db = tmp_path / "h.sqlite3"
    settings = SimpleNamespace(macd_entry_filter=True, macd_entry_skip_fraction=0.3, macd_entry_rank_seconds=0.0)
    gate = es.EntryGate(settings, str(db))
    weak = feats(expiry="2026-10-01", price=60.0, crowd_same=0, macd_preview=-0.5)     # expires inside the 2-day hold
    strong_a = feats(expiry="2026-11-24", crowd_same=12)
    strong_b = feats(expiry="2026-10-27", crowd_same=6)
    sig = lambda s: SimpleNamespace(symbol=s, signal_id=s)
    assert gate.admit(sig("NSE:WEAK26OCT100PE"), weak) is False
    assert gate.admit(sig("NSE:BETTER26OCT100PE"), strong_b) is True
    assert gate.admit(sig("NSE:BEST26NOV100PE"), strong_a) is True
    book = Book()
    asyncio.run(gate._drain(book, book))
    assert book.asked == ["NSE:BEST26NOV100PE", "NSE:BETTER26OCT100PE"]   # best-first
    assert gate.counts["TAKEN"] == 1 and gate.counts["NOT_FILLED"] == 1 and gate.counts[es.EXPIRES_WITHIN_HOLD] == 1
    gate._write(gate._journal)
    rows = sqlite3.connect(db).execute("SELECT symbol, decision FROM macd_entry_scores ORDER BY rowid").fetchall()
    assert rows == [("NSE:WEAK26OCT100PE", "EXPIRES_WITHIN_HOLD"), ("NSE:BEST26NOV100PE", "TAKEN"),
                    ("NSE:BETTER26OCT100PE", "NOT_FILLED")]


def test_the_day_tracker_resets_each_session():
    days = es.DayTracker()
    days.observe("X", 10.0, datetime(2026, 9, 29, 5, 0, tzinfo=UTC))
    days.observe("X", 12.0, datetime(2026, 9, 29, 6, 0, tzinfo=UTC))
    days.observe("X", 9.0, datetime(2026, 9, 29, 7, 0, tzinfo=UTC))
    assert days.get("X") == (10.0, 12.0, 9.0)
    days.observe("X", 11.0, datetime(2026, 9, 30, 4, 0, tzinfo=UTC))
    assert days.get("X") == (11.0, 11.0, 11.0)


def test_a_low_score_is_queued_with_a_smaller_size_never_skipped(tmp_path):
    # Owner, 26 Oct 2026: no score cut-off (ATHERENERG 1400 CE scored 0.441 against a 0.452 cut, then 76.5 -> 240).
    settings = SimpleNamespace(macd_entry_filter=True, macd_entry_skip_fraction=0.3, macd_entry_rank_seconds=0.0,
                               macd_entry_min_size_fraction=0.25)
    gate = es.EntryGate(settings, str(tmp_path / "h.sqlite3"))
    sig = lambda s: SimpleNamespace(symbol=s, signal_id=s)
    weak, strong = sig("NSE:WEAK26NOV100CE"), sig("NSE:STRONG26NOV100CE")
    assert gate.admit(weak, feats(expiry="2026-11-24", crowd_same=0, macd_preview=-0.5)) is True
    assert gate.admit(strong, feats(expiry="2026-11-24", crowd_same=12)) is True
    assert 0.25 <= weak.size_fraction < strong.size_fraction <= 1.0
    assert "SKIPPED_LOW_SCORE" not in gate.counts


def test_size_fraction_is_linear_in_rank_with_a_floor():
    assert es.size_fraction(0.0, 0.25) == pytest.approx(0.25)
    assert es.size_fraction(1.0, 0.25) == pytest.approx(1.0)
    assert es.size_fraction(0.5, 0.25) == pytest.approx(0.625)
    day = [i / 100 for i in range(100)]
    assert es.score_percentile(0.441, day) == pytest.approx(0.45)
    assert 0.0 <= es.score_percentile(0.0, []) <= es.score_percentile(1.0, []) == 1.0
