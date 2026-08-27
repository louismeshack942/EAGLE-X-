"""Phase 5 — execution API (HARNESS / PAPER / LIVE), lifecycle, ledger, safety.

LIVE is DISABLED BY DEFAULT (server-side `execution_live_enabled=False`). The kill
switch, execution lock, idempotency and risk gate are all enforced in the engine; these
endpoints never interpret frontend inputs as authority. Prices are read from real Deriv
proposals when configured, otherwise HARNESS (clearly labeled).

Security: there is no hidden "live" flag the frontend can set. `mode=LIVE` requests are
rejected unless the server-side master switch and all gates pass.
"""

from __future__ import annotations


from fastapi import APIRouter, HTTPException

from app.config import settings
from app.services.broker import MODE_HARNESS, MODE_LIVE, MODES
from app.services.decision_service import get_decision_service
from app.services.trade_persistence import list_signals, list_trades

router = APIRouter(prefix="/api", tags=["execution"])

_SERVICE_HINT = "LIVE execution is DISABLED on this server (server-side switch)."


@router.get("/exec/config")
def exec_config():
    """Read-only execution configuration. Deliberately exposes the live switch state."""
    return {
        "modes": list(MODES),
        "default_mode": settings.execution_mode_default,
        "live_enabled": settings.execution_live_enabled,
        "paper_enabled": settings.execution_paper_enabled,
        "live_stake_max": settings.live_stake_max,
        "risk_max_stake": settings.risk_max_stake,
        "risk_max_open": settings.execution_max_open,
        "daily_loss_limit": settings.risk_daily_loss_limit,
        "note": "Live purchase requires server-side live_enabled + risk PASS + kill switch off.",
    }


@router.post("/exec/mode")
def set_mode(payload: dict):
    """Set the EXPLICIT execution mode for the NEXT request. Returns live switch state."""
    mode = (payload or {}).get("mode", settings.execution_mode_default).upper()
    if mode not in MODES:
        raise HTTPException(status_code=400, detail=f"mode must be one of {list(MODES)}")
    return {
        "mode": mode,
        "live_enabled": settings.execution_live_enabled,
        "note": (
            "LIVE remains governed by the server; frontend can only select HARNESS/PAPER/LIVE "
            "request preference. Actual live execution still requires server-side enable."
            if mode == MODE_LIVE
            else "mode selected (read-only until execution request)."
        ),
    }


@router.post("/exec/killswitch")
def set_killswitch(payload: dict):
    """Server-side kill switch. `on` true blocks all new trades."""
    ds = get_decision_service()
    on = bool((payload or {}).get("on"))
    ds.set_kill(on)
    return {"kill_switch": ds.kill.enabled}


@router.get("/exec/killswitch")
def get_killswitch():
    return {"kill_switch": get_decision_service().kill.enabled}


@router.post("/exec/lock")
def set_lock(payload: dict):
    """Set/clear the global execution lock (prevents concurrent purchases)."""
    ds = get_decision_service()
    on = bool((payload or {}).get("on"))
    if on:
        ds.lock.acquire()
    else:
        ds.lock.release()
    return {"execution_lock": ds.lock.held}


@router.get("/exec/ledger")
def ledger(mode: str = ""):
    return {"trades": get_decision_service().exec.ledger(mode=mode)}


@router.get("/exec/open")
def open_contracts():
    return {"open": get_decision_service().exec.open_contracts(),
            "count": get_decision_service().exec.open_count()}


@router.get("/exec/performance")
def performance(mode: str = ""):
    return get_decision_service().exec.performance(mode=mode)


@router.get("/exec/history")
def history(limit: int = 50):
    return {"signals": list_signals(limit), "trades": list_trades(limit)}


@router.get("/exec/signals")
def signal_history(limit: int = 50):
    return {"signals": list_signals(limit)}


@router.get("/signal/{symbol}")
async def signal_for_symbol(symbol: str, family: str, window: int = 100,
                            barrier: int | None = None, duration_ticks: int = 5,
                            stake: float = 1.0, mode: str = MODE_HARNESS):
    """Produce (and risk-qualify) a signal for one contract — like a live decision card.

    This is read-only: it builds the signal + risk result but does NOT purchase.
    Execution only happens through POST /exec/execute.
    """
    if family.upper() not in ("MATCHES", "DIFFERS", "ODD", "EVEN", "OVER", "UNDER"):
        raise HTTPException(status_code=400, detail="unknown family")
    if mode.upper() not in MODES:
        raise HTTPException(status_code=400, detail="unknown mode")
    ds = get_decision_service()
    from app.services.analysis_engine import analysis_manager
    from app.services.contracts import build_spec

    spec = build_spec(symbol, family.upper(), barrier=barrier, duration_ticks=duration_ticks, stake=stake)

    # Deterministic pricing source: live if a live proposal flow is actually configured,
    # else a clearly-labeled HARNESS simulation. Never fabricate a live response.
    use_live = settings.oauth_configured and ds.proposals.live_configured
    if use_live:
        try:
            proposal = await ds.proposals.request(spec)
        except Exception:
            proposal = ds.proposals.harness_proposal(spec)  # honest fallback, labeled HARNESS
    else:
        proposal = ds.proposals.harness_proposal(spec)

    asnap = analysis_manager.snapshot(symbol, window=window)
    sig = ds.produce_signal(
        symbol=symbol, family=family.upper(), barrier=barrier, window=window,
        duration_ticks=duration_ticks, stake=stake,
        source_tag=asnap.get("source", ""),
        proposal=proposal,
        multi_window_state=asnap.get("multi_window", {}).get("state", "INSUFFICIENT_DATA"),
    )
    sig, risk = ds.qualify(sig, mode=mode.upper())
    return {
        "signal": sig.to_dict(),
        "risk": risk,
        "executable": sig.is_executable(),
        "live_enabled": settings.execution_live_enabled,
        "proposal_source": sig.proposal_source,
    }


@router.post("/exec/execute")
async def execute(payload: dict):
    """Execute a pre-qualified signal. Requires signal_id + explicit mode.

    If the signal isn't already EXECUTION_READY/ELIGIBLE (e.g. its risk gate was not run
    or it expired), this returns BLOCKED — the engine revalidates everything. PAPER/HARNESS
    are exercised; LIVE will be rejected by the master switch by default.
    """
    ds = get_decision_service()
    signal_id = (payload or {}).get("signal_id", "")
    mode = (payload or {}).get("mode", MODE_HARNESS).upper()
    if not signal_id:
        raise HTTPException(status_code=400, detail="signal_id required")
    if mode not in MODES:
        raise HTTPException(status_code=400, detail=f"mode must be one of {list(MODES)}")

    # Locate the signal object the caller references. We keep a small registry on the
    # decision service for the most recent produced signal so the engine can act on it.
    sig = ds.produced_signal(signal_id)
    if sig is None:
        raise HTTPException(status_code=404, detail=f"unknown signal {signal_id}")

    res = await ds.execute(sig, mode=mode)
    return {**res, "signal_id": signal_id, "mode": mode}


@router.post("/exec/resolve")
def resolve(payload: dict):
    """Resolve an open contract result (PAPER/HARNESS) from an explicit outcome."""
    ds = get_decision_service()
    contract_id = (payload or {}).get("contract_id", "")
    next_digit = (payload or {}).get("next_digit")
    win = (payload or {}).get("win")
    if not next_digit and win is None:
        raise HTTPException(status_code=400, detail="provide next_digit or win")
    return ds.resolve_contract(contract_id, next_digit=next_digit, win=win)


@router.get("/exec/probe")
def probe(mode: str = MODE_LIVE):
    """Honest indicator that a potential live purchase WOULD be blocked now (read-only)."""
    mode = mode.upper()
    if mode != MODE_LIVE:
        return {"can_purchase": True, "note": f"{mode} mode may be purchasable."}
    live_ok = settings.execution_live_enabled and not get_decision_service().kill.enabled
    return {
        "can_purchase": live_ok,
        "live_enabled": settings.execution_live_enabled,
        "kill_switch": get_decision_service().kill.enabled,
        "note": _SERVICE_HINT if not settings.execution_live_enabled else "live enabled",
    }


__all__ = ["router"]