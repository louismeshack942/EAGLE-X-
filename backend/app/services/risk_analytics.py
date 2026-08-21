"""Performance analytics + risk analytics — VaR, drawdown, distribution."""
import math
import statistics
from typing import List, Optional

from app.services.persistence import journal_engine


def _pnls(entries: List[dict]) -> List[float]:
    return [e["pnl"] for e in entries if e.get("pnl") is not None]


def performance_analytics() -> dict:
    entries = journal_engine.list_entries(limit=1000)
    wins = [e for e in entries if e["result"] == "win"]
    losses = [e for e in entries if e["result"] == "loss"]
    pnls = _pnls(entries)
    avg_win = statistics.mean([e["pnl"] for e in wins]) if wins else 0.0
    avg_loss = statistics.mean([e["pnl"] for e in losses]) if losses else 0.0
    gross_win = sum(e["pnl"] for e in wins)
    gross_loss = abs(sum(e["pnl"] for e in losses))
    # by market/contract breakdown
    by_market: dict[str, dict] = {}
    by_contract: dict[str, dict] = {}
    for e in entries:
        m = e.get("market", "UNKNOWN")
        c = e.get("contract", "UNKNOWN")
        by_market.setdefault(m, {"trades": 0, "wins": 0, "pnl": 0.0})
        by_contract.setdefault(c, {"trades": 0, "wins": 0, "pnl": 0.0})
        by_market[m]["trades"] += 1
        by_contract[c]["trades"] += 1
        by_market[m]["wins"] += 1 if e["result"] == "win" else 0
        by_contract[c]["wins"] += 1 if e["result"] == "win" else 0
        by_market[m]["pnl"] += e["pnl"]
        by_contract[c]["pnl"] += e["pnl"]
    sorted_entries = sorted(entries, key=lambda e: e["pnl"])
    return {
        "total_trades": len(entries),
        "win_rate": round(len(wins) / len(entries) * 100, 2) if entries else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else 0.0,
        "net_profit": round(sum(pnls), 4),
        "average_win": round(avg_win, 4),
        "average_loss": round(avg_loss, 4),
        "best_trades": sorted_entries[-5:] if sorted_entries else [],
        "worst_trades": sorted_entries[:5] if sorted_entries else [],
        "by_market": by_market,
        "by_contract": by_contract,
    }


def value_at_risk(confidence: float = 0.95) -> dict:
    """Historical VaR and parametric VaR over journal P&L."""
    entries = journal_engine.list_entries(limit=1000)
    pnls = _pnls(entries)
    if len(pnls) < 5:
        return {"var_historical": 0.0, "var_parametric": 0.0, "confidence": confidence, "sample_size": len(pnls)}
    sorted_p = sorted(pnls)
    idx = max(0, int((1 - confidence) * len(sorted_p)))
    var_h = -sorted_p[idx]
    mu = statistics.mean(pnls)
    sd = statistics.pstdev(pnls) if len(pnls) > 1 else 0.0
    z = 1.645 if confidence >= 0.95 else 1.28
    var_p = -(mu - z * sd)
    return {
        "var_historical": round(var_h, 4),
        "var_parametric": round(var_p, 4),
        "confidence": confidence,
        "sample_size": len(pnls),
        "cvar": round(-statistics.mean(sorted_p[: max(1, idx)]), 4),
    }


def drawdown_analysis() -> dict:
    entries = list(reversed(journal_engine.list_entries(limit=1000)))
    equity = [0.0]
    for e in entries:
        equity.append(equity[-1] + e["pnl"])
    peak = 0.0
    max_dd = 0.0
    for v in equity:
        peak = max(peak, v)
        max_dd = max(max_dd, peak - v)
    net = equity[-1] if equity else 0.0
    sharpe = None
    if len(entries) > 1:
        rets = [e["pnl"] for e in entries]
        sd = statistics.pstdev(rets)
        if sd > 0:
            sharpe = round(statistics.mean(rets) / sd, 4)
    return {
        "max_drawdown": round(max_dd, 4),
        "net_profit": round(net, 4),
        "sharpe_ratio": sharpe,
        "equity_curve": equity[-200:],
    }
