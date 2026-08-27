"""Deriv OAuth2 authorization-code + PKCE flow (server-side).

Legitimate mechanism per Deriv's public OAuth docs. EAGLE-X never handles a Deriv
password. If DERIV_OAUTH_* are not configured, endpoints report NOT CONFIGURED instead
of faking success.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import urllib.parse

import httpx

from app.config import settings


def generate_pkce_pair() -> tuple[str, str]:
    """Return (verifier, challenge)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorize_url(state: str, verifier: str) -> str:
    if not settings.oauth_configured:
        raise RuntimeError("Deriv OAuth is not configured (missing client id/secret).")
    params = {
        "response_type": "code",
        "client_id": settings.deriv_oauth_client_id,
        "redirect_uri": settings.deriv_oauth_redirect_uri,
        "scope": "trade read",
        "state": state,
        "code_challenge": verifier,
        "code_challenge_method": "S256",
    }
    return f"{settings.deriv_oauth_authorize_url}?{urllib.parse.urlencode(params)}"


async def exchange_code_for_token(
    code: str, verifier: str
) -> dict:
    """Exchange the authorization code for access/refresh tokens (server-side)."""
    if not settings.oauth_configured:
        raise RuntimeError("Deriv OAuth is not configured (missing client id/secret).")
    data = {
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": settings.deriv_oauth_redirect_uri,
        "client_id": settings.deriv_oauth_client_id,
        "client_secret": settings.deriv_oauth_client_secret,
        "code_verifier": verifier,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(settings.deriv_oauth_token_url, data=data)
        resp.raise_for_status()
        return resp.json()


async def fetch_account_info(token: str) -> dict:
    """Fetch authorized account loginid + currency via the authenticated REST API."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{settings.deriv_rest_base}/options/accounts",
            headers={"Authorization": f"Bearer {token}", "Deriv-App-ID": settings.deriv_oauth_client_id},
        )
        resp.raise_for_status()
        return resp.json()


__all__ = [
    "generate_pkce_pair",
    "build_authorize_url",
    "exchange_code_for_token",
    "fetch_account_info",
]