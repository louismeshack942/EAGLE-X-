# EAGLE-X — Phase Boundary (Batch 2)

This document records exactly what is **real and enabled** today versus what is a
**placeholder/disabled** stub. It is the single source of truth for what a user can
observe in Batch 2 (Phases 0–1 foundation + Phase 2 statistical analysis + Phase 3
read-only contract recommendations). EAGLE-X's operating rule: never label simulation as
live, and never show a control as functional when it is not.

## IMPLEMENTED across Batches

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

### Phase 2 — Real tick statistical analysis (`services/window_engine.py`, `analytics.py`, `analysis_engine.py`, `core/data_quality.py`, `api/analysis.py`)
- Ring windows per symbol at `DEFAULT_WINDOWS = (25, 50, 100, 250, 500, 1000)` ticks;
  every snapshot carries `n`, per-window `data_quality` (`DATA_READY / INSUFFICIENT_DATA /
  STALE / DISCONNECTED / INVALID`), and an honest `source` (`deriv_live` vs `harness`).
- Pure-Python statistical digit analysis (no scipy): digit frequency + ranks + z-scores,
  parity (ODD/EVEN counts + deviation vs 50/50), OVER/UNDER per barrier vs fair baseline,
  MATCHES/DIFFERS per digit vs 1/10 & 9/10 baselines, same-digit & parity streaks, gaps,
  Pearson chi-square uniformity (lower incomplete gamma CDF), and multi-window agreement
  (`STABLE / MULTI_WINDOW_SUPPORT / CONFLICTING / INSUFFICIENT_DATA`).
- `AnalysisManager` (multi-window registry) wired into `data_bus` + `main._on_tick`-equivalent
  hooks so snapshots are computed in realtime. Endpoints: `/api/analysis/{symbol}`,
  `/digits`, `/gaps`, `/streaks`, `/parity`, `/over-under`, `/matches-differs`, `/quality`,
  `/windows`.

### Phase 3 — Contracts + read-only recommendations (`services/contracts.py`, `proposal_engine.py`, `recommender.py`, `phase3_service.py`, `api/phase3.py`)
- Contract family spec (`DIGITMATCH/DIGITDIFF/DIGITODD/DIGITEVEN/DIGITOVER/DIGITUNDER`),
  barrier rules, fair win rates, board builder.
- Proposal service: normalizes real Deriv proposal payloads
  (`{proposal:{id, ask_price, payout, spot}}`) to EV/breakeven/Net-profit, and provides a
  **clearly-labeled HARNESS** simulated price fallback (never implied real). Real proposals
  only flow when a live/authenticated feed is configured.
- Recommendation engine: honest states `QUALIFIED / WATCH / NO TRADE / INSUFFICIENT DATA`.
  A positive-EV edge with a **real** proposal → `QUALIFIED`; the same edge against a
  **simulated** price → `WATCH` (cannot qualify on a fake quote). Insufficient sample /
  stale data / conflicting horizons never fire.
- Read-only scanner + quick-analysis over the whole board (42 contracts per symbol);
  every response carries `readonly_note: "READ-ONLY: priced and recommended, no trade was
  executed."` and a per-proposal `proposal_source` (`LIVE` vs `HARNESS` vs `UNAVAILABLE`).
  Registered `use_live = settings.oauth_configured`; otherwise HARNESS pricing is labeled.
- Endpoints: `/api/contracts`, `/api/contracts/{symbol}`, `/api/quick-analysis`,
  `/api/scan/{symbol}`, `/api/proposal-flow`.

### Frontend (Next.js / TS)
- Cockpit page with: market selector, Connect (Harness) + Connect (Live), live price chart,
  rolling last-digit frequency grid, data-source badge (HARNESS vs LIVE), connection-state
  badge, WS indicator, status log, honest empty/error states.
- `components/AnalysisPanel.tsx` (mounted in the cockpit): three tabs —
  Stats (real digit/parity/over-under/chi-square + multi-window state),
  Contracts (quick-analysis with family/barrier selectors, honest WATCH-on-HARNESS),
  Scan (read-only board scan with qualified/watch/no-trade counts + top candidates).
- Landing page showing real platform status + honest data policy.

### Quality gates (all green)
- 87 backend pytest tests passing (34 Batch 1 + 26 Phase 2 + 17 Phase 3 unit + 6 Phase 3 API +
  4 extended); `mypy` (31 source files) and `ruff` clean; `next build` (type-check + lint)
  passes; full stack verified in-browser (harness ticks stream to charts/analysis; quick-
  analysis + scan return honest states).

## DELIBERATELY NOT IMPLEMENTED / DISABLED

| Area | Status | When |
|------|--------|------|
| Real-money trade **execution** | Disabled — Phase 3 is read-only (no broker call anywhere) | Later phases |
| Live Deriv **data** | Refused with `AUTHORIZATION_REQUIRED` when OAuth unconfigured; server falls back to **HARNESS**, always labeled | After OAuth setup |
| Contract **pricing** as real | Real Deriv pricing only when an authenticated feed is configured; otherwise **HARNESS** (labeled) or `UNAVAILABLE` | Configured live only |
| Postgres/Redis | Dev falls back to SQLite; Postgres string ready | When infra provisioned |
| Multi-user/multi-account UI | Not built | Later |

Nothing in this list is faked to look like a working feature. Controls either explain
their placeholder status or the backend honestly refuses.

## Honesty invariants (enforced in code + tests)
1. A harness tick is never labeled `deriv_live`.
2. `/api/status` never claims live data when OAuth is unconfigured.
3. A recommendation never qualifies on a **simulated** (`HARNESS`) proposal — it is capped
   at `WATCH` and the reason names the simulation. Tests: `test_harness_proposal_watch_only`.
4. `INSUFFICIENT`/`NO TRADE` are returned whenever data is too thin, stale, or horizons
   conflict — never an invented edge.
5. Every analysis/proposal/scan response carries an explicit `source` /
   `proposal_source` enum and the phase-3 responses carry `readonly_note`.
6. Phase 3 never executes a trade or calls a broker: it is read-only by construction.
7. Connecting "Live" without OAuth returns a clear `AUTHORIZATION_REQUIRED` error.
8. No password is stored, accepted, or even referenced.
9. Placeholder features are shown as placeholders, not as working tools.