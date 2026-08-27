"""Phase 5 — Execution Engine: full trade lifecycle.

    EXECUTION-ELIGIBLE SIGNAL
      -> PRE-TRADE VALIDATION (revalidate everything)
      -> EXECUTION REQUEST
      -> BROKER PURCHASE (HARNESS | PAPER | LIVE)
      -> PURCHASE CONFIRMATION
      -> OPEN CONTRACT
      -> RESULT (WIN / LOSS / VOID / ERROR / UNKNOWN)
      -> LEDGER
      -> PERFORMANCE

Safety principles enforced here:
  - No trade without a signal that is EXECUTION_READY + ELIGIBLE + not expired + risk PASS.
  - NEVER treat request-sent as executed; only a broker CONFIRMATION marks EXECUTED.
  - Ambiguous outcome -> EXECUTION_UNCERTAIN -> reconcile. WHEN UNCERTAIN, DO NOT BUY AGAIN.
  - Idempotency: same idempotency_key never double-purchases.
  - No Martingale: stake is fixed per request; there is no code path that grows stake.
  - Live trade NEVER runs unless the master server-side switch + all gates are on.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.services.broker import (
    MODE_HARNESS,
    MODE_LIVE,
    ExecutionLock,
    ExecutionRequest,
    KillSwitch,
    PurchaseResult,
)
from app.services.signal_engine import ExecutionState, Signal, SignalState

logger = logging.getLogger("eaglex.execution")


@dataclass
class OpenContract:
    contract_id: str
    symbol: str
    family: str
    barrier: int | None
    prediction: str
    duration_ticks: int
    stake: float
    entry: float | None
    buy_price: float | None
    payout: float | None
    opened_ts: float
    expiry_ts: float
    execution_mode: str
    signal_id: str = ""
    status: str = "OPEN"             # OPEN | WON | LOST | VOID | ERROR
    entry_digit: int | None = None
    result_digit: int | None = None
    result_payout: float | None = None
    profit_loss: float | None = None

    def to_dict(self) -> dict:
        return {
            "contract_id": self.contract_id,
            "symbol": self.symbol,
            "family": self.family,
            "barrier": self.barrier,
            "prediction": self.prediction,
            "duration_ticks": self.duration_ticks,
            "stake": self.stake,
            "entry": self.entry,
            "buy_price": self.buy_price,
            "payout": self.payout,
            "opened_ts": self.opened_ts,
            "expiry_ts": self.expiry_ts,
            "execution_mode": self.execution_mode,
            "signal_id": self.signal_id,
            "status": self.status,
            "result_digit": self.result_digit,
            "result_payout": self.result_payout,
            "profit_loss": self.profit_loss,
        }


@dataclass
class LedgerEntry:
    trade_id: str
    signal_id: str
    execution_id: str
    contract_id: str
    mode: str
    symbol: str
    contract_type: str
    prediction: str
    stake: float
    buy_price: float | None
    payout: float | None
    profit_loss: float | None
    status: str            # OPEN | WON | LOST | VOID | ERROR | UNKNOWN
    created_ts: float = 0.0
    updated_ts: float = 0.0
    error: str = ""
    source: str = ""       # broker confirmation provenance
    timestamps: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "trade_id": self.trade_id,
            "signal_id": self.signal_id,
            "execution_id": self.execution_id,
            "contract_id": self.contract_id,
            "mode": self.mode,
            "symbol": self.symbol,
            "contract_type": self.contract_type,
            "prediction": self.prediction,
            "stake": self.stake,
            "buy_price": self.buy_price,
            "payout": self.payout,
            "profit_loss": self.profit_loss,
            "status": self.status,
            "created_ts": self.created_ts,
            "updated_ts": self.updated_ts,
            "error": self.error,
            "source": self.source,
            "timestamps": self.timestamps,
        }


def _trade_outcome(family: str, barrier: int | None, entry: int, next_digit: int) -> bool:
    """Determine whether `next_digit` WINS the digit contract opened at `entry` digit.

    Digit contracts settle on the next tick; entry digit is irrelevant to DETERMINING
    win except for family barrier strings. This is the honest, documented rule:
        MATCHES  next_digit == barrier
        DIFFERS  next_digit != barrier
        ODD      next_digit is odd
        EVEN     next_digit is even
        OVER     next_digit >  barrier
        UNDER    next_digit <  barrier
    """
    b = barrier if barrier is not None else -1
    if family == "MATCHES":
        return next_digit == b
    if family == "DIFFERS":
        return next_digit != b
    if family == "ODD":
        return next_digit in {1, 3, 5, 7, 9}
    if family == "EVEN":
        return next_digit in {0, 2, 4, 6, 8}
    if family == "OVER":
        return next_digit > b
    if family == "UNDER":
        return next_digit < b
    return False


class ExecutionEngine:
    def __init__(
        self,
        *,
        kill_switch: KillSwitch | None = None,
        lock: ExecutionLock | None = None,
        brokers: dict | None = None,
        spot_provider=None,
    ) -> None:
        self.kill = kill_switch or KillSwitch()
        self.lock = lock or ExecutionLock()
        self.brokers: dict[str, Any] = brokers or {}
        self.spot_provider = spot_provider
        self._ledger: dict[str, LedgerEntry] = {}          # trade_id -> entry
        self._by_idem: dict[str, str] = {}                 # idempotency_key -> contract_id
        self._by_signal: dict[str, str] = {}               # signal_id -> current contract_id
        self._open: dict[str, OpenContract] = {}           # contract_id -> open
        self._boot_ts = time.time()

    # ---- observable accessors ----------------------------------------------
    def ledger(self, mode: str = "") -> list[dict]:
        rows = sorted(self._ledger.values(), key=lambda e: e.created_ts)
        if mode:
            rows = [r for r in rows if r.mode == mode]
        return [r.to_dict() for r in rows]

    def open_contracts(self) -> list[dict]:
        return [c.to_dict() for c in self._open.values()]

    def open_count(self) -> int:
        return sum(1 for c in self._open.values() if c.status in ("OPEN", "UNCERTAIN"))

    def has_idempotency(self, key: str) -> bool:
        return key in self._by_idem

    # ---- the lifecycle -------------------------------------------------------
    async def execute(self, signal: Signal, *, mode: str = MODE_HARNESS) -> dict:
        """Run pre-trade validation + broker purchase, returning the outcome.

        Revalidates EVERYTHING. Returns a dict summarising the attempt so callers (API,
        scheduler) can report honest execution_state.
        """
        now = time.time()

        # (1) Duplicate purchase protection is checked FIRST: a retry of the same
        #     idempotency key must never create a second purchase, regardless of the
        #     signal's current state.
        idem = signal.signal_id  # deterministic per analysis+contract
        if idem in self._by_idem:
            return {"status": ExecutionState.BLOCKED.value,
                    "reason": "duplicate idempotency_key; only one purchase allowed.",
                    "existing_trade_id": self._by_idem[idem]}

        # (2) The signal must be explicitly execution-ready and eligible.
        if signal.signal_state != SignalState.EXECUTION_READY.value:
            return {"status": ExecutionState.FAILED.value,
                    "reason": f"signal_state={signal.signal_state}; not execution-ready."}
        if signal.execution_state != ExecutionState.ELIGIBLE.value:
            return {"status": ExecutionState.FAILED.value,
                    "reason": f"execution_state={signal.execution_state}; not eligible."}
        if signal.expiry > 0 and now > signal.expiry:
            signal.expire()
            return {"status": ExecutionState.BLOCKED.value, "reason": "signal expired."}
        if signal.risk_state != "PASS":
            return {"status": ExecutionState.BLOCKED.value,
                    "reason": f"risk_state={signal.risk_state}; gate not passed."}
        if mode == MODE_LIVE and not settings.execution_live_enabled:
            return {"status": ExecutionState.BLOCKED.value, "reason": "LIVE execution is disabled."}
        if mode == MODE_LIVE and self.kill.enabled:
            return {"status": ExecutionState.BLOCKED.value, "reason": "kill switch active."}

        # (3) acquire execution lock (prevents concurrent accidental purchases)
        if not self.lock.acquire():
            return {"status": ExecutionState.BLOCKED.value, "reason": "execution lock held."}
        try:
            # (4) normalize execution request and call the broker
            req = ExecutionRequest(
                signal_id=signal.signal_id,
                symbol=signal.symbol,
                contract_type=signal.contract_type,
                family=signal.contract_family,
                barrier=signal.barrier,
                prediction=signal.prediction,
                duration_ticks=signal.duration_ticks,
                stake=signal.stake,
                idempotency_key=idem,
                execution_mode=mode,
                proposal_id=signal.proposal_id,
                proposal_source=signal.proposal_source,
                ask_price=signal.ask_price,
            )
            broker: Any = self.brokers.get(mode)
            from app.services.broker import broker_for

            if broker is None:
                broker = broker_for(
                    mode,
                    live_enabled=settings.execution_live_enabled,
                    spot_provider=self.spot_provider,
                )
            result: PurchaseResult = await broker.buy(req)
        finally:
            self.lock.release()

        # confirmation handling
        if result.status in ("REJECTED", "ERROR"):
            return {"status": result.status, "reason": result.message,
                    "request_id": result.request_id}
        if result.status != "EXECUTED":
            # Ambiguous / timeout -> EXECUTION_UNCERTAIN -> reconcile (never re-buy).
            signal.execution_state = ExecutionState.UNCERTAIN.value
            signal.signal_state = SignalState.ERROR.value
            self._record_ledger(signal, req, result, "UNKNOWN")
            return {"status": ExecutionState.UNCERTAIN.value,
                    "reason": result.message or "execution confirmation ambiguous.",
                    "contract_id": result.contract_id}

        # (5) confirmation received -> EXECUTED -> OPEN
        signal.execution_state = ExecutionState.SUCCEEDED.value
        signal.signal_state = SignalState.OPEN.value
        self._open_contract(signal, req, result)
        self._record_ledger(signal, req, result, "OPEN")
        return {
            "status": ExecutionState.SUCCEEDED.value,
            "reason": result.message,
            "contract_id": result.contract_id,
        }

    def _open_contract(self, signal: Signal, req: ExecutionRequest, result: PurchaseResult) -> None:
        dur = max(1, req.duration_ticks)
        open_ts = result.confirmed_ts or time.time()
        contract = OpenContract(
            contract_id=result.contract_id,
            symbol=req.symbol,
            family=req.family,
            barrier=req.barrier,
            prediction=req.prediction,
            duration_ticks=dur,
            stake=req.stake,
            entry=result.entry,
            buy_price=result.buy_price,
            payout=result.payout,
            opened_ts=open_ts,
            expiry_ts=open_ts + dur * 1.0,   # tick duration ~ seconds in sim
            execution_mode=req.execution_mode,
            signal_id=req.signal_id,
            status="OPEN",
        )
        self._open[result.contract_id] = contract
        self._by_idem[req.idempotency_key] = result.contract_id
        self._by_signal[req.signal_id] = result.contract_id

    def _record_ledger(self, signal: Signal, req: ExecutionRequest, result: PurchaseResult, status: str) -> None:
        trade_id = f"tr-{result.contract_id}"
        entry = LedgerEntry(
            trade_id=trade_id,
            signal_id=req.signal_id,
            execution_id=req.request_id,
            contract_id=result.contract_id,
            mode=req.execution_mode,
            symbol=req.symbol,
            contract_type=req.contract_type,
            prediction=req.prediction,
            stake=req.stake,
            buy_price=result.buy_price,
            payout=result.payout,
            profit_loss=None,
            status=status,
            created_ts=time.time(),
            updated_ts=time.time(),
            error=result.message if status != "OPEN" else "",
            source="broker",
            timestamps={"opened": time.time()},
        )
        self._ledger[trade_id] = entry
        self._by_idem.setdefault(req.idempotency_key, result.contract_id)

    # ---- result resolution ---------------------------------------------------
    def resolve_result(self, contract_id: str, *, next_digit: int | None = None,
                       win: bool | None = None, err: str = "") -> dict:
        """Resolve an open contract. Accepts either an explicit `win` or a `next_digit`.

        Never infers a win/loss from missing data: if neither `win` nor `next_digit` is
        provided the contract stays UNKNOWN.
        """
        contract = self._open.get(contract_id)
        if contract is None:
            return {"status": "UNKNOWN", "reason": "unknown contract id."}
        if contract.status != "OPEN":
            return {"status": contract.status, "reason": f"already {contract.status}."}

        if next_digit is None and win is None:
            contract.status = "UNKNOWN"
            self._update_ledger_status(contract_id, "UNKNOWN", err or "no result data.")
            return {"status": "UNKNOWN", "reason": "missing result data (no inference)."}

        if next_digit is None:
            next_digit_resolved: int | None = None
        else:
            next_digit_resolved = int(next_digit)
        won = bool(win) if win is not None else _trade_outcome(
            contract.family, contract.barrier, int(contract.entry or -1),
            next_digit_resolved if next_digit_resolved is not None else -1,
        )
        payout = contract.payout or contract.stake
        profit = (payout - contract.stake) if won else -contract.stake
        contract.status = "WON" if won else "LOST"
        contract.result_digit = next_digit
        contract.result_payout = payout
        contract.profit_loss = round(profit, 4)
        contract.entry_digit = next_digit
        self._update_ledger_status(contract_id, contract.status, "", contract.profit_loss)
        return {
            "status": contract.status,
            "contract_id": contract_id,
            "result": contract.status,
            "payout": payout,
            "profit_loss": contract.profit_loss,
        }

    def _update_ledger_status(self, contract_id: str, status: str, err: str = "",
                              profit: float | None = None) -> None:
        for e in self._ledger.values():
            if e.contract_id == contract_id:
                e.status = status
                e.error = err or e.error
                if profit is not None:
                    e.profit_loss = profit
                e.updated_ts = time.time()
                e.timestamps["resolved"] = time.time()

    # ---- performance (Phase 5 §36) -----------------------------------------
    def performance(self, mode: str = "") -> dict:
        settled = [e for e in self._ledger.values()
                   if e.status in ("WON", "LOST", "VOID", "ERROR")]
        if mode:
            settled = [e for e in settled if e.mode == mode]
        wins = [e for e in settled if e.status == "WON"]
        losses = [e for e in settled if e.status == "LOST"]
        gross_profit = round(sum(e.profit_loss for e in wins if e.profit_loss and e.profit_loss > 0), 4) \
            if wins else 0.0
        gross_loss = round(sum(-e.profit_loss for e in losses if e.profit_loss and e.profit_loss < 0), 4) \
            if losses else 0.0
        net = round(sum((e.profit_loss or 0) for e in settled), 4)
        profit_factor = round(gross_profit / gross_loss, 4) if gross_loss else (None if gross_profit == 0 else "inf")
        # losing streak
        streak = 0
        max_streak = 0
        for e in sorted(settled, key=lambda x: x.created_ts):
            if e.status == "LOST":
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0
        by_market: dict[str, dict] = {}
        by_contract: dict[str, dict] = {}
        for e in settled:
            for bucket in (by_market.setdefault(e.symbol, {"trades": 0, "wins": 0, "net": 0.0}),
                           by_contract.setdefault(e.contract_type, {"trades": 0, "wins": 0, "net": 0.0})):
                bucket["trades"] += 1
                bucket["wins"] += 1 if e.status == "WON" else 0
                bucket["net"] = round(bucket["net"] + (e.profit_loss or 0), 4)
        return {
            "trades": len(settled),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(settled), 4) if settled else 0.0,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "net_profit": net,
            "profit_factor": profit_factor,
            "max_drawdown": round(self._max_drawdown(settled), 4),
            "losing_streak": max_streak,
            "by_market": by_market,
            "by_contract": by_contract,
            "mode": mode or "ALL",
        }

    @staticmethod
    def _max_drawdown(settled) -> float:
        running = 0.0
        peak = 0.0
        max_dd = 0.0
        for e in sorted(settled, key=lambda x: x.created_ts):
            running += (e.profit_loss or 0)
            peak = max(peak, running)
            max_dd = max(max_dd, peak - running)
        return max_dd

    # ---- reconciliation (Phase 5 §25) ---------------------------------------
    async def reconcile_uncertain(self, signal: Signal, *, contract_id: str = "") -> dict:
        """Recovery for an ambiguous purchase. NEVER blindly re-buys.

        If we cannot determine the broker accepted the request, we mark UNKNOWN and STOP.
        A later reconciliation pass (external) can inspect the ledger; the system does
        not auto-submit a duplicate.
        """
        if signal.execution_state == ExecutionState.UNCERTAIN.value:
            return {
                "status": "UNKNOWN",
                "reason": "Purchase outcome uncertain; reconciled to UNKNOWN. No re-buy.",
                "signal_id": signal.signal_id,
            }
        return {"status": "OPEN", "reason": "no uncertainty to reconcile."}


__all__ = ["ExecutionEngine", "LedgerEntry", "OpenContract"]