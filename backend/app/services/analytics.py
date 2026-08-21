"""Basic statistical analytics — latest, mean, stdev, tick timer."""
import math
import statistics
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.core.queue import tick_queue
from app.models.tick import Tick


class AnalyticsEngine:
    def __init__(self, queue=None):
        self.queue = queue or tick_queue

    def get_stats(self, symbol: str, window: Optional[int] = None) -> dict:
        ticks = self.queue.recent(symbol, limit=window or 100)
        if not ticks:
            return {"symbol": symbol, "count": 0, "latest": None, "mean": None, "stdev": None}
        quotes = [t.quote for t in ticks]
        return {
            "symbol": symbol,
            "count": len(quotes),
            "latest": round(quotes[-1], 5),
            "mean": round(sum(quotes) / len(quotes), 5),
            "stdev": round(statistics.pstdev(quotes), 5) if len(quotes) > 1 else 0.0,
        }

    def time_to_next_tick(self, symbol: str) -> dict:
        """Estimate seconds to next tick from recent inter-tick intervals."""
        ticks = self.queue.recent(symbol, limit=50)
        if len(ticks) < 2:
            return {
                "symbol": symbol,
                "seconds": 0.0,
                "avg_interval": 0.0,
                "since_last": 0.0,
                "status": "RED",
            }
        intervals = [
            (b.timestamp - a.timestamp).total_seconds()
            for a, b in zip(ticks[:-1], ticks[1:])
        ]
        avg_interval = sum(intervals) / len(intervals)
        now = datetime.now(timezone.utc)
        since_last = (now - ticks[-1].timestamp).total_seconds()
        remaining = max(0.0, avg_interval - since_last)
        if remaining > 1.5:
            status = "GREEN"
        elif remaining > 1.0:
            status = "YELLOW"
        elif remaining > 0.5:
            status = "ORANGE"
        else:
            status = "RED"
        return {
            "symbol": symbol,
            "seconds": round(remaining, 2),
            "avg_interval": round(avg_interval, 3),
            "since_last": round(since_last, 2),
            "status": status,
        }


analytics_engine = AnalyticsEngine()
