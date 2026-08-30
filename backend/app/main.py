"""EAGLE-X FastAPI application — Batch 1 (Phase 0 + Phase 1 foundation).

Single service: FastAPI backend + (optionally) serves the exported frontend build.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import analysis, auth, automation, cockpit, execution, phase3
from app.config import settings
from app.db.init_db import init_db

logging.basicConfig(level=logging.INFO)


def _bootstrap_env_token() -> None:
    """Boot-time live connect from env: DERIV_API_TOKEN (+ DERIV_PAT_APP_ID..

    Mirrors the production main-branch mechanic so a fresh deploy comes up on
    LIVE DATA (PAT session validated via REST + OTP) without any manual reconnect.

    Failures log loudly and never crash boot;the app shows an honest auth_required
    state until the operator fixes the env or connects via /auth/token. No silent
    demo/harness slide happens when a token is configured — market data simply
    stays unavailable until the feed can authenticate. Demo/harness only exists when
    there is no token at all..
    """
    if not settings.deriv_api_token.strip():
        return
    import asyncio

    from app.services.deriv_session import get_session

    async def _connect() -> None:
        session = get_session()
        if session.live_configured:
            return
        await session.connect(settings.deriv_api_token.strip(), settings.deriv_pat_app_id.strip() or None)

    try:
        asyncio.run(_connect())
    except Exception as exc:  # noqa: BLE001 — never crash boot on an invalid token
        logging.getLogger("eaglex.boot").warning("env PAT token connect failed at boot: %s", exc)


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    init_db()
    _bootstrap_env_token()

    app.include_router(auth.router)
    app.include_router(cockpit.router)
    app.include_router(analysis.router)
    app.include_router(phase3.router)
    app.include_router(execution.router)
    app.include_router(automation.router)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/info")
    def info():
        return {
            "name": settings.app_name,
            "env": settings.env,
            "oauth_configured": settings.oauth_configured,
            "phase": "4+5",
            "execution_mode": settings.execution_mode_default,
            "live_enabled": settings.execution_live_enabled,
        }

    # Serve the exported frontend (Next.js `output: export`) when built.
    # Routers above win for /api/* and /health; everything else falls through
    # to the static SPA so the whole product runs on one origin.
    frontend_dir = Path(settings.frontend_dir)
    if frontend_dir.is_dir():
        index = frontend_dir / "index.html"
        if index.is_file():
            app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

    return app


app = create_app()