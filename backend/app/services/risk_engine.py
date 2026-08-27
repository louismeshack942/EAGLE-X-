"""Phase 4 §10 — Risk Gate.

A hard, transparent gate between a VALIDATING signal and execution. It may VETO
execution; it NEVER magically improves a bad signal score. Approval is conditional on
evidence plus payout plus EV plus risk conditions ALL taken together.

The gate is pure and deterministic given a `RiskContext` snapshot (open trades, realized
loss, consecutive losses, cooldowns, connection/authorization state, kill switch, live
settings) so it is unit-testable and reproducible.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.config import settings
from app.services.signal_engine import (
    RiskState,
    VETO_AUTHORIZATION_FAILURE,
    VETO_CONFLICTING_WINDOWS,
    VETO_CONNECTION_FAILURE,
    VETO_COOLDOWN,
    VETO_DAILY_LOSS_EXCEEDED,
    VETO_DUPLICATE_SIGNAL,
    VETO_EXECUTION_LOCK,
    VETO_KILL_SWITCH,
    VETO_LOSS_STREAK_EXCEEDED,
    VETO_MISSING_PROPOSAL,
    VETO_NEGATIVE_EV,
    VETO_NOT_ENABLED,
    VETO_SIGNAL_EXPIRED,
    VETO_STALE_DATA,
    VETO_STAKE_ABOVE_LIMIT,
    VETO_TOO_MANY_OPEN,
    Signal,
)


@dataclass
class RiskContext:
    """A point-in-time snapshot of everything the gate evaluates."""

    execution_mode: str = "HARNESS"
    live_enabled: bool = False
    live_authenticated: bool = False
    connected: bool = False
    kill_switch: bool = False
    execution_lock: bool = False
    open_trades: int = 0
    realized_daily_loss: float = 0.0
    realized_session_loss: float = 0.0
    consecutive_losses: int = 0
    cooldown_until: float = 0.0            # unix ts; 0 = no cooldown
    balance: float | None = None
    now: float = 0.0                       # monotonic-safe wall clock injectable for tests
    # Optional pre-seen signal_id (dedupe) — set when the caller already recorded this id.
    seen_signal_ids: set[str] = field(default_factory=set)


class RiskGate:
    def evaluate(self, signal: Signal, ctx: RiskContext) -> tuple[str, str, list]:
        """Return (state, reason, veto_list). Deterministic, ordered, explicit."""
        vetos: list[str] = []
        reason_parts: list[str] = []

        if signal.signal_state == "REJECTED":
            vetos.append(signal.reason or "signal rejected")
        if signal.expiry > 0 and (ctx.now if ctx.now else time.time()) > signal.expiry:
            vetos.append(VETO_SIGNAL_EXPIRED)
        if signal.multi_window_state in ("CONFLICTING", "INSUFFICIENT_DATA"):
            vetos.append(VETO_CONFLICTING_WINDOWS)
        if signal.estimated_probability is None:
            vetos.append("probability missing")
        if signal.expected_value is None:
            vetos.append("missing proposal")
        elif signal.expected_value <= settings.signal_min_ev:
            vetos.append(VETO_NEGATIVE_EV)
        if not signal.proposal_source or signal.proposal_source == "UNAVAILABLE":
            vetos.append(VETO_MISSING_PROPOSAL)

        # ---- proposal / data freshness ---------------------------------------
        if signal.proposal_source == "HARNESS" and ctx.execution_mode != "HARNESS":
            # A simulated price must never drive a real/PAPER purchase.
            vetos.append("simulated proposal not usable outside HARNESS")
        if signal.data_quality in ("STALE", "DISCONNECTED", "INVALID"):
            vetos.append(VETO_STALE_DATA)

        # ---- stake / limits ---------------------------------------------------
        stake = signal.stake
        if stake < settings.risk_min_stake:
            vetos.append("stake below minimum")
        if stake > settings.risk_max_stake:
            vetos.append(VETO_STAKE_ABOVE_LIMIT)
        if ctx.balance is not None:
            if ctx.balance < stake:
                vetos.append("insufficient balance")
            elif ctx.balance - stake < settings.risk_min_reserve:
                vetos.append("reserve below minimum after trade")
        if ctx.open_trades >= settings.execution_max_open:
            vetos.append(VETO_TOO_MANY_OPEN)
        if ctx.realized_daily_loss >= settings.risk_daily_loss_limit:
            vetos.append(VETO_DAILY_LOSS_EXCEEDED)
        if ctx.realized_session_loss >= settings.risk_session_loss_limit:
            vetos.append("session loss limit exceeded")
        if ctx.consecutive_losses >= settings.risk_max_consecutive_losses:
            vetos.append(VETO_LOSS_STREAK_EXCEEDED)
        if ctx.cooldown_until and (ctx.now if ctx.now else time.time()) < ctx.cooldown_until:
            vetos.append(VETO_COOLDOWN)

        # ---- connectivity / auth ----------------------------------------------
        if ctx.execution_mode in ("PAPER", "LIVE") and not ctx.connected:
            vetos.append(VETO_CONNECTION_FAILURE)
        if ctx.execution_mode == "LIVE":
            if not ctx.live_enabled:
                vetos.append(VETO_NOT_ENABLED)
            if not ctx.live_authenticated:
                vetos.append(VETO_AUTHORIZATION_FAILURE)

        # ---- execution-level guards -------------------------------------------
        if ctx.kill_switch:
            vetos.append(VETO_KILL_SWITCH)
        if ctx.execution_lock:
            vetos.append(VETO_EXECUTION_LOCK)
        if signal.signal_id in ctx.seen_signal_ids:
            vetos.append(VETO_DUPLICATE_SIGNAL)

        if vetos:
            reason = "; ".join(dict.fromkeys(vetos))
            return RiskState.VETO.value, reason or "risk veto", list(dict.fromkeys(vetos))

        reason_parts.append(f"EV {signal.expected_value:.4f} > {settings.signal_min_ev}")
        reason_parts.append(f"multi-window {signal.multi_window_state}")
        return RiskState.PASS.value, "; ".join(reason_parts), []


__all__ = ["RiskContext", "RiskGate"]