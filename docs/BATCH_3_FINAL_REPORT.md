# EAGLE-X — Batch 3 Final Report (Phases 4–5)

**Status:** Implemented, tested, committed.
**Starting head:** `bb8f14a` (Batch 2). **Scope:** Phase 4 (validated signal pipeline) +
Phase 5 (execution engine → controlled real Deriv trading). Built on top of Batch 1/2;
no Batch 1/2 functionality was removed and the single-service architecture and Phase 4/5
roadmap were not changed.

## Mission

Turn the Batch 2 *read-only* board (analysis + priced recommendations) into an honest,
gate-first decision + execution layer:

> Fewer losses > better decisions > risk-adjusted profit. **NO TRADE is a valid, frequent
> answer.** Nothing is ever faked as live; any simulated/harness path is labeled and refused
> at the execution gate.

## Phase 4 — Validated signal pipeline

A `Signal` is the single object that carries an analysis + price + edge + risk decision.

- `services/probability.py` — transparent Bayesian digit-probability estimator. Posterior
  mean of a Beta over the digit counts (shrunken toward the 1/10 prior by a pseudo-count),
  with the regularized incomplete beta implemented in-file via Lentz' continued fraction
  (no scipy). Honest shrinkage means a 10/10 sample never reports 100%.
- `services/signal_engine.py` — `Signal` + state machine
  (`REJECTED → VALIDATING → EXECUTION_READY → OPEN → WON / LOST / VOID / ERROR /
  EXPIRED / BLOCKED`). Enforces hard gates at build time: data quality, minimum sample,
  valid proposal, valid price. `deterministic_signal_id` gives per-analysis+contract
  idempotency. A fresh signal is **never** executable; it must explicitly pass the risk gate.
- `services/risk_engine.py` — the Phase 4 risk gate (§10). Returns `PASS | VETO` with a
  human-readable reason and the exact vetos: kill switch, live not enabled, not
  authenticated, too many open, daily/session loss exceeded, consecutive-loss streak,
  negative EV, conflicting windows, stale data, expired signal, duplicate signal,
  insufficient balance, per-trade stake limits, execution lock held, invalid
  contract/duration/price, missing proposal, authorization failure.
- `config.py` — Phase 4/5 thresholds with conservative defaults: `signal_min_sample`,
  `signal_min_ev`, `risk_*` limits tuned to **NO TRADE** on a fair board.
- `models/models.py` — `SignalRecord` (immutable signal history) + `TradeRecord`.
- `services/trade_persistence.py` — `save_signal` / `save_trade` / list helpers (SQLite by
  default). Persistence is non-fatal: a persistence fault never blocks the signal path.

## Phase 5 — Execution engine

- `services/broker.py` — normalized `ExecutionRequest` + a `Broker` abstraction with three
  explicit modes:
  - **HARNESS** deterministic simulation (tests/demo; never labeled real),
  - **PAPER** realistic paper execution priced off the current spot and resolved by the
    next tick (same lifecycle/ledger as live, no money),
  - **LIVE** only when ALL gates pass *and* the server-side master switch
    (`execution_live_enabled`, default OFF) is enabled; otherwise **REJECTED**.
  - `KillSwitch` + `ExecutionLock`; `ExecutionLock` serializes purchases so two concurrent
    requests can never both buy.
- `services/execution_engine.py` — the lifecycle controller: revalidates EVERYTHING at
  execution time (state + expiry + risk PASS + mode), duplicate-purchase protection
  (idempotency key), ledger (`CREATED → SUBMITTED → CONFIRMED`), open-contract registry,
  `EXECUTION_UNCERTAIN` handling (ambiguous broker confirmation → **never re-buy**, goes to
  reconciliation), result resolution (explicit win OR next-tick digit OR UNKNOWN — never
  invented), and per-mode performance metrics (trades/wins/losses/win-rate/net/profit
  factor/max-drawdown/losing-streak/open).
- `services/decision_service.py` — ties it together: `produce_signal` (analysis→signal),
  `qualify` (signal→risk gate), `execute` (signal→broker), `resolve_contract`. Persists each
  signal and settled trade.
- `api/execution.py` — the API: config, mode, kill-switch, open contracts, ledger, live
  probe, performance, signals/history, duplicate-detection around manual execution. A live
  probe honestly reports `can_purchase` from the server's master switch + kill switch.

### Honesty & safety invariants (enforced in code + tests)
1. A live purchase fires ONLY when `execution_live_enabled` (server-side) is ON, the signal
   is `EXECUTION_READY`, risk is `PASS`, the signal is not expired, the kill switch is off,
   and the lock is free. Every other route returns `REJECTED/BLOCKED` with a reason.
2. **NEVER re-buy** after an ambiguous (UNKNOWN/UNCERTAIN) confirmation — reconciliation
   refuses, never blind-retries.
3. **NEVER invent a result** — resolution requires an explicit outcome or a real next digit;
   otherwise `UNKNOWN`.
4. **No one-tap live buy** in the UI: Request → Confirm, and the server still revalidates.
5. Every signal/trade/response carries an explicit `source`/`proposal_source`/`mode`
   (`HARNESS`/`PAPER`/`LIVE`) so a simulated path can never be mistaken for live.
6. The full flow never fakes data — a fair board yields **NO TRADE** (verified live).

## From Batch 2 to Batch 3 — boundaries honoured
- Batch 1/2 read-only scanner/quick-analysis remain intact and unchanged.
- Batch 3 adds the execution layer ON TOP; enabling it does not disable the read-only
  analysis/recommendation UI.
- Phase 5→Phase 6 boundary: live money stays governed by a single server-side switch; the
  UI cannot enable it. Postgres/Redis remain future infra (SQLite JSON persistence today).

## Quality gates (all green)
- Backend: **147 pytest tests passing** (87 Batch 1/2 baseline retained + 32 Phase 4 signal +
  16 Phase 5 execution + 12 Phase 5 API). `mypy` clean (39 files). `ruff` clean
  (`app/` + `tests/`).
- Frontend: `next build` (type-check + lint) passes with the new `ExecutionPanel`.
- Runtime E2E verified: harness connect → signal build → risk VETO on a fair board →
  execution engine `FAILED` (not execution-ready) → live probe reports `can_purchase=false` →
  signal persistence confirmed → all state endpoints respond. No real trade was ever
  attempted by the suite.
- No stray artifacts: `/tmp` used for e2e; `__pycache__`, `out/`, `.venv` not committed.

## Deliverables
- Phase 4 signal engine + risk gate + probability estimator + persistence
- Phase 5 broker (HARNESS/PAPER/LIVE) + execution engine + ledger + API + frontend panel
- Tests, docs, and a clean working tree

## Running
```bash
cd backend && . .venv/bin/activate && python -m pytest tests/ -q
cd frontend && npm run build   # emits out/
cd ../backend && FRONTEND_DIR=$PWD/../frontend/out uvicorn app.main:app --port 12000
```
Then open `/cockpit` → "Phase 4/5 — Signal pipeline & execution".