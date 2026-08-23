"""Season — the league table of your own weeks.

Every trading week gets a position on the table: TITLE RACE, TOP FOUR,
MIDTABLE, or RELEGATION BATTLE. Weekly reports grade the seven days like
a fixture list, and the season chart shows whether the club is trending
up or down. All from the journal — your own results, no fiction.
"""
from datetime import datetime, timezone
from typing import Optional

from app.services.persistence import journal_engine


def _entries(limit: int = 5000) -> list[dict]:
    return list(reversed(journal_engine.list_entries(limit=limit)))  # chronological


def _iso_week(ts) -> str:
    y, w, _ = ts.isocalendar()
    return f"{y}-W{w:02d}"


def weekly_table() -> dict:
    """P&L by ISO week — the league table of your own season."""
    weeks: dict[str, dict] = {}
    for e in _entries():
        try:
            ts = datetime.fromisoformat(e["timestamp"])
        except Exception:  # noqa: BLE001
            continue
        wk = _iso_week(ts)
        w = weeks.setdefault(wk, {"trades": 0, "wins": 0, "pnl": 0.0})
        w["trades"] += 1
        w["wins"] += 1 if e["result"] == "win" else 0
        w["pnl"] += e.get("pnl", 0.0)
    table = []
    for wk in sorted(weeks):
        w = weeks[wk]
        table.append({
            "week": wk,
            "trades": w["trades"],
            "win_rate": round(w["wins"] / w["trades"] * 100, 1) if w["trades"] else 0.0,
            "pnl": round(w["pnl"], 2),
            "position": (
                "TITLE RACE" if w["pnl"] > 5 else
                "TOP FOUR" if w["pnl"] > 0 else
                "MIDTABLE" if w["pnl"] > -5 else
                "RELEGATION BATTLE"
            ),
        })
    return {"weeks": table, "played": len(table)}


def weekly_report(week: Optional[str] = None) -> dict:
    """The manager's weekly report: fixtures, form, and the verdict."""
    table = weekly_table()["weeks"]
    if not table:
        return {"note": "no journaled trades yet — the season hasn't kicked off"}
    current = week or table[-1]["week"]
    idx = next((i for i, w in enumerate(table) if w["week"] == current), None)
    if idx is None:
        return {"note": f"no data for {current}", "weeks_available": [w["week"] for w in table]}
    wk = table[idx]
    prev = table[idx - 1] if idx > 0 else None
    trend = None
    if prev:
        trend = "improving" if wk["pnl"] > prev["pnl"] else "declining" if wk["pnl"] < prev["pnl"] else "flat"
    verdict = {
        "TITLE RACE": "Championship form — protect the routine, change nothing.",
        "TOP FOUR": "Solid week. One cleaner habit and it's a title race.",
        "MIDTABLE": "Neither here nor there — check the lessons feed before next week.",
        "RELEGATION BATTLE": "Crisis week. Cut stakes, DIFFERS only, or sit it out entirely.",
    }[wk["position"]]
    return {
        "week": current,
        "position": wk["position"],
        "trades": wk["trades"],
        "win_rate": wk["win_rate"],
        "pnl": wk["pnl"],
        "trend_vs_last_week": trend,
        "verdict": verdict,
        "table": table,
    }


def season_chart() -> dict:
    """Cumulative P&L per week — is the club climbing or sinking?"""
    table = weekly_table()["weeks"]
    cum = 0.0
    chart = []
    for w in table:
        cum += w["pnl"]
        chart.append({"week": w["week"], "cum_pnl": round(cum, 2)})
    direction = "climbing" if len(chart) >= 2 and chart[-1]["cum_pnl"] > chart[-2]["cum_pnl"] else "flat"
    return {"chart": chart, "season_pnl": round(cum, 2), "direction": direction}
