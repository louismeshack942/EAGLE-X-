"""Support engines — volatility regime, movement regime, streaks, anomalies, data quality."""
import statistics
from collections import Counter
from typing import List

from app.core.queue import tick_queue


def _quotes(ticks) -> List[float]:
    return [t.quote for t in ticks]


class VolatilityEngine:
    def __init__(self, queue=None):
        self.queue = queue or tick_queue

    def analyze(self, symbol: str, window: int = 100) -> dict:
        ticks = self.queue.recent(symbol, limit=window)
        if len(ticks) < 2:
            return {"symbol": symbol, "regime": "INSUFFICIENT_DATA", "volatility_pct": 0.0}
        quotes = _quotes(ticks)
        rets = [(b - a) / a * 100 for a, b in zip(quotes[:-1], quotes[1:]) if a]
        vol = statistics.pstdev(rets) if len(rets) > 1 else 0.0
        regime = "LOW" if vol < 0.25 else ("NORMAL" if vol < 0.75 else "HIGH")
        return {"symbol": symbol, "regime": regime, "volatility_pct": round(vol, 4)}


class MovementEngine:
    def __init__(self, queue=None):
        self.queue = queue or tick_queue

    def analyze(self, symbol: str, window: int = 100) -> dict:
        ticks = self.queue.recent(symbol, limit=window)
        if len(ticks) < 2:
            return {"symbol": symbol, "regime": "INSUFFICIENT_DATA", "direction": "FLAT", "net_change_pct": 0.0}
        quotes = _quotes(ticks)
        first, last = quotes[0], quotes[-1]
        net = (last - first) / first * 100 if first else 0.0
        direction = "UP" if net > 0.05 else ("DOWN" if net < -0.05 else "FLAT")
        # trending if persistent drift; ranging if oscillation around mean
        half = len(quotes) // 2
        first_avg = sum(quotes[:half]) / half
        second_avg = sum(quotes[half:]) / (len(quotes) - half)
        drift = abs(second_avg - first_avg) / first_avg * 100 if first_avg else 0
        regime = "TRENDING" if drift > 0.2 else "RANGING"
        return {
            "symbol": symbol,
            "regime": regime,
            "direction": direction,
            "net_change_pct": round(net, 4),
        }


class StreakEngine:
    def __init__(self, queue=None):
        self.queue = queue or tick_queue

    def analyze(self, symbol: str, window: int = 100) -> dict:
        ticks = self.queue.recent(symbol, limit=window)
        if len(ticks) < 2:
            return {"symbol": symbol, "price_streak": {"type": "FLAT", "length": 0}, "digit_streak": {"type": "FLAT", "length": 0}}
        quotes = _quotes(ticks)
        # price streak
        last_dir = None
        length = 0
        for a, b in zip(quotes[:-1], quotes[1:]):
            d = "UP" if b > a else ("DOWN" if b < a else "FLAT")
            if d == "FLAT":
                continue
            if d == last_dir:
                length += 1
            else:
                last_dir = d
                length = 1
        digits = [t.digit for t in ticks]
        digit_length = 0
        last_d = None
        for d in digits:
            if d == last_d:
                digit_length += 1
            else:
                last_d = d
                digit_length = 1
        return {
            "symbol": symbol,
            "price_streak": {"type": last_dir or "FLAT", "length": length},
            "digit_streak": {"type": str(last_d) if last_d is not None else "FLAT", "length": digit_length},
        }


class AnomalyEngine:
    """Simple z-score anomaly detection on returns and digit gaps."""

    def __init__(self, queue=None):
        self.queue = queue or tick_queue

    def detect(self, symbol: str, window: int = 100, z_thresh: float = 3.0) -> dict:
        ticks = self.queue.recent(symbol, limit=window)
        anomalies: list[dict] = []
        if len(ticks) < 10:
            return {"symbol": symbol, "count": 0, "level": 0, "anomalies": []}
        quotes = _quotes(ticks)
        rets = [(b - a) / a * 100 for a, b in zip(quotes[:-1], quotes[1:]) if a]
        if len(rets) > 5:
            mu = statistics.mean(rets)
            sd = statistics.pstdev(rets) or 1e-9
            for i, r in enumerate(rets):
                z = (r - mu) / sd
                if abs(z) > z_thresh:
                    anomalies.append({
                        "kind": "price_spike",
                        "index": i,
                        "z_score": round(z, 2),
                        "timestamp": ticks[i + 1].timestamp.isoformat() if i + 1 < len(ticks) else None,
                    })
        # digit gap anomalies: digit missing for > 15 ticks
        digits = [t.digit for t in ticks]
        n = len(digits)
        for d in range(10):
            gap = n - 1 - max((i for i, v in enumerate(digits) if v == d), default=-1)
            if gap > 15:
                anomalies.append({"kind": "digit_gap", "digit": d, "gap": gap})
        level = len(anomalies)
        return {
            "symbol": symbol,
            "count": level,
            "level": level,
            "anomalies": anomalies[:20],
        }


class QualityEngine:
    """Score tick data 0-100 on completeness, timeliness, consistency, validity."""

    def __init__(self, queue=None):
        self.queue = queue or tick_queue

    def score(self, symbol: str, window: int = 100) -> dict:
        ticks = self.queue.recent(symbol, limit=window)
        n = len(ticks)
        if n == 0:
            return {"symbol": symbol, "score": 0.0, "grade": "NO_DATA", "components": {}}
        # completeness: expected count vs actual (target >= 100 ticks)
        completeness = min(100.0, n / window * 100 if window else 100.0)
        # validity: all quotes finite and positive
        valid = all(t.quote > 0 and t.quote == t.quote for t in ticks)
        validity = 100.0 if valid else 40.0
        # consistency: pct of monotonic timestamps
        monotone = sum(1 for a, b in zip(ticks[:-1], ticks[1:]) if b.timestamp >= a.timestamp)
        consistency = monotone / (n - 1) * 100 if n > 1 else 100.0
        # timeliness: recent ticks present
        timeliness = 100.0 if n and (ticks[-1].timestamp) else 50.0
        avg_quality = sum(t.quality for t in ticks) / n
        score = (completeness * 0.3 + validity * 0.3 + consistency * 0.2 + timeliness * 0.1 + avg_quality * 0.1)
        score = round(score, 1)
        grade = "HIGH" if score >= 80 else ("MEDIUM" if score >= 50 else "LOW")
        return {
            "symbol": symbol,
            "score": score,
            "grade": grade,
            "components": {
                "completeness": round(completeness, 1),
                "validity": round(validity, 1),
                "consistency": round(consistency, 1),
                "timeliness": round(timeliness, 1),
                "avg_tick_quality": round(avg_quality, 1),
            },
        }


volatility_engine = VolatilityEngine()
movement_engine = MovementEngine()
streak_engine = StreakEngine()
anomaly_engine = AnomalyEngine()
quality_engine = QualityEngine()
