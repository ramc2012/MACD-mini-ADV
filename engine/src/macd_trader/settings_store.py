from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any


class RuntimeSettingsStore:
    """Durable local settings, including broker credentials.

    The file lives in the Docker runtime volume and is permissioned to the
    owning user. Secrets are never returned by the public settings endpoint.
    """

    def __init__(self, path: str):
        self.path = Path(path)
        self._lock = RLock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return {}
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
            return payload if isinstance(payload, dict) else {}

    def save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        encoded = json.dumps(payload, indent=2, sort_keys=True)
        with self._lock:
            temporary.write_text(encoded, encoding="utf-8")
            os.chmod(temporary, 0o600)
            temporary.replace(self.path)

