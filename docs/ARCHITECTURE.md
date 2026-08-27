# EAGLE-X — Architecture (Batch 1)

Single-service architecture: a FastAPI backend serves both the JSON/WS API and the
statically-exported Next.js frontend from one origin. No proxy, no cross-service URLs.

## Repo layout

```
.
├── .env.example            # all env vars + comments (no secrets)
├── .gitignore
├── backend/
│   ├── app/
│   │   ├── main.py         # FastAPI app; mounts exported frontend when built
│   │   ├── config.py       # pydantic-settings (env-driven)
│   │   ├── db/init_db.py   # create_all + seed markets
│   │   ├── models/models.py# SQLAlchemy models (User, Authorization, Market, Tick, Events)
│   │   ├── core/           # ticks (normalization+digit), events bus, status, crypto
│   │   ├── services/       # connector interface, harness (clearly labeled), deriv_client, data_bus, oauth
│   │   └── api/            # auth.py (OAuth) + cockpit.py (markets, connect, ticks, WS)
│   ├── tests/              # pytest suite (backend)
│   ├── pyproject.toml
│   └── requirements.txt
├── frontend/
│   ├── app/                # Next.js App Router (client components)
│   ├── components/         # LiveChart (TradingView lightweight-charts)
│   └── lib/api.ts          # typed API helpers (relative, same-origin)
└── docs/                   # Phase 0 forensic spec + protrader research
```

## Data flow

1. **Provider** (`MarketDataProvider`): `harness` (local simulator, clearly tagged) or
   `deriv_live` (async Deriv WS client). Both are async generators of `NormalizedTick`.
2. **DataBus** (`services/data_bus.py`): connects the provider, forwards ticks on the
   in-process event bus, and persists each tick to the DB (non-fatal on failure).
3. **API / WS** (`api/cockpit.py`): `/api/connect` starts a bus; `/api/ticks/{symbol}`
   reads recent rows; `/ws/ticks` streams live ticks + connection `status` events.
4. **Frontend** subscribes to `/ws/ticks` and renders the chart/digit grid, tagging the
   data source honestly (`harness` vs `deriv_live`).

## Data-source honesty

- Ticks carry a `provider` field (`harness` | `deriv_live`). A harness tick is NEVER
  labeled live.
- `/api/status` reports `data_source` derived from config (`harness` unless Deriv OAuth
  is configured) plus an explicit `note`.
- When OAuth is unconfigured, live connections are refused with `AUTHORIZATION_REQUIRED`
  (503) — no silent simulation disguised as real.

## Auth (legitimate only)

- Deriv **OAuth2 Authorization Code + PKCE** (`services/oauth.py`). The backend mints
  the authorize URL with a `code_challenge` and swaps the returned `code` (+verifier)
  at the token endpoint via `httpx` (HTTPS). Tokens are encrypted at rest with a
  per-deployment key (`core/crypto.py`).
- `OAuthService.is_configured` gates the `/auth/deriv/login` flow; the flow is inert
  (`NOT_CONFIGURED`) until env values are present, so there is never a fake login.
- No passwords are stored or used anywhere. Session signing uses an HMAC cookie backed
  by `SECRET_KEY`.

## Analysis pipeline (Phase 2)

1. **Tick windows** (`services/window_engine.py`): `WindowManager` keeps ring windows per
   symbol at `(25, 50, 100, 250, 500, 1000)` ticks.
2. **Data quality** (`core/data_quality.py`): honest per-window state
   (`DATA_READY / INSUFFICIENT_DATA / STALE / DISCONNECTED / INVALID`).
3. **Statistical analysis** (`services/analytics.py`): pure-Python digit frequency/ranks/
   z-scores, parity, OVER/UNDER, MATCHES/DIFFERS, streaks, gaps, chi-square (lower
   incomplete gamma) — no scipy.
4. **AnalysisManager** (`services/analysis_engine.py`) aggregates multi-window snapshots
   and is updated in realtime from `data_bus`. Served at `/api/analysis/{symbol}` + panels.

## Proposal & recommendation pipeline (Phase 3, read-only)

5. **Contracts** (`services/contracts.py`): family specs + board builder.
6. **Proposals** (`services/proposal_engine.py`): real Deriv proposal normalization
   (`{proposal:{id, ask_price, payout, spot}}`) → EV/breakeven; labeled HARNESS fallback.
7. **Recommender** (`services/recommender.py`): `QUALIFIED / WATCH / NO TRADE /
   INSUFFICIENT DATA` with sample/quality/breakeven/EV gates; a simulated price caps the
   state at `WATCH`.
8. **Phase3Service** (`services/phase3_service.py`) + `api/phase3.py`: rate-limited,
   cached, read-only quick-analysis + 42-contract board scan. **No trade execution.**

## Database

- Dev: SQLite file (`./backend/eaglex_dev.db`). Prod: Postgres via
  `postgresql+psycopg`. Models import cleanly (no hard Postgres-only types in Batch 1).
- Redis is targeted for later phases (caching/session), with a fallback today.

## Testing & quality gates

- `backend/tests/` — 87 pytest tests (API, ticks/digit extraction, harness tagging,
  auth/OAuth helpers, crypto, Phase 2 analytics/window/quality, Phase 3 contracts/
  proposals/recommender + API). `test_phase3.py` + extended `test_api.py`.
- `mypy` clean on `app/` (31 files); `ruff` clean on `app/` + `tests/`.
- Frontend: `next build` runs type-check + lint (ESLint via next); verified in-browser
  (chart renders, WS streams digits, honest source/state badges, analysis + quick-analysis +
  scan tabs return honest states).

## Running locally

```bash
# Backend
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000

# Frontend dev (different tab)
cd frontend && npm install && npm run dev   # http://localhost:3000

# Full app on ONE origin (production-like):
cd frontend && npm run build                # produces frontend/out
cd ../backend && FRONTEND_DIR=$PWD/../frontend/out uvicorn app.main:app --port 8000
```