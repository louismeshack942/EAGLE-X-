"""Deriv proposal (pricing) engine — READ-ONLY.

Requests a price quote for a digit contract via the Deriv WebSocket `proposal` API and
normalizes the real response. Real pricing is used when a live authenticated Deriv
connection is available; otherwise EAGLE-X surfaces a clearly-labeled HARNESS (no
fabricated live price) or a RECORDED fixture.

Official proposal request (Read API / public docs):
    {proposal:1, amount, contract_type, currency, symbol, duration, duration_unit, barrier, subscribe}
Response:
    {proposal:{id, ask_price, payout, spot, ...}, msg_type:"proposal"}

Basic math:
    profit_net = payout - stake   (payout is the gross return incl. stake)
    payout_pct = (payout / stake - 1) * 100
    breakeven_win_rate = stake / payout     (the win rate needed to break even)

A real proposal is authoritative and must never be replaced by an EAGLE-X estimate.
When unavailable we say so (PROPOSAL_UNAVAILABLE) — we never invent a price.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from app.services.contracts import ContractSpec

logger = logging.getLogger("eaglex.proposal")

SOURCE_LIVE = "LIVE"
SOURCE_HARNESS = "HARNESS"
SOURCE_RECORDED = "RECORDED"
SOURCE_UNAVAILABLE = "UNAVAILABLE"


class ProposalError(Exception):
    """A typed, user-visible proposal failure."""

    def __init__(self, state: str, message: str) -> None:
        super().__init__(message)
        self.state = state
        self.message = message


@dataclass
class NormalizedProposal:
    source: str = SOURCE_UNAVAILABLE
    state: str = "PROPOSAL_UNAVAILABLE"
    message: str = ""
    proposal_id: str = ""
    symbol: str = ""
    contract_type: str = ""
    barrier: int | None = None
    duration_ticks: int = 0
    currency: str = ""
    stake: float = 0.0
    ask_price: float | None = None
    payout: float | None = None
    profit_net: float | None = None
    payout_pct: float | None = None
    breakeven_win_rate: float | None = None
    spot: float | None = None
    expires_at: float | None = None
    quote_timestamp: float | None = None
    request: dict | None = None

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "state": self.state,
            "message": self.message,
            "proposal_id": self.proposal_id,
            "symbol": self.symbol,
            "contract_type": self.contract_type,
            "barrier": self.barrier,
            "duration_ticks": self.duration_ticks,
            "currency": self.currency,
            "stake": self.stake,
            "ask_price": self.ask_price,
            "payout": self.payout,
            "profit_net": self.profit_net,
            "payout_pct": self.payout_pct,
            "breakeven_win_rate": self.breakeven_win_rate,
            "spot": self.spot,
            "expires_at": self.expires_at,
            "quote_timestamp": self.quote_timestamp,
            "request": self.request,
        }


def normalize_deriv_proposal(payload: dict, spec: ContractSpec, *, source: str) -> NormalizedProposal:
    """Build a NormalizedProposal from a real Deriv `proposal` response payload."""
    p = payload.get("proposal") or {}
    ask = _to_float(p.get("ask_price"))
    payout = _to_float(p.get("payout"))
    profit = None
    payout_pct = None
    breakeven = None
    if ask is not None and payout is not None:
        profit = round(payout - ask, 4)
        if ask > 0:
            payout_pct = round((payout / ask - 1) * 100.0, 3)
            if payout > 0:
                breakeven = round(ask / payout, 5)
    spot = _to_float(p.get("spot"))
    return NormalizedProposal(
        source=source,
        state="OK",
        message="",
        proposal_id=str(p.get("id") or ""),
        symbol=spec.symbol,
        contract_type=spec.contract_type,
        barrier=spec.barrier,
        duration_ticks=spec.duration_ticks,
        currency=spec.currency,
        stake=float(spec.stake),
        ask_price=ask,
        payout=payout,
        profit_net=profit,
        payout_pct=payout_pct,
        breakeven_win_rate=breakeven,
        spot=spot,
        expires_at=_to_float(p.get("date_expiry")),
        quote_timestamp=time.time(),
        request=_request_payload(spec),
    )


def _request_payload(spec: ContractSpec) -> dict:
    req = {
        "proposal": 1,
        "amount": float(spec.stake),
        "contract_type": spec.contract_type,
        "currency": spec.currency,
        "symbol": spec.symbol,
        "duration": spec.duration_ticks,
        "duration_unit": spec.duration_unit,
        "basis": spec.basis,
    }
    if spec.barrier is not None:
        req["barrier"] = str(spec.barrier)
    return req


def _to_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


class ProposalService:
    """Fetches real Deriv proposals over the authenticated WS when possible.

    The service is read-only: it only requests a quote; it never buys.
    To avoid request storms, callers must respect `delay_between_secs`.
    """

    def __init__(self, *, ws=None, balance_provider=None, use_live: bool = False,
                 delay_between_secs: float = 1.0) -> None:
        self._ws = ws
        self._balance = balance_provider
        self.use_live = use_live
        self.delay = delay_between_secs
        self._last_request_at: float | None = None

    @property
    def live_configured(self) -> bool:
        return self.use_live and self._ws is not None

    async def request(self, spec: ContractSpec) -> NormalizedProposal:
        """Request (and normalize) a proposal for one contract.

        Real flow requires an authenticated WS. When not available, returns a
        PROPOSAL_UNAVAILABLE state (never a fake price).
        """
        if not self.live_configured:
            return NormalizedProposal(
                source=SOURCE_UNAVAILABLE,
                state="PROPOSAL_UNAVAILABLE",
                message="No authenticated Deriv connection; pricing unavailable.",
                symbol=spec.symbol,
                contract_type=spec.contract_type,
                barrier=spec.barrier,
                duration_ticks=spec.duration_ticks,
                currency=spec.currency,
                stake=float(spec.stake),
                request=_request_payload(spec),
            )

        # Simple rate control (non-blocking minimum gap).
        if self._last_request_at is not None:
            wait = self.delay - (time.time() - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
        self._last_request_at = time.time()

        req = _request_payload(spec)
        req["req_id"] = 9000 + hash((spec.symbol, spec.contract_type, spec.barrier)) % 1000
        try:
            await self._ws.send(json.dumps(req))
            deadline = time.time() + 10
            while time.time() < deadline:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=timeout_choice())
                msg = json.loads(raw)
                if msg.get("req_id") != req["req_id"] and not msg.get("proposal"):
                    continue
                if "error" in msg:
                    state = _error_state(msg["error"].get("code") or msg["error"].get("message", ""))
                    raise ProposalError(state, f"Proposal rejected: {msg['error'].get('message')}")
                if msg.get("msg_type") == "proposal":
                    return normalize_deriv_proposal(msg, spec, source=SOURCE_LIVE)
                if msg.get("proposal") and msg.get("proposal", {}).get("id"):
                    return normalize_deriv_proposal(msg, spec, source=SOURCE_LIVE)
            raise ProposalError("TIMEOUT", "Proposal request timed out.")
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError as exc:
            raise ProposalError("TIMEOUT", "Proposal request timed out.") from exc
        except ProposalError:
            raise
        except Exception as exc:  # ws-level failures surface honestly
            raise ProposalError("CONNECTION_LOST", f"Proposal request failed: {exc}") from exc

    # ---- harness / recorded fixtures (clearly labeled, never masquerade) ------
    def harness_proposal(self, spec: ContractSpec) -> NormalizedProposal:
        """A fully-labeled simulation proposal for harness mode (HARNESS source).

        Uses a fixed simulated payout schedule so tests/dev are deterministic and the
        UI can demo the pricing panel without ever claiming a real Deriv price.
        """
        stake = float(spec.stake)
        payout = stake * _harness_payout_multiplier(spec)
        ask = stake  # no spread in the simulation
        return NormalizedProposal(
            source=SOURCE_HARNESS,
            state="OK",
            message="SIMULATED price (HARNESS). Not a real Deriv quote.",
            proposal_id=f"harness-{spec.symbol}-{spec.contract_type}-{spec.barrier}",
            symbol=spec.symbol,
            contract_type=spec.contract_type,
            barrier=spec.barrier,
            duration_ticks=spec.duration_ticks,
            currency=spec.currency,
            stake=stake,
            ask_price=ask,
            payout=payout,
            profit_net=round(payout - ask, 4),
            payout_pct=round((payout / ask - 1) * 100.0, 3) if ask else None,
            breakeven_win_rate=round(ask / payout, 5) if payout else None,
            spot=1000.0,
            quote_timestamp=time.time(),
            request=_request_payload(spec),
        )


def _harness_payout_multiplier(spec: ContractSpec) -> float:
    """Deterministic simulated gross-payout multiplier per family (HARNESS only)."""
    if spec.family == "MATCHES":
        return 6.5
    if spec.family == "DIFFERS":
        return spec.duration_ticks / 6.0 + 1.05  # ~1.15 at 5 ticks
    if spec.family in ("ODD", "EVEN"):
        return 1.9
    barrier = spec.barrier or 0
    if spec.family == "OVER":
        return max(1.05, 1.0 + (barrier + 1) * 0.05)  # ~1.25 at barrier 4
    if spec.family == "UNDER":
        return max(1.05, 1.0 + (9 - barrier + 1) * 0.05)
    return 1.0


def _error_state(code: str) -> str:
    u = code.upper() if code else ""
    if "AUTH" in u or "UNAUTHOR" in u or "TOKEN" in u:
        return "AUTHORIZATION_REQUIRED"
    if "RATE" in u:
        return "RATE_LIMIT"
    if "BARRIER" in u or "PREDICTION" in u:
        return "INVALID_BARRIER"
    if "DURATION" in u:
        return "INVALID_DURATION"
    if "CONTRACT" in u:
        return "INVALID_CONTRACT"
    if "SYMBOL" in u or "MARKET" in u or "AVAILABLE" in u:
        return "MARKET_UNAVAILABLE"
    return "API_ERROR"


def timeout_choice() -> float:
    return 10.0


__all__ = [
    "SOURCE_HARNESS",
    "SOURCE_LIVE",
    "SOURCE_RECORDED",
    "SOURCE_UNAVAILABLE",
    "NormalizedProposal",
    "ProposalError",
    "ProposalService",
    "normalize_deriv_proposal",
]