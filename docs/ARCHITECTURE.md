# EAGLE-X ‚Äî Architecture (Batch 1)

Single-service architecture: a FastAPI backend serves both the JSON/WS API and the
statically-exported Next.js frontend from one origin. No proxy, no cross-service URLs.

## Repo layout

```
.
‚îú‚îÄ‚îÄ .env.example            # all env vars + comments (no secrets)
‚îú‚îÄ‚îÄ .gitignore
‚îú‚îÄ‚îÄ backend/
‚îÇ   ‚îú‚îÄ‚îÄ app/
‚îÇ   ‚îÇ   ‚îú‚îÄ‚îÄ main.py         # FastAPI app; mounts exported frontend when built
‚îÇ   ‚îÇ   ‚îú‚îÄ‚îÄ config.py       # pydantic-settings (env-driven)
‚îÇ   ‚îÇ   ‚îú‚îÄ‚îÄ db/init_db.py   # create_all + seed markets
‚îÇ   ‚îÇ   ‚îú‚îÄ‚îÄ models/models.py# SQLAlchemy models (User, Authorization, Market, Tick, Events)
‚îÇ   ‚îÇ   ‚îú‚îÄ‚îÄ core/           # ticks (normalization+digit), events bus, status, crypto
‚îÇ   ‚îÇ   ‚îú‚îÄ‚îÄ services/       # connector interface, harness (clearly labeled), deriv_client, data_bus, oauth
‚îÇ   ‚îÇ   ‚îî‚îÄ‚îÄ api/            # auth.py (OAuth) + cockpit.py (markets, connect, ticks, WS)
‚îÇ   ‚îú‚îÄ‚îÄ tests/              # pytest suite (backend)
‚îÇ   ‚îú‚îÄ‚îÄ pyproject.toml
‚îÇ   ‚îî‚îÄ‚îÄ requirements.txt
‚îú‚îÄ‚îÄ frontend/
‚îÇ   ‚îú‚îÄ‚îÄ app/                # Next.js App Router (client components)
‚îÇ   ‚îú‚îÄ‚îÄ components/         # LiveChart (TradingView lightweight-charts)
‚îÇ   ‚îî‚îÄ‚îÄ lib/api.ts          # typed API helpers (relative, same-origin)
‚îî‚îÄ‚îÄ docs/                   # Phase 0 forensic spec + protrader research
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
  (503) ‚Äî no silent simulation disguised as real.

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
   incomplete gamma) ‚Äî no scipy.
4. **AnalysisManager** (`services/analysis_engine.py`) aggregates multi-window snapshots
   and is updated in realtime from `data_bus`. Served at `/api/analysis/{symbol}` + panels.

## Proposal & recommendation pipeline (Phase 3, read-only)

5. **Contracts** (`services/contracts.py`): family specs + board builder.
6. **Proposals** (`services/proposal_engine.py`): real Deriv proposal normalization
   (`{proposal:{id, ask_price, payout, spot}}`) ‚Üí EV/breakeven; labeled HARNESS fallback.
7. **Recommender** (`services/recommender.py`): `QUALIFIED / WATCH / NO TRADE /
   INSUFFICIENT DATA` with sample/quality/breakeven/EV gates; a simulated price caps the
   state at `WATCH`.
8. **Phase3Service** (`services/phase3_service.py`) + `api/phase3.py`: rate-limited,
   cached, read-only quick-analysis + 42-contract board scan. **No trade execution.**

## Phase 4 ‚Äî Validated signal pipeline

9. **Probability** (`services/probability.py`): transparent Bayesian digit-probability
   estimator ‚Äî Beta posterior mean shrunken toward the 1/10 prior, regularized lower
   incomplete beta implemented in-file (Lentz continued fraction), no scipy.
10. **Signal engine** (`services/signal_engine.py`): `Signal` state machine
    (`REJECTED ‚Üí VALIDATING ‚Üí EXECUTION_READY ‚Üí OPEN ‚Üí WON/LOST/VOID/ERROR/EXPIRED/BLOCKED`).
    Hard build-time gates (data quality, min sample, valid proposal, valid price) + EV
    computed from the proposal payout; every signal carries `estimated_probability`,
    `expected_value`, `source`, `proposal_source`, `multi_window_state`.
11. **Risk engine** (`services/risk_engine.py`): the Phase 4 risk gate ‚Äî `PASS | VETO`
    (kill switch, live not enabled, not authenticated, too many open, loss limits, streak,
    negative EV, conflicting windows, stale, expired, duplicate, balance, stake limits,
    lock held). Deterministic `signal_id` per analysis+contract for idempotency.
12. **Decision service** (`services/decision_service.py`): ties analysis ‚Üí signal ‚Üí risk ‚Üí
    execution; persists each signal.

## Phase 5 ‚Äî Execution engine

13. **Broker** (`services/broker.py`): `ExecutionRequest` + `Broker`. Three explicit modes ‚Äî
    **HARNESS** (deterministic sim, labeled), **PAPER** (priced off the spot, resolved next
    tick), **LIVE** (REJECTED unless the server-side master switch `execution_live_enabled`
    is ON and every gate passes). `KillSwitch` + `ExecutionLock`.
14. **Execution engine** (`services/execution_engine.py`): lifecycle controller ‚Äî
    revalidates everything, duplicate-purchase protection (idempotency), ledger,
    `EXECUTION_UNCERTAIN ‚Üí reconciliation (NEVER re-buy)`, result resolution (explicit win /
    next digit / UNKNOWN ‚Äî never invented), per-mode performance metrics.
15. **API** (`api/execution.py`): `/api/exec/{config,mode,killswitch,open,ledger,probe,
    performance,signals,history,execute,resolve}` and `/api/signal/{symbol}` (read-only
    decision card). A live probe honestly reports `can_purchase` from the server switch.
16. **Frontend** (`components/ExecutionPanel.tsx`, mounted in `/cockpit`): mode selector,
    signal decision card, risk state, kill switch, open contracts, performance, trade
    history, live-safety notice. Request ‚Üí Confirm; the server still revalidates.

## Database

- Dev: SQLite file (`./backend/eaglex_dev.db`). Prod: Postgres via
  `postgresql+psycopg`. Models import cleanly (no hard Postgres-only types in Batch 1).
- Redis is targeted for later phases (caching/session), with a fallback today.

## Testing & quality gates

- `backend/tests/` ‚Äî 87 pytest tests (API, ticks/digit extraction, harness tagging,
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

## Phase 6 — Automated Trader

Decidedly a CLIENT of the Phase 4/5 pipeline, not a new execution path.

- `backend/app/services/automated_trader.py` — orchestrator with a conservative state
  machine (`OFF → STARTING → MONITORING → ANALYZING → VALIDATING → READY → EXECUTING →
  TRACKING → PAUSED → STOPPING → ERROR`), default `OFF`. Modes:
  - `MONITOR` — dry-run: analysis + risk only, NEVER executes.
  - `PAPER` — full lifecycle through `PaperBroker` only.
  - `LIVE` — requires the server-side `execution_live_enabled` master switch, account
    authentication, and every gate; the API/frontend can never enable it.
- Every execution goes through `DecisionService.execute` → `ExecutionEngine` → broker.
  The trader never calls a broker directly, never grows its own stake (no martingale),
  and applies freshness/loss/cooldown/duplicate/concurrency/crash-safe autorecovery gates.
- API (`/api/automation/*`): status, config (GET/POST, server-side caps), start, stop,
  pause, resume, set-mode, decisions audit log, state, scan.
- UI: `AutomationPanel.tsx` wired into the cockpit with unambiguous OFF/MONITOR/PAPER/LIVE
  badges and LIVE-switch + kill-switch state.
- Runtime E2E: harness → MONITOR armed → scan of 42 candidates → 0 trades (honest
  dry-run); LIVE set-mode refused (`execution_live_enabled is FALSE`, `not authenticated`);
  audit log populates; stop → OFF.

## Phase 7 — Validation, hardening & honesty

- **Regression:** 184 pytest tests, mypy clean (41 files), ruff clean, `next build` clean.
- **Failure injection:** ambiguous broker → `UNKNOWN` ledger, NO re-buy on retry;
  duplicate idempotency → `BLOCKED`; kill switch vetoes live flows; reconcile-uncertain
  refuses re-buys.
- **Soak:** 3000 engine cycles — no leaked/duplicated ledger entries, open count stays 0.
- **Stats integrity:** automation P/L derives only from engine results.
- **Audits:** no martingale/stake-growth code in `app/`; no hardcoded secrets.
- See `docs/BATCH_4_FINAL_REPORT.md` and `backend/tests/test_automation_phase7.py`.