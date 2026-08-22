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

import httpx
import websockets
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from app.config import get_settings
from app.services.token_vault import VAULT

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenBody(BaseModel):
    token: str
    app_id: Optional[str] = None   # required for pat_ tokens


async def _pat_validate(token: str, app_id: Optional[str]) -> dict:
    """Validate a modern PAT token via Deriv's REST trading API.

    Finds the primary trading account, then mints a one-time authenticated
    websocket URL from the OTP endpoint. Raises on any failure."""
    settings = get_settings()
    headers = {"Authorization": f"Bearer {token}"}
    if app_id:
        headers["Deriv-App-ID"] = str(app_id)
    base = settings.deriv_rest_base.rstrip("/")
    async with httpx.AsyncClient(timeout=10) as client:
        accounts_resp = await client.get(f"{base}/options/accounts", headers=headers)
        if accounts_resp.status_code == 401:
            raise ValueError("Deriv rejected this token")
        if accounts_resp.status_code != 200:
            raise ValueError(f"accounts lookup failed ({accounts_resp.status_code})")
        accounts = (accounts_resp.json().get("data") or {}).get("accounts", [])
        if not accounts:
            raise ValueError("no Deriv accounts visible to this token")
        accounts.sort(key=lambda a: a.get("account_id", ""))
        primary = accounts[0]
        account_id = primary.get("account_id")
        if not account_id:
            raise ValueError("accounts entry has no account_id")
        otp_resp = await client.post(f"{base}/options/accounts/{account_id}/otp", headers=headers)
        if otp_resp.status_code == 401:
            raise ValueError("Deriv rejected this token on OTP")
        if otp_resp.status_code != 200:
            raise ValueError(f"otp generation failed ({otp_resp.status_code})")
        ws_url = (otp_resp.json().get("data") or {}).get("url")
        if not ws_url:
            raise ValueError("no OTP URL returned by Deriv")
    return {
        "loginid": primary.get("loginid") or account_id,
        "currency": primary.get("currency"),
        "balance": primary.get("balance"),
        "account_id": account_id,
        "ws_url": ws_url,
        "app_id": app_id,
    }


async def _validate_token(token: str, app_id: Optional[str] = None) -> dict:
    """Validate a token. PAT tokens go through the REST flow; everything
    else uses the legacy websocket authorize call. Raises on failure."""
    if token.startswith("pat_"):
        return await _pat_validate(token, app_id)
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
    """Redirect to Deriv's OAuth page.

    With a registered app id (set DERIV_APP_ID in env) we use client_id
    against the modern auth endpoint. With the legacy id 1089 (default) the
    older authorize screen stays available until a real app is registered.
    """
    settings = get_settings()
    if settings.deriv_app_id != 1089:
        params = urlencode({
            "client_id": str(settings.deriv_app_id),
            "response_type": "code",
            "scope": "trade account_manage",
            "l": "EN",
        })
    else:
        params = urlencode({"app_id": settings.deriv_app_id, "l": "EN"})
    return RedirectResponse(f"{settings.deriv_oauth_url}?{params}")


@router.get("/deriv/callback", response_class=HTMLResponse)
async def deriv_callback(
    acct1: Optional[str] = None,
    token1: Optional[str] = None,
    cur1: Optional[str] = None,
    code: Optional[str] = None,
):
    """Deriv OAuth redirect target. Handles legacy tokenN params and the
    modern Authorization Code flow (requires DERIV_CLIENT_SECRET to be
    ready in env)."""
    settings = get_settings()
    dashboard = (settings.frontend_url or "/").rstrip("/") + "/dashboard" if settings.frontend_url else "https://frontend-ob4u.onrender.com/dashboard"

    if code and not token1:
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;background:#0d1117;color:#f85149;padding:2rem'>"
            "<h3>OAuth needs DERIV_APP_ID &amp; DERIV_CLIENT_SECRET</h3>"
            "<p>Deriv returned an authorization code, but code exchange "
            "requires your own registered app credentials. Register an OAuth "
            "app on developers.deriv.com, set DERIV_APP_ID and "
            "DERIV_CLIENT_SECRET in env, then retry. Otherwise paste the "
            "token manually (pat_ tokens are supported) via the form.</p></body>",
            status_code=400,
        )

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

    await VAULT.set(token1, loginid=info.get("loginid") or acct1, currency=info.get("currency") or cur1,
                    account_id=info.get("account_id"), ws_url=info.get("ws_url"), app_id=info.get("app_id"))
    if info.get("balance") is not None:
        await VAULT.set_balance(float(info["balance"]))

    return RedirectResponse(f"{dashboard}?deriv=connected")


@router.post("/token")
async def connect_token(body: TokenBody):
    """Manually connect by pasting a token over HTTPS. Validated before storage."""
    try:
        info = await _validate_token(body.token, body.app_id)
    except Exception as exc:
        return {"connected": False, "error": str(exc)}
    await VAULT.set(body.token, loginid=info.get("loginid"), currency=info.get("currency"),
                    account_id=info.get("account_id"), ws_url=info.get("ws_url"), app_id=info.get("app_id"))
    if info.get("balance") is not None:
        await VAULT.set_balance(float(info["balance"]))
    return {"connected": True, "loginid": info.get("loginid"), "currency": info.get("currency"), "balance": info.get("balance")}


@router.delete("/token")
async def disconnect_token():
    await VAULT.clear()
    return {"connected": False}
