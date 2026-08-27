"""Phase 4+5 orchestration — analysis → signal → risk → (read-only) → execution.

This is the service the API+scheduler call. It reuses the Phase-2 analysis_manager
(windows), Phase-3 proposal engine, and the new signal + risk + execution engines.

Honesty gates preserved:
  - HARNESS proposal -> signal can never qualify for PAPER/LIVE (blocked at risk gate).
  - LIVE purchase requires execution_live_enabled (server-side, off by default) AND the
    signal must be truly EXECUTION_READY + risk PASS + not expired.
  - No auto execution: the scheduler inspects; a purchase always goes through execute().
"""

from __future__ import annotations

import time

from app.config import settings
from app.services.analysis_engine import analysis_manager
from app.services.broker import (
    ExecutionLock,
    KillSwitch,
    MODE_HARNESS,
    MODES,
)
from app.services.execution_engine import ExecutionEngine
from app.services.proposal_engine import ProposalService
from app.services.risk_engine import RiskContext, RiskGate
from app.services.signal_engine import (
    ExecutionState,
    RiskState,
    Signal,
    SignalState,
    build_signal,
)
from app.services.trade_persistence import save_signal, save_trade


class DecisionService:
    """Ties the pipeline together and exposes a small scheduling surface."""

    class AuthSnapshot:
        """A stub for the authenticated Deriv session/provider.

        In a real deployment this reflects the OAuth session/token. Here it is wired to
        settings so the LIVE gate can be exercised with the master switch OFF and no
        credentials. Live is genuinely not verifiable without legit credentials.
        """

        @staticmethod
        def authenticated() -> bool:
            return bool(settings.oauth_configured)

        @staticmethod
        def trading_scope() -> bool:
            return settings.oauth_configured and settings.execution_live_enabled

    def __init__(self, *, kill: KillSwitch | None = None, lock: ExecutionLock | None = None) -> None:
        self.kill = kill or KillSwitch()
        self.lock = lock or ExecutionLock()
        self.exec = ExecutionEngine(kill_switch=self.kill, lock=self.lock)
        self.proposals = ProposalService(use_live=settings.oauth_configured)
        self.risk_gate = RiskGate()
        self._seen: set[str] = set()      # signal ids we've already risk/execution processed
        self._cooldown_until: float = 0.0
        self._session_loss = 0.0
        self._daily_loss = 0.0
        self._consecutive_losses = 0
        self._produced: dict[str, Signal] = {}   # signal_id -> last produced signal

    # ---- healthy, self-maintained snapshot for risk ---------------------------
    def _risk_context(self, signal: Signal, mode: str) -> RiskContext:
        return RiskContext(
            execution_mode=mode,
            live_enabled=settings.execution_live_enabled,
            live_authenticated=self.AuthSnapshot.authenticated(),
            connected=analysis_manager.connection_state(signal.symbol) == "connected",
            kill_switch=self.kill.enabled,
            execution_lock=self.lock.held,
            open_trades=self.exec.open_count(),
            realized_daily_loss=self._daily_loss,
            realized_session_loss=self._session_loss,
            consecutive_losses=self._consecutive_losses,
            cooldown_until=self._cooldown_until,
            balance=self._balance(),
            seen_signal_ids=self._seen,
        )

    def _balance(self) -> float | None:
        # In paper/harness we don't assume a real balance; LIVE would read it. Return
        # None means "balance not known" — the risk gate then skips the reserve check
        # but STILL refuses LIVE without an authentic balance requirement upstream.
        return None

    # ---- produce a signal from current analysis + an available proposal ---------
    def produce_signal(
        self,
        *,
        symbol: str,
        family: str,
        barrier: int | None,
        window: int,
        duration_ticks: int,
        stake: float,
        source_tag: str,
        proposal,
        multi_window_state: str,
    ) -> Signal:
        """Build a VALIDATING/REJECTED signal (no risk run) from a single window + proposal."""
        import datetime

        snap = analysis_manager.snapshot(symbol, window=window)
        wins = snap.get("windows", {})
        key = str(window)
        wa = wins.get(key) or (next(iter(wins.values())) if wins else {})
        from app.services.contracts import build_spec

        spec = build_spec(symbol, family, barrier=barrier, duration_ticks=duration_ticks, stake=stake)
        snapshot_tag = f"{symbol}:{window}:{int(time.time())//60}"

        sig = build_signal(
            spec,
            window_analysis=flatten_window(wa, window),
            proposal=proposal,
            data_quality=wa.get("data_quality") or {"state": "INSUFFICIENT_DATA"},
            multi_window_state=multi_window_state,
            snapshot_tag=snapshot_tag,
            source=snap.get("source", ""),
        )
        sig.analysis_snapshot = {
            "window": window,
            "n": wa.get("n", 0),
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        self._produced[sig.signal_id] = sig
        return sig

    def produced_signal(self, signal_id: str) -> Signal | None:
        return self._produced.get(signal_id)

    # ---- risk-run a signal (moves VALIDATING -> EXECUTION_READY / REJECTED) ----
    def qualify(self, signal: Signal, mode: str = MODE_HARNESS) -> tuple[Signal, dict]:
        """Run the risk gate over a signal. Returns (signal, gate_result)."""
        ctx = self._risk_context(signal, mode)
        state, reason, vetos = self.risk_gate.evaluate(signal, ctx)
        if state == RiskState.PASS.value:
            from app.services.signal_engine import SignalEngine

            SignalEngine().confirm_eligible(signal, risk_state=state, risk_reason=reason, vetos=vetos)
        else:
            signal.risk_state = state
            signal.risk_reason = reason
            signal.risk_vetos = vetos
            signal.signal_state = SignalState.REJECTED.value
            signal.execution_state = ExecutionState.BLOCKED.value
        self._seen.add(signal.signal_id)
        save_signal(signal.to_dict())
        return signal, {"state": state, "reason": reason, "vetos": vetos}

    # ---- execute an already-qualified signal -----------------------------------
    async def execute(self, signal: Signal, *, mode: str = MODE_HARNESS) -> dict:
        if mode not in MODES:
            return {"status": "BLOCKED", "reason": f"unknown mode {mode}"}
        res = await self.exec.execute(signal, mode=mode)
        # Persist the ledger on any concrete outcome.
        for trade in self.exec.ledger(mode):
            save_trade(trade)
        return res

    # ---- lifecycle helpers ------------------------------------------------------
    def resolve_contract(self, contract_id: str, *, next_digit: int | None = None,
                         win: bool | None = None) -> dict:
        r = self.exec.resolve_result(contract_id, next_digit=next_digit, win=win)
        if r["status"] in ("WON", "LOST"):
            self._update_loss_bookkeeping(r)
        return r

    def _update_loss_bookkeeping(self, r: dict) -> None:
        if r["status"] == "LOST":
            loss = abs(r.get("profit_loss") or 0)
            self._session_loss += loss
            self._daily_loss += loss
            self._consecutive_losses += 1
            self._cooldown_until = time.time() + settings.risk_cooldown_after_loss_secs
        elif r["status"] == "WON":
            self._consecutive_losses = 0

    # ---- kill switch ----------------------------------------------------------
    def set_kill(self, on: bool) -> None:
        if on:
            self.kill.enable()
        else:
            self.kill.disable()


def flatten_window(wa: dict, window: int) -> dict:
    """Normalize an analysis window dict into {n, counts, data_quality} shape."""
    df = (wa.get("digit_frequency") or {}) if isinstance(wa, dict) else {}
    return {
        "n": (wa or {}).get("n", 0),
        "size": window,
        "counts": (df or {}).get("counts", [0] * 10),
        "data_quality": (wa or {}).get("data_quality", {"state": "INSUFFICIENT_DATA"}),
    }


_decision = None


def get_decision_service() -> DecisionService:
    global _decision
    if _decision is None:
        _decision = DecisionService()
    return _decision


__all__ = ["DecisionService", "flatten_window", "get_decision_service"]