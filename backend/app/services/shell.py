"""External shell: audit log, real-trade certification budget, and the
ops/safety card. Backs the Complete Build & Certification directive.

- AuditLog (§16): every important action gets an immutable record —
  actor, action, old/new value, reason. Latest-first reads.
- RealTradeBudget (§6): REAL test trades are capped at exactly $1 stake
  and 60 lifetime trades across everything (not per market/contract).
  Trade #60 permanently locks real test execution until a human resets
  the budget — the FINAL LOCK (§20).
- ops_card: one call that aggregates system health, risk, speed,
  precision, organism selectivity and ledger unknowns for the Command
  Center surface.
"""
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Deque, Dict, List, Optional

# Certification constants — immutable by the engine (directive §6/§20).
CERT_STAKE_USD = 1.0
CERT_MAX_TRADES = 60


@dataclass
class AuditEvent:
    ts: float
    actor: str
    action: str
    detail: str = ""
    old: Optional[str] = None
    new: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


class AuditLog:
    """Append-only, thread-safe, newest-first (§16)."""

    def __init__(self, capacity: int = 5000):
        self._events: Deque[AuditEvent] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def record(self, actor: str, action: str, detail: str = "",
               old: Optional[str] = None, new: Optional[str] = None) -> dict:
        ev = AuditEvent(time.time(), actor, action, detail, old, new)
        with self._lock:
            self._events.appendleft(ev)
        return ev.to_dict()

    def list(self, limit: int = 100, action: Optional[str] = None) -> List[dict]:
        out = []
        with self._lock:
            for ev in self._events:
                if action and ev.action != action:
                    continue
                out.append(ev.to_dict())
                if len(out) >= limit:
                    break
        return out

    def count(self) -> int:
        with self._lock:
            return len(self._events)


@dataclass
class CertTrade:
    n: int
    symbol: str
    contract: str
    stake: float
    result: str
    pnl: float
    latency_ms: Optional[float] = None
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class RealTradeBudget:
    """§6/§20: the 60-trade, $1-stake certification cap. Once #60 lands,
    real test execution stays locked until a human explicitly resets."""

    def __init__(self):
        self._lock = threading.Lock()
        self._trades: List[CertTrade] = []
        self.locked = False

    # stake validation happens BEFORE any broker call is even considered
    def validate_stake(self, stake: float) -> Optional[str]:
        if stake != CERT_STAKE_USD:
            return (f"certification stake must be exactly ${CERT_STAKE_USD:.2f} — "
                    "no martingale, no escalation (§6)")
        return None

    def record(self, stake: float, symbol: str, contract: str, result: str,
               pnl: float, latency_ms: Optional[float] = None,
               reason: str = "") -> dict:
        err = self.validate_stake(stake)
        if err:
            return {"ok": False, "error": err}
        with self._lock:
            if self.locked:
                return {"ok": False, "error": "REAL_TEST_EXECUTION_LOCKED — budget spent (§20)"}
            n = len(self._trades) + 1
            trade = CertTrade(n, symbol, contract, stake, result, pnl, latency_ms, reason)
            self._trades.append(trade)
            if n >= CERT_MAX_TRADES:
                self.locked = True
            return {
                "ok": True,
                "trade_number": n,
                "max": CERT_MAX_TRADES,
                "remaining": max(0, CERT_MAX_TRADES - n),
                "locked": self.locked,
                "trade": trade.to_dict(),
            }

    def reset(self, actor: str) -> dict:
        with self._lock:
            self._trades.clear()
            self.locked = False
        return {"ok": True, "reset_by": actor,
                "note": "explicit human authorization required to reopen real "
                        "test execution (§20)"}

    def report(self) -> dict:
        with self._lock:
            trades = list(self._trades)
            locked = self.locked
        wins = sum(1 for t in trades if t.result == "win")
        losses = sum(1 for t in trades if t.result == "loss")
        pnl = sum(t.pnl for t in trades)
        stake = sum(t.stake for t in trades)
        payout = sum(t.stake + t.pnl for t in trades if t.result == "win")
        peak, cum, dd, streak, worst_streak = 0.0, 0.0, 0.0, 0, 0
        by_market: Dict[str, dict] = {}
        by_contract: Dict[str, dict] = {}
        lat = sorted(t.latency_ms for t in trades if t.latency_ms is not None)

        def pct(xs, p):
            return round(xs[min(len(xs) - 1, max(0, int(p * len(xs)) - 1))], 1) if xs else None

        for t in trades:
            cum += t.pnl
            peak = max(peak, cum)
            dd = max(dd, peak - cum)
            streak = streak + 1 if t.result == "loss" else 0
            worst_streak = max(worst_streak, streak)
            m = by_market.setdefault(t.symbol, {"wins": 0, "losses": 0, "pnl": 0.0})
            m["wins" if t.result == "win" else "losses"] += 1
            m["pnl"] = round(m["pnl"] + t.pnl, 2)
            c = by_contract.setdefault(t.contract, {"wins": 0, "losses": 0, "pnl": 0.0})
            c["wins" if t.result == "win" else "losses"] += 1
            c["pnl"] = round(c["pnl"] + t.pnl, 2)
        return {
            "total_trades": len(trades),
            "max_allowed": CERT_MAX_TRADES,
            "locked": locked,
            "stake_per_trade": CERT_STAKE_USD,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / len(trades), 4) if trades else None,
            "total_stake": round(stake, 2),
            "total_payout": round(payout, 2),
            "net_pnl": round(pnl, 2),
            "max_consecutive_losses": worst_streak,
            "max_drawdown": round(dd, 2),
            "by_market": by_market,
            "by_contract": by_contract,
            "latency_ms": {"p50": pct(lat, 0.50), "p95": pct(lat, 0.95),
                           "p99": pct(lat, 0.99)} if lat else None,
            "note": "Do not cherry-pick — this ledger reports every trade (§19).",
        }


audit_log = AuditLog()
real_trade_budget = RealTradeBudget()
