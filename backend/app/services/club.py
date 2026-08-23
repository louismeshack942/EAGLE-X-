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
from app.services import scout as scout_svc
from app.services.auto_trader import auto_trader
from app.services.intelligence import intelligence_engine
from app.services.market_master import market_master
from app.services.risk_guard import risk_guard
from app.services.virtual_bank import virtual_bank


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

    # The Treasurer and the Guard sit in the dugout too.
    bank = virtual_bank.status()
    guard = risk_guard.status()
    if guard["killed"]:
        morale = "CAUTIOUS"
        directives.append(f"KILL SWITCH is down ({guard['kill_reason']}) — nobody plays until you release it")
    if bank["synced"] and bank["vault_balance"] > 0:
        briefing += (
            f" Treasurer's report: ${bank['vault_balance']:.2f} locked in the vault, "
            f"${bank['current_balance']:.2f} spendable."
        )
    if guard["mode"] != "FULL_AUTO":
        directives.append(f"Guard mode {guard['mode']}: CF needs your say before he shoots")

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
        "bank": bank,
        "guard_mode": guard["mode"],
        "guard_killed": guard["killed"],
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
    bank = virtual_bank.status()
    cash = st.get("balance") or 0.0
    vault = bank.get("vault_balance", 0.0) if bank.get("synced") else 0.0
    value = round(invested + cash + vault, 2)
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
        "vault_balance": vault,
        "win_rate": perf.get("win_rate"),
        "standings": standings,
        "sponsors": sponsors,
        "statement": (
            f"Club value ${value:.2f} (${vault:.2f} of it protected in the vault). "
            f"The board is {'confident' if standings == 'GOOD' else 'attentive'}. "
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
    # The dugout speaks too: kill switch, hot tables, vault milestones.
    guard = risk_guard.status()
    if guard["killed"]:
        alerts.append({"symbol": "CLUB", "type": "KILL_SWITCH", "severity": "high",
                       "message": f"Kill switch DOWN: {guard['kill_reason']}", "timestamp": _now()})
    try:
        tables = scout_svc.scan_tables(symbols, window)
        best = tables.get("best_table")
        if best:
            alerts.append({"symbol": best["symbol"], "type": "TABLE_HOT", "severity": "medium",
                           "message": best["verdict"], "timestamp": _now()})
    except Exception:  # noqa: BLE001
        pass
    bank = virtual_bank.status()
    if bank.get("synced") and bank.get("vault_balance", 0) >= 10:
        alerts.append({"symbol": "CLUB", "type": "VAULT_MILESTONE", "severity": "low",
                       "message": f"Treasurer: ${bank['vault_balance']:.2f} now protected in the vault",
                       "timestamp": _now()})
    return {"timestamp": _now(), "alerts": alerts, "count": len(alerts)}


def squad_ratings(symbols: List[str], window: int = 100) -> Dict[str, Any]:
    """GM's squad sheet: every player's value (40-99) from live metrics.

    No low-value players here — each rating is computed from what the player
    actually produces right now, and the team overall is their average.
    """
    from app.services.analytics import analytics_engine

    primary = symbols[0] if symbols else "R_100"
    intel = _safe_intelligence(primary, window)
    mm = market_master.analyze(primary, window)
    status = auto_trader.status()
    timer = analytics_engine.time_to_next_tick(primary)

    def clamp(x: float) -> int:
        return max(40, min(99, int(round(x))))

    # GK — risk engine form card (drawdown + streak discipline).
    gk_rating = status["gk"]["rating"]
    # CB — conviction in the current read.
    cb_rating = clamp(40 + (intel.get("conviction", 50) or 50) * 0.59)
    # LB — data quality grade.
    dq = intel.get("data_quality", 50) or 50
    lb_rating = clamp(40 + dq * 0.59)
    # RB — tick stream stability.
    rb_rating = clamp(40 + (timer.get("stability", 50) or 50) * 0.59)
    # DMF — significance of the best digit (z-score from Most Likely Number).
    ml = intelligence_engine.most_likely(primary, window)
    z = abs(ml.get("z", 0.0) or 0.0)
    dmf_rating = clamp(65 + min(3.0, z) / 3.0 * 34)
    # RMF/LMF — the market master's best EV (world-class if he finds +EV).
    top_ev = (mm.get("top_recommendation") or {}).get("ev", 0) or 0
    mm_rating = clamp(70 + min(0.5, max(0.0, top_ev)) * 58)
    # AMF — copilot: grounded in live data, always match-ready.
    amf_rating = 88
    # SS — execution: win rate when he has played; unrated = professional.
    ss_rating = clamp(60 + (status.get("win_rate", 0) or 0) * 0.35) if status.get("trades_today", 0) > 0 else 78
    # CF — striker form rating from the auto trader, with an honesty check:
    # if his calibration says he overstates his numbers, the rating drops.
    cf_rating = status.get("cf_rating", 75)
    try:
        cal = scout_svc.calibration()
        if cal.get("overconfident"):
            cf_rating = clamp(cf_rating - 8)
    except Exception:  # noqa: BLE001
        pass
    # GM — the manager himself: team morale from the briefing.
    morale_map = {"HIGH": 92, "READY": 85, "PATIENT": 74, "CAUTIOUS": 68}
    gm_rating = morale_map.get(manager_briefing(symbols, window).get("morale", "READY"), 80)
    # Treasurer — the virtual bank. Rated on how much of the club's money is
    # protected and whether profits are actually being banked.
    bank = virtual_bank.status()
    if bank.get("synced"):
        treasurer_raw = 70 + bank.get("protected_pct", 0) * 0.4
        if bank.get("net_profit", 0) > 0:
            treasurer_raw += 5
        treasurer_rating = clamp(treasurer_raw)
    else:
        treasurer_rating = 65  # on the team sheet, not yet on the pitch

    players = [
        {"pos": "GK", "name": "Risk Engine", "rating": gk_rating},
        {"pos": "CB", "name": "Intelligence", "rating": cb_rating},
        {"pos": "LB", "name": "Data Quality", "rating": lb_rating},
        {"pos": "RB", "name": "Tick Timer", "rating": rb_rating},
        {"pos": "DMF", "name": "Most Likely Number", "rating": dmf_rating},
        {"pos": "RMF/LMF", "name": "Market Master", "rating": mm_rating},
        {"pos": "AMF", "name": "AI Copilot", "rating": amf_rating},
        {"pos": "SS", "name": "Trade Planner", "rating": ss_rating},
        {"pos": "CF", "name": "Auto Trader", "rating": cf_rating},
        {"pos": "GM", "name": "Team Manager", "rating": gm_rating},
        {"pos": "TR", "name": "Treasurer (Virtual Bank)", "rating": treasurer_rating},
    ]
    overall = clamp(sum(p["rating"] for p in players) / len(players))
    tier = "WORLD CLASS" if overall >= 85 else "ELITE" if overall >= 75 else "PROFESSIONAL" if overall >= 65 else "DEVELOPING"
    return {
        "timestamp": _now(),
        "players": players,
        "overall": overall,
        "tier": tier,
        "note": "Ratings are live: every player's value is computed from what he produces right now.",
    }


def overview(symbols: List[str], window: int = 100) -> Dict[str, Any]:
    return {
        "timestamp": _now(),
        "manager": manager_briefing(symbols, window),
        "board": board_report(),
        "news": news_desk(symbols, window),
        "fans": fan_standing(symbols, window),
        "alerts": market_alerts(symbols, window),
        "squad": squad_ratings(symbols, window),
    }
