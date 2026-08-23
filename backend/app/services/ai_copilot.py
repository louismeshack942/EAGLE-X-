"""AI Copilot — natural language assistant (AMF).

Rule-based over live analytics by default: every answer is grounded in real
EV, z-scores, Kelly stakes, and the squad's current form. When OPENAI_API_KEY
or ANTHROPIC_API_KEY is set, those can be layered on (not required).
"""
from typing import Optional

from app.services import forensics as forensics_svc
from app.services import scout as scout_svc
from app.services import season as season_svc
from app.services.analytics import analytics_engine
from app.services.analytics_advanced import digit_engine
from app.services.auto_trader import auto_trader
from app.services.intelligence import intelligence_engine
from app.services.market_master import market_master
from app.services.money_management import kelly_stake, risk_state
from app.services.risk_guard import risk_guard
from app.services.virtual_bank import virtual_bank


class AICopilot:
    def ask(self, question: str, symbol: Optional[str] = None) -> dict:
        q = question.lower().strip()
        symbol = symbol or "R_100"

        if any(w in q for w in ["ruin", "blow up", "broke", "monte carlo", "risk of ruin"]):
            r = forensics_svc.risk_of_ruin()
            if "note" in r:
                answer = r["note"] + " Until then the Monte Carlo on the standard DIFFERS table says: " \
                    + forensics_svc.monte_carlo(0.9, 1.1, sims=300)["verdict"]
            else:
                answer = (f"From your own {r['based_on_trades']} trades: win rate {r['observed_win_rate']}%, "
                          f"risk of ruin {r['risk_of_ruin_pct']}%, median drawdown {r['median_drawdown_pct']}%. "
                          f"{r['verdict']}")
            return {"question": question, "answer": answer, "symbol": symbol, "data": r}

        if any(w in q for w in ["how am i doing", "scorecard", "session", "grade"]):
            sc = forensics_svc.session_scorecard(
                risk_guard.equity_curve, auto_trader.daily_pnl,
                auto_trader.trades_today, auto_trader.wins_today,
            )
            return {
                "question": question,
                "answer": f"Session grade {sc.get('grade', 'N/A')}: {sc.get('note', '')} "
                          f"(result {sc.get('components', {}).get('result', '-')}, "
                          f"discipline {sc.get('components', {}).get('discipline', '-')}, "
                          f"smoothness {sc.get('components', {}).get('smoothness', '-')}).",
                "symbol": symbol, "data": sc,
            }

        if any(w in q for w in ["mistake", "lesson", "went wrong", "debrief"]):
            ls = forensics_svc.lessons()
            lines = "; ".join(f"{x['lesson']} ({x['count']}x)" for x in ls["lessons"])
            return {
                "question": question,
                "answer": f"Match analyst's debrief: {lines}",
                "symbol": symbol, "data": ls,
            }

        if any(w in q for w in ["improve", "suggestion", "get better", "advice"]):
            sg = forensics_svc.suggestions()
            lines = " | ".join(f"[{s['priority']}] {s['text']}" for s in sg["suggestions"])
            return {"question": question, "answer": lines, "symbol": symbol, "data": sg}

        if any(w in q for w in ["season", "this week", "weekly", "league"]):
            rep = season_svc.weekly_report()
            return {
                "question": question,
                "answer": rep.get("note") or (
                    f"Week {rep['week']}: {rep['position']} — {rep['verdict']} "
                    f"(P&L ${rep['pnl']:+.2f}, {rep['win_rate']}% WR, {rep['trades']} trades, "
                    f"trend {rep['trend_vs_last_week'] or 'first week'})."
                ),
                "symbol": symbol, "data": rep,
            }

        if any(w in q for w in ["bank", "vault", "treasurer", "protect", "profit split"]):
            b = virtual_bank.status()
            if not b["synced"]:
                answer = ("The virtual bank opens when the CF starts a session. "
                          "From then on, 60% of every profit is locked in the vault "
                          "and losses only hit the spendable balance.")
            else:
                answer = (
                    f"Treasurer's ledger: current ${b['current_balance']:.2f} (spendable — stakes come from this), "
                    f"vault ${b['vault_balance']:.2f} (protected — {b['split_label']}), "
                    f"total ${b['total_balance']:.2f}. "
                    f"Lifetime: +${b['total_profit']:.2f} profit, -${b['total_loss']:.2f} losses. "
                    f"{b['protected_pct']}% of the account is untouchable right now."
                )
            return {"question": question, "answer": answer, "symbol": symbol, "data": b}

        if any(w in q for w in ["should i trade", "trade now", "good time", "hot hour", "best time", "when to"]):
            tables = scout_svc.scan_tables([symbol])
            hours = scout_svc.performance_by_hour()
            best = tables.get("best_table")
            hot_txt = (
                f"This hour (UTC {hours['current_hour_utc']}) has been kind to you historically."
                if hours["current_window_ok"] is True else
                f"This hour (UTC {hours['current_hour_utc']}) has been a GRAVEYARD in your own journal — stay out."
                if hours["current_window_ok"] is False else
                "Not enough of your own history yet to judge this hour."
            )
            guard = risk_guard.status()
            block = " Kill switch is DOWN — nothing fires." if guard["killed"] else ""
            return {
                "question": question,
                "answer": f"{tables['summary']} {hot_txt}{block}",
                "symbol": symbol,
                "data": {"table": best, "hot_hours": hours},
            }

        if any(w in q for w in ["kill", "limit", "guard", "stop loss", "circuit"]):
            g = risk_guard.status()
            return {
                "question": question,
                "answer": (
                    f"Guard state: {'KILL SWITCH DOWN — ' + g['kill_reason'] if g['killed'] else 'all circuits live'}. "
                    f"Mode {g['mode']}. Daily loss limit ${g['daily_loss_limit'] or 'off'}, "
                    f"take-profit ${g['session_take_profit'] or 'off'}, "
                    f"hourly trade cap {g['max_trades_per_hour'] or 'off'}. "
                    f"Trades this hour: {g['trades_last_hour']}. "
                    "Streak halving is on: every straight loss halves the next stake."
                ),
                "symbol": symbol,
                "data": g,
            }

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
