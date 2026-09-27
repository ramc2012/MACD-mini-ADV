import importlib.util
from pathlib import Path

from macd_trader.models import Candle


def _research_module():
    path = Path(__file__).parents[1] / "scripts" / "premium_confirmation_research.py"
    spec = importlib.util.spec_from_file_location("premium_confirmation_research", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _bar(timestamp: int, price: float = 10.0) -> Candle:
    return Candle("X", timestamp, price, price, price, price, 100, True)


def test_forward_option_return_requires_a_complete_clock_aligned_path():
    research = _research_module()
    assert research.forward_trade([_bar(0), None, _bar(600)], 0, .30, .30, .25, 2) is None
    assert research.forward_trade([_bar(0), _bar(300), _bar(900)], 0, .30, .30, .25, 2) is None


def test_forward_option_return_includes_two_sided_slippage():
    research = _research_module()
    value = research.forward_trade(
        [_bar(0), _bar(300), _bar(600)], 0, .30, .30, .25, 2,
    )
    assert value is not None
    assert -0.51 < value < -0.49
