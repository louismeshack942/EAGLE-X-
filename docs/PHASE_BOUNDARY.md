# EAGLE-X — Phase Boundary (Batch 1)

This document records exactly what is **real and enabled** today versus what is a
**placeholder/disabled** stub. It is the single source of truth for what a user can
observe in Batch 1. EAGLE-X's operating rule: never label simulation as live, and never
show a control as functional when it is not.

## IMPLEMENTED in Batch 1 (Phase 0 + Phase 1)

### Backend
- Settings/env loading (`config.py`), SQLAlchemy models + DB init with seeded markets.
- Realtime tick pipeline:
  - `core/ticks.py` — validation + transparent last-digit extraction (deterministic, tested).
  - `services/harness.py` — simulation provider, **every tick tagged `provider="harness"`**,
    surfaced in the UI as HARNESS. Never labeled real.
  - `services/deriv_client.py` — async Deriv WS client reserved for `deriv_live`; only
    usable after OAuth is configured (honest refusal otherwise).
  - `services/data_bus.py` — provider → event bus → DB persistence (recorder faults non-fatal).
- Auth:
  - `services/oauth.py` + `api/auth.py` — legitimate **Deriv OAuth2 Authorization Code +
    PKCE**. Inert (`NOT_CONFIGURED`) until env values are supplied; no fake passwords.
  - Session status endpoint (`/auth/status`), login/logout scaffolding.
- API:
  - `/health`, `/api/info`, `/api/status`, `/api/markets`, `/api/connect`,
    `/api/ticks/{symbol}`, `/ws/ticks`.
- Single-origin serving of the exported frontend build.

### Frontend (Next.js / TS / TradingView lightweight-charts)
- Cockpit page with: market selector, Connect (Harness) + Connect (Live), live price chart,
  rolling last-digit frequency grid, data-source badge (HARNESS vs LIVE), connection-state
  badge, WS indicator, status log, honest empty/error states.
- Landing page showing real platform status + honest data policy.

### Quality gates (all green)
- 34 backend pytest tests passing; `mypy` and `ruff` clean; `next build` (type-check + lint) passes;
  full stack verified in-browser (harness ticks stream to chart and digits).

## DELIBERATELY NOT IMPLEMENTED / DISABLED

| Area | Status in Batch 1 | When |
|------|-------------------|------|
| Real-money trading execution | Disabled (placeholders only) | Later phases |
| Advanced digit analysis (streaks/gaps/probabilities/recommendations) | Placeholder | Phases 2–3 |
| Contract pricing via Deriv `proposal` API | Placeholder (contract chips shown, not priced) | Phase 2+ |
| Live Deriv data | Refused with `AUTHORIZATION_REQUIRED` when OAuth unconfigured | After OAuth setup |
| Postgres/Redis | Dev falls back to SQLite; Postgres string ready | When infra provisioned |
| Multi-user/multi-account UI | Not built | Later |

Nothing in this list is faked to look like a working feature. Controls either explain
their placeholder status or the backend honestly refuses.

## Honesty invariants (enforced in code + tests)
1. A harness tick is never labeled `deriv_live`.
2. `/api/status` never claims live data when OAuth is unconfigured.
3. Connecting "Live" without OAuth returns a clear `AUTHORIZATION_REQUIRED` error.
4. No password is stored, accepted, or even referenced.
5. Placeholder features are shown as placeholders, not as working tools.