"""EAGLE-X FastAPI application — Batch 1 (Phase 0 + Phase 1 foundation).

Single service: FastAPI backend + (optionally) serves the exported frontend build.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import analysis, auth, cockpit, phase3
from app.config import settings
from app.db.init_db import init_db

logging.basicConfig(level=logging.INFO)


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

    app.include_router(auth.router)
    app.include_router(cockpit.router)
    app.include_router(analysis.router)
    app.include_router(phase3.router)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/info")
    def info():
        return {
            "name": settings.app_name,
            "env": settings.env,
            "oauth_configured": settings.oauth_configured,
            "phase": "2+3",
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