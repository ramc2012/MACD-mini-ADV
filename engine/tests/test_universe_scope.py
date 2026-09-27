from macd_trader.config import Settings
from macd_trader.universe import FNO_STOCKS, INDEX_SPOTS, STOCK_SPOTS


def test_empty_csv_falls_back_to_the_full_master_list():
    from macd_trader.universe import SPOT_SYMBOLS
    assert Settings(symbols_csv="").symbols == list(SPOT_SYMBOLS)


def test_csv_narrows_the_universe():
    s = Settings(symbols_csv="NSE:NIFTY50-INDEX,NSE:ICICIBANK-EQ")
    assert s.symbols == ["NSE:NIFTY50-INDEX", "NSE:ICICIBANK-EQ"]


def test_whitespace_and_blanks_are_tolerated():
    s = Settings(symbols_csv=" NSE:A-EQ , ,NSE:B-EQ ,")
    assert s.symbols == ["NSE:A-EQ", "NSE:B-EQ"]


def test_the_five_underlying_test_universe():
    csv = "NSE:NIFTY50-INDEX,NSE:NIFTYBANK-INDEX,BSE:SENSEX-INDEX,NSE:ICICIBANK-EQ,NSE:BSE-EQ"
    s = Settings(symbols_csv=csv)
    assert len(s.symbols) == 5


def test_default_universe_covers_current_stock_derivatives():
    assert len(FNO_STOCKS) == 210
    assert len(set(FNO_STOCKS)) == len(FNO_STOCKS)
    assert {"ATHERENERG", "MAHABANK", "SAGILITY"} <= set(FNO_STOCKS)
    assert "DALBHARAT" not in FNO_STOCKS
    assert set(Settings(symbols_csv="").symbols) == set(INDEX_SPOTS.values()) | set(STOCK_SPOTS.values())
    assert len(Settings(symbols_csv="").symbols) == 214
