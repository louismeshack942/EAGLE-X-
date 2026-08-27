# EAGLE-X — Go‑Live Checklist (operator-only, read-only runbook)

Live trading can only be enabled by a **human operator** on the server. This checklist is the
procedural gate. No secrets belong in this repo (or any repo). All credentials go to
Render's dashboard secret env only.

**Current truth:** `EXECUTION_LIVE_ENABLED=false` (the single master switch). The API/UI/
trader **cannot** change it. This repo has never placed a real-money trade and cannot do so
until an operator takes steps 4–5 below AND all gates pass.

## Phase A — Deploy Batch 4 (no money at risk)

- [ ] Push `main` (HEAD `22d73de`) to the repo; Render auto-deploys the image built by
      the repo root `Dockerfile` (single service, serves API + static frontend同origin),
      blueprint isolated in `render.yaml` (name `eaglex`, plan free, health `/health`,
      1GB disk at `/app/backend` — SQLite + JSON persistence).
- [ ] On the Render dashboard confirm: image built, `/health` → `{"status":"ok"}`, `/cockpit` loads.

## Phase B — Set server-side env (Render dashboard secret env)

Non-secret (fine in dashboard):
- [ ] `ENV=production`, `FRONTEND_DIR=/app/frontend_static`
- [ ] `EXECUTION_LIVE_ENABLED=false` (explicit, stays false until Phase D)
- [ ] `CF_AUTOSTART=off` for the first validation days (the CF never-stop standing order
      re-arms it later, once proven).

Secrets (never in repo — Render secret env only):
- [ ] `DERIV_API_TOKEN` (=`pat_…` PAT token) + `DERIV_PAT_APP_ID`
      (or `DERIV_APP_ID` / `DERIV_CLIENT_SECRET` for OAuth login)
- [ ] `SECRET_KEY` (long random), `COOKIE_SECURE=true`
- [ ] `DATABASE_URL` — either empty (SQLite file on the 1GB disk) or Postgres URL

## Phase C — Validation window (MONITOR → PAPER; STILL no real money

- [ ] Wait for live feed: `/api/status` shows `data_source=deriv_live` and `connection[].state=connected`.
- [ ] Auth: complete `/auth/deriv/login` (or the PAT flow); `/auth/status` → `authenticated=true`.
- [ ] Run automation in **MONITOR** for ≥1 session: `/api/automation/set-mode {"mode":"MONITOR"}`,
      start, scan a handful of cycles. Expect: candidates analyzed, **0 trades, 0 open**,
      audit log populates (dead honest dry-run).
- [ ] Promote to **PAPER**: run the full lifecycle (PaperBroker) for at least one session win+loss;
      watch: ledger correct, kill switch, cooldown, consecutive-loss limit, no duplicate idempotency keys.（test suite already proves these; this is the live-data confirmation.)

## Phase D — Live enable (operator decision, FINAL gate

- [ ] Run the preflight gate (read-only):
      `python scripts/preflight_live.py --base https://<app>.onrender.com --paper-check --require-auth`
      → **must print `VERDICT: GO`** (all PASS). If any FAIL — fix that gate first, never skip.)
- [ ] On the Render dashboard set `EXECUTION_LIVE_ENABLED=true`（explicitly, by hand).
- [ ] Confirm `/api/automation/status` → `live_enabled: true`, `authenticated: true`.
- [ ] Keep `LIVE_STAKE_MAX=1.0` (default). Start trading in **LIVE**; watch the first trade:
      ONE open max, stake $1, all gate annotations visible, no unexpected duplicate requests.

## Phase E — Ongoing safety

- [ ] Daily: `/api/automation/status` (kill switch, daily loss, consecutive losses, open trades)
- [ ] Any anomaly → `/api/automation/stop` + announce; never "人 fix" live trades manually
- [ ] Periodic: run `tests/` full suite (184 tests), the preflight re-check, the ledger
      reconciliation (journal truth engine) before any stake/session-limit change.

---
**Hard rules:** no martingale, no stake growth, no API/frontend live toggle, no
fabricated data, no secrets in repo, no skipping gates. STOP is a valid, preferred verdict.