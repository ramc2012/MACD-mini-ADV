"""Publishing rules for the pre-close swing watchlist."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from model.preclose_swing import IST, MIN_UNIVERSE_ROWS, create_watchlist
import model.preclose_swing as preclose_swing

TS = datetime(2026, 9, 15, 14, 15, tzinfo=IST)


class _Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):
        return None

    def fetchone(self):
        return None


class _Connection:
    def cursor(self, *args, **kwargs):
        return _Cursor()


def _stub_models(monkeypatch, universe_rows: int):
    monkeypatch.setattr(
        preclose_swing, "_model",
        lambda connection, role: SimpleNamespace(version=f"{role}-v2", status="shadow",
                                                  feature_names=("horizon_2", "horizon_3")),
    )
    monkeypatch.setattr(preclose_swing, "_decision_rows", lambda connection: (TS, [{"symbol": "NIFTY"}]))
    monkeypatch.setattr(
        preclose_swing, "form_candidates",
        lambda *args, **kwargs: ([], {"universe_rows": universe_rows, "chain_ts": TS}),
    )


def test_a_thin_universe_refuses_to_publish_a_ranking(monkeypatch) -> None:
    """358 contracts is a sliver of the market, not a ranking of it.

    The resolvable universe swung 358-7,242 contracts across 04..11-Sep; a
    "top ten either side" drawn from the thin end states a ranking the data
    cannot support.
    """
    _stub_models(monkeypatch, universe_rows=358)

    result = create_watchlist(
        _Connection(), top_n=10, allow_replay=True, capital=1_000_000.0,
        now=TS + timedelta(minutes=20),
    )

    assert result["created"] is False
    assert result["universe_rows"] == 358
    assert str(MIN_UNIVERSE_ROWS) in result["reason"]


def test_a_broad_universe_is_not_refused_for_breadth(monkeypatch) -> None:
    """The breadth gate must not swallow a healthy session: an empty candidate
    list here is refused for its own reason, not for universe size."""
    _stub_models(monkeypatch, universe_rows=MIN_UNIVERSE_ROWS + 1)

    result = create_watchlist(
        _Connection(), top_n=10, allow_replay=True, capital=1_000_000.0,
        now=TS + timedelta(minutes=20),
    )

    assert result["created"] is False
    assert result["reason"] == "no liquid contract expressions"
