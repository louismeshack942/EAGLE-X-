# EAGLE-X — Agent Notes

## Real-data mission (2026-08-25) — geo-block defeated

**Root cause of every phantom edge:** the generic Deriv endpoint
(`wss://ws.derivws.com/websockets/v3`) geo-blocks whole regions — it served
ZERO synthetic symbols to BOTH the DE (Render/Frankfurt) egress and a US
sandbox. `/status` showed `clients_country: de` + "Deriv serves no symbols to
country 'de'". The system silently fell back to DemoGenerator (GBM) ticks, so
the Truth Engine computed "edges" on a near-random walk and the CF fired
MATCHES lottery tickets (the -8 session). The repo note "frankfurt works" is
OUTDATED.

**The fix (works):** route the market-data WS through the account OTP URL
(`wss://api.derivws.com/trading/v1/options/ws/<demo|real>?otp=...`). Deriv
decides symbol availability by ACCOUNT there, not egress IP — the same IP
that got InvalidSymbol on the generic endpoint streams real ticks (R_100
quote 605.83) once the OTP URL is used.
- `DerivClient._connect` now resolves its URL via `deriv_trader._url(token)`,
  which mints a FRESH single-use OTP per connection (needs `account_id` +
  `app_id` in the vault) and falls back to the generic endpoint when no
  account is connected.
- Fixed trader fallback URL: was `deriv_ws_url + "/websocket"` (Deriv 404s
  that -> forced demo on fresh boots); now `deriv_ws_url + "?app_id=..."`.
- After GeoRestrictedError the demo loop re-probes live every 300s — after
  connecting the token, live mode resumes within ~5 min (watch /status).

**Tighter gate:** CF may only fire on `truth_engine.proven_edges(symbol)` =
an EDGE that survives ALL of the 100/300/1000-tick windows (min_ticks=50 is
a thin-tape floor only; 200 would blanket-ban). Single-window flukes (the
exact MATCHES bug) are refused. Regression tests in TestProvenEdges.
Suite: 232 passed. Live commit: 8f9cb5d.

**To get live data on a fresh boot:** the token must be connected BEFORE the
first stream probe, else it parks in demo for 300s. Connect via
POST /auth/token {token, app_id} early, or just wait for the re-probe.

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

## Analysis Lab / Truth Engine (2026-08-25)

The honest layer. Answers "do I have a real edge right now?" with math:

- `backend/app/services/truth_engine.py` — per contract per symbol:
  breakeven win rate (1/payout), Bayes-shrunk observed rate, margin (pp),
  EV, verdict: EDGE (significant + positive EV), FAIR (nothing mispriced),
  TRAP (significant and still losing). `projection()` is quarter-Kelly,
  capped 10%, and returns $0/day (not a hedge) when no EDGE exists.
  `reconcile_journal()` replays the journal against breakeven math —
  verdicts SUSTAINABLE / BREAKEVEN / SLOW BLEED. The canonical example:
  18W/2L at 1.1 payout (DIFFERS) needs 90.9% to break even; 90.0% is
  below it — variance, not edge.
- `backend/app/services/tick_recorder.py` — every tick appended to
  `data/ticks/{symbol}.jsonl` (20MB rotation, one backup), provider
  tagged (`deriv_live` vs `demo`). Hooked in `_on_tick` in main.py;
  recorder faults never interrupt ingestion.
- Routes: `/lab/edge-board`, `/lab/expectancy/{symbol}`,
  `/lab/projection/{symbol}`, `/lab/reconcile`, `/lab/recordings[/{symbol}]`.
- Frontend: `AnalysisLab.tsx` (4 tabs: Edge Board / Contract Truth /
  Journal Truth / The Tape), own dashboard section.
- Tests: `tests/test_truth_engine.py` (20 tests incl. the 18W/2L
  reconciliation). Suite: 228.

Demo-feed honesty fix (same day): the GBM step was ~100x smaller than
the 4th decimal, so rounded quotes froze the last digit — digit 0 never
occurred in demo mode and every analytic manufactured fake "OVER 0"
edges. `DemoGenerator` now stamps a uniform 0-9 digit per tick in
`raw["digit"]`; `Tick.digit` reads the last digit at the quote's own
decimal precision and prefers the stamped digit when present.

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


## Bottom-Up Profitability Engine (2026-08-26 directive)

`backend/app/services/bottom_up.py` — the gate-first decision layer on top of
Pro Trader stats. Mission order: fewer losses > better decisions > risk-adjusted
profit. Survival before profit; NO TRADE is a valid, frequent answer.

- **Contract hierarchy (§1):** MATCHES > OVER > UNDER > ODD > EVEN > DIFFERS.
  The hierarchy orders FOCUS among validated edges only — it never forces a
  trade. This intentionally conflicts with the old "CF trades DIFFERS only"
  lesson; resolved by keeping every hard gate, so a fair board still yields
  zero trades. Never loosen gates to force MATCHES trades.
- **Hard gates (§4):** data_quality, sample (>=100), FDR statistics,
  confidence (Wilson LB > breakeven, §8), safety_margin (edge >= +3pp default,
  preferred +5pp, §7), multi-window stability over 50/250/1000 (§5),
  long_term anti-spike (the long window must independently clear the margin,
  §10), edge_not_decaying (chronological chunk slope, §12), latency <=500ms,
  risk (risk_guard.killed has veto authority, §16).
- **Score 0-100 + grade A+/A/B/C/D (§14/§15):** any failed gate => grade D.
  The score NEVER overrides a hard rejection. Only A+/A are auto-executable.
- **Signal persistence (§11/§12):** detection is not execution. A passing
  candidate is tracked (EDGE_LIFETIME: initial/current edge, slope,
  volatility) and must survive `confirmation_ticks` (default 5) re-evaluations
  before EXECUTE; decay below the floor or disappearing evidence CANCELS it.
  Tracker is in-memory by design (signals are short-lived). `on_tick` is
  hooked in `main._on_tick` and only re-evaluates symbols with live signals.
- **Journal analytics:** postmortem (§20, 11 loss classes + variance-vs-model
  Wilson verdict), win_analysis (§21, SKILL_CONSISTENT vs VARIANCE_NOT_PROVEN),
  scorecard (§24: win rate, realized vs expected EV, ROI, profit factor,
  max drawdown, losing streaks, per-contract/market/barrier, grade win rates),
  kill switches (§25: rolling-100 EV<0 AND Wilson UB below breakeven),
  validate_thresholds (§7/§23: in-sample grid, refuses to declare winners
  without out-of-sample evidence).
- **Martingale (§18):** capped plans only via required-recovery formula;
  unlimited is prohibited; plan aborts when bankroll breaks.
- Config: GET/POST `/bottom-up/config` (persisted in settings_store, env
  BU_MIN_EDGE / BU_CONFIRMATION_TICKS). Routes: `/bottom-up/rank`,
  `/signal/{symbol}`, `/candidates/{symbol}`, `/tracker`, `/risk-profile`,
  `/martingale`, `/postmortem`, `/win-analysis`, `/scorecard`, `/validate`.
- Tests: `tests/test_bottom_up.py` (22 tests). Suite: 271 passed.
- Still advisory: the auto_trader execution path does not yet consume
  bottom-up decisions; frontend panel not yet built. Both are deliberate
  follow-ups.


## Super-Profitability Engine (2026-08-26 directive #2)

`backend/app/services/super_profit.py` — seven-brain ensemble over the
bottom-up gate layer. A candidate EXECUTES only when bottom-up hard gates
pass AND the tracker confirms AND the brains reach consensus AND uncertainty
stays under the ceiling AND health/regime/meta gates pass.

- **Brains:** A frequency (adaptive window, no lookahead), B probability
  (Wilson bounds), C sequence (transition-conditioned, gated by a cached
  shuffle test — no sequential info => abstains), D anomaly (FDR vs
  contradiction), E contract specialist (per-family margin multipliers),
  F execution (latency/payout-source/freshness), G risk (risk_guard veto +
  model health). One OPPOSE vetoes the consensus.
- **Ensemble:** agreement = (support-oppose)/7; needs >=5 SUPPORT and >=0.70
  agreement. **Uncertainty** (sample gap, confidence deficit, opposing
  brains, tracked edge volatility, calibration error) must stay <= 0.60.
  **Meta-model** score >= 60 required. Regime classifier: NORMAL /
  CONCENTRATED / DISTRIBUTION_SHIFT / HIGH_ANOMALY / LOW_INFORMATION /
  UNSTABLE; LOW_INFORMATION blocks by default.
- **Offline honesty tools:** `/super/conditional/{symbol}` (P(outcome|STATE)
  with per-position states computed only from past ticks; features without
  significant lift are DISCARD), `/super/ablation/{symbol}` (walk-forward
  brain-removal impact, no future information), `/super/calibration`
  (predicted vs realized bins, MIS_CALIBRATED verdict), `/super/health`
  (GREEN/YELLOW/ORANGE/RED per contract family with exposure multipliers),
  `/super/matrix` (symbol x contract institutional memory), `/super/profiles`
  (per-market learned personalities from realized results only),
  `/super/auction` (best validated offer wins; zero valid => zero trades,
  frequency target never forced), `/super/allocate` (EV x confidence x
  health weighting), `/super/profit-lock` (session pnl tiers: +2% -> 0.75x,
  +3% -> 0.5x, +4% -> stop).
- Decision card at `/super/decision/{symbol}` (GET) and POST with
  {payouts, latency_ms} for live-proposal pricing (§9 dynamic payout).
- Config: GET/POST `/super/config` (settings_store key super_config).
- Tests: `tests/test_super_profit.py` (25 tests). Suite: 297 passed.
- Still advisory: the execution path (auto_trader) is untouched. The
  ensemble gates decisions; it does not place trades.


## Lightning + Eagle + Organism (2026-08-26 directives #3-#6)
- `backend/app/services/lightning.py` — event-driven hot path: RingWindow
  O(1) circular buffers per symbol (50/250/1000), two-tier brain (fast
  filter skips quiet markets, deep ensemble only for survivors), priority
  event bus (P0 trade ... P5 logging), TradeLedger duplicate protection
  (CREATED->SUBMITTED->CONFIRMED/REJECTED/UNKNOWN, timeout => UNKNOWN,
  never blind-retry), failsafe (dead connection/stale feed/UNKNOWN
  executions block), latency profiler P50/P90/P95/P99, /lightning/*
  routes (dashboard, profiler, events, ledger, failsafe).
- `backend/app/services/eagle.py` — precision layer: three-horizon vision
  (EYE 500/1000, FOCUS 100/250/500, STRIKE 10/25/50), horizon agreement
  requires every horizon's mean edge >= max(min_edge, 0.05), probability
  consensus (raw/Bayesian/rolling/conditional, normalized variance),
  12-level signal stack, anti-overconfidence (uncertainty/contradiction
  ceilings), EntryPrecisionScore bands A+/A/B/C/<65 NO_TRADE, exact
  barrier ranking per family, false-positive hunting from the loss DB,
  precision scoreboard with grading-monotonicity check. /eagle/* routes.
- `backend/app/services/organism.py` — the conveyor-belt body: Data Armor
  -> Speed -> Vision -> Precision -> Competition -> Venom -> Strength ->
  Final Gate -> STRIKE|REJECT, driven by the ControlSpine state machine
  (OBSERVING..HARDENING, FAILURE->SAFE_STATE from anywhere). Immutable
  safety rules listed in spine_status. Per-stage tail profiling at
  /organism/performance; /organism/process pushes one tick through the
  whole body. Advisory-only: STRIKE emits an armed card, no broker call.
- Tests: test_lightning (14), test_eagle (13), test_organism (10).
  Suite: 336 passed.

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

## Permanent live-data fix (2026-08-25)

Root cause of the recurring DEMO DATA after restarts: Render's filesystem is
ephemeral, so the token vault file dies with every restart. Fixed permanently:

- `main.py _bootstrap_env_token()`: at boot, DERIV_API_TOKEN (+DERIV_PAT_APP_ID
  for pat_ tokens) is validated via the PAT REST flow and the vault is filled
  BEFORE the stream starts — first connection is live. Both env vars are set
  on the Render service (never in the repo).
- `deriv_client.stream_lifecycle`: with a token configured, demo ticks are
  NEVER emitted — failures give an honest "reconnecting to live feed" state
  with 15-20s re-probes. Demo only exists when there is no token at all.
- `deriv_trader._url`: PAT flow active + OTP mint failure raises instead of
  degrading to the geo-blocked generic endpoint (the silent demo slide).
- Verified: fresh deploy AND a hard restart both come up LIVE DATA with zero
  manual steps. Suite: 241 passed.

## CF never-stop architecture (2026-08-26)

Standing order: the CF must NEVER stop. Four failure classes found and fixed:

- **Vault self-wipe** (`token_vault.set`): `DerivClient.authorize()` re-saved
  the vault with only loginid/currency, wiping account_id/ws_url/app_id and
  the balance mid-session -> OTP mints failed -> live starts refused.
  `set()` now preserves PAT fields + balance when re-setting the SAME token;
  a different token still resets everything. `set_balance()` persists.
- **Fragile balance reads** (`deriv_trader.get_balance`): PAT tokens read the
  balance via the REST `/options/accounts` endpoint FIRST (pure HTTP, no OTP
  mint, no rate-limit exposure); websocket is fallback only. All failures are
  logged, never swallowed.
- **Killable loop** (`auto_trader._main_loop`): now a never-die wrapper
  around `_scan_session` — any exception is logged + throttled-alerted, the
  CF regroups 2s and re-enters. Only `stop()`, the kill switch, or task
  cancellation end the loop.
- **Death by risk stops**: session hard stops (drawdown %, consecutive
  losses, session time, daily trade cap) now pause 120s, rebase the session
  on the current balance, and play on. Guard violations (hourly cap, daily
  money limits, schedule) hold 60s and auto-resume when clear. The KILL
  SWITCH is the only guard verdict that still stops trading.
- **Deploy/restart survival**: `CF_AUTOSTART=live` (env) makes boot
  auto-resume the CF (15 attempts, 5s apart). `GUARD_STAKE_OVERRIDE=1`
  pins the $1 stake across restarts. Both set on the Render service.
  Verified live: deploy -> CF resumed by itself in live mode at $1 within
  ~60s, 23 trades @ $1 completed without a single stop.

Suite: 274 passed.
