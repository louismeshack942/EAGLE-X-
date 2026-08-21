"""Deriv account connection endpoints.

Two ways the account owner can connect their personal Deriv account — both
keep the token out of chat and off other people's screens:

1. OAuth: GET /auth/deriv/login redirects the owner to Deriv's own login
   page. Deriv redirects back to GET /auth/deriv/callback with account
   tokens in the query string; the backend authorizes, stores the token in
   the vault and redirects to the dashboard.

2. Manual: POST /auth/token with {"token": "..."} over HTTPS. The token is
   validated against Deriv (authorize call) before being stored.

DELETE /auth/token disconnects. GET /auth/status reports masked metadata
(loginid, currency, balance) — never the token itself.
"""
import asyncio
import json
from typing import Optional
from urllib.parse import urlencode

import websockets
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from app.config import get_settings
from app.services.token_vault import VAULT

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenBody(BaseModel):
    token: str


async def _validate_token(token: str) -> dict:
    """Authorize against Deriv and return account metadata. Raises on failure."""
    settings = get_settings()
    url = f"{settings.deriv_ws_url}?app_id={settings.deriv_app_id}&l=EN"
    async with websockets.connect(url, open_timeout=10) as ws:
        await ws.send(json.dumps({"authorize": token}))
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
    if "error" in msg:
        raise ValueError(msg["error"].get("message", "Deriv rejected the token"))
    auth = msg.get("authorize") or {}
    return {
        "loginid": auth.get("loginid"),
        "currency": auth.get("currency"),
        "balance": auth.get("balance"),
    }


@router.get("/status")
async def auth_status():
    return await VAULT.status()


@router.get("/deriv/login")
def deriv_login():
    settings = get_settings()
    params = urlencode({"app_id": settings.deriv_app_id, "l": "EN"})
    return RedirectResponse(f"{settings.deriv_oauth_url}?{params}")


@router.get("/deriv/callback", response_class=HTMLResponse)
async def deriv_callback(
    acct1: Optional[str] = None,
    token1: Optional[str] = None,
    cur1: Optional[str] = None,
):
    """Deriv OAuth redirect target. Deriv appends acctN/tokenN/curN params."""
    settings = get_settings()
    dashboard = (settings.frontend_url or "/").rstrip("/") + "/dashboard" if settings.frontend_url else "https://frontend-ob4u.onrender.com/dashboard"

    if not token1:
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;background:#0d1117;color:#c9d1d9;padding:2rem'>"
            "<h3>No account selected</h3><p>Deriv did not return an account token. "
            "Please pick an account on the Deriv login page.</p></body></html>",
            status_code=400,
        )
    try:
        info = await _validate_token(token1)
    except Exception as exc:
        return HTMLResponse(
            f"<html><body style='font-family:sans-serif;background:#0d1117;color:#f85149;padding:2rem'>"
            f"<h3>Connection failed</h3><p>{exc}</p></body></html>",
            status_code=400,
        )

    await VAULT.set(token1, loginid=info.get("loginid") or acct1, currency=info.get("currency") or cur1)
    if info.get("balance") is not None:
        await VAULT.set_balance(float(info["balance"]))

    return RedirectResponse(f"{dashboard}?deriv=connected")


@router.post("/token")
async def connect_token(body: TokenBody):
    """Manually connect by pasting a token over HTTPS. Validated before storage."""
    try:
        info = await _validate_token(body.token)
    except Exception as exc:
        return {"connected": False, "error": str(exc)}
    await VAULT.set(body.token, loginid=info.get("loginid"), currency=info.get("currency"))
    if info.get("balance") is not None:
        await VAULT.set_balance(float(info["balance"]))
    return {"connected": True, "loginid": info.get("loginid"), "currency": info.get("currency"), "balance": info.get("balance")}


@router.delete("/token")
async def disconnect_token():
    await VAULT.clear()
    return {"connected": False}
