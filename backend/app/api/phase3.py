"""Phase 3 API — contract board, quick-analysis, and read-only scan.

Everything here is READ-ONLY: we price and recommend, never execute. Sources are
surfaced honestly (LIVE proposal vs HARNESS simulation) at every level so the UI and
consumers can never misread a simulated price as a real Deriv quote.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.services.analysis_engine import analysis_manager
from app.services.contracts import (
    DEFAULT_DURATION_TICKS,
    DEFAULT_STAKE,
    FAMILIES,
    SUPPORTED_FAMILIES,
)
from app.services.phase3_service import Phase3Service, READONLY_NOTE
from app.services.proposal_engine import ProposalService

router = APIRouter(prefix="/api", tags=["phase3"])


def _live_proposal_service() -> ProposalService:
    """Build the proposal service.

    Real Deriv proposals are requested over the authenticated WS only when OAuth is
    configured. Otherwise we stay HARNESS (simulated and clearly labeled) or UNAVAILABLE.
    """
    use_live = settings.oauth_configured
    return ProposalService(use_live=use_live)


_phase3 = Phase3Service(proposal_service=_live_proposal_service())


def _pick_window_snapshot(family: str, symbol: str, window: int) -> dict:
    snap = analysis_manager.snapshot(symbol, window=window)
    wins = snap.get("windows", {})
    key = str(window)
    wa = wins.get(key)
    if wa is None:
        wa = next(iter(wins.values()), {}) if wins else {}
    # Flatten the analysis window dict into a shape the recommender expects:
    #   n, counts, data_quality
    df = wa.get("digit_frequency", {}) or {}
    return {
        "n": wa.get("n", 0),
        "size": wa.get("size", window),
        "counts": df.get("counts", [0] * 10),
        "data_quality": wa.get("data_quality", {}),
    }


@router.get("/contracts")
def list_contracts():
    return {
        "families": [
            {
                "family": k,
                "contract_type": v.contract_type,
                "requires_barrier": v.requires_barrier,
                "requires_digit_focus": v.requires_digit_focus,
                "fair_win_rate": v.fair_win_rate,
                "description": v.description,
            }
            for k, v in FAMILIES.items()
        ],
        "max_duration_ticks": 10,
        "duration_unit": "t",
        "note": "Contract definitions (metadata). Live pricing via Deriv proposals.",
    }


@router.get("/contracts/{symbol}")
def contract_field_map(symbol: str, duration_ticks: int = DEFAULT_DURATION_TICKS):
    """The candidate field map for a symbol: families x barriers (metadata only)."""
    if not (1 <= duration_ticks <= 10):
        raise HTTPException(status_code=400, detail="duration_ticks must be 1..10")
    board = []
    for family in SUPPORTED_FAMILIES:
        fam = FAMILIES[family]
        if fam.requires_barrier:
            board.append(
                {
                    "family": family,
                    "contract_type": fam.contract_type,
                    "barriers": list(range(0, 10)),
                    "default_barrier": 4,
                }
            )
        else:
            board.append({"family": family, "contract_type": fam.contract_type, "barriers": None})
    return {
        "symbol": symbol,
        "duration_ticks": duration_ticks,
        "duration_unit": "t",
        "board": board,
        "readonly_note": READONLY_NOTE,
    }


@router.get("/quick-analysis")
async def quick_analysis(
    symbol: str,
    family: str,
    barrier: int | None = None,
    window: int = 100,
    duration_ticks: int = DEFAULT_DURATION_TICKS,
    stake: float = DEFAULT_STAKE,
):
    if family not in SUPPORTED_FAMILIES:
        raise HTTPException(status_code=400, detail=f"unknown family {family}")
    wa = _pick_window_snapshot(family, symbol, window)
    qa = await _phase3.analyze_contract(
        symbol, family, barrier,
        window_analysis=wa, duration_ticks=duration_ticks, strike=stake,
    )
    return qa.to_dict()


@router.get("/scan/{symbol}")
async def scan_symbol(symbol: str, window: int = 100):
    """Read-only scan of the full board for a symbol (families x barriers)."""
    by_family = {f: _pick_window_snapshot(f, symbol, window) for f in SUPPORTED_FAMILIES}
    return await _phase3.scan(symbol, window_analysis_by_family=by_family)


@router.get("/proposal-flow")
def proposal_flow():
    """Honest indicator of current pricing source."""
    return {
        "live_configured": settings.oauth_configured,
        "mode": "LIVE" if settings.oauth_configured else "HARNESS",
        "note": (
            "Pricing reflects real Deriv proposals when configured; otherwise simulated "
            "(HARNESS) pricing is clearly labeled and never implied to be real."
        ),
    }


__all__ = ["router"]