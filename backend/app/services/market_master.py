"""Market Master — full decision matrix across the digit contracts.

Every contract is priced against its fair base rate. We only surface a
positive expected value (EV). "Confidence" becomes an honest number: the
distance of observed frequency from fair, expressed as an edge — not a
feel-good score.
"""
from typing import Optional

from app.services.analytics_advanced import digit_engine
from app.services.intelligence import SIGNAL_WEIGHT, intelligence_engine
from app.services.engines import anomaly_engine, quality_engine
from app.services.money_management import compute_stake

MAX_SCORE = 100.0

# Fair payout multipliers Deriv offers (approx; used for EV honesty, not display).
PAYOUTS = {"MATCHES": 9.0, "DIFFERS": 1.1, "ODD": 1.9, "EVEN": 1.9, "OVER": 1.9, "UNDER": 1.9}


def _edge_confidence(edge_pct: float) -> float:
    """Honest confidence: 50 at zero edge, scaling with observed deviation."""
    return round(min(100.0, max(0.0, 50.0 + edge_pct * 5.0)), 1)


def _ev(p_win: float, payout: float) -> float:
    """Expected value per 1.0 staked."""
    return round(p_win * payout - 1.0, 4)


class MarketMaster:
    def analyze(self, symbol: str, window: int = 100) -> dict:
        intel = intelligence_engine.analyze(symbol, window)
        signal = intel["decision"]
        weight = SIGNAL_WEIGHT.get(signal, 0)
        digit_analysis = digit_engine.get_digit_analysis(symbol, window)
        freq = digit_analysis.get("frequency", {})
        contracts: list[dict] = []

        if freq:
            # Use the shrunk (Bayesian) estimate, not the raw percent, so small
            # windows don't manufacture fake edges. z is the binomial z-score of
            # each digit vs the fair 10% hypothesis (95% significance at |z|>=1.96).
            est = {d: (freq[str(d)].get("estimate", freq[str(d)]["percent"])) for d in range(10)}
            zscores = {d: float(freq[str(d)].get("z", 0.0) or 0.0) for d in range(10)}
            odd_freq = sum(est[d] for d in range(1, 10, 2))
            even_freq = 100.0 - odd_freq

            for d in range(10):
                obs = est[d]
                z_d = zscores[d]
                # MATCHES: fair 10%. Significant only when the digit is OVERFED
                # beyond the 95% level.
                m_edge = obs - 10.0
                p_match = max(0.01, min(0.99, obs / 100.0))
                contracts.append({
                    "name": f"MATCHES on {d}",
                    "type": "MATCHES",
                    "digit": d,
                    "score": round(m_edge * 5 + weight, 1),
                    "confidence": _edge_confidence(m_edge),
                    "evidence": signal,
                    "observed_pct": round(obs, 2),
                    "fair_pct": 10.0,
                    "observed_edge": round(m_edge, 2),
                    "z": round(z_d, 2),
                    "significant": z_d >= 1.96,
                    "ev": _ev(p_match, PAYOUTS["MATCHES"]),
                })
                # DIFFERS: fair 90%. Significant only when the digit is STARVING
                # beyond the 95% level.
                d_edge = (100.0 - obs) - 90.0
                p_diff = max(0.01, min(0.99, (100.0 - obs) / 100.0))
                contracts.append({
                    "name": f"DIFFERS on {d}",
                    "type": "DIFFERS",
                    "digit": d,
                    "score": round(d_edge * 5 + weight, 1),
                    "confidence": _edge_confidence(d_edge),
                    "evidence": signal,
                    "observed_pct": round(100.0 - obs, 2),
                    "fair_pct": 90.0,
                    "observed_edge": round(d_edge, 2),
                    "z": round(-z_d, 2),
                    "significant": z_d <= -1.96,
                    "ev": _ev(p_diff, PAYOUTS["DIFFERS"]),
                })

            # ODD/EVEN: fair 50%.
            o_edge = odd_freq - 50.0
            contracts.append({
                "name": "ODD",
                "type": "ODD",
                "digit": None,
                "score": round(o_edge * 10 + weight, 1),
                "confidence": _edge_confidence(o_edge),
                "evidence": signal,
                "observed_pct": round(odd_freq, 2),
                "fair_pct": 50.0,
                "observed_edge": round(o_edge, 2),
                "ev": _ev(max(0.01, min(0.99, odd_freq / 100.0)), PAYOUTS["ODD"]),
            })
            e_edge = even_freq - 50.0
            contracts.append({
                "name": "EVEN",
                "type": "EVEN",
                "digit": None,
                "score": round(e_edge * 10 + weight, 1),
                "confidence": _edge_confidence(e_edge),
                "evidence": signal,
                "observed_pct": round(even_freq, 2),
                "fair_pct": 50.0,
                "observed_edge": round(e_edge, 2),
                "ev": _ev(max(0.01, min(0.99, even_freq / 100.0)), PAYOUTS["EVEN"]),
            })

            for d in range(1, 10):
                high_freq = sum(est[x] for x in range(d, 10))
                low_freq = sum(est[x] for x in range(0, d))
                # OVER d: fair = (10-d)*10%. UNDER d: fair = d*10%.
                fair_over = (10 - d) * 10.0
                fair_under = d * 10.0
                ov_edge = high_freq - fair_over
                contracts.append({
                    "name": f"OVER {d}",
                    "type": "OVER",
                    "digit": d,
                    "score": round(ov_edge * 10 + weight, 1),
                    "confidence": _edge_confidence(ov_edge),
                    "evidence": signal,
                    "observed_pct": round(high_freq, 2),
                    "fair_pct": round(fair_over, 1),
                    "observed_edge": round(ov_edge, 2),
                    "ev": _ev(max(0.01, min(0.99, high_freq / 100.0)), PAYOUTS["OVER"]),
                })
                un_edge = low_freq - fair_under
                contracts.append({
                    "name": f"UNDER {d}",
                    "type": "UNDER",
                    "digit": d,
                    "score": round(un_edge * 10 + weight, 1),
                    "confidence": _edge_confidence(un_edge),
                    "evidence": signal,
                    "observed_pct": round(low_freq, 2),
                    "fair_pct": round(fair_under, 1),
                    "observed_edge": round(un_edge, 2),
                    "ev": _ev(max(0.01, min(0.99, low_freq / 100.0)), PAYOUTS["UNDER"]),
                })

        # Keep the full board for scouting; the UI shows the top 6 by name order
        # but the CF evaluates every contract, so honest DIFFERS edges are seen.
        contracts.sort(key=lambda c: c["ev"], reverse=True)
        top = contracts[0] if contracts else None

        data_quality = intel["data_quality"]
        anomaly_count = intel["anomaly_level"]
        if data_quality and data_quality < 70:
            recommendation = "Wait — Poor Data"
        elif anomaly_count > 3:
            recommendation = "Wait — Anomalies Detected"
        elif top is None or top["ev"] <= 0:
            recommendation = "Wait — No Positive Edge"
        else:
            recommendation = f"Trade {top['name']} (EV +{top['ev']:.2f})"

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
            "contracts": contracts[:6],   # UI top board
            "all_contracts": contracts,   # full scouting board for the CF
            "recommendation": recommendation,
            "evidence_summary": evidence_summary,
            "signal": signal,
            "conviction": intel.get("conviction"),
            "data_quality": data_quality,
            "anomaly_count": anomaly_count,
            "volatility": intel["volatility"],
            "movement": intel["movement"],
        }


market_master = MarketMaster()
