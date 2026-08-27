"""Phase 3 orchestration: proposal pricing + read-only recommendations.

This module wires the recommendation engine to real (or clearly-simulated) Deriv
proposals and exposes a small, cached, rate-limited scanner. Everything here is
READ-ONLY: it prices and recommends; it never executes a trade.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from app.services.contracts import (
    DEFAULT_DURATION_TICKS,
    DEFAULT_STAKE,
    ContractSpec,
    all_specs_for_symbol,
)
from app.services.proposal_engine import NormalizedProposal, ProposalService
from app.services.recommender import NO_TRADE, QUALIFIED, WATCH, RecommendationEngine

logger = logging.getLogger("eaglex.phase3")

READONLY_NOTE = "READ-ONLY: priced and recommended, no trade was executed."


@dataclass
class QuickAnalysis:
    symbol: str
    timestamp: int
    data_source: str
    proposal_source: str
    family: str
    barrier: int | None
    prediction: str
    window_size: int
    recommendation: dict
    evidence: dict = field(default_factory=dict)
    readonly_note: str = READONLY_NOTE

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "data_source": self.data_source,
            "proposal_source": self.proposal_source,
            "family": self.family,
            "barrier": self.barrier,
            "prediction": self.prediction,
            "window_size": self.window_size,
            "recommendation": self.recommendation,
            "evidence": self.evidence,
            "readonly_note": self.readonly_note,
        }


class RateLimiter:
    """Simple token-bucket-ish rate limiter for external proposal requests."""

    def __init__(self, max_calls: int = 6, window_secs: float = 10.0) -> None:
        self.max = max_calls
        self.window = window_secs
        self._calls: list[float] = []

    def allow(self) -> bool:
        now = time.monotonic()
        self._calls = [c for c in self._calls if now - c < self.window]
        if len(self._calls) >= self.max:
            return False
        self._calls.append(now)
        return True


class Phase3Service:
    """Scans contract boards with pricing + recommendations (cached, rate-limited)."""

    def __init__(
        self,
        engine: RecommendationEngine | None = None,
        proposal_service: ProposalService | None = None,
    ) -> None:
        self.engine = engine or RecommendationEngine()
        self.proposals = proposal_service or ProposalService(use_live=False)
        self.rate = RateLimiter()
        self._cache: dict[str, tuple[float, QuickAnalysis]] = {}
        self._board_cache: dict[str, tuple[float, dict]] = {}
        self.cache_ttl = 3.0  # seconds

    # ---- quick analysis (single contract) ---------------------------------
    async def analyze_contract(
        self,
        symbol: str,
        family: str,
        barrier: int | None = None,
        *,
        window_analysis: dict,
        duration_ticks: int = DEFAULT_DURATION_TICKS,
        strike: float = DEFAULT_STAKE,
    ) -> QuickAnalysis:
        spec = ContractSpec(
            symbol=symbol, family=family, contract_type=_type_for(family),
            barrier=barrier, duration_ticks=duration_ticks, stake=strike,
            currency="USD",
        )
        cache_key = f"{symbol}:{family}:{barrier}:{duration_ticks}"
        now = time.time()
        if cache_key in self._cache:
            ts, cached = self._cache[cache_key]
            if now - ts < self.cache_ttl:
                logger.info("quick-analysis cache hit for %s", cache_key)
                return cached

        # Proposal: real when live flow; else clearly-labeled HARNESS for demo/dev.
        proposal = await self._pricing(spec)
        if proposal is None:
            # still return an honest recommendation (NO TRADE / INSUFFICIENT)
            proposal = _unavailable(spec)

        rec = self.engine.evaluate(
            spec,
            window_analysis=window_analysis,
            proposal=proposal,
            data_quality=window_analysis.get("data_quality"),
            multi_window_state="STABLE",
        )
        qa = QuickAnalysis(
            symbol=symbol, timestamp=int(time.time() * 1000),
            data_source=window_analysis.get("data_quality", {}).get("source", ""),
            proposal_source=proposal.source,
            family=family, barrier=spec.barrier, prediction=spec.prediction,
            window_size=window_analysis.get("size", 100),
            recommendation=rec.to_dict(),
            evidence=rec.evidence or {},
        )
        self._cache[cache_key] = (time.time(), qa)
        return qa

    async def _pricing(self, spec: ContractSpec, use_harness: bool | None = None) -> NormalizedProposal:
        """Return a real proposal when live is configured+rated, else HARNESS sim."""
        if self.rate.allow() and self.proposals.live_configured:
            try:
                return await self.proposals.request(spec)
            except Exception as exc:  # typed ProposalError or network
                logger.warning("live proposal failed; falling back to HARNESS: %s", exc)
                return self.proposals.harness_proposal(spec)
        # Simulation fallback is always clearly labeled HARNESS.
        return self.proposals.harness_proposal(spec)

    # ---- scanner (whole board) ----------------------------------------------
    async def scan(self, symbol: str, *, window_analysis_by_family: dict) -> dict:
        """Evaluate the full contract board (all families × barriers) read-only."""
        specs = all_specs_for_symbol(symbol, barriers=(0, 1, 2, 3, 4, 5, 6, 7, 8, 9))
        recommendations: list[dict] = []
        for spec in specs:
            wa = window_analysis_by_family.get(spec.family) or {}
            proposal = (
                _unavailable(spec)
                if not self.proposals.live_configured
                else self.proposals.harness_proposal(spec)
            )
            rec = self.engine.evaluate(
                spec,
                window_analysis=wa,
                proposal=proposal,
                data_quality=wa.get("data_quality"),
                multi_window_state="STABLE",
            )
            recommendations.append(rec.to_dict())
        await asyncio.sleep(0)
        top = [
            r for r in recommendations
            if r["state"] in (QUALIFIED, WATCH) and r.get("ev", 0) is not None
        ]
        top.sort(key=lambda r: -(r.get("ev") or 0))
        return {
            "symbol": symbol,
            "timestamp": int(time.time() * 1000),
            "recommendations": recommendations,
            "qualified": [r for r in recommendations if r["state"] == QUALIFIED],
            "watch": [r for r in recommendations if r["state"] == WATCH],
            "no_trade": [r for r in recommendations if r["state"] == NO_TRADE],
            "top_candidates": top[:8],
            "readonly_note": READONLY_NOTE,
        }


def _type_for(family: str) -> str:
    from app.services.contracts import FAMILIES

    return FAMILIES[family].contract_type


def _unavailable(spec: ContractSpec) -> NormalizedProposal:
    return NormalizedProposal(
        source="UNAVAILABLE", state="PROPOSAL_UNAVAILABLE", symbol=spec.symbol,
        contract_type=spec.contract_type, barrier=spec.barrier,
        duration_ticks=spec.duration_ticks, currency=spec.currency, stake=float(spec.stake),
        message="Pricing unavailable (no live Deriv proposal flow).",
    )


__all__ = ["Phase3Service", "QuickAnalysis", "RateLimiter", "READONLY_NOTE"]