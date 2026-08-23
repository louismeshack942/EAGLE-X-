"""Risk Guard — the circuit breakers, in money terms.

The existing RiskRules speak percentages. The Guard speaks dollars and
moments: how much you may lose today before the shutters come down, how
much profit banks the session, how fast stakes halve during a losing
streak, and the big red KILL SWITCH that stops everything instantly.

Also home of the manager's control mode:
- FULL_AUTO    — the CF trades without asking (current behaviour)
- COACH        — the CF proposes, you confirm each strike (approval queue)
- FULL_MANUAL  — the CF only advises; nothing fires without your click

And the tilt detector: rapid manual firing after a loss gets flagged
before it becomes a revenge-trading spiral.
"""
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from app.services.persistence import settings_store

_STATE_KEY = "risk_guard_state"
_lock = threading.Lock()

MODES = ("FULL_AUTO", "COACH", "FULL_MANUAL")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class RiskGuard:
    def __init__(self) -> None:
        self.killed: bool = False
        self.kill_reason: str = ""
        self.mode: str = "FULL_AUTO"
        # Money limits (0 = disabled)
        self.daily_loss_limit: float = 0.0     # e.g. 20.0 -> stop at -$20 today
        self.session_take_profit: float = 0.0  # e.g. 30.0 -> bank it at +$30
        self.max_trades_per_hour: int = 0      # 0 = unlimited
        # Streak halving
        self.streak_halving: bool = True
        # Session tracking
        self.session_start_balance: Optional[float] = None
        self.session_started_at: Optional[str] = None
        self.equity_curve: list[dict] = []
        self.trade_times: deque = deque(maxlen=500)
        self.manual_trade_times: deque = deque(maxlen=100)
        self.pending_approvals: list[dict] = []
        self._load()

    # ---------------- persistence ----------------
    def _load(self) -> None:
        s = settings_store.get(_STATE_KEY)
        if isinstance(s, dict):
            self.mode = s.get("mode", "FULL_AUTO") if s.get("mode") in MODES else "FULL_AUTO"
            self.daily_loss_limit = float(s.get("daily_loss_limit", 0.0))
            self.session_take_profit = float(s.get("session_take_profit", 0.0))
            self.max_trades_per_hour = int(s.get("max_trades_per_hour", 0))
            self.streak_halving = bool(s.get("streak_halving", True))
            self.equity_curve = list(s.get("equity_curve", []))[-500:]

    def _save(self) -> None:
        settings_store.set(_STATE_KEY, {
            "mode": self.mode,
            "daily_loss_limit": self.daily_loss_limit,
            "session_take_profit": self.session_take_profit,
            "max_trades_per_hour": self.max_trades_per_hour,
            "streak_halving": self.streak_halving,
            "equity_curve": self.equity_curve[-500:],
        })

    # ---------------- kill switch ----------------
    def kill(self, reason: str = "manager pulled the kill switch") -> dict:
        with _lock:
            self.killed = True
            self.kill_reason = reason
            self._save()
            return {"killed": True, "reason": reason}

    def release(self) -> dict:
        with _lock:
            self.killed = False
            self.kill_reason = ""
            return {"killed": False}

    # ---------------- session ----------------
    def open_session(self, balance: float) -> None:
        with _lock:
            self.session_start_balance = float(balance)
            self.session_started_at = _utcnow()
            self.equity_curve = [{"ts": self.session_started_at, "balance": float(balance)}]
            self.trade_times.clear()
            self.pending_approvals = []
            self._save()

    def record_trade(self, balance: float, manual: bool = False) -> None:
        with _lock:
            now = time.time()
            self.trade_times.append(now)
            if manual:
                self.manual_trade_times.append(now)
            self.equity_curve.append({"ts": _utcnow(), "balance": round(float(balance), 2)})
            self.equity_curve = self.equity_curve[-500:]
            self._save()

    def trades_last_hour(self) -> int:
        cutoff = time.time() - 3600
        return sum(1 for t in self.trade_times if t >= cutoff)

    # ---------------- gates ----------------
    def check(self, session_pnl: float) -> list[str]:
        """Money-terms violations. Empty list = clear to trade."""
        violations: list[str] = []
        if self.killed:
            violations.append(f"KILL_SWITCH: {self.kill_reason}")
        if self.daily_loss_limit > 0 and session_pnl <= -abs(self.daily_loss_limit):
            violations.append(
                f"DAILY_LOSS_LIMIT: session P&L ${session_pnl:.2f} hit the -${abs(self.daily_loss_limit):.2f} floor"
            )
        if self.session_take_profit > 0 and session_pnl >= self.session_take_profit:
            violations.append(
                f"TAKE_PROFIT: session P&L +${session_pnl:.2f} reached the +${self.session_take_profit:.2f} target — banking it"
            )
        if self.max_trades_per_hour > 0 and self.trades_last_hour() >= self.max_trades_per_hour:
            violations.append(
                f"MAX_TRADES_PER_HOUR: {self.trades_last_hour()} (limit {self.max_trades_per_hour})"
            )
        if self.mode == "FULL_MANUAL":
            violations.append("FULL_MANUAL mode: CF advises only — no auto strikes")
        return violations

    def needs_approval(self) -> bool:
        return self.mode == "COACH"

    def queue_approval(self, play: dict) -> dict:
        item = {"id": f"appr-{int(time.time() * 1000)}", "ts": _utcnow(), "play": play, "status": "pending"}
        with _lock:
            self.pending_approvals.append(item)
            self.pending_approvals = self.pending_approvals[-20:]
        return item

    def resolve_approval(self, approval_id: str, approve: bool) -> Optional[dict]:
        with _lock:
            for a in self.pending_approvals:
                if a["id"] == approval_id and a["status"] == "pending":
                    a["status"] = "approved" if approve else "rejected"
                    return a
        return None

    def next_pending(self) -> Optional[dict]:
        for a in self.pending_approvals:
            if a["status"] == "pending":
                return a
        return None

    # ---------------- stake shaping ----------------
    def streak_multiplier(self, consecutive_losses: int) -> float:
        """Halve the stake for every consecutive loss: 1.0, 0.5, 0.25, 0.125..."""
        if not self.streak_halving or consecutive_losses <= 0:
            return 1.0
        return round(0.5 ** consecutive_losses, 4)

    def cooldown_escalator(self, base_cooldown: float, consecutive_losses: int) -> float:
        """Each straight loss doubles the pause: 30s -> 60s -> 120s (cap 5 min)."""
        if consecutive_losses <= 0:
            return base_cooldown
        return min(300.0, base_cooldown * (2 ** consecutive_losses))

    # ---------------- tilt detector ----------------
    def tilt_warning(self, last_result: Optional[str]) -> Optional[str]:
        """3+ manual trades within 90s right after a loss = tilt."""
        if last_result != "loss":
            return None
        cutoff = time.time() - 90
        recent = sum(1 for t in self.manual_trade_times if t >= cutoff)
        if recent >= 3:
            return (
                f"TILT WARNING: {recent} manual trades in 90 seconds right after a loss. "
                "That's revenge trading. Walk away for five minutes."
            )
        return None

    # ---------------- reporting ----------------
    def status(self) -> dict:
        return {
            "killed": self.killed,
            "kill_reason": self.kill_reason,
            "mode": self.mode,
            "daily_loss_limit": self.daily_loss_limit,
            "session_take_profit": self.session_take_profit,
            "max_trades_per_hour": self.max_trades_per_hour,
            "streak_halving": self.streak_halving,
            "trades_last_hour": self.trades_last_hour(),
            "session_start_balance": self.session_start_balance,
            "session_started_at": self.session_started_at,
            "pending_approvals": [a for a in self.pending_approvals if a["status"] == "pending"],
            "equity_curve": self.equity_curve[-200:],
        }

    def set_limits(self, **kw) -> dict:
        with _lock:
            for k in ("daily_loss_limit", "session_take_profit"):
                if k in kw and kw[k] is not None:
                    setattr(self, k, max(0.0, float(kw[k])))
            if kw.get("max_trades_per_hour") is not None:
                self.max_trades_per_hour = max(0, int(kw["max_trades_per_hour"]))
            if kw.get("streak_halving") is not None:
                self.streak_halving = bool(kw["streak_halving"])
            self._save()
            return self.status()

    def set_mode(self, mode: str) -> dict:
        if mode not in MODES:
            return {"error": f"mode must be one of {MODES}"}
        with _lock:
            self.mode = mode
            self._save()
            return {"mode": self.mode}


risk_guard = RiskGuard()
