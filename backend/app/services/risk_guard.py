"""Risk Guard — the circuit breakers, in money terms.

The existing RiskRules speak percentages. The Guard speaks dollars and
moments: how much you may lose today before the shutters come down, how
much profit banks the session, how fast stakes halve during a losing
streak, and the big red KILL SWITCH that stops everything instantly.

Also home of the manager's control mode:
- FULL_AUTO    — the CF trades without asking (current behaviour)
- COACH        — the CF proposes, you confirm each strike (approval queue)
- FULL_MANUAL  — the CF only advises; nothing fires without your click
- HYBRID       — overwhelming evidence fires instantly, the rest asks you

Arming is durable: GUARD_* env vars override the persisted store, so a
Render redeploy (which wipes store.json) can never disarm the account.
"""
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from app.services.persistence import settings_store

_STATE_KEY = "risk_guard_state"
_lock = threading.Lock()

MODES = ("FULL_AUTO", "COACH", "FULL_MANUAL", "HYBRID")


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
        # Trailing session stop: once peak P&L >= trail_arm, stop if P&L
        # falls back below peak * (1 - trail_pct). 0 = disabled.
        self.trail_arm: float = 0.0
        self.trail_pct: float = 0.5
        self.peak_balance: Optional[float] = None
        # Auto-arm: kill everything automatically at this session drawdown
        self.auto_kill_drawdown_pct: float = 0.0  # 0 = disabled
        # Schedule: UTC hours when trading is allowed. Empty = always.
        self.allowed_hours_utc: list[int] = []
        # Quiet hours for low-priority alerts (risk alerts always fire)
        self.quiet_hours_utc: list[int] = []
        # Mode escalation: auto-switch to COACH after this many straight losses
        self.escalate_after_losses: int = 0  # 0 = disabled
        # Manual stake: the manager sets the stake himself in dollars.
        # 0 = auto (10% of spendable, Kelly-capped). When set, every play
        # fires at exactly this amount — no Kelly, no 10% rule, no drawdown
        # scaling, no streak halving. The manager owns the bullet; the
        # Guard's dollar limits and kill switch own the gun.
        self.stake_override: float = 0.0
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
            self.trail_arm = float(s.get("trail_arm", 0.0))
            self.trail_pct = float(s.get("trail_pct", 0.5))
            self.auto_kill_drawdown_pct = float(s.get("auto_kill_drawdown_pct", 0.0))
            self.allowed_hours_utc = list(s.get("allowed_hours_utc", []))
            self.quiet_hours_utc = list(s.get("quiet_hours_utc", []))
            self.escalate_after_losses = int(s.get("escalate_after_losses", 0))
            self.stake_override = float(s.get("stake_override", 0.0))
        self._apply_env()

    def _apply_env(self) -> None:
        """GUARD_* env vars override the store. store.json dies on every
        Render redeploy; env vars are forever. This is how the account
        stays armed across deploys."""
        def f(name):
            v = os.environ.get(name)
            return float(v) if v not in (None, "") else None

        def i(name):
            v = f(name)
            return int(v) if v is not None else None

        for env_name, attr in (
            ("GUARD_DAILY_LOSS_LIMIT", "daily_loss_limit"),
            ("GUARD_TAKE_PROFIT", "session_take_profit"),
            ("GUARD_TRAIL_ARM", "trail_arm"),
            ("GUARD_AUTO_KILL_DD", "auto_kill_drawdown_pct"),
        ):
            v = f(env_name)
            if v is not None:
                setattr(self, attr, max(0.0, v))
        v = i("GUARD_MAX_TRADES_PER_HOUR")
        if v is not None:
            self.max_trades_per_hour = max(0, v)
        v = i("GUARD_ESCALATE_LOSSES")
        if v is not None:
            self.escalate_after_losses = max(0, v)
        v = f("GUARD_STAKE_OVERRIDE")
        if v is not None:
            self.stake_override = max(0.0, v)

    def set_stake(self, amount: float) -> dict:
        """Manager sets the stake himself. 0 returns control to the GK."""
        with _lock:
            self.stake_override = max(0.0, float(amount))
            self._save()
            return {
                "stake_override": self.stake_override,
                "mode": "manual stake" if self.stake_override > 0 else "auto (10% rule)",
            }

    def _save(self) -> None:
        settings_store.set(_STATE_KEY, {
            "mode": self.mode,
            "stake_override": self.stake_override,
            "daily_loss_limit": self.daily_loss_limit,
            "session_take_profit": self.session_take_profit,
            "max_trades_per_hour": self.max_trades_per_hour,
            "streak_halving": self.streak_halving,
            "equity_curve": self.equity_curve[-500:],
            "trail_arm": self.trail_arm,
            "trail_pct": self.trail_pct,
            "auto_kill_drawdown_pct": self.auto_kill_drawdown_pct,
            "allowed_hours_utc": self.allowed_hours_utc,
            "quiet_hours_utc": self.quiet_hours_utc,
            "escalate_after_losses": self.escalate_after_losses,
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
            self.peak_balance = float(balance)
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
            self.peak_balance = max(self.peak_balance or balance, balance)
            self.equity_curve.append({"ts": _utcnow(), "balance": round(float(balance), 2)})
            self.equity_curve = self.equity_curve[-500:]
            self._save()

    def trades_last_hour(self) -> int:
        cutoff = time.time() - 3600
        return sum(1 for t in self.trade_times if t >= cutoff)

    def peak_pnl(self) -> float:
        if self.session_start_balance is None or self.peak_balance is None:
            return 0.0
        return self.peak_balance - self.session_start_balance

    def alerts_quiet_now(self) -> bool:
        """Quiet hours: low-priority alerts sleep; risk alerts always fire."""
        if not self.quiet_hours_utc:
            return False
        return datetime.now(timezone.utc).hour in self.quiet_hours_utc

    # ---------------- gates ----------------
    def check(self, session_pnl: float, balance: Optional[float] = None) -> list[str]:
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
        # Trailing session stop: armed once peak P&L clears trail_arm, fires
        # when P&L gives back trail_pct of the peak. Winning then handing it
        # back is the sweat-bucket filler — this nails the shutters.
        peak = self.peak_pnl()
        if self.trail_arm > 0 and peak >= self.trail_arm:
            floor = peak * (1.0 - self.trail_pct)
            if session_pnl <= floor:
                violations.append(
                    f"TRAILING_STOP: gave back the peak — P&L ${session_pnl:.2f} vs peak +${peak:.2f} (floor ${floor:.2f})"
                )
        # Auto-arm: the kill switch pulls ITSELF at the drawdown you set.
        if (
            self.auto_kill_drawdown_pct > 0
            and balance is not None
            and self.session_start_balance
            and balance <= self.session_start_balance * (1.0 - self.auto_kill_drawdown_pct)
        ):
            self.kill(f"auto-armed at {self.auto_kill_drawdown_pct * 100:.0f}% session drawdown")
            violations.append(
                f"AUTO_KILL: drawdown {self.auto_kill_drawdown_pct * 100:.0f}% reached — kill switch pulled itself"
            )
        # Schedule: only the hours you said you trade well.
        if self.allowed_hours_utc and datetime.now(timezone.utc).hour not in self.allowed_hours_utc:
            violations.append(
                f"SCHEDULE: outside your allowed hours (UTC {self.allowed_hours_utc})"
            )
        if self.mode == "FULL_MANUAL":
            violations.append("FULL_MANUAL mode: CF advises only — no auto strikes")
        return violations

    def maybe_escalate(self, consecutive_losses: int) -> Optional[str]:
        """Mode escalation ladder: enough straight losses and the manager
        takes the trigger away from the CF — COACH mode engages itself."""
        if self.escalate_after_losses > 0 and self.mode == "FULL_AUTO" \
                and consecutive_losses >= self.escalate_after_losses:
            self.set_mode("COACH")
            return (
                f"ESCALATION: {consecutive_losses} straight losses — "
                "manager takes the trigger, COACH mode engaged"
            )
        return None

    PRESETS = {
        "SAFE": {
            "daily_loss_limit": 10.0, "session_take_profit": 10.0,
            "max_trades_per_hour": 6, "streak_halving": True,
            "trail_arm": 5.0, "trail_pct": 0.5, "auto_kill_drawdown_pct": 0.15,
            "escalate_after_losses": 2,
        },
        "BALANCED": {
            "daily_loss_limit": 20.0, "session_take_profit": 25.0,
            "max_trades_per_hour": 12, "streak_halving": True,
            "trail_arm": 10.0, "trail_pct": 0.5, "auto_kill_drawdown_pct": 0.25,
            "escalate_after_losses": 3,
        },
        "AGGRESSIVE": {
            "daily_loss_limit": 40.0, "session_take_profit": 0.0,
            "max_trades_per_hour": 25, "streak_halving": False,
            "trail_arm": 0.0, "trail_pct": 0.5, "auto_kill_drawdown_pct": 0.0,
            "escalate_after_losses": 0,
        },
    }

    def apply_preset(self, name: str) -> dict:
        preset = self.PRESETS.get(name.upper())
        if not preset:
            return {"error": f"unknown preset — choose from {list(self.PRESETS)}"}
        with _lock:
            for k, v in preset.items():
                setattr(self, k, v)
            self._save()
            return {"preset": name.upper(), "applied": preset, "status": self.status()}

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
            "trail_arm": self.trail_arm,
            "trail_pct": self.trail_pct,
            "peak_pnl": round(self.peak_pnl(), 2),
            "auto_kill_drawdown_pct": self.auto_kill_drawdown_pct,
            "allowed_hours_utc": self.allowed_hours_utc,
            "quiet_hours_utc": self.quiet_hours_utc,
            "escalate_after_losses": self.escalate_after_losses,
            "stake_override": self.stake_override,
            "trades_last_hour": self.trades_last_hour(),
            "session_start_balance": self.session_start_balance,
            "session_started_at": self.session_started_at,
            "pending_approvals": [a for a in self.pending_approvals if a["status"] == "pending"],
            "equity_curve": self.equity_curve[-200:],
        }

    def set_limits(self, **kw) -> dict:
        with _lock:
            for k in ("daily_loss_limit", "session_take_profit", "trail_arm"):
                if k in kw and kw[k] is not None:
                    setattr(self, k, max(0.0, float(kw[k])))
            if kw.get("trail_pct") is not None:
                self.trail_pct = max(0.05, min(0.95, float(kw["trail_pct"])))
            if kw.get("auto_kill_drawdown_pct") is not None:
                self.auto_kill_drawdown_pct = max(0.0, min(0.9, float(kw["auto_kill_drawdown_pct"])))
            if kw.get("max_trades_per_hour") is not None:
                self.max_trades_per_hour = max(0, int(kw["max_trades_per_hour"]))
            if kw.get("streak_halving") is not None:
                self.streak_halving = bool(kw["streak_halving"])
            if kw.get("escalate_after_losses") is not None:
                self.escalate_after_losses = max(0, int(kw["escalate_after_losses"]))
            if kw.get("allowed_hours_utc") is not None:
                self.allowed_hours_utc = sorted({int(h) % 24 for h in kw["allowed_hours_utc"]})
            if kw.get("quiet_hours_utc") is not None:
                self.quiet_hours_utc = sorted({int(h) % 24 for h in kw["quiet_hours_utc"]})
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
