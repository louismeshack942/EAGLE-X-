"""Rolling tick-window engine.

Maintains a set of configurable trailing tick windows per symbol. Updates are
incremental (append-only within a bounded deque); per-window statistics are computed
from the stored arrays only when requested. We never silently mix providers: every
window is built from a single provider tag, enforced at ingest time.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from app.core.ticks import NormalizedTick

# Default set of trailing windows (ticks). Ordered from short to long horizon.
DEFAULT_WINDOWS = (25, 50, 100, 250, 500, 1000)


@dataclass
class WindowSnapshot:
    """Lightweight view of a trailing window's raw data."""

    size: int = 0  # requested window size (the window it belongs to)
    n: int = 0  # actual number of ticks in the window
    oldest_epoch_ms: int = 0
    newest_epoch_ms: int = 0
    oldest_quote: float | None = None
    newest_quote: float | None = None
    digits: list[int] = field(default_factory=list)
    provider: str = ""


class TickWindow:
    """A single bounded trailing window (FIFO) for one symbol."""

    __slots__ = ("limit", "digits", "epochs", "quotes", "n_total")

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.digits: deque[int] = deque(maxlen=limit)
        self.epochs: deque[int] = deque(maxlen=limit)
        self.quotes: deque[float] = deque(maxlen=limit)
        # total ticks fed (grows) — used for freshness/gap bookkeeping
        self.n_total = 0

    def push(self, tick: NormalizedTick) -> None:
        """Append a tick; O(1) amortized. Never recalculates aggregates."""
        self.digits.append(tick.last_digit)
        self.epochs.append(tick.epoch_ms)
        self.quotes.append(tick.quote)
        self.n_total += 1

    @property
    def n(self) -> int:
        return len(self.digits)

    def is_full(self) -> bool:
        return self.n >= self.limit

    def as_snapshot(self) -> WindowSnapshot:
        snap = WindowSnapshot(size=self.limit, n=self.n, provider="")
        if self.n:
            snap.oldest_epoch_ms = self.epochs[0]
            snap.newest_epoch_ms = self.epochs[-1]
            snap.oldest_quote = self.quotes[0]
            snap.newest_quote = self.quotes[-1]
            snap.digits = list(self.digits)
        return snap


class WindowManager:
    """Tracks several configurable windows + gap bookkeeping for one symbol.

    A single provider tag is enforced per manager; mixing harness into a deriv_live
    window (or vice-versa) is a hard error rather than a silent symptom.
    """

    def __init__(
        self,
        symbol: str,
        provider: str,
        sizes: tuple[int, ...] = DEFAULT_WINDOWS,
        *,
        max_total_ticks: int = 100_000,
    ) -> None:
        self.symbol = symbol
        self.provider = provider
        self.sizes = sizes
        self.max_total_ticks = max_total_ticks
        self._windows: dict[int, TickWindow] = {s: TickWindow(s) for s in sizes}
        self._epoch_seen: set[int] = set()  # duplicate detection within a large horizon
        self._last_seen_tick: dict[int, int] = {}  # per digit: global tick counter
        self.duplicate_ticks = 0
        self.invalid_ticks = 0
        self.n_total = 0
        self.exceeds_horizon = False  # too many ticks to fully dedupe

    def push(self, tick: NormalizedTick) -> None:
        if tick.provider != self.provider:
            raise ValueError(
                f"provider mismatch in window manager for {self.symbol}: "
                f"window={self.provider}, tick={tick.provider}"
            )
        # Duplicate detection: keep a windowed set of recent epochs. When the horizon
        # is exceeded we stop counting duplicates (data too old relative to queue size).
        if not self.exceeds_horizon:
            if tick.epoch_ms in self._epoch_seen:
                self.duplicate_ticks += 1
                return  # do not feed duplicates into windows
            self._epoch_seen.add(tick.epoch_ms)
            if len(self._epoch_seen) >= self.max_total_ticks:
                self.exceeds_horizon = True

        digit = tick.last_digit
        if not (0 <= digit <= 9):
            self.invalid_ticks += 1
            return

        for w in self._windows.values():
            w.push(tick)
        self._last_seen_tick[digit] = self.n_total
        self.n_total += 1

    def window(self, size: int) -> TickWindow:
        return self._windows[size]

    def snapshots(self, *, only: int | None = None) -> dict[int, WindowSnapshot]:
        if only is not None:
            return {only: self._windows[only].as_snapshot()}
        return {s: self._windows[s].as_snapshot() for s in self.sizes}

    def ticks_since_last(self, digit: int) -> int:
        """Number of ticks observed since `digit` last appeared (0 if never / looks current)."""
        last = self._last_seen_tick.get(digit)
        if last is None:
            return -1  # never seen
        return max(self.n_total - last - 1, 0)

    def ticks_since_last_safe(self, digit: int) -> int | None:
        last = self._last_seen_tick.get(digit)
        if last is None:
            return None
        return max(self.n_total - last - 1, 0)


__all__ = ["DEFAULT_WINDOWS", "TickWindow", "WindowManager", "WindowSnapshot"]