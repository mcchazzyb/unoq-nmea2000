"""Owner-set boat settings, persisted on the board (tank sizes today).

Many fuel senders report level only (PGN 127505 capacity = N/A), so litres
remaining, endurance and range need a size the owner types in once. The file
lives in the capture directory (bind-mounted on the UNO Q, so it survives app
restarts) and is read tolerantly: a missing or corrupt file means "no
settings", never a broken page (steering 11).

Keys are "<instance>:<source_address>", the same key the /live API uses for
tanks (steering 07: never instance alone).
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
from pathlib import Path

_TANK_KEY = re.compile(r"^\d{1,3}:\d{1,3}$")
MAX_TANK_LITRES = 20000.0  # sanity bound at the trust boundary, not a tank spec


def validate_tank_capacities(raw: object) -> dict[str, float]:
    """Return a clean {key: litres} dict or raise ValueError naming the problem."""
    if not isinstance(raw, dict):
        raise ValueError("capacities must be an object of tank key to litres")
    out: dict[str, float] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not _TANK_KEY.match(key):
            raise ValueError(f"tank key {key!r} must look like '<instance>:<source_address>'")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"capacity for {key} must be a number of litres")
        if not math.isfinite(value) or not 0 < value <= MAX_TANK_LITRES:
            raise ValueError(f"capacity for {key} must be above 0 and at most {MAX_TANK_LITRES:g} litres")
        out[key] = float(value)
    return out


class SettingsStore:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._tank_capacities = self._load()

    def _load(self) -> dict[str, float]:
        try:
            raw = json.loads(self._path.read_text())
            return validate_tank_capacities(raw["tank_capacity_litres"])
        except (OSError, ValueError, KeyError, TypeError):
            return {}  # missing/corrupt/invalid file: start empty, never crash

    def tank_capacities(self) -> dict[str, float]:
        with self._lock:
            return dict(self._tank_capacities)

    def set_tank_capacities(self, raw: object) -> dict[str, float]:
        """Replace all tank sizes. Validates, writes atomically, then swaps the
        in-memory copy, so a failed write (disk full) leaves the old value."""
        clean = validate_tank_capacities(raw)
        payload = json.dumps({"schema_version": 1, "tank_capacity_litres": clean})
        with self._lock:
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(payload)  # OSError propagates; caller reports it
            os.replace(tmp, self._path)
            self._tank_capacities = clean
            return dict(clean)
