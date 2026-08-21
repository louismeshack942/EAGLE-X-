"""Market Master — full decision matrix across the 6 digit contracts."""
from typing import Optional

from app.services.analytics_advanced import digit_engine
from app.services.intelligence import SIGNAL_WEIGHT, intelligence_engine
from app.services.engines import anomaly_engine, quality_engine
from app.services.money_management import compute_stake

MAX_SCORE = 100.0


def _score_to_confidence(score: float) -> float:
    return round(min(100.0, max(0.0, 50 + (score / MAX_SCORE) * 50)), 1)


class MarketMaster:
    def analyze(self, symbol: str, window: int = 100) -> dict:
        intel = intelligence_engine.analyze(symbol, window)
        signal = intel["decision"]
        weight = SIGNAL_WEIGHT.get(signal, 0)
        digit_analysis = digit_engine.get_digit_analysis(symbol, window)
        freq = digit_analysis.get("frequency", {})
        contracts: list[dict] = []

        if freq:
            odd_pct = sum(freq[str(d)]["count"] for d in range(1, 10, 2))
            even_pct = sum(freq[str(d)]["count"] for d in range(0, 10, 2))
            total = odd_pct + even_pct
            odd_freq = odd_pct / total * 100 if total else 50.0
            even_freq = even_pct / total * 100 if total else 50.0

            for d in range(10):
                obs = freq[str(d)]["percent"]
                contracts.append({
                    "name": f"MATCHES on {d}",
                    "type": "MATCHES",
                    "digit": d,
                    "score": round((obs - 10) * 5 + weight, 1),
                    "confidence": _score_to_confidence((obs - 10) * 5 + weight),
                    "evidence": signal,
                })
                contracts.append({
                    "name": f"DIFFERS on {d}",
                    "type": "DIFFERS",
                    "digit": d,
                    "score": round((10 - obs) * 5 + weight, 1),
                    "confidence": _score_to_confidence((10 - obs) * 5 + weight),
                    "evidence": signal,
                })

            contracts.append({
                "name": "ODD",
                "type": "ODD",
                "digit": None,
                "score": round((odd_freq - 50) * 10 + weight, 1),
                "confidence": _score_to_confidence((odd_freq - 50) * 10 + weight),
                "evidence": signal,
            })
            contracts.append({
                "name": "EVEN",
                "type": "EVEN",
                "digit": None,
                "score": round((even_freq - 50) * 10 + weight, 1),
                "confidence": _score_to_confidence((even_freq - 50) * 10 + weight),
                "evidence": signal,
            })

            for d in range(1, 10):
                high_freq = sum(freq[str(x)]["percent"] for x in range(d, 10))
                low_freq = sum(freq[str(x)]["percent"] for x in range(0, d))
                contracts.append({
                    "name": f"OVER {d}",
                    "type": "OVER",
                    "digit": d,
                    "score": round((high_freq - (10 - d) * 10) * 10 + weight, 1),
                    "confidence": _score_to_confidence((high_freq - (10 - d) * 10) * 10 + weight),
                    "evidence": signal,
                })
                contracts.append({
                    "name": f"UNDER {d}",
                    "type": "UNDER",
                    "digit": d,
                    "score": round((low_freq - d * 10) * 10 + weight, 1),
                    "confidence": _score_to_confidence((low_freq - d * 10) * 10 + weight),
                    "evidence": signal,
                })

        contracts.sort(key=lambda c: c["score"], reverse=True)
        contracts = contracts[:6]
        top = contracts[0] if contracts else None

        data_quality = intel["data_quality"]
        anomaly_count = intel["anomaly_level"]
        if data_quality and data_quality < 70:
            recommendation = "Wait — Poor Data"
        elif anomaly_count > 3:
            recommendation = "Wait — Anomalies Detected"
        elif top is None or top["score"] < 0:
            recommendation = "Wait — No Clear Edge"
        else:
            recommendation = f"Trade {top['name']}"

        if top:
            top = {**top, "stake": compute_stake(10.0), "duration_seconds": 60}

        evidence_summary = []
        if top:
            evidence_summary.append(
                f"Top edge: {top['name']} (score +{top['score']}, confidence {top['confidence']}%)."
            )
        psycho = digit_engine.get_psychology(symbol, window)
        if psycho.get("overfed"):
            evidence_summary.append(
                f"Overfed digit {psycho['overfed']['digit']} at {psycho['overfed']['percent']}% vs 10% fair; "
                f"starving digit {psycho['starving']['digit']} at {psycho['starving']['percent']}%."
            )
        evidence_summary.append(
            f"Data quality {data_quality}, signal {signal}, anomaly count {anomaly_count}."
        )

        return {
            "symbol": symbol,
            "window": window,
            "top_recommendation": top,
            "contracts": contracts,
            "recommendation": recommendation,
            "evidence_summary": evidence_summary,
            "signal": signal,
            "data_quality": data_quality,
            "anomaly_count": anomaly_count,
            "volatility": intel["volatility"],
            "movement": intel["movement"],
        }


market_master = MarketMaster()
