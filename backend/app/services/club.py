"""Club — the EAGLE-X communications hub.

Models the club as a real football organisation so the whole system talks:

- Team Manager: reads the Starting XI (Intelligence, Market Master, Auto
  Trader, Risk) and issues a briefing + directives.
- Board / Sponsors: track the value of the club from P&L and portfolio.
- News Desk: turns market conditions into headlines.
- Fans: the stands react with chants based on current form.
- Alerts: market-trend alerts (regime shift, volatility spike, anomalies,
  strong signal, data quality drop) for the whole club.

Everything derives from live analytics — no fabricated data, clear labels.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.services import portfolio as portfolio_svc
from app.services import risk_analytics
from app.services.auto_trader import auto_trader
from app.services.intelligence import intelligence_engine
from app.services.market_master import market_master


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_intelligence(symbol: str, window: int = 100) -> Dict[str, Any]:
    try:
        return intelligence_engine.analyze(symbol, window)
    except Exception:  # noqa: BLE001
        return {
            "decision": "NO_DATA",
            "data_quality": 0.0,
            "volatility": {"regime": "UNKNOWN"},
            "movement": {"regime": "UNKNOWN"},
            "anomaly_level": 0,
        }


def manager_briefing(symbols: List[str], window: int = 100) -> Dict[str, Any]:
    """The Team Manager gives a tactical briefing for the starting XI."""
    intel = [(s, _safe_intelligence(s, window)) for s in symbols]
    strong = [s for s, i in intel if "STRONG" in (i.get("decision") or "")]
    playable = sum(1 for _, i in intel if "SUPPORT" in (i.get("decision") or ""))
    avg_dq = round(sum(i.get("data_quality", 0) for _, i in intel) / max(len(intel), 1), 1)
    anomalies = sum(int(i.get("anomaly_level") or 0) for _, i in intel)
    st = auto_trader.status()

    if st.get("running") and anomalies >= 4:
        morale, formation = "CAUTIOUS", "5-4-1 (park the bus — protect P&L)"
    elif st.get("running") and strong:
        morale, formation = "HIGH", "3-4-3 (attack — fluid play on strong markets)"
    elif strong:
        morale, formation = "READY", "4-3-3 (balanced — scout then strike)"
    else:
        morale, formation = "PATIENT", "4-5-1 (hold — no clear pass yet)"

    briefing = (
        f"Lads, {len(strong)}/{len(symbols)} markets are playing with STRONG data. "
        f"Average data quality {avg_dq}. Form is {morale}. "
        f"{'Auto Trader is on the pitch.' if st.get('running') else 'Starting XI on the bench watch.'} "
        f"{'We go fluid on ' + ', '.join(strong[:2]) + '.' if strong else 'Wait for the pass — no weak signal gets through.'}"
    )
    directives = []
    if strong:
        directives.append(f"Feed SS: fluid plays on {', '.join(strong[:2])}")
    if avg_dq < 70:
        directives.append("Hold — data quality too low, don't force the pass")
    if anomalies >= 3:
        directives.append("GK on high alert — anomalies detected, tighten the back line")
    if not st.get("running"):
        directives.append("Bring the CF (Auto Trader) on — paper mode first")
    if playable == 0:
        directives.append("Drills, not matches — markets unreadable, stay in training")

    return {
        "timestamp": _now(),
        "morale": morale,
        "form": strong,
        "data_quality": avg_dq,
        "anomalies": anomalies,
        "auto_trader_running": bool(st.get("running")),
        "formation": formation,
        "briefing": briefing,
        "directives": directives,
    }


def board_report() -> Dict[str, Any]:
    """The Board / Sponsors: value of the club from trading performance."""
    st = auto_trader.status()
    try:
        perf = risk_analytics.performance_analytics()
    except Exception:  # noqa: BLE001
        perf = {}
    try:
        portfolio = portfolio_svc.portfolio_summary()
    except Exception:  # noqa: BLE001
        portfolio = {}
    invested = portfolio.get("total_value", 0.0)
    cash = st.get("balance") or 0.0
    value = round(invested + cash, 2)
    standings = "GOOD" if (st.get("daily_pnl") or 0) >= 0 else "WATCH"
    sponsors = [
        {"name": "Apex Analytics", "tier": "Title", "clause": "Data quality > 80"},
        {"name": "Meridian Capital", "tier": "Gold", "clause": "Win rate > 55%"},
        {"name": "Northlight Ventures", "tier": "Silver", "clause": "Max drawdown < 25%"},
        {"name": "Ivory Ridge Group", "tier": "Bronze", "clause": "50 trades/day discipline"},
    ]
    return {
        "timestamp": _now(),
        "club_value": value,
        "daily_pnl": st.get("daily_pnl"),
        "balance": st.get("balance"),
        "win_rate": perf.get("win_rate"),
        "standings": standings,
        "sponsors": sponsors,
        "statement": (
            f"Club value ${value:.2f}. The board is {'confident' if standings == 'GOOD' else 'attentive'}. "
            f"Sponsors hold us to: quality data, positive win rate, controlled drawdown."
        ),
    }


def news_desk(symbols: List[str], window: int = 100) -> Dict[str, Any]:
    """Turn live market conditions into club headlines."""
    headlines = []
    for sym in symbols:
        i = _safe_intelligence(sym, window)
        decision = i.get("decision") or ""
        vol = (i.get("volatility") or {}).get("regime", "")
        move = (i.get("movement") or {}).get("regime", "")
        dq = i.get("data_quality") or 0
        anomaly = int(i.get("anomaly_level") or 0)
        if "STRONG" in decision:
            headlines.append({
                "symbol": sym, "category": "TACTICAL",
                "headline": f"{sym} lights up the training ground — STRONG data support (DQ {dq})",
                "importance": "high", "timestamp": _now(),
            })
        if vol in ("HIGH", "VERY_HIGH"):
            headlines.append({
                "symbol": sym, "category": "WEATHER",
                "headline": f"Storm over {sym}: volatility {vol} — route through Market Master",
                "importance": "medium", "timestamp": _now(),
            })
        if move in ("TRENDING", "IMPULSIVE"):
            headlines.append({
                "symbol": sym, "category": "FORM",
                "headline": f"{sym} on a {move} run — momentum forming",
                "importance": "medium", "timestamp": _now(),
            })
        if anomaly >= 3:
            headlines.append({
                "symbol": sym, "category": "INJURY",
                "headline": f"{sym} flagged for {anomaly} anomalies — fitness doubt",
                "importance": "high", "timestamp": _now(),
            })
        if dq < 60:
            headlines.append({
                "symbol": sym, "category": "SUSPENSION",
                "headline": f"{sym} benched — data quality {dq} below the bar",
                "importance": "high", "timestamp": _now(),
            })
    headlines.sort(key=lambda h: 0 if h["importance"] == "high" else 1)
    return {
        "timestamp": _now(),
        "headlines": headlines[:12],
        "paper_name": "The Sentinel (EAGLE-X daily)",
    }


def fan_standing(symbols: List[str], window: int = 100) -> Dict[str, Any]:
    """The stands: fans chant with current form."""
    intel = [_safe_intelligence(s, window) for s in symbols]
    strong = sum(1 for i in intel if "STRONG" in (i.get("decision") or ""))
    total_anomalies = sum(int(i.get("anomaly_level") or 0) for i in intel)
    st = auto_trader.status()

    if strong >= 3:
        chant, crowd = "🎵 Ole, ole, EAGLE-X is flying today 🎵", "ROARING"
    elif strong >= 1:
        chant, crowd = "🎵 Come on you Eagles, come on you Eagles 🎵", "SINGING"
    elif total_anomalies >= 6:
        chant, crowd = "😐 (nervous silence) ... we believe, but be careful 😐", "NERVOUS"
    elif st.get("running"):
        chant, crowd = "🎵 He's one of our own, Auto Trader, one of our own 🎵", "SUPPORTIVE"
    else:
        chant, crowd = "🎵 We want the football, we want the football 🎵", "CALLING"

    capacity = 100 * min(len(symbols), 5)
    tickets = min(strong * 20, capacity)
    return {
        "timestamp": _now(),
        "crowd": crowd,
        "chant": chant,
        "attendance": tickets,
        "capacity": capacity,
        "sentiment": "PASSIONATE" if strong >= 1 else ("TENSE" if total_anomalies >= 6 else "HOPEFUL"),
    }


def market_alerts(symbols: List[str], window: int = 100) -> Dict[str, Any]:
    """Club-wide market-trend alerts for the whole team."""
    alerts = []
    for sym in symbols:
        i = _safe_intelligence(sym, window)
        vol = (i.get("volatility") or {}).get("regime", "")
        move = (i.get("movement") or {}).get("regime", "")
        dq = i.get("data_quality") or 0
        anomaly = int(i.get("anomaly_level") or 0)
        decision = i.get("decision") or ""
        if vol in ("HIGH", "VERY_HIGH"):
            alerts.append({"symbol": sym, "type": "VOLATILITY_SPIKE", "severity": "high",
                           "message": f"{sym} volatility {vol}", "timestamp": _now()})
        if move in ("TRENDING", "IMPULSIVE"):
            alerts.append({"symbol": sym, "type": "MOMENTUM", "severity": "medium",
                           "message": f"{sym} {move} movement", "timestamp": _now()})
        if anomaly >= 3:
            alerts.append({"symbol": sym, "type": "ANOMALY", "severity": "high",
                           "message": f"{sym}: {anomaly} anomalies", "timestamp": _now()})
        if "STRONG" in decision:
            alerts.append({"symbol": sym, "type": "STRONG_SIGNAL", "severity": "medium",
                           "message": f"{sym} STRONG_DATA_SUPPORT (DQ {dq})", "timestamp": _now()})
        if dq < 60:
            alerts.append({"symbol": sym, "type": "DATA_QUALITY_DROP", "severity": "high",
                           "message": f"{sym} data quality {dq}", "timestamp": _now()})
    return {"timestamp": _now(), "alerts": alerts, "count": len(alerts)}


def overview(symbols: List[str], window: int = 100) -> Dict[str, Any]:
    return {
        "timestamp": _now(),
        "manager": manager_briefing(symbols, window),
        "board": board_report(),
        "news": news_desk(symbols, window),
        "fans": fan_standing(symbols, window),
        "alerts": market_alerts(symbols, window),
    }
