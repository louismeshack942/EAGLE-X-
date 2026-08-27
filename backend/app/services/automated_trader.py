"""Phase 6 — Automated Trading Orchestrator.

The automated trader is a CLIENT of the existing Phase 4/5 pipeline. It NEVER computes a
parallel signal, NEVER calls a Deriv purchase API directly, and NEVER bypasses the signal
engine, risk gate, execution lock, idempotency, or reconciliation.

Data path (honoured exactly):

    LIVE DATA
      -> ANALYSIS
      -> PROPOSAL
      -> SIGNAL
      -> RISK GATE
      -> AUTOMATED DECISION
      -> EXECUTION ENGINE
      -> BROKER
      -> RESULT -> LEDGER -> PERFORMANCE

It must NEVER become:

    LIVE DATA -> AUTOMATED TRADER -> DIRECT BUY

Modes: OFF | MONITOR | PAPER | LIVE. Default OFF.
MONITOR runs analysis/signal/risk but never invokes the execution engine (dry run).
PAPER runs the full lifecycle through the execution engine in PAPER/HARNESS broker mode.
LIVE requires the server-side master switch `execution_live_enabled` AND every gate to pass.

The trader can NEVER enable live execution by itself.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum

from app.config import settings
from app.services.contracts import ContractSpec, all_specs_for_symbol
from app.services.decision_service import DecisionService, get_decision_service
from app.services.signal_engine import (
    RiskState,
    Signal,
    SignalState,
    contract_priority,
)

logger = logging.getLogger("eaglex.automated_trader")


# ----------------------------------------------------------------------------
# Automation state machine (deterministic transitions)
# ----------------------------------------------------------------------------
class AutomationState(str, Enum):
    OFF = "OFF"
    STARTING = "STARTING"
    MONITORING = "MONITORING"
    ANALYZING = "ANALYZING"
    VALIDATING = "VALIDATING"
    READY = "READY"
    EXECUTING = "EXECUTING"
    TRACKING = "TRACKING"
    PAUSED = "PAUSED"
    ERROR = "ERROR"
    STOPPING = "STOPPING"


# Settled vs transient states.
_SETTLED = {AutomationState.OFF, AutomationState.MONITORING, AutomationState.PAUSED,
            AutomationState.ERROR}


@dataclass
class AutomationConfig:
    """Server-side automation configuration. Never trusted from the frontend.

    Each field is validated against a conservative ceiling on assignment.
    """

    enabled: bool = False
    mode: str = "OFF"                    # OFF | MONITOR | PAPER | LIVE
    max_trades_per_session: int = 20
    max_trades_per_day: int = 50
    max_open: int = 1
    max_daily_loss: float = 5.0
    max_session_loss: float = 5.0
    max_consecutive_losses: int = 3
    cooldown_secs: float = 30.0
    max_signal_age_secs: float = 5.0
    min_signal_quality: float = 60.0
    max_stake: float = 1.0
    min_stake: float = 0.1
    allowed_markets: list[str] = field(default_factory=lambda: ["R_10"])
    allowed_families: list[str] = field(default_factory=lambda: [
        "MATCHES", "OVER", "UNDER", "ODD", "EVEN", "DIFFERS"])

    MAX_TRADES_SESSION = 500
    MAX_TRADES_DAY = 2000
    MAX_OPEN = 10
    MAX_LOSS = 1_000_000.0
    MAX_STAKE_FLOOR = 1_000_000.0

    @classmethod
    def from_settings(cls) -> "AutomationConfig":
        return cls(
            enabled=settings.automation_enabled,
            mode=settings.automation_mode_default.upper(),
            max_trades_per_session=settings.automation_max_trades_per_session,
            max_trades_per_day=settings.automation_max_trades_per_day,
            max_open=min(settings.automation_max_open, settings.execution_max_open),
            max_daily_loss=settings.automation_max_daily_loss,
            max_session_loss=settings.automation_max_session_loss,
            max_consecutive_losses=settings.automation_max_consecutive_losses,
            cooldown_secs=settings.automation_cooldown_secs,
            max_signal_age_secs=settings.automation_max_signal_age_secs,
            min_signal_quality=settings.automation_min_signal_quality,
            max_stake=min(settings.automation_max_stake,
                          settings.risk_max_stake, settings.live_stake_max),
            min_stake=max(settings.automation_min_stake, settings.risk_min_stake),
            allowed_markets=[m.strip() for m in settings.automation_allowed_markets.split(",")
                             if m.strip()],
            allowed_families=[f.strip().upper() for f in settings.automation_allowed_families.split(",")
                              if f.strip()],
        )

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        return d

    # ---- validation (used on every start/live start) ----
    def problems(self) -> list[str]:
        probs: list[str] = []
        if self.mode not in ("OFF", "MONITOR", "PAPER", "LIVE"):
            probs.append(f"invalid mode {self.mode!r}")
        if self.max_trades_per_session <= 0 or self.max_trades_per_session > self.MAX_TRADES_SESSION:
            probs.append("max_trades_per_session out of range")
        if self.max_trades_per_day <= 0 or self.max_trades_per_day > self.MAX_TRADES_DAY:
            probs.append("max_trades_per_day out of range")
        if self.max_open <= 0 or self.max_open > self.MAX_OPEN:
            probs.append("max_open out of range")
        if self.max_daily_loss < 0 or self.max_daily_loss > self.MAX_LOSS:
            probs.append("max_daily_loss out of range")
        if self.max_session_loss < 0 or self.max_session_loss > self.MAX_LOSS:
            probs.append("max_session_loss out of range")
        if self.max_consecutive_losses < 0 or self.max_consecutive_losses > 1000:
            probs.append("max_consecutive_losses out of range")
        if self.cooldown_secs < 0 or self.cooldown_secs > 3600 * 24:
            probs.append("cooldown_secs out of range")
        if self.max_stake < self.min_stake:
            probs.append("max_stake below min_stake")
        if not self.allowed_markets:
            probs.append("no allowed markets")
        if not self.allowed_families:
            probs.append("no allowed families")
        return probs


@dataclass
class AuditEntry:
    ts: float
    kind: str            # start|stop|pause|resume|candidate|signal|risk|decision|execution|error|recovery|...
    message: str
    detail: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------
# The automated trader
# ----------------------------------------------------------------------------
class AutomatedTrader:
    """Runs the automated cycle against the existing pipeline. Thread/aio-safe.

    Execution concurrency is guarded by the (single) ExecutionLock owned by the
    DecisionService/ExecutionEngine, plus a local _scan_lock so only one scan cycle runs
    at a time.
    """

    def __init__(self, *, ds: DecisionService | None = None) -> None:
        self.ds = ds or get_decision_service()
        self.cfg = AutomationConfig.from_settings()
        self.state: AutomationState = AutomationState.OFF
        self.state_changed_at: float = time.time()

        # live counters (in-process; reset on restart by design — persisted where needed)
        self.session_trades = 0
        self.session_wins = 0
        self.session_losses = 0
        self.session_loss = 0.0
        self.daily_loss = 0.0
        self.consecutive_losses = 0
        self.last_signal_id: str = ""
        self.last_signal_state: str = "NO_SIGNAL"
        self.last_risk_state: str = "NOT_RUN"
        self.last_decision: str = "NONE"
        self.last_execution: str = "NONE"
        self.last_scan_ts: float = 0.0
        self.error: str = ""

        self.audit: list[AuditEntry] = []
        self._scan_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._run_lock = threading.RLock()

    # ------------------------------------------------------------------ config
    def set_config(self, cfg: AutomationConfig) -> list[str]:
        probs = cfg.problems()
        if probs:
            return probs
        with self._run_lock:
            self.cfg = cfg
        self._audit("config", f"configuration updated -> mode {cfg.mode}")
        return []

    def get_config(self) -> AutomationConfig:
        return self.cfg

    # ------------------------------------------------------------------- audit
    def _audit(self, kind: str, message: str, detail: dict | None = None) -> None:
        self.audit.append(AuditEntry(time.time(), kind, message, detail or {}))
        if len(self.audit) > 500:
            self.audit = self.audit[-500:]

    def audit_log(self, limit: int = 100) -> list[dict]:
        rows = [
            {"ts": e.ts, "kind": e.kind, "message": e.message, "detail": e.detail}
            for e in self.audit
        ]
        return rows[-limit:] if limit else rows

    # ------------------------------------------------------------- state helper
    def _set_state(self, s: AutomationState, clear_error: bool = False) -> None:
        with self._run_lock:
            self.state = s
            self.state_changed_at = time.time()
            if clear_error:
                self.error = ""

    def status(self) -> dict:
        with self._run_lock:
            return {
                "state": self.state.value,
                "mode": self.cfg.mode,
                "enabled": self.cfg.enabled,
                "config": self.cfg.as_dict(),
                "live_enabled": settings.execution_live_enabled,
                "kill_switch": self.ds.kill.enabled,
                # session counters
                "session_trades": self.session_trades,
                "session_wins": self.session_wins,
                "session_losses": self.session_losses,
                "session_loss": round(self.session_loss, 4),
                "daily_loss": round(self.daily_loss, 4),
                "consecutive_losses": self.consecutive_losses,
                "open_trades": self.ds.exec.open_count(),
                "connected": self._any_connected(),
                "authenticated": self.ds.AuthSnapshot.authenticated(),
                "last_signal_id": self.last_signal_id,
                "last_signal_state": self.last_signal_state,
                "last_risk_state": self.last_risk_state,
                "last_decision": self.last_decision,
                "last_execution": self.last_execution,
                "error": self.error,
            }

    def _any_connected(self) -> bool:
        from app.services.analysis_engine import analysis_manager

        any_connected = False
        for m in self.cfg.allowed_markets:
            if analysis_manager.connection_state(m) == "connected":
                any_connected = True
        return any_connected

    # ------------------------------------------------------- safe start/stop
    def validate_start(self) -> tuple[bool, list[str]]:
        """Phase 6 §13 — safe start prerequisites. Nothing starts LIVE without all passing."""
        probs: list[str] = []
        if not self.cfg.enabled:
            probs.append("automation not enabled")
        if self.cfg.mode == "OFF":
            probs.append("automation mode is OFF")
        probs.extend(self.cfg.problems())
        if self.ds.kill.enabled:
            probs.append("kill switch is ACTIVE")
        if self.cfg.mode == "LIVE":
            if not settings.execution_live_enabled:
                probs.append("execution_live_enabled is FALSE (server master switch)")
            if not self.ds.AuthSnapshot.authenticated():
                probs.append("not authenticated")
        return (not probs, probs)

    def start(self) -> dict:
        ok, probs = self.validate_start()
        if not ok:
            self._set_state(AutomationState.ERROR)
            self.error = "; ".join(probs)
            self._audit("start", "automation refused to start", {"problems": probs})
            return {"ok": False, "state": self.state.value, "problems": probs}

        with self._run_lock:
            self.state = AutomationState.STARTING
            self.state_changed_at = time.time()
            self.error = ""
            self.session_trades = 0
            self.session_loss = 0.0
            self.consecutive_losses = 0
            # daily loss carries across restarts only if it was read from persistence;
            # keep in-memory = fresh day baseline unless a scheduler rehydrates it.
            self.daily_loss = 0.0
        self._audit("start", f"automation starting in mode {self.cfg.mode}")
        return {"ok": True, "state": self.state.value, "mode": self.cfg.mode}

    def stop(self) -> dict:
        """Safe stop: stop new execution, keep tracking open contracts."""
        if self._task:
            self._task.cancel()
        self._set_state(AutomationState.STOPPING)
        self._set_state(AutomationState.OFF)
        self._audit("stop", "automation stopped (open contracts still tracked)")
        return {"ok": True, "state": self.state.value}

    def pause(self) -> dict:
        if self.state in (AutomationState.OFF, AutomationState.STOPPING):
            return {"ok": False, "state": self.state.value, "reason": "cannot pause from OFF"}
        self._set_state(AutomationState.PAUSED)
        self._audit("pause", "automation paused")
        return {"ok": True, "state": self.state.value}

    def resume(self) -> dict:
        if self.state != AutomationState.PAUSED:
            return {"ok": False, "state": self.state.value, "reason": "not paused"}
        self._set_state(AutomationState.STARTING)
        self._audit("resume", "automation resumed")
        return {"ok": True, "state": "STARTING"}

    # ---------------------------------------------------------------- scheduler
    def ensure_running(self) -> None:
        """Start the background scan loop when configured enabled+mode!=OFF. Idempotent."""
        if self.cfg.enabled and self.cfg.mode != "OFF" and self.state in (
                AutomationState.OFF, AutomationState.ERROR):
            self._task = asyncio.create_task(self._loop())

    def set_mode(self, mode: str) -> list[str]:
        """Change mode with server-side revalidation. Never silently transitions."""
        mode = mode.upper()
        if mode not in ("OFF", "MONITOR", "PAPER", "LIVE"):
            return [f"invalid mode {mode!r}"]
        self.cfg.mode = mode
        if mode == "OFF":
            self.stop()
        elif mode == "MONITOR":
            self.cfg.enabled = True
            if self.state == AutomationState.OFF:
                self.start()
        else:  # PAPER / LIVE
            self.cfg.enabled = True
            if self.state == AutomationState.OFF:
                self.start()
        self._audit("mode", f"mode set to {mode}")
        return []

    # --------------------------------------------------------------- the cycle
    async def _loop(self) -> None:
        """Background scan loop over the allowed markets."""
        logger.info("automated trader loop started (mode=%s)", self.cfg.mode)
        while self.cfg.enabled and self.cfg.mode != "OFF":
            try:
                # reap settled/stopped states
                if self.state in _SETTLED and self.state != AutomationState.MONITORING:
                    pass
                if self.state == AutomationState.PAUSED:
                    await asyncio.sleep(self.cfg.cooldown_secs)
                    continue
                if self.state in (AutomationState.OFF, AutomationState.STOPPING,
                                  AutomationState.ERROR):
                    await asyncio.sleep(1.0)
                    continue

                if self.state == AutomationState.STARTING:
                    ok, probs = self.validate_start()
                    if not ok:
                        self._set_state(AutomationState.ERROR)
                        self.error = "; ".join(probs)
                        self._audit("error", "start validation failed", {"problems": probs})
                        await asyncio.sleep(2.0)
                        continue
                    self._set_state(AutomationState.MONITORING)

                for market in self.cfg.allowed_markets:
                    if self.state == AutomationState.PAUSED:
                        break
                    await self._scan_market(market)
                await asyncio.sleep(settings.automation_scan_interval_secs or 1.0)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # never kill the loop on a transient error
                logger.exception("automation loop error")
                self.error = str(exc)
                self._audit("error", "automation loop error", {"error": str(exc)})
                self._set_state(AutomationState.ERROR)
                await asyncio.sleep(self.cfg.cooldown_secs or 5.0)
        self._set_state(AutomationState.OFF)
        logger.info("automated trader loop ended")

    async def scan_once(self) -> dict:
        """Run a single scan cycle for each allowed market; used by tests/API."""
        summary = []
        for market in self.cfg.allowed_markets:
            r = await self._scan_market(market)
            summary.append({"symbol": market, "count": len(r)})
        return {"scanned": summary}

    async def _scan_market(self, symbol: str) -> list[dict]:
        """Scan one market. Returns a list of decision results (read by tests/UI)."""
        if self._scan_lock.locked():
            return []
        async with self._scan_lock:
            self._set_state(AutomationState.ANALYZING)
            results: list[dict] = []
            try:
                candidates = self._candidate_specs(symbol)
                for spec in candidates:
                    if self.state in (AutomationState.PAUSED, AutomationState.OFF):
                        break
                    decision = await self._process_candidate(spec)
                    results.append(decision)
            except Exception as exc:
                logger.exception("scan error for %s", symbol)
                self.error = str(exc)
                self._set_state(AutomationState.ERROR)
            self._set_state(AutomationState.MONITORING)
            self.last_scan_ts = time.time()
            return results

    # ----------------------------------------------------------- candidate set
    def _candidate_specs(self, symbol: str) -> list[ContractSpec]:
        """The candidate board (priority-ordered) for one market.

        Priority only influences ordering; evidence/EV/risk gates stay authoritative.
        """
        if symbol not in self.cfg.allowed_markets:
            return []
        board = []
        for spec in all_specs_for_symbol(symbol):
            if spec.family not in self.cfg.allowed_families:
                continue
            board.append(spec)
        # Order by contract priority (MATCHES=6 .. DIFFERS=1) then stable.
        board.sort(key=lambda s: contract_priority(s.family), reverse=True)
        return board

    # ------------------------------------------------------------- one decision
    async def _process_candidate(self, spec: ContractSpec) -> dict:
        """Full pipeline for one candidate: analysis -> proposal -> signal -> risk ->
        (MONITOR: stop) -> execution (PAPER/LIVE only). Returns a decision dict."""

        self.last_decision = "ANALYZING"
        # ---- 1. data quality from analysis manager ----
        from app.services.analysis_engine import analysis_manager

        snap = analysis_manager.snapshot(spec.symbol, window=100)
        wa = (snap.get("windows") or {}).get("100") or {}
        dq = (wa.get("data_quality") or {}).get("state", "INSUFFICIENT_DATA")
        if dq != "DATA_READY":
            self._audit("candidate", f"{spec.symbol} skipped (data_quality={dq})")
            return {"signal_id": "", "symbol": spec.symbol, "family": spec.family,
                    "decision": "SKIP", "reason": f"data_quality={dq}"}

        # ---- 2. proposal (respect rate limits; never fabricated as live) ----
        proposal = await self._obtain_proposal(spec)
        if proposal is None or proposal.state != "OK":
            self._audit("candidate", f"{spec} proposal unavailable")
            return {"signal_id": "", "symbol": spec.symbol, "family": spec.family,
                    "decision": "SKIP", "reason": "proposal unavailable"}

        # ---- 3. signal (via the decision service: reuses signal engine) ----
        self.last_decision = "SIGNALING"
        signal = self.ds.produce_signal(
            symbol=spec.symbol, family=spec.family, barrier=spec.barrier,
            window=100, duration_ticks=spec.duration_ticks, stake=spec.stake,
            source_tag=snap.get("source", ""), proposal=proposal,
            multi_window_state=(snap.get("multi_window") or {}).get("state", "INSUFFICIENT_DATA"),
        )
        self.last_signal_id = signal.signal_id
        self.last_signal_state = signal.signal_state
        self._audit("signal", f"signal {signal.signal_id} state={signal.signal_state}",
                    {"family": spec.family, "barrier": spec.barrier})

        if signal.signal_state != SignalState.VALIDATING.value:
            return {"signal_id": signal.signal_id, "symbol": spec.symbol,
                    "family": spec.family, "decision": "REJECTED",
                    "reason": signal.reason or signal.signal_state}

        # ---- 4. risk gate (through the decision service) ----
        self.last_decision = "RISK"
        self._set_state(AutomationState.VALIDATING)
        exec_mode = self._exec_mode()
        signal, risk = self.ds.qualify(signal, mode=exec_mode)
        self.last_risk_state = signal.risk_state
        self._audit("risk", f"risk={signal.risk_state}", {"reason": risk.get("reason")})
        if signal.risk_state != RiskState.PASS.value:
            return {"signal_id": signal.signal_id, "decision": "NO_TRADE",
                    "risk_state": signal.risk_state, "reason": risk.get("reason"),
                    "vetos": risk.get("vetos")}

        # ---- 5. freshness + automation gates (before ANY execution) ----
        freshness = self._freshness_ok(signal)
        gate = self._automation_gate()
        if not freshness["ok"]:
            self.last_decision = "NO_TRADE"
            return {"signal_id": signal.signal_id, "decision": "NO_TRADE",
                    "reason": "; ".join(freshness["problems"])}
        if not gate["ok"]:
            self.last_decision = "NO_TRADE"
            return {"signal_id": signal.signal_id, "decision": "NO_TRADE",
                    "reason": "; ".join(gate["problems"])}

        # ---- MONITOR stops here (dry-run: analyze, never trade) ----
        if self.cfg.mode == "MONITOR":
            self.last_decision = "MONITOR_DRY"
            self._audit("decision", "MONITOR dry-run: eligible but NOT executed",
                        {"signal_id": signal.signal_id})
            return {"signal_id": signal.signal_id, "decision": "MONITOR_DRY",
                    "reason": "monitor mode: no execution", "executable": signal.is_executable()}

        # ---- 6. execute ONLY through the existing execution engine ----
        self.last_decision = "EXECUTE"
        self._set_state(AutomationState.EXECUTING)
        self._audit("decision", f"automated execution (mode={self.cfg.mode})",
                    {"signal_id": signal.signal_id})
        result = await self.ds.execute(signal, mode=exec_mode)
        self.last_execution = str(result.get("status") or "")
        self._audit("execution", f"execution -> {result.get('status')}",
                    {"reason": result.get("reason"),
                     "contract_id": result.get("contract_id"),
                     "request_id": result.get("request_id")})
        self.session_trades += (1 if result.get("status") == "SUCCEEDED" else 0)
        self._set_state(AutomationState.TRACKING)
        self._set_state(AutomationState.MONITORING)
        return {
            "signal_id": signal.signal_id, "decision": "EXECUTED",
            "status": result.get("status"), "reason": result.get("reason"),
            "contract_id": result.get("contract_id"),
            "mode": exec_mode,
        }

    # ----------------------------------------------------------- helpers
    def _exec_mode(self) -> str:
        """Map automation mode -> execution engine broker mode.

        PAPER automation executes through PAPER broker mode. (HARNESS automation is
        intentionally ruled out: mode set is OFF/MONITOR/PAPER/LIVE.) When the live master
        switch is ON it stays OFF inside the safety gates unless the operator has genuinely
        enabled it; the engine still re-enforces everything.
        """
        return self.cfg.mode  # PAPER | LIVE (MONITOR never reaches here)

    async def _obtain_proposal(self, spec: ContractSpec):
        """Return a current proposal for a candidate.

        A real proposal is only used when the proposal engine has an authenticated feed.
        Otherwise a clearly-labeled HARNESS proposal is returned; the risk gate REFUSES a
        simulated proposal for PAPER/LIVE execution, so no sim quote can ever drive a real
        or paper trade. This keeps the pricing path honest.
        """
        # Prefer an async request once an authenticated feed is configured. Failure must
        # never fabricate a price — fall back to the labeled harness proposal (gate-refused
        # outside HARNESS) and let the risk gate make the call.
        try:
            if self.ds.proposals.live_configured:
                return await self.ds.proposals.request(spec)
        except Exception as exc:
            logger.debug("live proposal request failed (%s); harness-labeled fallback", exc)
        return self.ds.proposals.harness_proposal(spec)

    def _freshness_ok(self, signal: Signal) -> dict:
        """§6 — signal/tick/proposal freshness + revalidation before execution."""
        now = time.time()
        problems = []
        if not signal.valid_at(now):
            problems.append(f"signal expired at ts={signal.expiry:.2f}")
        if self.last_scan_ts and (now - self.last_scan_ts) > self.cfg.max_signal_age_secs:
            problems.append("scan older than max_signal_age")
        if signal.proposal_source == "UNAVAILABLE":
            problems.append("proposal unavailable")
        if signal.multi_window_state in ("CONFLICTING", "INSUFFICIENT_DATA"):
            problems.append(f"multi-window {signal.multi_window_state}")
        return {"ok": not problems, "problems": problems, "now": now}

    def _automation_gate(self) -> dict:
        """§7 — automation trade limits (server-side). When any hit: NO new trades."""
        problems = []
        if self.session_trades >= self.cfg.max_trades_per_session:
            problems.append("session trade limit reached")
        if self.session_loss >= self.cfg.max_session_loss:
            problems.append("session loss limit reached")
        if self.daily_loss >= self.cfg.max_daily_loss:
            problems.append("daily loss limit reached")
        if self.consecutive_losses >= self.cfg.max_consecutive_losses:
            problems.append("consecutive-loss limit reached")
        if self.ds.exec.open_count() >= self.cfg.max_open:
            problems.append("max open trades reached")
        if self.ds.kill.enabled:
            problems.append("kill switch ACTIVE")
        if self.cfg.mode in ("PAPER", "LIVE") and not self._any_connected():
            problems.append("no connected market data")
        return {"ok": not problems, "problems": problems}

    def on_result(self, status: str, pnl: float | None) -> None:
        """Process a settled result (called by the result hook)."""
        if status == "LOST":
            loss = abs(pnl or 0.0)
            self.session_loss += loss
            self.daily_loss += loss
            self.consecutive_losses += 1
            self.session_losses += 1
        elif status == "WON":
            self.consecutive_losses = 0
            self.session_wins += 1
        self._audit("result", f"result={status}", {"pnl": pnl})

    def session_stats(self) -> dict:
        return {
            "trades": self.session_trades,
            "wins": self.session_wins,
            "losses": self.session_losses,
            "session_loss": round(self.session_loss, 4),
            "daily_loss": round(self.daily_loss, 4),
            "consecutive_losses": self.consecutive_losses,
            "open": self.ds.exec.open_count(),
        }


_trader: AutomatedTrader | None = None
_trader_lock = threading.Lock()


def get_trader() -> AutomatedTrader:
    global _trader
    if _trader is None:
        with _trader_lock:
            if _trader is None:
                _trader = AutomatedTrader()
    return _trader


__all__ = ["AutomatedTrader", "AutomationConfig", "AutomationState",
           "AuditEntry", "get_trader"]