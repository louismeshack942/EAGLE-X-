"""Bounded tick queue — thread-safe circular buffer per symbol."""
import threading
from collections import deque
from typing import Deque, Dict, List, Optional

from app.models.tick import Tick


class BoundedTickQueue:
    """Per-symbol bounded buffer. Oldest ticks evicted when full."""

    def __init__(self, maxlen: int = 2000):
        self._maxlen = maxlen
        self._buffers: Dict[str, Deque[Tick]] = {}
        self._lock = threading.Lock()

    def push(self, tick: Tick) -> None:
        with self._lock:
            if tick.symbol not in self._buffers:
                self._buffers[tick.symbol] = deque(maxlen=self._maxlen)
            self._buffers[tick.symbol].append(tick)

    def recent(self, symbol: str, limit: int = 100) -> List[Tick]:
        with self._lock:
            buf = self._buffers.get(symbol)
            if not buf:
                return []
            items = list(buf)
        return items[-limit:]

    def latest(self, symbol: str) -> Optional[Tick]:
        with self._lock:
            buf = self._buffers.get(symbol)
            if buf:
                return buf[-1]
        return None

    def count(self, symbol: str) -> int:
        with self._lock:
            buf = self._buffers.get(symbol)
            return len(buf) if buf else 0

    def clear(self, symbol: Optional[str] = None) -> None:
        with self._lock:
            if symbol is None:
                self._buffers.clear()
            elif symbol in self._buffers:
                self._buffers[symbol].clear()


# Global singleton
tick_queue = BoundedTickQueue(maxlen=2000)
