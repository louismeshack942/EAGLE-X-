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

        return {
            "symbol": symbol,
            "window": window,
            "decision": decision,
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
        psycho = digit_engine.get_psychology(symbol, window)
        overfed = psycho.get("overfed")
        starving = psycho.get("starving")
        if not overfed:
            return {"symbol": symbol, "digit": None, "confidence": 0.0, "evidence": "no data", "contract": None}
        if overfed["deviation"] >= abs(starving["deviation"] if starving else 0):
            digit = overfed["digit"]
            deviation = overfed["deviation"]
            contract = "MATCHES"
        else:
            digit = starving["digit"]
            deviation = abs(starving["deviation"])
            contract = "DIFFERS"
        confidence = min(100.0, 50 + deviation * 5)
        evidence = f"Digit {digit} appears {overfed['percent'] if contract == 'MATCHES' else starving['percent']}% vs expected 10.0%"
        return {
            "symbol": symbol,
            "digit": digit,
            "confidence": round(confidence, 1),
            "evidence": evidence,
            "contract": contract,
        }


intelligence_engine = IntelligenceEngine()
