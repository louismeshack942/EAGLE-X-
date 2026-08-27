"""Phase 6 — Automated Trader API.

The automated trader is a CLIENT of the Phase 4/5 pipeline. These endpoints only start,
stop, inspect and configure the orchestrator. LIVE automation remains server-side gated:
the trader can never enable `execution_live_enabled` itself, and the frontend can never
override it.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.services.automated_trader import (
    AutomationConfig,
    get_trader,
)

router = APIRouter(prefix="/api/automation", tags=["automation"])


@router.get("/status")
def status():
    t = get_trader()
    return {"ok": True, **t.status()}


@router.get("/config")
def config():
    t = get_trader()
    return {"ok": True, "config": t.get_config().as_dict(),
            "live_enabled": settings.execution_live_enabled}


@router.post("/config")
def set_config(payload: dict):
    """Update server-side automation config. Values are validated with conservative caps.

    `execution_live_enabled` is NEVER accepted here — it is a server-only master switch.
    """
    p = payload or {}
    if "execution_live_enabled" in p:
        raise HTTPException(status_code=400,
                            detail="execution_live_enabled cannot be changed via automation config")
    t = get_trader()
    base = t.get_config()
    # Build from current config, overlay only provided keys.
    merged = AutomationConfig(
        enabled=bool(p.get("enabled", base.enabled)),
        mode=str(p.get("mode", base.mode)).upper(),
        max_trades_per_session=int(p.get("max_trades_per_session", base.max_trades_per_session)),
        max_trades_per_day=int(p.get("max_trades_per_day", base.max_trades_per_day)),
        max_open=int(p.get("max_open", base.max_open)),
        max_daily_loss=float(p.get("max_daily_loss", base.max_daily_loss)),
        max_session_loss=float(p.get("max_session_loss", base.max_session_loss)),
        max_consecutive_losses=int(p.get("max_consecutive_losses", base.max_consecutive_losses)),
        cooldown_secs=float(p.get("cooldown_secs", base.cooldown_secs)),
        max_signal_age_secs=float(p.get("max_signal_age_secs", base.max_signal_age_secs)),
        min_signal_quality=float(p.get("min_signal_quality", base.min_signal_quality)),
        max_stake=float(p.get("max_stake", base.max_stake)),
        min_stake=float(p.get("min_stake", base.min_stake)),
        allowed_markets=[str(s).strip() for s in p.get("allowed_markets", base.allowed_markets)],
        allowed_families=[str(s).strip().upper()
                          for s in p.get("allowed_families", base.allowed_families)],
    )
    probs = t.set_config(merged)
    if probs:
        raise HTTPException(status_code=400, detail="; ".join(probs))
    return {"ok": True, "config": t.get_config().as_dict(),
            "live_enabled": settings.execution_live_enabled}


@router.post("/start")
def start(payload: dict | None = None):
    t = get_trader()
    return t.start()


@router.post("/stop")
def stop():
    t = get_trader()
    return t.stop()


@router.post("/pause")
def pause():
    t = get_trader()
    return t.pause()


@router.post("/resume")
def resume():
    t = get_trader()
    return t.resume()


@router.post("/set-mode")
def set_mode(payload: dict):
    mode = str((payload or {}).get("mode", "")).upper()
    if mode not in ("OFF", "MONITOR", "PAPER", "LIVE"):
        raise HTTPException(status_code=400, detail="mode must be OFF|MONITOR|PAPER|LIVE")
    t = get_trader()
    probs = t.set_mode(mode)
    if probs:
        raise HTTPException(status_code=400, detail="; ".join(probs))
    if mode in ("PAPER", "LIVE"):
        # Live still cannot start if the server master switch is off.
        ok, reasons = t.validate_start()
        return {"ok": ok, "mode": mode, "state": t.status()["state"],
                "problems": reasons if not ok else [],
                "note": ("LIVE automation requires execution_live_enabled (server-side) + "
                         "authentication + all gates." if mode == "LIVE" else
                         "PAPER automation enabled (paper only).")}
    return {"ok": True, "mode": mode, "state": t.status()["state"]}


@router.get("/decisions")
def decisions(limit: int = 50):
    t = get_trader()
    rows = [{"ts": e["ts"], "kind": e["kind"], "message": e["message"], "detail": e["detail"]}
            for e in t.audit_log(limit)]
    return {"ok": True, "count": len(rows), "decisions": rows}


@router.get("/state")
def state_detail():
    t = get_trader()
    s = t.status()
    s["session_stats"] = t.session_stats()
    return {"ok": True, **s}


@router.post("/scan")
async def scan(payload: dict | None = None):
    """Run a single manual scan cycle (used for validation/demo). Never executes in MONITOR."""
    t = get_trader()
    if t.state.value not in ("MONITORING", "STARTING", "READY", "TRACKING", "ANALYZING"):
        # Allow an explicit scan for validation even if automation is idle, but only in
        # a non-executing mode to keep it safe.
        if t.cfg.mode not in ("OFF", "MONITOR"):
            return {"ok": False, "reason": "automation not in a scannable state"}
    res = await t.scan_once()
    return {"ok": True, **res}