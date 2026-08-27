# EAGLE-X — Batch 2 Final Report

**Batch 2 (Phase 2 + Phase 3):** real tick analysis + real Deriv proposal pricing
→ read-only recommendations. Continuing from Batch 1 (`6713143`). No rebuild of Batch 1,
no roadmap/architecture change, no phase skipping.

Date: 2026-08-27 · Working repo: `/workspace/project` (branch `main`)

## Mission posture
- Long-term goal: reproduce the complete **observable** ProTrader surface.
- **Never fake data.** Any MOCK/simulation is surfaced honestly with an explicit `source` /
  `proposal_source` label and a plain-English note. A simulated price is never presented as
  a real Deriv quote (a recommendation on a simulated price is capped at `WATCH`).

## Phase 2 — Real tick statistical analysis

New:
- `app/services/window_engine.py` — `TickWindow` + `WindowManager`: ring windows per symbol
  at `DEFAULT_WINDOWS = (25, 50, 100, 250, 500, 1000)`.
- `app/core/data_quality.py` — honest per-window quality: `DATA_READY / INSUFFICIENT_DATA /
  STALE / DISCONNECTED / INVALID` (sample, window-complete, age, dup/invalid tick counts).
- `app/services/analytics.py` — pure-Python math (no scipy): digit frequency + ranks +
  z-scores, parity, OVER/UNDER per barrier vs fair baseline, MATCHES/DIFFERS per digit,
  same-digit + parity streaks, gaps, and **Pearson chi-square** uniformity (lower incomplete
  gamma CDF via `_gamma_lower_series`).
- `app/services/analysis_engine.py` — `AnalysisManager` + `WindowManager` registry;
  multi-window snapshots + realtime.
- `app/services/data_bus.py` — integrated `analysis_manager` hooks + `provider_connected`.
- `app/api/analysis.py` — `/api/analysis/{symbol}` + sub-resource routes (`/digits`,
  `/gaps`, `/streaks`, `/parity`, `/over-under`, `/matches-differs`, `/quality`, `/windows`).
- `app/main.py` — router registration, phase `"2+3"`.

## Phase 3 — Real Deriv proposals → read-only recommendations

New:
- `app/services/contracts.py` — family specs (`DIGITMATCH/DIGITDIFF/DIGITODD/DIGITEVEN/
  DIGITOVER/DIGITUNDER`), barrier rules, fair win rates, board builder, spec validation.
- `app/services/proposal_engine.py` — `ProposalService` + `normalize_deriv_proposal`
  (parses the documented `{proposal:{id, ask_price, payout, spot}}` shape into
  EV / breakeven_win_rate / payout_pct / profit_net with honest `state`). Provides a
  **labeled HARNESS** simulated-price fallback for dev/demo.
- `app/services/recommender.py` — `RecommendationEngine`: honest states
  `QUALIFIED / WATCH / NO TRADE / INSUFFICIENT DATA`. Gates: sample floor, data quality,
  breakeven margin, positive-EV requirement, proposal source (HARNESS ⇒ capped WATCH).
- `app/services/phase3_service.py` — `Phase3Service`: rate-limited, cached, read-only
  quick-analysis + full-board scan (42 contracts); carries `READONLY_NOTE`.
- `app/api/phase3.py` — `/api/contracts`, `/api/contracts/{symbol}`, `/api/quick-analysis`,
  `/api/scan/{symbol}`, `/api/proposal-flow`. `use_live = settings.oauth_configured`.

Frontend:
- `frontend/components/AnalysisPanel.tsx` — three tabs mounted in the cockpit:
  Stats / Contracts / Scan. Every source is labeled (HARNESS vs LIVE), recommendations show
  pwin/EV/breakeven/payout with the honest state badge and reason.
- `frontend/app/globals.css` — added `.tbl` + `.btn.active`.
- `frontend/app/cockpit/page.tsx` — replaces the Phase 1 placeholder panels with
  `AnalysisPanel`.

## Verification
- Backend: **87 pytest tests pass** (34 Batch 1 + 26 Phase 2 + 17 Phase 3 unit + 10 Phase 3 API).
- Static analysis: **mypy clean** (31 source files); **ruff clean** (`app/` + `tests/`).
- Frontend: **`next build` passes** (type-check + lint).
- Full stack verified in a real browser against a running instance with a harness feed:
  - `/api/analysis` populates digit/parity/over-under/chi-square + multi-window state in realtime.
  - quick-analysis DIFFERS @ 0 with `pwin 1.0`, sample 100, payout 1.88 → state **WATCH**,
    reason `SIMULATED (HARNESS) proposal — not a real Deriv quote. Cannot qualify based on a
    simulated price.` — the honest labeling works end-to-end.
  - Board scan returns all 42 contracts as honest NO TRADE / WATCH (no fake qualified picks
    on simulated pricing).
  - `DATA SOURCE: HARNESS`, `CONNECTED`, `WS: OPEN` badges all correct.

## Honesty / security posture
- A recommendation **never qualifies on a simulated price** — capped at `WATCH` with an
  explicit reason and each card shows the exact `proposal_source`.
- All phase-3 responses carry `readonly_note`; no broker call or trade execution anywhere.
- Live Deriv proposals only flow when an authenticated feed is configured; otherwise
  `HARNESS` (labeled) or `UNAVAILABLE`.
- `/proposal-flow` reports the current live vs harness mode honestly.

## Known limitations / next steps
- Postgres/Redis targeted; dev uses SQLite (no binaries installed in this env).
- Live Deriv proposal flow is wired but inert until a real Deriv app
  (client_id / client_secret or PAT) is supplied.
- Trade **execution** is deliberately not built; phase 3 is read-only by construction.

### Next batches
1. Later — risk controls (Kelly sizing, drawdown scales, kill switch).
2. Later — execution demo on a test account + trade journal.
3. Later — multi-account UI and ProTrader-style journals/scorecards.