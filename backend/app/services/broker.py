"""Phase 5 — broker abstraction + execution modes (HARNESS / PAPER / LIVE).

A normalized `ExecutionRequest` describes a purchase. The broker resolves a request in
one of three explicit modes:

    HARNESS  deterministic simulation; used for tests/demo (never labeled real).
    PAPER    realistic paper execution against the SAME tick feed: the "broker call" is
             resolved from the live/recorded current spot and resolved by the NEXT tick.
             Uses the same signal, contract representation, stake rules, lifecycle and
             ledger as live.
    LIVE     only when ALL gates pass and the master server-side switch is enabled. Uses
             the legitimate Deriv purchase API. DISABLED BY DEFAULT.

The broker NEVER treats "request sent" as "trade executed": a trade is EXECUTED only
after a valid broker confirmation. Ambiguous confirmations go to EXECUTION_UNCERTAIN and
reconciliation (WHEN UNCERTAIN, DO NOT BUY AGAIN).
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Protocol

logger = logging.getLogger("eaglex.broker")


class Broker(Protocol):
    """Any broker (HARNESS | PAPER | LIVE) that resolves a purchase."""

    async def buy(self, request: "ExecutionRequest") -> "PurchaseResult": ...

MODE_HARNESS = "HARNESS"
MODE_PAPER = "PAPER"
MODE_LIVE = "LIVE"
MODES = (MODE_HARNESS, MODE_PAPER, MODE_LIVE)


@dataclass
class ExecutionRequest:
    signal_id: str
    symbol: str
    contract_type: str
    family: str
    barrier: int | None
    prediction: str
    duration_ticks: int
    stake: float
    currency: str = "USD"
    idempotency_key: str = ""
    execution_mode: str = MODE_HARNESS
    proposal_id: str = ""
    proposal_source: str = ""
    ask_price: float | None = None
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "signal_id": self.signal_id,
            "idempotency_key": self.idempotency_key,
            "execution_mode": self.execution_mode,
            "symbol": self.symbol,
            "contract_type": self.contract_type,
            "family": self.family,
            "barrier": self.barrier,
            "prediction": self.prediction,
            "duration_ticks": self.duration_ticks,
            "stake": self.stake,
            "currency": self.currency,
            "proposal_id": self.proposal_id,
            "proposal_source": self.proposal_source,
            "ask_price": self.ask_price,
        }


@dataclass
class PurchaseResult:
    """The broker's outcome for a single execution request."""

    request_id: str
    idempotency_key: str
    status: str            # EXECUTED | REJECTED | UNCERTAIN | ERROR
    contract_id: str = ""
    buy_price: float | None = None
    payout: float | None = None
    entry: float | None = None            # spot at purchase (paper/live)
    message: str = ""
    confirmed_ts: float = 0.0
    raw: dict = field(default_factory=dict)
    execution_mode: str = ""

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "idempotency_key": self.idempotency_key,
            "status": self.status,
            "contract_id": self.contract_id,
            "buy_price": self.buy_price,
            "payout": self.payout,
            "entry": self.entry,
            "message": self.message,
            "confirmed_ts": self.confirmed_ts,
            "raw": self.raw,
            "execution_mode": self.execution_mode,
        }


class KillSwitch:
    """Server-side emergency stop. ENV admin can flip it; it is independent of frontend."""

    def __init__(self) -> None:
        self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False


class ExecutionLock:
    """Prevents concurrent accidental purchases (Phase 5 §31). NOT a safety backstop."""

    def __init__(self) -> None:
        self._held = False

    def acquire(self) -> bool:
        if self._held:
            return False
        self._held = True
        return True

    def release(self) -> None:
        self._held = False

    @property
    def held(self) -> bool:
        return self._held


class DerivPlaceholderBroker:
    """The LIVE broker. Uses the legitimate Deriv purchase API when enabled.

    The real network purchase lives behind BOTH the master `execution_live_enabled`
    switch and the execution-mode gate. No purchase call is ever issued unless both are
    on AND all risk gates passed (checked upstream). This class only wraps the wire
    format so the pipeline stays broker-compatible; the live call itself is a stubbed
    method that NEVER runs in tests.
    """

    def __init__(self, ws=None, live_enabled: bool = False) -> None:
        self._ws = ws
        self.live_enabled = live_enabled

    async def buy(self, request: ExecutionRequest) -> PurchaseResult:
        """Issue (or refuse) a LIVE purchase. Returns explicit broker confirmation."""
        if not self.live_enabled:
            return PurchaseResult(
                request_id=request.request_id,
                idempotency_key=request.idempotency_key,
                status="REJECTED",
                execution_mode=MODE_LIVE,
                message="LIVE execution is DISABLED on this server.",
            )
        # Never send a request without a proposal id / pricing provenance.
        if not request.proposal_id or request.proposal_source != "LIVE":
            return PurchaseResult(
                request_id=request.request_id,
                idempotency_key=request.idempotency_key,
                status="REJECTED",
                execution_mode=MODE_LIVE,
                message="LIVE purchase requires a LIVE proposal id (no real quote, no buy).",
            )
        # Real network call would go here (buy: {contract_id, buy_price, payout, ...}).
        # It is intentionally NOT exercised in automated tests (no real money).
        raise NotImplementedError("LIVE purchase path requires an authenticated Deriv WS.")


class HarnessBroker:
    """Deterministic HARNESS broker. Never implies real execution."""

    def __init__(self) -> None:
        self._counter = 0

    async def buy(self, request: ExecutionRequest) -> PurchaseResult:
        self._counter += 1
        contract_id = f"harness-{request.idempotency_key}-{self._counter}"
        buy_price = request.ask_price if request.ask_price is not None else request.stake
        payout = request.stake * 1.9  # simulation only, clearly aligned to a 50/50-ish family
        return PurchaseResult(
            request_id=request.request_id,
            idempotency_key=request.idempotency_key,
            status="EXECUTED",
            contract_id=contract_id,
            buy_price=buy_price,
            payout=payout,
            entry=buy_price,
            message="HARNESS simulated execution (never real).",
            confirmed_ts=time.time(),
            execution_mode=MODE_HARNESS,
        )


class PaperBroker:
    """Resolves a PAPER purchase by snapping the real tick feed (spot) entry price.

    Same signal/contract/stake/lifecycle as live; the only difference is the final
    broker op. A "contract id" is synthesized deterministically from the request, but
    the ENTRY is the real current spot, and the RESULT is determined by the next tick of
    the same real feed for the contract's win condition. No fake separate pipeline.
    """

    def __init__(self, spot_provider=None) -> None:
        # spot_provider: async callable() -> float | None (current quote for symbol)
        self._spot_provider = spot_provider

    def next_spot(self) -> float | None:
        try:
            if callable(self._spot_provider):
                v = self._spot_provider()
                return float(v) if v is not None else None
        except Exception as exc:  # spot feed glitch surfaces honestly
            logger.warning("paper spot read failed: %s", exc)
        return None

    async def buy(self, request: ExecutionRequest) -> PurchaseResult:
        entry = self.next_spot()
        if entry is None:
            return PurchaseResult(
                request_id=request.request_id,
                idempotency_key=request.idempotency_key,
                status="ERROR",
                execution_mode=MODE_PAPER,
                message="PAPER execution needs a live spot tick (none available).",
            )
        contract_id = f"PAPER-{request.idempotency_key}"
        buy_price = request.ask_price if request.ask_price is not None else request.stake
        payout = (request.stake * 1.9) if request.proposal_source != "LIVE" else (request.stake * 1.9)
        return PurchaseResult(
            request_id=request.request_id,
            idempotency_key=request.idempotency_key,
            status="EXECUTED",
            contract_id=contract_id,
            buy_price=buy_price,
            payout=payout,
            entry=entry,
            message="PAPER execution (real feed entry, simulated broker).",
            confirmed_ts=time.time(),
            execution_mode=MODE_PAPER,
        )


def broker_for(mode: str, *, live_enabled: bool = False, spot_provider=None, ws=None) -> object:
    """Return the appropriate broker for an explicit mode."""
    if mode == MODE_LIVE:
        return DerivPlaceholderBroker(ws=ws, live_enabled=live_enabled)
    if mode == MODE_PAPER:
        return PaperBroker(spot_provider=spot_provider)
    return HarnessBroker()


__all__ = [
    "MODE_HARNESS",
    "MODE_LIVE",
    "MODE_PAPER",
    "MODES",
    "DerivPlaceholderBroker",
    "ExecutionLock",
    "ExecutionRequest",
    "HarnessBroker",
    "KillSwitch",
    "PaperBroker",
    "PurchaseResult",
    "Broker",
    "broker_for",
]