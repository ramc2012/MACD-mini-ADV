"""The NSE trading calendar (Nomad Curie's module, vendored in _vendor/ when Vanguard moved to MACD-mini-ADV, 2026-10-25)."""
import importlib.util
from pathlib import Path
from datetime import timedelta

path = Path(__file__).resolve().parents[1] / "_vendor" / "trading_calendar.py"
spec = importlib.util.spec_from_file_location("vanguard_trading_calendar", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
calendar = module.trading_calendar


def is_session(day):
    return calendar.has_exchange_session("NSE", day)


def previous_session(day):
    day -= timedelta(days=1)
    while not is_session(day):
        day -= timedelta(days=1)
    return day
