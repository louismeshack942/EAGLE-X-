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

## Squad upgrades (world-class team)

Every position was raised to elite level; ratings are computed live and shown
in the Club panel's Squad tab (`/club/squad`):

- **GK (Risk)** — Kelly-criterion sizing (`kelly_fraction`, `kelly_stake` at
  quarter-Kelly capped at 10%), drawdown-scaled stakes (`drawdown_multiplier`),
  and a live form card (`risk_state`: posture FULL_ATTACK/BALANCED/CAUTIOUS/
  DEFEND). Kelly returns 0 on negative-EV — the GK refuses them.
- **CB (Intelligence)** — numeric conviction 0-100 alongside the decision.
- **RB (Tick Timer)** — jitter, p25/p75 arrival window, and a stability score.
- **DMF (Most Likely)** — binomial z-scores per digit; confidence and pick are
  driven by significance (|z|>=1.96), with `significant` + `z` fields.
- **RMF/LMF (Market Master)** — per contract `z` and `significant`; the CF
  gate requires significance. EV stays the ordering key.
- **AMF (Copilot)** — answers grounded in EV, z, Kelly and live squad status.
- **CF (Auto Trader)** — significance gate + Kelly stakes + form rating
  (`cf_rating`), GK posture surfaced in status.
- **GM (Club)** — `squad_ratings()`: 10 players rated 40-99 from live metrics;
  overall + tier (WORLD CLASS 85+, ELITE 75+, PROFESSIONAL 65+).

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

## Deriv connection (2026-08-22 fix: PAT tokens)

Deriv now issues **PAT tokens** (`pat_...`) on `developers.deriv.com`. The
older `api.deriv.com` redirects there today. The REST flow must be used to
connect them:

- POST `/auth/token` with `{token, app_id}` (backend `app/api/auth.py`):
  validate against `DERIV_REST_BASE + /options/accounts` (headers
  `Authorization: Bearer`, `Deriv-App-ID`) to find the account id, then
  `POST .../{acct}/otp` to mint an authenticated WS URL. All-or-nothing —
  on failure, the token is never stored.
- The vault stores `token + app_id + account_id + ws_url` (0600-file).
  `DerivTrader._url(token)` prefers `VAULT.get_ws_url()` (OTP URL, no
  authorize call needed); legacy tokens fall back to the classic
  `wss://ws.derivws.com/websockets/v3?app_id=...` endpoint with the old
  authorize call inside `place_trade`/`get_balance`.
- OAuth `/auth/deriv/login` uses the modern `client_id` + PKCE-style flow
  once `DERIV_APP_ID` (env) holds a registered app id; the legacy app_id
  1089 fallback keeps the older screen available. `/auth/deriv/callback`
  understands both `code` (asks for DERIV_CLIENT_SECRET when missing) and
  the old tokenN params.
- Frontend panel accepts an optional **App id** field; only needed for
  `pat_` tokens. Without one, legacy tokens still succeed.

`DERIV_REST_BASE` default is `https://api.derivws.com/trading/v1` in
`backend/app/config.py`; env override kept. Tests
`test_vault_stores_pat_fields` and `test_trader_url_prefers_pat_and_falls_back_legacy`
live in `tests/test_auth_and_live.py` (74 tests).

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

## Pro Trader layer (2026-08-25)

backend/app/services/pro_trader.py — additive statistical decision engine
(ChatGPT-derived spec). Reuses tick_queue; pure-math only (no scipy):
chi-square survival via regularized incomplete gamma, Benjamini-Hochberg FDR
over all digit x window tests, Wilson lower-bound confidence, Dirichlet-smoothed
contract probabilities, gap/streak/entropy/transition/autocorrelation features,
time-chunked edge-decay slope. Hard gates: sample>=100, FDR p<0.05,
Wilson LB > breakeven, EV >= 0.03, multi-window stability, data quality,
latency <=500ms, edge not decaying. Composite signal_score never overrides
gates. Default payouts are assumptions — live path must re-price via Deriv
proposal and re-run gates (payouts= param). Every response carries
RNG_NOTE: stats are descriptive, not predictive.

Endpoints: /pro-trader/scan (registered before /pro-trader/{symbol}),
/pro-trader/signal/{symbol}, /pro-trader/{symbol}.
Tests: tests/test_pro_trader.py (15 tests; 208 total).
