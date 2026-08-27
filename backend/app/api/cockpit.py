"""Cockpit endpoints: markets, connection state, data status, settings.

Phase 1 serves navigation + honest connection/data state. It does not fabricate analysis
or trades.
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, WebSocket
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.events import SUBJECT_STATUS, SUBJECT_TICKS, event_bus
from app.db import get_db
from app.models.models import Market, Tick
from app.services.data_bus import DataBus, make_provider

router = APIRouter(tags=["cockpit"])

# active bus per symbol
_buses: dict[str, DataBus] = {}


@router.get("/api/markets")
def list_markets(db: Session = Depends(get_db)):
    rows = db.query(Market).all()
    return {"markets": [{"symbol": m.symbol, "name": m.display_name, "category": m.category,
                         "active": m.is_active} for m in rows]}


@router.get("/api/status")
def system_status(db: Session = Depends(get_db)):
    buses = [
        {"symbol": s, "state": bus.provider.state, "latest": bus.latest.get(s)}
        for s, bus in list(_buses.items())
    ]
    source = "harness" if settings.use_unauth_public_data and not settings.deriv_oauth_client_id else "deriv_live"
    return {
        "status": "ok",
        "server_time": time.time(),
        "connection": buses,
        "data_source": source,
        "oauth_configured": settings.oauth_configured,
        "note": "MOCK/SIMULATION labels are surfaced honestly; no fake 'live/real' claims.",
    }


@router.post("/api/connect")
async def connect_market(payload: dict, db: Session = Depends(get_db)):
    symbol = (payload or {}).get("symbol", "")
    mode = (payload or {}).get("mode", "")  # "live" | "harness"
    market = db.query(Market).filter_by(symbol=symbol).first()
    if not market:
        return JSONResponse({"state": "MARKET_UNAVAILABLE", "error": "unknown symbol"}, status_code=404)

    if mode == "live" and not settings.oauth_configured:
        return JSONResponse(
            {"state": "AUTHORIZATION_REQUIRED",
             "error": "Live data requires a configured Deriv OAuth app."},
            status_code=503,
        )

    force = mode == "harness"
    bus = _buses.get(symbol)
    if bus is None:
        provider = make_provider(use_harness=force, app_id=settings.deriv_oauth_client_id)
        if provider is None:
            return JSONResponse({"state": "UNAVAILABLE", "error": "no provider available"}, status_code=503)
        bus = DataBus(provider)
        _buses[symbol] = bus
    bus.start(symbol)
    # Let connect() reach at least CONNECTING/CONNECTED before reporting state.
    await asyncio.sleep(0.05)
    return {"ok": True, "symbol": symbol, "state": bus.provider.state}


@router.get("/api/ticks/{symbol}")
def recent_ticks(symbol: str, limit: int = 100, db: Session = Depends(get_db)):
    rows = (
        db.query(Tick)
        .filter_by(symbol=symbol)
        .order_by(Tick.epoch_ms.desc())
        .limit(limit)
        .all()
    )
    return {"symbol": symbol, "ticks": [
        {"epoch_ms": t.epoch_ms, "quote": t.quote, "digit": t.last_digit, "provider": t.provider}
        for t in reversed(rows)
    ]}


@router.websocket("/ws/ticks")
async def ws_ticks(websocket: WebSocket):
    await websocket.accept()
    tick_q = await event_bus.subscribe(SUBJECT_TICKS)
    status_q = await event_bus.subscribe(SUBJECT_STATUS)
    try:
        while True:
            # prefer ticks; also forward status transitions
            for q in (tick_q, status_q):
                if not q.empty():
                    payload = q.get_nowait()
                    item = {"type": "tick"} if q is tick_q else {"type": "status"}
                    await websocket.send_json({**item, "data": payload})
                    break
            else:
                await asyncio.sleep(0.05)
    except Exception:
        pass
    finally:
        event_bus.unsubscribe(SUBJECT_TICKS, tick_q)
        event_bus.unsubscribe(SUBJECT_STATUS, status_q)