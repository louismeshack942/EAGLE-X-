"""Intelligence Engine — combines all signals into a single clear decision."""
from typing import List, Optional

from app.core.queue import tick_queue
from app.services.analytics_advanced import digit_engine
from app.services.engines import (
    anomaly_engine,
    movement_engine,
    quality_engine,
    streak_engine,
    volatility_engine,
)

SIGNALS = [
    "STRONG_DATA_SUPPORT",
    "WEAK_DATA_SUPPORT",
    "NEUTRAL",
    "NO_CLEAR_STATISTICAL_EDGE",
    "INSUFFICIENT_DATA",
]

SIGNAL_WEIGHT = {
    "STRONG_DATA_SUPPORT": 20,
    "WEAK_DATA_SUPPORT": 10,
    "NEUTRAL": 0,
    "NO_CLEAR_STATISTICAL_EDGE": -10,
    "WEAK_DATA_CONTRARY": -10,
    "INSUFFICIENT_DATA": -20,
}


class IntelligenceEngine:
    def __init__(self, queue=None):
        self.queue = queue or tick_queue

    def analyze(self, symbol: str, window: int = 100) -> dict:
        ticks = self.queue.recent(symbol, limit=window)
        n = len(ticks)
        quality = quality_engine.score(symbol, window)
        vol = volatility_engine.analyze(symbol, window)
        mov = movement_engine.analyze(symbol, window)
        anomaly = anomaly_engine.detect(symbol, window)
        streak = streak_engine.analyze(symbol, window)
        digit_analysis = digit_engine.get_digit_analysis(symbol, window)
        digit_stability = digit_analysis.get("balance", 0.0)

        if n < 10:
            decision = "INSUFFICIENT_DATA"
        else:
            score = 0
            if quality["score"] >= 80:
                score += 2
            elif quality["score"] < 50:
                score -= 2
            if vol["regime"] == "LOW":
                score += 1
            elif vol["regime"] == "HIGH":
                score -= 1
            if mov["regime"] == "RANGING":
                score += 1
            if anomaly["count"] > 3:
                score -= 3
            if digit_stability > 95:
                score += 1

            if score >= 4:
                decision = "STRONG_DATA_SUPPORT"
            elif score >= 2:
                decision = "WEAK_DATA_SUPPORT"
            elif score <= -4:
                decision = "NO_CLEAR_STATISTICAL_EDGE"
            else:
                decision = "NEUTRAL"

        reasons = [
            f"Data quality {quality['score']} ({quality['grade']})",
            f"Volatility {vol['regime']} (pct {vol.get('volatility_pct', 0)})",
            f"Movement {mov['regime']} ({mov.get('net_change_pct', 0)}%)",
            f"{anomaly['count']} anomalies detected",
        ]

        # CB's conviction card: a single 0-100 number the manager can read at a
        # glance — data quality and stability build it, anomalies erode it.
        conviction = (
            quality["score"] * 0.5
            + digit_stability * 0.3
            + (10 if vol["regime"] == "LOW" else 5 if vol["regime"] == "NORMAL" else 0)
            + (10 if mov["regime"] == "RANGING" else 5)
            - anomaly["count"] * 5
        )
        conviction = round(max(0.0, min(100.0, conviction)), 1)

        return {
            "symbol": symbol,
            "window": window,
            "decision": decision,
            "conviction": conviction,
            "data_quality": quality["score"],
            "volatility": vol,
            "movement": mov,
            "anomaly_level": anomaly["count"],
            "digit_stability": digit_stability,
            "streaks": streak,
            "reasons": reasons,
        }

    def scan_all(self, symbols: List[str], window: int = 100) -> dict:
        markets = []
        for symbol in symbols:
            analysis = self.analyze(symbol, window)
            score = 0
            if "STRONG" in analysis["decision"]:
                score = 6
            elif "WEAK" in analysis["decision"]:
                score = 5
            elif analysis["decision"] == "NEUTRAL":
                score = 4
            else:
                score = 2
            markets.append({
                "symbol": symbol,
                "score": score,
                "signal": analysis["decision"],
                "data_quality": analysis["data_quality"],
                "volatility": analysis["volatility"].get("regime", "UNKNOWN"),
                "movement": analysis["movement"].get("regime", "UNKNOWN"),
                "anomaly_level": analysis["anomaly_level"],
                "digit_stability": analysis["digit_stability"],
                "evidence": round(
                    (analysis["data_quality"] / 100) * 20
                    + (20 if "STRONG" in analysis["decision"] else 10 if "WEAK" in analysis["decision"] else 0),
                    1,
                ),
            })
        markets.sort(key=lambda m: (m["score"], m["evidence"]), reverse=True)
        return {"window": window, "markets": markets}

    def most_likely(self, symbol: str, window: int = 100) -> dict:
        """DMF's pick — backed by shrunk estimates and z-score significance."""
        analysis = digit_engine.get_digit_analysis(symbol, window)
        freq = analysis.get("frequency", {})
        if not freq:
            return {"symbol": symbol, "digit": None, "confidence": 0.0, "evidence": "no data", "contract": None}
        # Rank by absolute z-score: the most statistically unusual digit wins.
        ranked = sorted(
            ((float(v.get("z", 0.0) or 0.0), int(d), float(v.get("estimate", 10.0))) for d, v in freq.items()),
            key=lambda t: abs(t[0]),
            reverse=True,
        )
        z, digit, est = ranked[0]
        if z >= 0:
            contract = "MATCHES"
            evidence = f"Digit {digit} overfed: est {est}% vs 10% fair (z={z:+.2f})"
        else:
            contract = "DIFFERS"
            evidence = f"Digit {digit} starving: est {est}% vs 10% fair (z={z:+.2f})"
        significant = abs(z) >= 1.96
        confidence = round(min(100.0, 50.0 + abs(z) * 15.0), 1)
        return {
            "symbol": symbol,
            "digit": digit,
            "confidence": confidence,
            "evidence": evidence,
            "contract": contract,
            "z": round(z, 2),
            "significant": significant,
        }


intelligence_engine = IntelligenceEngine()
