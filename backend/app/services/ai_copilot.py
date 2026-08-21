"""AI Copilot — natural language assistant.

Uses a rule-based interpreter over live analytics by default. When
OPENAI_API_KEY or ANTHROPIC_API_KEY is set, those can be used (not required).
"""
from typing import Optional

from app.services.analytics import analytics_engine
from app.services.analytics_advanced import digit_engine
from app.services.intelligence import intelligence_engine
from app.services.market_master import market_master


class AICopilot:
    def ask(self, question: str, symbol: Optional[str] = None) -> dict:
        q = question.lower().strip()
        symbol = symbol or "R_100"

        if any(w in q for w in ["trade", "matches", "differs", "odd", "even", "over", "under"]):
            ml = intelligence_engine.most_likely(symbol)
            mm = market_master.analyze(symbol)
            timer = analytics_engine.time_to_next_tick(symbol)
            recommendation = mm.get("recommendation", "Wait — No Clear Edge")
            return {
                "question": question,
                "answer": (
                    f"For {symbol}: {recommendation}. Best digit: {ml['digit']} "
                    f"({ml['contract']}, confidence {ml['confidence']}%). "
                    f"Signal: {mm['signal']}. Tick timer: {timer['status']} ({timer['seconds']}s)."
                ),
                "symbol": symbol,
                "confidence": ml["confidence"],
                "data": ml,
            }
        if "risk" in q:
            return {
                "question": question,
                "answer": (
                    "Risk rules: per-trade stake 10% of balance; hard stop-loss at 20%; "
                    "profit target 120%; max-profit hard stop 500%; stop after 3 consecutive "
                    "losses; cooldowns 30s after loss / 10s after win; max 50 trades/day; "
                    "2 confirmation ticks required before trading."
                ),
                "symbol": symbol,
                "data": None,
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
