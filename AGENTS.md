# EAGLE-X — Agent Notes

## Architecture (single-service, since 2026-08-21 rebuild)

ONE service. The FastAPI backend serves the statically-exported Next.js
frontend from the same origin. Single port, single health check, no proxy.
This was a deliberate rebuild after repeated 502s caused by a two-service
deployment (proxy, cross-service URLs, renamed services, wrong health checks).

- `frontend/` — Next.js 14, `output: "export"` → static site in `frontend/out`.
  All pages are client components; API calls are plain relative fetches
  (same origin) via `lib/api.ts` (`apiGet/apiPost/apiPatch/apiDel/api/fmtUsd/API_BASE`).
- `backend/` — FastAPI. `app/main.py` holds all routes; a catch-all HTTP
  middleware (`serve_frontend`) converts 404 GETs into SPA pages/static files
  when `FRONTEND_DIR` is set. API routes resolve first (routes win over the
  middleware). `/api/*` 404s stay JSON.
- `Dockerfile` (repo root) — multi-stage: node builds `frontend/out`, poetry
  installs backend deps, python runtime runs uvicorn with
  `FRONTEND_DIR=/app/frontend_static`. Port comes from `$PORT` (default 8000).
- `render.yaml` — single service `eaglex`, frankfurt, free, healthCheckPath `/health`.

## Fluid play (Auto Trader)

`select_plays()` in `backend/app/services/auto_trader.py` feeds SS (trade
execution). Gate: STRONG signal + data quality >= 70 + confidence >= 60 +
supportive evidence. A second contract joins only if its confidence reaches
75% of the top's (`FLUID_PAIR_RATIO`). Max 2 simultaneous plays (`FLUID_MAX_PLAYS`).
The 10%-of-balance stake is recomputed from the CURRENT balance on every
trade and split evenly across the plays; if a split share would fall below
Deriv's 0.35 minimum, only the top play runs. Tests: `TestFluidPlay` in
`backend/tests/test_intel_and_trading.py`.

## Commands

- Frontend build: `cd frontend && npm run build` → `out/`
- Backend tests: `cd backend && ../backend/.venv/bin/python -m pytest tests/ -q` (50 tests)
- Run unified locally: `cd backend && FRONTEND_DIR=$PWD/../frontend/out ../backend/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 12000`

## Render traps (learned the hard way)

- Service name changes on blueprint re-apply (`eaglex-backend` → `eaglex-backend-excn`).
  Never hardcode onrender URLs between services — same origin removes the need.
- A wrong/missing `dockerfilePath` deploys garbage and 502s. Root `Dockerfile` only.
- Free tier spins down after ~15 min idle; first request cold-starts ~1 min.
- Deriv blocks some countries; frankfurt (de) works. `DERIV_API_TOKEN` must be
  set manually in the Render dashboard (never in the repo) for live mode.
- Render API key is available for service management (list/create/suspend/deploy).

## Repo

`louismeshack942/EAGLE-X-` (trailing dash). `eaglex` repo was empty; `eagle-ai`
is an unrelated older project — its Render services must stay suspended.
