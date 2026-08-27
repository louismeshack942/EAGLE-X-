# EAGLE-X — Batch 1 Final Report

**Batch 1 (Phase 0 + Phase 1):** ProTrader forensic specification + observable parity foundation.

Date: 2026-08-27 · Working repo: `/workspace/project` (fresh `git init`, branch `main`)

## What was delivered

### Phase 0 — Forensic specification (research-only, no product code)
- `docs/PHASE0_FINAL_SPECIFICATION.md` — the master forensic spec.
- `docs/protrader/` — `MARKETS`, `CONTRACTS`, `UI_INVENTORY`, `AUTH_FLOW`,
  `PROTRADER_PARITY_MATRIX`, `BLACK_BOX`. Marks each observable feature with a confidence
  level and documents what is **not** observable (proprietary scoring/formulas = black box).

Legitimate discovery findings (from public Deriv docs):
- Deriv OAuth2 Authorization Code + PKCE via a registered app (`client_id`, exact redirect
  URI, `code`+`code_verifier` exchange).
- Contract pairs: Matches/Differs, Even/Odd, Over/Under; durations ≤10 ticks; barriers 0–9.
- Synthetic symbols: R_10/25/50/75/100, RDBULL/RDBEAR, Volatility(1s) variants.

### Phase 1 — Foundation (working, tested, single-origin)
Backend (FastAPI + SQLAlchemy):
- Env-driven `config.py`; models (`User`, `Authorization`, `Market`, `Tick`, `Events`);
  `init_db` seeds the market list.
- `core/ticks.py` — strict validation + transparent last-digit extraction.
- `core/events.py` in-process event bus; `core/status.py` connection states;
  `core/crypto.py` AES-GCM token encryption at rest.
- `services/` — abstract `connector`, clearly-labeled `harness` simulator,
  `deriv_client` (Deriv WS), `data_bus` (provider → bus → DB), `oauth` (legit PKCE).
- `api/` — `auth.py` (OAuth scaffolding + session status) and `cockpit.py`
  (`/api/status`, `/api/markets`, `/api/connect`, `/api/ticks/{symbol}`, `/ws/ticks`).
- Single-origin static frontend serving.

Frontend (Next.js 14 `output: export`, TypeScript, TradingView lightweight-charts):
- Cockpit: market selector, honest Connect (Harness)/(Live), live price chart, rolling
  digit-frequency grid, data-source + connection-state badges, WS indicator, status log,
  honest placeholder/empty/error states. Mobile-responsive.
- Landing page with real platform/status info and the honest data policy.

## Verification results
- Backend: **34 pytest tests pass** (`backend/tests/`).
- Static analysis: **mypy clean** on `app/`, **ruff clean** on `app/` + `tests/`.
- Frontend: **`next build` passes** (type-check + lint). Verified in a real browser:
  harness connect → live ticks stream → chart + digit grid update; `CONNECTED`, `WS: OPEN`,
  `DATA SOURCE: HARNESS` all indicated correctly; "Connect (Live)" honestly refused
  without OAuth.
- Committed cleanly (initial state) — see git log.

## Honesty / security posture
- Simulation is always tagged `provider="harness"` and surfaced as HARNESS; never real.
- `AUTHORIZATION_REQUIRED` (503) when Live is requested without configured Deriv OAuth.
- Auth is legitimate Deriv OAuth2 (Auth Code + PKCE); **no passwords anywhere**.
- Tokens encrypted at rest; secrets from env only; `.env.example` documents all vars.

## Known limitations / next steps
- Postgres/Redis targeted; dev uses SQLite file (no binaries installed in this env).
- Advanced digit analysis and trading execution are placeholders for later phases.
- Live Deriv flow is built but inert until a real Deriv app (client_id/secret) is supplied.

### Next batches
1. Phase 2 — re-tick windowing, digit-frequency & streak analysis (real, tested).
2. Phase 3 — contract pricing via the Deriv proposal API + read-only recommendations.
3. Later — risk controls, execution demo on a test account, multi-account UI.