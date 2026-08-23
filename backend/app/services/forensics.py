"""Trade Forensics — the match analyst.

Every trade gets dissected after the final whistle: did it meet ALL the
gates at entry? What kind of mistake was it? What is the expectancy per
trade, in R-multiples? How smooth is the equity curve? And the two numbers
every real trader asks for:

- **Risk of ruin** — the probability the account breaks before the edge pays.
- **Monte Carlo** — a thousand simulated seasons of the current strategy,
  so tonight's sweat has a probability distribution, not a prayer.
"""
import math
import random
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

from app.services.persistence import journal_engine

MIN_STAKE = 0.35


def entry_quality(entry: dict) -> dict:
    """Grade one journaled trade against the gates it should have passed."""
    snap = entry.get("analysis_snapshot") or {}
    checks = {
        "positive_ev": (snap.get("ev") or 0) > 0,
        "significant": bool(snap.get("significant")),
        "z_gate": abs(snap.get("z") or 0.0) >= 1.96,
        "cb_support": "SUPPORT" in (snap.get("evidence") or ""),
    }
    passed = sum(checks.values())
    return {
        "score": passed * 25,
        "checks": checks,
        "grade": "A" if passed == 4 else "B" if passed == 3 else "C" if passed == 2 else "F",
    }


def mistakes(limit: int = 500) -> dict:
    """Classify every journaled trade into a mistake taxonomy.

    CHASE: fired within the loss-cooldown of the previous trade.
    OFF_TABLE: not a DIFFERS contract (the only sustainable edge).
    THIN_EDGE: observed edge below the scouts' floor.
    UNPROVEN: no 95% significance at entry.
    OVERSIZED: stake above the 10% rule (when balance-at-entry is known).
    """
    entries = list(reversed(journal_engine.list_entries(limit=limit)))  # chronological
    counts: Counter = Counter()
    flagged = []
    prev_ts: Optional[datetime] = None
    prev_result: Optional[str] = None
    for e in entries:
        snap = e.get("analysis_snapshot") or {}
        kinds = []
        if e.get("contract") and e["contract"] != "DIFFERS":
            kinds.append("OFF_TABLE")
        edge = snap.get("observed_edge")
        if edge is not None and edge < 1.0:
            kinds.append("THIN_EDGE")
        if snap and not snap.get("significant"):
            kinds.append("UNPROVEN")
        bal = snap.get("balance_at_entry")
        if bal and e.get("stake", 0) > bal * 0.10 + 0.01:
            kinds.append("OVERSIZED")
        try:
            ts = datetime.fromisoformat(e["timestamp"])
        except Exception:  # noqa: BLE001
            ts = None
        if ts and prev_ts and prev_result == "loss" and (ts - prev_ts).total_seconds() < 30:
            kinds.append("CHASE")
        for k in kinds:
            counts[k] += 1
        if kinds:
            flagged.append({
                "id": e.get("id"), "market": e.get("market"), "contract": e.get("contract"),
                "result": e.get("result"), "pnl": e.get("pnl"), "mistakes": kinds,
                "quality": entry_quality(e)["grade"], "timestamp": e.get("timestamp"),
            })
        prev_ts, prev_result = ts, e.get("result")
    top = [{"mistake": k, "count": v} for k, v in counts.most_common()]
    return {
        "trades_reviewed": len(entries),
        "clean_trades": len(entries) - len(flagged),
        "mistake_counts": top,
        "flagged": flagged[:50],
        "verdict": (
            f"Discipline holding: {len(entries) - len(flagged)}/{len(entries)} trades clean"
            if not counts else
            f"Top problem: {top[0]['mistake']} ({top[0]['count']}x) — fix that first"
        ),
    }


def lessons() -> dict:
    """The match analyst's three-bullet debrief."""
    m = mistakes()
    notes = []
    mapping = {
        "OFF_TABLE": "Stay at the DIFFERS table — everything else is the house's game.",
        "THIN_EDGE": "Demand a fatter edge before firing; thin edges are mirages.",
        "UNPROVEN": "No significance, no trade. Wait for z to prove it.",
        "OVERSIZED": "Stakes crept over the 10% rule — the Treasurer sizes, not you.",
        "CHASE": "Trades fired inside the loss cooldown — that's chasing. The escalator now doubles the pause.",
    }
    for item in m["mistake_counts"][:3]:
        notes.append({"mistake": item["mistake"], "count": item["count"], "lesson": mapping.get(item["mistake"], "")})
    if not notes:
        notes.append({"mistake": "NONE", "count": 0, "lesson": "Clean sheet. Keep the same routine."})
    return {"lessons": notes, "based_on_trades": m["trades_reviewed"]}


def expectancy(limit: int = 1000) -> dict:
    entries = journal_engine.list_entries(limit=limit)
    if not entries:
        return {"trades": 0, "note": "no journaled trades yet"}
    wins = [e for e in entries if e["result"] == "win"]
    losses = [e for e in entries if e["result"] == "loss"]
    pnl = [e.get("pnl", 0.0) for e in entries]
    r_mult = [e.get("pnl", 0.0) / e["stake"] for e in entries if e.get("stake")]
    avg_win = sum(e["pnl"] for e in wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(e["pnl"] for e in losses) / len(losses)) if losses else 0.0
    return {
        "trades": len(entries),
        "expectancy_per_trade": round(sum(pnl) / len(pnl), 4),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "payoff_ratio": round(avg_win / avg_loss, 2) if avg_loss else None,
        "avg_r_multiple": round(sum(r_mult) / len(r_mult), 3) if r_mult else None,
        "win_rate": round(len(wins) / len(entries) * 100, 1),
    }


def smoothness(limit: int = 1000) -> dict:
    """How calm is the ride? Ulcer-style pain index over the equity curve."""
    entries = list(reversed(journal_engine.list_entries(limit=limit)))
    if len(entries) < 5:
        return {"score": None, "note": "need 5+ trades to grade smoothness"}
    equity = [0.0]
    for e in entries:
        equity.append(equity[-1] + e.get("pnl", 0.0))
    peak = equity[0]
    pains = []
    max_dd = 0.0
    for v in equity:
        peak = max(peak, v)
        dd = peak - v
        max_dd = max(max_dd, dd)
        pains.append(dd * dd)
    ulcer = math.sqrt(sum(pains) / len(pains))
    pnls = [e.get("pnl", 0.0) for e in entries]
    mean = sum(pnls) / len(pnls)
    stdev = math.sqrt(sum((p - mean) ** 2 for p in pnls) / len(pnls))
    sharpe_like = mean / stdev if stdev else 0.0
    score = max(0, min(100, int(round(100 - ulcer * 10))))
    label = "cotton" if score >= 80 else "smooth" if score >= 60 else "bumpy" if score >= 40 else "stone"
    return {
        "score": score,
        "label": label,
        "ulcer_index": round(ulcer, 3),
        "max_drawdown": round(max_dd, 2),
        "sharpe_like": round(sharpe_like, 3),
        "pnl_stdev": round(stdev, 3),
    }


def monte_carlo(
    p_win: float,
    payout: float,
    stake_pct: float = 0.10,
    start_balance: float = 100.0,
    trades: int = 200,
    sims: int = 1000,
    ruin_level: float = 0.5,
    seed: int = 42,
) -> dict:
    """Simulate `sims` seasons of `trades` bets each at the current strategy.

    ruin_level 0.5 = 'ruined' when equity falls to 50% of the start.
    Deterministic seed so tests and dashboards agree.
    """
    rng = random.Random(seed)
    finals = []
    max_dds = []
    ruins = 0
    for _ in range(sims):
        bal = start_balance
        peak = bal
        worst_dd = 0.0
        ruined = False
        for _ in range(trades):
            stake = max(MIN_STAKE, bal * stake_pct)
            if bal < MIN_STAKE:
                ruined = True
                break
            if rng.random() < p_win:
                bal += stake * (payout - 1)
            else:
                bal -= stake
            peak = max(peak, bal)
            dd = (peak - bal) / peak if peak else 0.0
            worst_dd = max(worst_dd, dd)
            if bal <= start_balance * ruin_level:
                ruined = True
                break
        finals.append(bal)
        max_dds.append(worst_dd)
        ruins += ruined
    finals.sort()
    max_dds.sort()

    def pct(arr, q):
        return arr[min(len(arr) - 1, int(q * len(arr)))]

    ev_edge = round(p_win * payout - 1.0, 4)
    return {
        "params": {
            "p_win": p_win, "payout": payout, "stake_pct": stake_pct,
            "start_balance": start_balance, "trades": trades, "sims": sims,
            "edge_per_trade": ev_edge,
        },
        "final_p05": round(pct(finals, 0.05), 2),
        "final_p50": round(pct(finals, 0.50), 2),
        "final_p95": round(pct(finals, 0.95), 2),
        "max_drawdown_median": round(pct(max_dds, 0.5) * 100, 1),
        "max_drawdown_p95": round(pct(max_dds, 0.95) * 100, 1),
        "risk_of_ruin_pct": round(ruins / sims * 100, 1),
        "verdict": (
            f"{trades} trades at {stake_pct * 100:.0f}% stakes, edge {ev_edge:+.3f}: "
            f"median season ends ${pct(finals, 0.5):.0f}, "
            f"ruin risk {ruins / sims * 100:.1f}%, worst 5% of seasons draw down {pct(max_dds, 0.95) * 100:.0f}%."
        ),
    }


def risk_of_ruin(limit: int = 1000) -> dict:
    """Risk of ruin computed from the CF's OWN journaled record."""
    exp = expectancy(limit)
    if exp["trades"] < 20:
        return {"note": "need 20+ journaled trades to compute an honest ruin risk", "trades": exp["trades"]}
    p = exp["win_rate"] / 100.0
    payout = (exp["avg_win"] / exp["avg_loss"] + 1.0) if exp.get("avg_loss") else 1.1
    mc = monte_carlo(p_win=p, payout=max(1.01, payout), stake_pct=0.10)
    return {
        "based_on_trades": exp["trades"],
        "observed_win_rate": exp["win_rate"],
        "observed_payoff": exp["payoff_ratio"],
        "risk_of_ruin_pct": mc["risk_of_ruin_pct"],
        "median_drawdown_pct": mc["max_drawdown_median"],
        "verdict": (
            "RoR near zero — the edge and the 10% rule are doing their job"
            if mc["risk_of_ruin_pct"] < 1 else
            f"Ruin risk {mc['risk_of_ruin_pct']}% — cut stakes or tighten the gates"
        ),
        "simulation": mc,
    }


def suggestions() -> dict:
    """Top three things that would most improve the P&L, from the evidence."""
    from app.services.scout import calibration, journal_breakdown, performance_by_hour

    out = []
    cal = calibration()
    if cal.get("overconfident"):
        out.append(("HIGH", "The CF overstates his confidence — trust z-scores, not his chest-beating."))
    bd = journal_breakdown()
    by_contract = bd.get("by_contract", {})
    total = sum(v["trades"] for v in by_contract.values()) or 1
    differs = by_contract.get("DIFFERS", {}).get("trades", 0)
    if total >= 10 and differs / total < 0.8:
        out.append(("HIGH", f"Only {differs}/{total} trades were DIFFERS — drag the CF back to the one sustainable table."))
    by_symbol = bd.get("by_symbol", {})
    losers = [(s, v) for s, v in by_symbol.items() if v["trades"] >= 5 and v["pnl"] < 0]
    if losers:
        worst = min(losers, key=lambda kv: kv[1]["pnl"])
        out.append(("MEDIUM", f"{worst[0]} has cost ${abs(worst[1]['pnl']):.2f} over {worst[1]['trades']} trades — drop it from the rotation."))
    pf = bd.get("profit_factor")
    if pf is not None and pf < 1.0:
        out.append(("HIGH", f"Profit factor {pf} — losses outsize wins. Halve stakes until the gates tighten."))
    hours = performance_by_hour()
    if hours.get("bad_hours_utc"):
        out.append(("MEDIUM", f"Your own journal says UTC {hours['bad_hours_utc']} are losing hours — the hot-hours filter now benches those."))
    m = mistakes()
    if m["mistake_counts"]:
        top = m["mistake_counts"][0]
        out.append(("MEDIUM", f"Most frequent mistake: {top['mistake']} ({top['count']}x). See the lessons feed."))
    if not out:
        out.append(("LOW", "Nothing broken. The discipline is the edge — keep the same routine."))
    return {"suggestions": [{"priority": p, "text": t} for p, t in out[:3]]}


def session_scorecard(guard_equity: list[dict], session_pnl: float, trades: int, wins: int) -> dict:
    """Grade the current session A-F: result 40%, discipline 30%, smoothness 30%."""
    if trades == 0:
        return {"grade": "N/A", "note": "no trades yet this session", "components": {}}
    win_rate = wins / trades * 100
    result_score = min(100, max(0, 50 + session_pnl * 5))
    m = mistakes()
    discipline = max(0, 100 - len(m["flagged"]) * 15)
    smooth_score = smoothness().get("score") or 60
    total = result_score * 0.4 + discipline * 0.3 + smooth_score * 0.3
    grade = "A" if total >= 85 else "B" if total >= 70 else "C" if total >= 55 else "D" if total >= 40 else "F"
    return {
        "grade": grade,
        "score": round(total, 1),
        "components": {
            "result": round(result_score, 1),
            "discipline": round(discipline, 1),
            "smoothness": round(smooth_score, 1),
        },
        "session_pnl": round(session_pnl, 2),
        "trades": trades,
        "win_rate": round(win_rate, 1),
        "note": {
            "A": "Textbook session — bank the routine.",
            "B": "Solid. One habit away from an A.",
            "C": "Mixed bag — check the lessons feed.",
            "D": "Sloppy — the Guard should tighten the leash.",
            "F": "Stop. Read the debrief before the next session.",
        }[grade],
    }
