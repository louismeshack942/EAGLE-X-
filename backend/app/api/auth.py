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
import base64
import hashlib
import json
import secrets
import time
from typing import Optional
from urllib.parse import urlencode

import httpx
import websockets
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from app.config import get_settings
from app.services.token_vault import VAULT

router = APIRouter(prefix="/auth", tags=["auth"])

# state -> {verifier, redirect_uri, expires} for in-flight OAuth logins.
# Single-process server; entries expire after 10 minutes.
_PKCE_STATES: dict = {}


def _pkce_pair() -> tuple:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def _oauth_token_url(settings) -> str:
    """auth.deriv.com/oauth2/auth -> auth.deriv.com/oauth2/token"""
    return settings.deriv_oauth_url.rstrip("/").rsplit("/", 1)[0] + "/token"


class TokenBody(BaseModel):
    token: str
    app_id: Optional[str] = None   # required for pat_ tokens


_RETRYABLE = {429, 500, 502, 503, 504}


async def _request(client: httpx.AsyncClient, method: str, url: str, headers: dict,
                   attempts: int = 4) -> httpx.Response:
    """HTTP call resilient to flaky API/network behaviour: retries with
    exponential backoff on transport errors and retryable status codes."""
    last_error: Optional[Exception] = None
    for i in range(attempts):
        try:
            resp = await client.request(method, url, headers=headers)
            if resp.status_code in _RETRYABLE and i < attempts - 1:
                await asyncio.sleep(0.5 * (2 ** i))
                continue
            return resp
        except httpx.HTTPError as exc:
            last_error = exc
            if i < attempts - 1:
                await asyncio.sleep(0.5 * (2 ** i))
    raise ValueError(f"Deriv API unreachable after {attempts} attempts: {last_error}")


async def _pat_validate(token: str, app_id: Optional[str]) -> dict:
    """Validate a modern PAT token via Deriv's REST trading API.

    Finds the primary trading account, then mints a one-time authenticated
    websocket URL from the OTP endpoint. Tries with and without the
    Deriv-App-ID header so a wrong/missing app id doesn't block an otherwise
    valid token. Raises on any failure."""
    settings = get_settings()
    base = settings.deriv_rest_base.rstrip("/")
    header_variants = []
    if app_id:
        header_variants.append({"Authorization": f"Bearer {token}", "Deriv-App-ID": str(app_id)})
    header_variants.append({"Authorization": f"Bearer {token}"})

    async with httpx.AsyncClient(timeout=20) as client:
        accounts = None
        used_headers = header_variants[0]
        rejected = False
        for headers in header_variants:
            resp = await _request(client, "GET", f"{base}/options/accounts", headers)
            if resp.status_code == 200:
                accounts = (resp.json().get("data") or {}).get("accounts", [])
                used_headers = headers
                break
            if resp.status_code in (401, 403):
                rejected = True
                continue
            raise ValueError(f"accounts lookup failed ({resp.status_code})")
        if accounts is None:
            if rejected and not app_id:
                raise ValueError("Deriv rejected this token (401) — pat_ tokens usually need your registered app id too")
            raise ValueError("Deriv rejected this token")
        if not accounts:
            raise ValueError("no Deriv accounts visible to this token")
        accounts.sort(key=lambda a: a.get("account_id", ""))
        primary = accounts[0]
        account_id = primary.get("account_id")
        if not account_id:
            raise ValueError("accounts entry has no account_id")
        otp_resp = await _request(client, "POST", f"{base}/options/accounts/{account_id}/otp", used_headers)
        if otp_resp.status_code in (401, 403):
            raise ValueError("Deriv rejected this token on OTP — enable the trading scope for your token")
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


async def _ws_validate(token: str, app_id: Optional[int] = None) -> dict:
    """Legacy websocket authorize flow. Raises on failure."""
    settings = get_settings()
    ws_app_id = app_id or settings.deriv_app_id
    url = f"{settings.deriv_ws_url}?app_id={ws_app_id}&l=EN"
    async with websockets.connect(url, open_timeout=15) as ws:
        await ws.send(json.dumps({"authorize": token}))
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
    if "error" in msg:
        raise ValueError(msg["error"].get("message", "Deriv rejected the token"))
    auth = msg.get("authorize") or {}
    return {
        "loginid": auth.get("loginid"),
        "currency": auth.get("currency"),
        "balance": auth.get("balance"),
    }


async def _validate_token(token: str, app_id: Optional[str] = None) -> dict:
    """Validate a token. PAT tokens go through the REST flow first; if that
    fails we fall back to the legacy websocket authorize before giving up.
    Everything else uses the websocket flow directly. Raises on failure."""
    if token.startswith("pat_"):
        rest_error: Optional[Exception] = None
        try:
            return await _pat_validate(token, app_id)
        except Exception as exc:
            rest_error = exc
        numeric_app_id = int(app_id) if app_id and app_id.isdigit() else None
        try:
            return await _ws_validate(token, numeric_app_id)
        except Exception:
            raise ValueError(f"REST flow failed: {rest_error}") from rest_error
    return await _ws_validate(token)


@router.get("/status")
async def auth_status():
    return await VAULT.status()


@router.get("/deriv/login", response_class=HTMLResponse)
def deriv_login(request: Request):
    """Start Deriv OAuth login.

    Deriv retired the shared app_id=1089 authorize screen — with the default
    id there is nothing to redirect to, so we show setup instructions instead
    of sending the user to a dead page. With a registered OAuth app id
    (DERIV_APP_ID in env) we run the real Authorization Code + PKCE flow.
    """
    settings = get_settings()
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", request.url.netloc)
    callback_url = f"{proto}://{host}/auth/deriv/callback"

    if settings.deriv_app_id == 1089:
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;background:#0d1117;color:#c9d1d9;padding:2rem;max-width:640px;margin:auto'>"
            "<h3 style='color:#f0b72f'>One-time setup needed for the login button</h3>"
            "<p>Deriv retired the shared login screen this button used. Two options:</p>"
            "<p><b>Option A — fastest:</b> close this tab, click <b>PASTE TOKEN</b> on the "
            "dashboard and paste a <b>pat_</b> token + your app id from "
            "<a style='color:#58a6ff' href='https://developers.deriv.com' target='_blank'>developers.deriv.com</a>. "
            "It connects instantly.</p>"
            "<p><b>Option B — make this button work forever:</b> on developers.deriv.com register a free "
            "<b>OAuth app</b> with this exact redirect URL:<br>"
            f"<code style='color:#3fb950'>{callback_url}</code><br>"
            "then set <code>DERIV_APP_ID</code> to that app's id in the server environment and restart.</p>"
            "</body></html>",
            status_code=200,
        )

    state = secrets.token_urlsafe(24)
    verifier, challenge = _pkce_pair()
    _PKCE_STATES[state] = {
        "verifier": verifier,
        "redirect_uri": callback_url,
        "expires": time.time() + 600,
    }
    params = urlencode({
        "response_type": "code",
        "client_id": str(settings.deriv_app_id),
        "redirect_uri": callback_url,
        "scope": "trade account_manage",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "l": "EN",
    })
    return RedirectResponse(f"{settings.deriv_oauth_url}?{params}")


async def _exchange_code(code: str, state: Optional[str]) -> dict:
    """Swap the authorization code for an access token (PKCE), then validate
    it against the REST API and mint the websocket OTP URL."""
    settings = get_settings()
    entry = _PKCE_STATES.pop(state or "", None)
    if not entry or entry["expires"] < time.time():
        raise ValueError("login session expired or unknown — start the login again")
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            _oauth_token_url(settings),
            data={
                "grant_type": "authorization_code",
                "client_id": str(settings.deriv_app_id),
                "code": code,
                "code_verifier": entry["verifier"],
                "redirect_uri": entry["redirect_uri"],
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code != 200:
        raise ValueError(f"code exchange failed ({resp.status_code})")
    access_token = resp.json().get("access_token")
    if not access_token:
        raise ValueError("no access token returned by Deriv")
    info = await _pat_validate(access_token, str(settings.deriv_app_id))
    info["token"] = access_token
    return info


@router.get("/deriv/callback", response_class=HTMLResponse)
async def deriv_callback(
    acct1: Optional[str] = None,
    token1: Optional[str] = None,
    cur1: Optional[str] = None,
    code: Optional[str] = None,
    state: Optional[str] = None,
):
    """Deriv OAuth redirect target. Handles legacy tokenN params and the
    modern Authorization Code + PKCE flow."""
    settings = get_settings()
    dashboard = (settings.frontend_url or "").rstrip("/") + "/dashboard" if settings.frontend_url else "/dashboard"

    if code and not token1:
        try:
            info = await _exchange_code(code, state)
        except Exception as exc:
            return HTMLResponse(
                f"<html><body style='font-family:sans-serif;background:#0d1117;color:#f85149;padding:2rem'>"
                f"<h3>Login failed</h3><p>{exc}</p></body></html>",
                status_code=400,
            )
        await VAULT.set(info.get("token") or code, loginid=info.get("loginid"), currency=info.get("currency"),
                        account_id=info.get("account_id"), ws_url=info.get("ws_url"), app_id=info.get("app_id"))
        if info.get("balance") is not None:
            await VAULT.set_balance(float(info["balance"]))
        return RedirectResponse(f"{dashboard}?deriv=connected")

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
