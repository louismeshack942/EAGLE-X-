"""Analysis engine — unifies tick windows, statistics, and data quality.

Realtime flow:
    tick arrives -> WindowManager.push(normalized_tick) -> on each push the manager
    for the symbol is updated incrementally. The full analysis snapshot for a symbol is
    assembled on demand (cheap: pure-python over the stored deques).

Registry: per (symbol, provider) one WindowManager. Providers are never silently mixed:
pushing a harness tick into a deriv_live manager raises and is surfaced.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.core.data_quality import assess_data_quality
from app.core.ticks import NormalizedTick
from app.services import analytics as A
from app.services.window_engine import DEFAULT_WINDOWS, WindowManager

# windows used the short/medium/long multi-window comparison
SHORT_WIN = 50
MEDIUM_WIN = 250
LONG_WIN = 1000

# Barriers to evaluate for over/under (Deriv digit contract thresholds).
OVER_UNDER_BARRIERS = [d for d in range(0, 10)]
DEFAULT_OU_BARRIER = 4


@dataclass
class WindowAnalysis:
    size: int
    n: int
    data_quality: dict
    digit_frequency: dict = field(default_factory=dict)
    z_scores: list = field(default_factory=list)
    gaps: dict = field(default_factory=dict)
    streaks: dict = field(default_factory=dict)
    parity: dict = field(default_factory=dict)
    over_under: dict = field(default_factory=dict)
    matches_differs: dict = field(default_factory=dict)
    chi_square: dict = field(default_factory=dict)


class AnalysisManager:
    """In-memory registry of WindowManagers + snapshot assembly."""

    def __init__(self, sizes: tuple[int, ...] = DEFAULT_WINDOWS) -> None:
        self.sizes = sizes
        self._managers: dict[tuple[str, str], WindowManager] = {}
        self._states: dict[str, str] = {}  # symbol -> connection state

    # ---- registry ------------------------------------------------
    def manager(self, symbol: str, provider: str) -> WindowManager:
        key = (symbol, provider)
        if key not in self._managers:
            self._managers[key] = WindowManager(symbol, provider, self.sizes)
        return self._managers[key]

    def mark_connection(self, symbol: str, state: str) -> None:
        self._states[symbol] = state

    def connection_state(self, symbol: str) -> str:
        return self._states.get(symbol, "disconnected")

    def push(self, tick: NormalizedTick) -> None:
        if tick.provider not in ("harness", "deriv_live", "recorded"):
            raise ValueError(f"unknown provider {tick.provider!r}")
        mgr = self.manager(tick.symbol, tick.provider)
        mgr.push(tick)

    def push_raw_dict(self, d: dict) -> None:
        """Accept a normalized tick as a dict (from the event bus) and forward."""
        from app.core.ticks import NormalizedTick

        tick = NormalizedTick(
            symbol=d["symbol"],
            epoch_ms=d["epoch_ms"],
            quote=d["quote"],
            last_digit=d.get("last_digit", -1),
            provider=d["provider"],
        )
        self.push(tick)

    # ---- snapshot -------------------------------------------------
    def snapshot(self, symbol: str, *, window: int | None = None) -> dict:
        """Unified analysis object described in the Phase 2 spec (§13)."""
        mgr = self._find_manager(symbol)
        now_ms = int(time.time() * 1000)
        if mgr is None:
            return self._empty_snapshot(symbol, now_ms)

        windows = mgr.snapshots(only=window)
        conn = self.connection_state(symbol)
        source = mgr.provider

        win_analyses: dict[int, dict] = {}
        for size, snap in windows.items():
            win_analyses[size] = self._analyze_window(
                snap, mgr, now_ms, source, conn
            )

        # multi-window (uses best-available short/medium/long)
        mw = self._multi_window(mgr, now_ms, source, conn)

        return {
            "symbol": symbol,
            "timestamp": now_ms,
            "source": source,
            "connection_state": conn,
            "windows": win_analyses,
            "multi_window": mw,
            "provider": source,
        }

    def _find_manager(self, symbol: str) -> WindowManager | None:
        # prefer deriv_live manager, else harness, else recorded, else any
        for provider in ("deriv_live", "harness", "recorded"):
            m = self._managers.get((symbol, provider))
            if m is not None:
                return m
        for key, m in self._managers.items():
            if key[0] == symbol:
                return m
        return None

    def _empty_snapshot(self, symbol: str, now_ms: int) -> dict:
        return {
            "symbol": symbol,
            "timestamp": now_ms,
            "source": "",
            "connection_state": self.connection_state(symbol),
            "windows": {},
            "multi_window": {"state": "INSUFFICIENT_DATA", "summary": "No data yet."},
            "provider": "",
        }

    def _analyze_window(
        self, snap, mgr: WindowManager, now_ms: int, source: str, conn: str
    ) -> dict:
        digits = snap.digits
        n = snap.n
        q = assess_data_quality(
            n=n,
            window_size=snap.size,
            newest_epoch_ms=snap.newest_epoch_ms,
            now_ms=now_ms,
            duplicate_ticks=mgr.duplicate_ticks,
            invalid_ticks=mgr.invalid_ticks,
            source=source,
            connection_state=conn,
        )
        has_data = n > 0
        return {
            "size": snap.size,
            "n": n,
            "oldest_epoch_ms": snap.oldest_epoch_ms,
            "newest_epoch_ms": snap.newest_epoch_ms,
            "oldest_quote": snap.oldest_quote,
            "newest_quote": snap.newest_quote,
            "data_quality": q.to_dict(),
            "digit_frequency": (
                A.digit_frequency(digits) if has_data else {"n": 0}
            ),
            "z_scores": A.z_scores(digits) if has_data else [0.0] * 10,
            "gaps": A.gap_statistics(digits) if has_data else {},
            "streaks": A.streak_statistics(digits) if has_data else {},
            "parity": A.parity_analysis(digits) if has_data else {},
            "over_under": {
                b: A.over_under_analysis(digits, barrier=b)
                for b in OVER_UNDER_BARRIERS
            },
            "matches_differs": (
                A.matches_differs_analysis(digits) if has_data else {}
            ),
            "chi_square": A.chi_square_uniformity(digits)
            if has_data
            else {"n": 0, "applicable": False},
        }

    def _multi_window(self, mgr: WindowManager, now_ms: int, source: str, conn: str) -> dict:
        snaps = mgr.snapshots()
        def freq(size: int) -> dict:
            s = snaps.get(size)
            if not s or not s.n:
                return {"n": 0}
            return A.digit_frequency(s.digits)

        short = freq(SHORT_WIN)
        medium = freq(MEDIUM_WIN)
        long = freq(LONG_WIN)
        state = A.multi_window_state(short, medium, long)
        return {
            "short_window": SHORT_WIN,
            "medium_window": MEDIUM_WIN,
            "long_window": LONG_WIN,
            "short": {"n": short.get("n", 0), "most_frequent": short.get("most_frequent", -1)},
            "medium": {"n": medium.get("n", 0), "most_frequent": medium.get("most_frequent", -1)},
            "long": {"n": long.get("n", 0), "most_frequent": long.get("most_frequent", -1)},
            "agreement": state,
            "state": state,
            "summary": {
                "STABLE": "All horizons agree on the leading digit.",
                "MULTI_WINDOW_SUPPORT": "Short/medium/horizon lean consistent; longest window incomplete.",
                "CONFLICTING": "Horizons disagree; evidence not aligned.",
                "INSUFFICIENT_DATA": "Not enough data to compare horizons.",
            }.get(state, ""),
        }


analysis_manager = AnalysisManager()


def register_realtime_push(d: dict) -> None:
    """Hook called by the data bus for every normalized tick (Phase 2 realtime)."""
    try:
        analysis_manager.push_raw_dict(d)
    except Exception:
        # Never let analysis faults interrupt ingestion.
        pass


__all__ = ["AnalysisManager", "analysis_manager", "register_realtime_push"]