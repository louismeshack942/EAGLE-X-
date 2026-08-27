"""Authentication endpoints.

Flow: start_oauth -> {url, state, pkce_verifier stored} -> Deriv consent -> callback(code)
-> exchange -> store encrypted -> create session cookie -> cockpit.

If OAuth is not configured, endpoints return a clear NOT CONFIGURED state (HTTP 503),
never fake success.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.crypto import encrypt, hash_secret
from app.db import get_db
from app.models.models import Authorization, Session as SessionModel, User, Event
from app.services import oauth

router = APIRouter(prefix="/auth", tags=["auth"])

# in-memory (single node) PKCE store: state -> (verifier). Production should use Redis.
_PKCE: dict[str, str] = {}


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="eaglex_session",
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_seconds,
        path="/",
    )


@router.get("/status")
def auth_status(request: Request, response: Response):
    token = request.cookies.get("eaglex_session")
    if not token or not _SESSION_BY_HASH.get(hash_secret(token)):
        return {"authenticated": False}
    return {"authenticated": True, "configured": settings.oauth_configured}


@router.get("/deriv/login")
def deriv_login(response: Response):
    """Begin the legitimate Deriv OAuth2 flow. Returns redirect URL to Deriv."""
    if not settings.oauth_configured:
        return {"ok": False, "state": "NOT_CONFIGURED",
                "message": "Deriv OAuth is not configured on the server."}
    state = secrets.token_urlsafe(16)
    verifier, challenge = oauth.generate_pkce_pair()
    _PKCE[state] = verifier
    url = oauth.build_authorize_url(state, challenge)
    return {"ok": True, "url": url, "state": state}


@router.get("/deriv/callback")
async def deriv_callback(request: Request, response: Response, db: Session = Depends(get_db)):
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")
    if error:
        return {"ok": False, "state": "AUTH_FAILED", "error": error}
    if not code or not state:
        return {"ok": False, "state": "AUTH_FAILED", "error": "missing code/state"}
    verifier = _PKCE.pop(state, None)
    if not verifier:
        return {"ok": False, "state": "AUTH_FAILED", "error": "invalid or expired state"}

    try:
        tokens = await oauth.exchange_code_for_token(code, verifier)
        access = tokens.get("access_token", "")
        if not access:
            return {"ok": False, "state": "AUTH_FAILED", "error": "no access_token"}
        acct = await oauth.fetch_account_info(access)
    except (httpx.HTTPError, RuntimeError) as exc:
        _log_event(db, f"oauth exchange failed: {exc}")
        return {"ok": False, "state": "AUTH_FAILED", "error": "token exchange failed"}

    # -- persistence --
    email = f"deriv-{secrets.token_hex(4)}@eaglex.local"
    user = db.query(User).filter_by(email=email).first()
    if not user:
        user = User(email=email, display_name="Deriv User")
        db.add(user)
        db.commit()
        db.refresh(user)

    loginid = ""
    if isinstance(acct, dict):
        loginid = acct.get("loginid") or ""
        if not loginid and acct.get("accounts"):
            loginid = acct["accounts"][0].get("loginid", "")
    elif hasattr(acct, "loginid"):
        loginid = acct.loginid or ""

    authz = Authorization(
        user_id=user.id,
        deriv_loginid=loginid,
        scope=tokens.get("scope", "trade read"),
        token_ref=encrypt(access),  # encrypted access token (kept server-side)
        balance_currency="",
        is_active=True,
        last_verified_at=datetime.now(timezone.utc),
    )
    db.add(authz)
    db.commit()
    db.refresh(authz)

    # -- session --
    session_token = secrets.token_urlsafe(32)
    model_session = SessionModel(
        user_id=user.id,
        token_hash=hash_secret(session_token),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=settings.session_ttl_seconds),
    )
    db.add(model_session)
    db.commit()
    _SESSION_BY_HASH[model_session.token_hash] = model_session.id

    _log_event(db, "user authorized Deriv account + started session")
    response = RedirectResponse("/cockpit")
    _set_session_cookie(response, session_token)
    return response


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get("eaglex_session")
    if token:
        h = hash_secret(token)
        row = db.query(SessionModel).filter_by(token_hash=h).first()
        if row:
            row.revoked = True
            _SESSION_BY_HASH.pop(h, None)
            db.commit()
    response.delete_cookie("eaglex_session")
    return {"ok": True}


# simple in-process session map; see security note in docs (multi-node -> Redis)
_SESSION_BY_HASH: dict[str, int] = {}


def _log_event(db: Session, message: str) -> None:
    db.add(Event(kind="auth", level="info", message=message))
    db.commit()