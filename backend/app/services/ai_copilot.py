"""AI Copilot — natural language assistant (AMF).

Rule-based over live analytics by default: every answer is grounded in real
EV, z-scores, Kelly stakes, and the squad's current form. When OPENAI_API_KEY
or ANTHROPIC_API_KEY is set, those can be layered on (not required).
"""
from typing import Optional

from app.services.analytics import analytics_engine
from app.services.analytics_advanced import digit_engine
from app.services.auto_trader import auto_trader
from app.services.intelligence import intelligence_engine
from app.services.market_master import market_master
from app.services.money_management import kelly_stake, risk_state


class AICopilot:
    def ask(self, question: str, symbol: Optional[str] = None) -> dict:
        q = question.lower().strip()
        symbol = symbol or "R_100"

        if any(w in q for w in ["trade", "matches", "differs", "odd", "even", "over", "under", "play"]):
            ml = intelligence_engine.most_likely(symbol)
            mm = market_master.analyze(symbol)
            timer = analytics_engine.time_to_next_tick(symbol)
            recommendation = mm.get("recommendation", "Wait — No Clear Edge")
            top = mm.get("top_recommendation") or {}
            sig = "statistically significant" if ml.get("significant") else "NOT significant — wait"
            ev_txt = f"EV {top['ev']:+.2f}" if top.get("ev") is not None else "no EV edge"
            return {
                "question": question,
                "answer": (
                    f"For {symbol}: {recommendation}. "
                    f"Top contract: {top.get('name', 'none')} ({ev_txt}, z={top.get('z', 0):+.2f}). "
                    f"Best digit: {ml['digit']} ({ml['contract']}, {sig}, z={ml.get('z', 0):+.2f}). "
                    f"Signal: {mm['signal']} · conviction {mm.get('conviction', 'n/a')}/100. "
                    f"Tick timer: {timer['status']} ({timer['seconds']}s, stability {timer.get('stability', 0)}%)."
                ),
                "symbol": symbol,
                "confidence": ml["confidence"],
                "data": ml,
            }
        if "bench" in q or "form" in q or "win rate" in q or "striker" in q or "cf" == q.strip():
            s = auto_trader.status()
            return {
                "question": question,
                "answer": (
                    f"CF form: {s['wins_today']}W/{s['losses_today']}L today "
                    f"({s['win_rate']}% win rate, rating {s.get('cf_rating', 75)}/99). "
                    f"{'BENCHED by the manager — sitting out to reset.' if s.get('benched') else 'Available for selection.'} "
                    f"GK posture: {s['gk']['posture']} (drawdown {s['gk']['drawdown_pct']}%, stake x{s['gk']['stake_multiplier']})."
                ),
                "symbol": symbol,
                "data": s,
            }
        if "risk" in q or "kelly" in q or "stake" in q:
            s = auto_trader.status()
            return {
                "question": question,
                "answer": (
                    f"Risk engine (GK rating {s['gk']['rating']}/99, posture {s['gk']['posture']}): "
                    "stakes are quarter-Kelly capped at 10% of balance and scaled down as drawdown "
                    f"deepens (current multiplier x{s['gk']['stake_multiplier']}, drawdown {s['gk']['drawdown_pct']}%). "
                    "Hard stops: 20% stop-loss, 500% max-profit, 3 consecutive losses, 50 trades/day. "
                    "Kelly returns 0 on negative-EV plays — the GK refuses them outright."
                ),
                "symbol": symbol,
                "data": s["gk"],
            }
        if "anomal" in q or "explain" in q or "why" in q:
            intel = intelligence_engine.analyze(symbol)
            return {
                "question": question,
                "answer": (
                    f"{symbol} diagnostics: signal={intel['decision']}; "
                    f"quality={intel['data_quality']}; "
                    f"volatility={intel['volatility']['regime']}; "
                    f"movement={intel['movement']['regime']}; "
                    f"anomalies={intel['anomaly_level']}; "
                    f"reasons: {'; '.join(intel['reasons'])}"
                ),
                "symbol": symbol,
                "data": intel,
            }
        if "strategy" in q:
            return {
                "question": question,
                "answer": (
                    "Available strategies: DIGIT_MATCH (trade MATCHES on OVERFED digit), "
                    "DIGIT_DIFF (trade DIFFERS on STARVING digit), OVER_UNDER, ODD_EVEN, "
                    "TREND_FOLLOW, VOLATILITY_BREAKOUT. You can build these visually in the "
                    "Strategy Builder and backtest before deploying."
                ),
                "symbol": symbol,
                "data": None,
            }
        # fallback: general analysis snapshot
        intel = intelligence_engine.analyze(symbol)
        return {
            "question": question,
            "answer": (
                f"Snapshot for {symbol}: {intel['decision']} — "
                f"{'; '.join(intel['reasons'])}. "
                "Ask about trades, risk, anomalies or strategies for targeted answers."
            ),
            "symbol": symbol,
            "data": intel,
        }


ai_copilot = AICopilot()
