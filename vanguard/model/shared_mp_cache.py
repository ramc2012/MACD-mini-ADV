"""The dependency-light shared cache (Nomad Curie's mp_core/cache.py, vendored in _vendor/ on 2026-10-25)."""
import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "_vendor" / "mp_cache.py"
_name = "tradebot_shared_mp_cache"
if _name not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_name, _path)
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_name] = _module
    _spec.loader.exec_module(_module)
cached_json = sys.modules[_name].cached_json
