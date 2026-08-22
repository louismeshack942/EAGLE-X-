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

## Fluid play + CF discipline (Auto Trader)

`select_plays()` in `backend/app/services/auto_trader.py` feeds SS. Honest
rules, learned from the CF's losing streak:

- **EV, not confidence.** Each contract gets a real expected value
  (`ev = p_win * payout - 1`) in `market_master.py`. Confidence is now the
  observed-vs-fair edge, not a feel-good number.
- **DIFFERS only.** ODD/EVEN/OVER/UNDER are fair coin flips the house always
  wins long-term; MATCHES is a 10% lottery. Only DIFFERS (90% true base rate)
  carries a sustainable edge. The CF is benched off everything else.
- **Bayesian shrinkage.** `analytics_advanced.get_digit_analysis` adds an
  `estimate` per digit shrunk toward the 10% prior by a pseudo-count — small
  windows can no longer manufacture fake "100%" edges.
- **Scout the full board.** Market Master returns `contracts[:6]` for the UI
  and `all_contracts` for the CF, so edge plays aren't truncated away.
- **Positive-EV gate.** A contract must have `ev > 0` and a real edge
  (`MIN_EDGE_PCT`). On a fair market that means *no trades* — which is the
  correct, capital-preserving behaviour.
- **Benching.** `MAX_GAMES_WITHOUT_GOAL` consecutive losses → the manager
  benches the CF for `BENCH_GAMES` scans (log + Telegram alert), then he
  returns with a clean slate. Status exposes `win_rate` + `benched`.

Tests: `TestFluidPlay` in `backend/tests/test_intel_and_trading.py` (13 tests; 55 total).

## Club (communications hub)

`backend/app/services/club.py` — Team Manager briefing, Board/Sponsors
report, News Desk headlines, Fans chants, and market-trend Alerts, all
derived from live analytics. Routes: `/club`, `/club/manager`, `/club/board`,
`/club/news`, `/club/fans`, `/club/alerts`. Frontend panel:
`frontend/components/panels/ClubPanel.tsx` (tab-based, on the dashboard).
Tests: `test_club_endpoints` in `backend/tests/test_api.py`.

## Videos

`scripts/gen_videos.py` — Pillow slides + edge-tts narration (`JennyNeural`,
rate -5%) + ffmpeg into MP4. Never espeak. `/videos/*.mp4` served statically;
`videos.json` manifest drives the Video Hub UI list (elided titles, no
"THE CLUB" naming).

## Commands

- Frontend build: `cd frontend && npm run build` → `out/`
- Backend tests: `cd backend && ../backend/.venv/bin/python -m pytest tests/ -q` (51 tests)
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
