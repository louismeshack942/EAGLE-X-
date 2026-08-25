"""Tick Recorder — every tick the squad sees, persisted to disk.

Everything downstream (digit analytics, market master, the CF, forensics)
runs on the in-memory queue. That queue is volatile: restart the service
and the squad plays from an empty tape. The recorder writes one JSONL file
per symbol so the tape is real, auditable and replayable across restarts.

Rotation: when a symbol file exceeds max_bytes it is renamed to
{symbol}.1.jsonl (one backup kept) and a fresh file begins.
"""
import json
import logging
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Deque, List, Optional

from app.models.tick import Tick

logger = logging.getLogger(__name__)

_TICK_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "ticks"
_TICK_DIR.mkdir(parents=True, exist_ok=True)

MAX_BYTES = 20 * 1024 * 1024  # 20MB per symbol file before rotation


class TickRecorder:
    def __init__(self, directory: Path = _TICK_DIR, max_bytes: int = MAX_BYTES):
        self._dir = directory
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}
        self._providers: dict[str, set] = {}
        self._first_ts: dict[str, str] = {}
        self._last_ts: dict[str, str] = {}
        self._enabled = True

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def record(self, tick: Tick) -> bool:
        """Append a tick to its symbol's tape. Never raises — a recorder
        fault must not interrupt ingestion."""
        if not self._enabled:
            return False
        try:
            with self._lock:
                path = self._dir / f"{tick.symbol}.jsonl"
                if path.exists() and path.stat().st_size >= self._max_bytes:
                    backup = path.with_suffix(".1.jsonl")
                    path.replace(backup)
                entry = {
                    "ts": tick.timestamp.isoformat() if isinstance(tick.timestamp, datetime) else str(tick.timestamp),
                    "quote": tick.quote,
                    "digit": tick.digit,
                    "provider": tick.provider,
                }
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry) + "\n")
                self._counts[tick.symbol] = self._counts.get(tick.symbol, 0) + 1
                self._providers.setdefault(tick.symbol, set()).add(tick.provider)
                ts = entry["ts"]
                self._first_ts.setdefault(tick.symbol, ts)
                self._last_ts[tick.symbol] = ts
            return True
        except Exception:  # noqa: BLE001
            logger.exception("tick recorder fault for %s", tick.symbol)
            return False

    def stats(self) -> dict:
        with self._lock:
            files = sorted(self._dir.glob("*.jsonl"))
            symbols = []
            total_bytes = 0
            for f in files:
                if f.name.endswith(".1.jsonl"):
                    continue
                size = f.stat().st_size
                total_bytes += size
                symbols.append({
                    "symbol": f.stem,
                    "bytes": size,
                    "ticks_session": self._counts.get(f.stem, 0),
                    "providers": sorted(self._providers.get(f.stem, set())),
                    "first_ts": self._first_ts.get(f.stem),
                    "last_ts": self._last_ts.get(f.stem),
                })
            return {
                "enabled": self._enabled,
                "directory": str(self._dir),
                "symbols": symbols,
                "total_bytes": total_bytes,
            }

    def load(self, symbol: str, limit: int = 1000) -> List[dict]:
        """Tail-read the most recent entries for a symbol."""
        path = self._dir / f"{symbol}.jsonl"
        if not path.exists():
            return []
        out: Deque[dict] = deque(maxlen=max(1, limit))
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return list(out)

    def purge(self, symbol: Optional[str] = None) -> int:
        """Delete tapes (one symbol or all). Returns files removed."""
        removed = 0
        with self._lock:
            pattern = f"{symbol}*.jsonl" if symbol else "*.jsonl"
            for f in self._dir.glob(pattern):
                f.unlink(missing_ok=True)
                removed += 1
            if symbol:
                self._counts.pop(symbol, None)
                self._providers.pop(symbol, None)
                self._first_ts.pop(symbol, None)
                self._last_ts.pop(symbol, None)
            else:
                self._counts.clear()
                self._providers.clear()
                self._first_ts.clear()
                self._last_ts.clear()
        return removed


tick_recorder = TickRecorder()
