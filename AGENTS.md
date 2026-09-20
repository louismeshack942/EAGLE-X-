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

- `twin/` — Next.js 14, `output: "export"` → static site in `twin/out`. This is
  the ONLY frontend (the old `frontend/` Starting-XI build was deleted).
  Pages: `/` landing, `/auth`, `/app` (the cockpit); all client components;
  API calls are plain relative fetches (same origin).
- `backend/` — FastAPI. `app/main.py` holds all routes; a catch-all HTTP
  middleware (`serve_frontend`) converts 404 GETs into SPA pages/static files
  when `FRONTEND_DIR` is set. API routes resolve first (routes win over the
  middleware). `/api/*` 404s stay JSON.
- `Dockerfile` (repo root) — multi-stage: node builds `twin/out`, poetry
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
`/club/news`, `/club/fans`, `/club/alerts`.
Tests: `test_club_endpoints` in `backend/tests/test_api.py`.


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


## External Shell + Certification harness (2026-08-26 directive #7)
- `backend/app/services/shell.py` — AuditLog (append-only, newest-first)
  and RealTradeBudget: REAL test trades must be exactly \$1, lifetime cap
  60 across everything; #60 sets REAL_TEST_EXECUTION_LOCKED until a human
  resets via /shell/certification/reset.
- Routes: /shell/ops-card (system+risk+speed+organism+ledger), /shell/audit,
  /shell/certification/{trade,report,reset}.
- `tests/test_shell.py` (12): chaos (bad digit armor, disconnect/stale
  failsafe, risk lock), recovery (ledger UNKNOWN), security (no secrets in
  responses, malformed input), budget math. Suite: 348 passed.
- Infra reality: Postgres/Redis stay unavailable on Render free tier;
  persistence is JSON files. WS routes are unauthenticated by design —
  add an auth gateway before multi-user exposure. No /api/v1 prefix; the
  whole API is same-origin on one service.


## Forge (2026-08-26) — venom + tanker completion
- `backend/app/services/forge.py` closes the unfinished directive pieces:
  `disaster_simulation` (§4), `self_destruct` (§5 overfit kill),
  `chaos_engine` (§17 safe degradation), `survivability` (§29 capital
  wall), `eagle_strength` (§32 0-100 Fortress..PRODUCTION PROHIBITED).
- Routes: /forge/{strength,survivability,self-destruct,disaster,chaos}.
- Tests: tests/test_forge.py (9). Suite: 357 passed.


## Rivalry — Self-Competitive / Limit-Breaking (2026-08-26)
- `backend/app/services/rivalry.py` — champion/challenger promotion via a
  no-lookahead walk-forward comparator: at each decision point the
  candidate is picked from digits BEFORE it and resolved on the NEXT
  tick. Promotion needs a better EAGLE_SCORE, robustness >= 50, and
  walk-forward consistency >= 0.5. Robustness = fold consistency +
  instability + window perturbation + shuffle dissonance.
- Routes: /rivalry/{status,generate,compete,tournament,adversarial,decay}.
  Tournament mines the journal per contract/market; adversarial mines
  for blind spots; decay emits GREEN/YELLOW/RED (+rollback at RED).
- Tests: tests/test_rivalry.py (9 incl. explicit no-lookahead regression).
  Suite: 366 passed.

## Commands

- Frontend build: `cd twin && npm run build` → `twin/out`
- Backend tests: `cd backend && ../backend/.venv/bin/python -m pytest tests/ -q` (51 tests)
- Run unified locally: `cd backend && FRONTEND_DIR=$PWD/../twin/out ../backend/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 12000`

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


## Cockpit strategy() bundle composer (2026-09-17)

`CockpitEngine.strategy()` in `backend/app/services/cockpit.py` sits between
`fbi()` and `_parse_duration()`. It composes a bundle of user predictions
(`[{"side": "UNDER", "barrier": 6}, {"side": "OVER", "barrier": 8},
{"side": "PRED", "barrier": 4}]`) into one advisory card.

- **No duplicated tape math.** It calls `self.fbi()` and reads only
  `over_digits` / `under_digits` / `n` / `provider`. Same live Deriv tape,
  same gates.
- **Winning-digit bands.** OVER b -> b+1..9, UNDER b -> 0..b-1,
  PRED/MATCHES/EXACT/DIGIT b -> `{b}`. `band_union` is their sorted union;
  `cover_all` is true when it spans all 10 digits.
- **Tighten, never widen.** A leg picks the best playable FBI row whose own
  band is a SUBSET of the requested band (OVER b accepts OVER d with d >= b;
  UNDER b accepts UNDER d with d <= b), max EV then edge_pp. A leg may
  execute a tighter, better-paying contract than asked - never a cheaper one.
  `leg["tightened"]` flags it.
- **Legs are always reported.** A leg with no playable candidate still
  appears (digit falls back to the barrier, `playable: false`) so
  `band_union` / `cover_all` cannot lie. OVER 9 and UNDER 0 are dropped -
  their band is empty, they win on nothing.
- **PRED legs are never playable.** No FBI barrier row prices an exact
  digit: MATCHES is a 10% lottery, so it is reported and benched.
- **Payout** = 10 / winning-digit-count (the same basis as the FBI
  breakeven): OVER 8 -> 10, UNDER 6 -> 10/6.
- **Verdict**: EDGE if any leg is playable; FAIR if `n == 0` (no live tape);
  TRAP only when the band union covers all 10 digits and nothing is playable.
- **Entry**: best playable leg by max EV, tie-break edge_pp, carrying leg /
  side / barrier / digit / confidence / observed_pct / breakeven_pct /
  edge_pp / ev / payout / available.
- **Martingale ladder**: capped doubling, `steps = min(requested, 5)` with a
  hard floor of 1, filtered to `<= budget` (falls back to `[base_stake]` when
  nothing fits). `recovery_step2 = base_stake * payout / (payout - 1)` only
  when a payout > 1 exists, else `None`; `capped` / `steps_capped` /
  `payout` (mirrors entry) report what happened. Capped only - never
  unlimited, and it never claims to create edge.
- **Advisory only.** Builds a card, places nothing - like every other
  cockpit method. No `place_payload` / `scheme_entry` in the output.

Route: `POST /cockpit/strategy/{symbol}` with
`{predictions, window, martingale_steps, base_stake, budget}` (all optional;
an empty body returns a clean FAIR card with no legs).

Tests: `tests/test_cockpit_strategy.py` (33 tests). Suite: 410 passed.

Note: `strategy()` did not exist at HEAD (`git show HEAD:...cockpit.py` has
zero `def strategy`), so the composer was added rather than repaired. The
block was spliced in with the marker-based approach (find `def strategy(`,
find the next `_parse_duration`, insert before its decorator). Two real bugs
were caught by the tests before commit: legs with no playable candidate were
being dropped entirely (hiding evidence and letting `band_union` / `cover_all`
lie), and `martingale.payout` reported `0.0` while `entry.payout` reported
`None`. Both fixed. Verified after splicing: zero non-ASCII and zero doubled
commas in the added lines (the 6/1 in cockpit.py are pre-existing baseline).

The deployed frontend is `twin/` (the Pro Trader twin) - the root Dockerfile
builds it in stage 1. It is now the ONLY frontend: the `frontend/` Starting-XI
build (and its video/learn pages) was deleted.

## Live-money cockpit (2026-09-20)

`twin/` could analyse but not trade — zero POST calls, no account UI. The
live path now exists end to end:

- `deriv_trader.get_proposal()` prices a contract WITHOUT buying it
  (payout / ask_price / implied multiple / breakeven). This is the number
  Deriv is actually paying; `DEFAULT_PAYOUTS` in pro_trader.py is only a
  guess and must not price a real order.
- Routes: `GET/POST/DELETE /live/account`, `GET /live/quote/{symbol}`.
  Connect reuses `auth._validate_token` (all-or-nothing — a bad token is
  never stored) and surfaces its failure as HTTP 400, not a 500.
  `GET /live/account` re-reads the balance from Deriv every call.
- `POST /trade` now REFUSES a VRTC (demo) account unless
  `allow_demo=true` — the owner trades real money, so a virtual account
  must never quietly book a pretend win. `TradeBody.allow_demo` is the
  escape hatch.
- `/live/` is in `_API_PREFIXES` so unknown `/live/*` stays JSON 404.
- twin's LIVE MONEY panel: connect, REAL MONEY/DEMO badge, live balance,
  stake/ticks, six manual contracts + "Trade FBI ENTRY", a confirmation
  dialog per order, settled P&L, and the live autopilot toggle.

**Known gap (deliberate):** the auto-trader's live path still sizes on the
assumed payout table, not `/live/quote`. Before trusting live autopilot,
wire proposals through the CF. Manual trading is the honest path today.

**Paper numbers are not evidence.** The 279-entry journal is 100% paper; the
paper simulator in `auto_trader._simulate_contract_outcome` adds the
strategy's *claimed* edge straight into the win probability, so it can
never discover a fake edge. Two payout inconsistencies live in that data:
`PAYOUTS` in market_master.py quotes plain OVER/UNDER at 1.94 while
`_digit_payout` (the repo's own formula, `(1-edge)/p_fair`) says OVER 2 =
1.41 and OVER 4 = 1.97. The journal also contains an OVER batch settled at
1.95 and a MATCHES batch settled at 1.10 (should be ~9). Do not read
profitability into any of it.

## Adviser-only autopilot (2026-09-20)

Owner's instruction: the auto-trader may analyse but MUST NOT place a real
trade, and is out of practical view. Enforced in depth — a hidden button
still leaves the route reachable.

- `settings.cf_adviser_only` (default **True**). Set `CF_ADVISER_ONLY=0`
  to re-arm.
- `POST /auto-trader/start` refuses `mode=live` outright.
- `auto_trader.place_trade` refuses at the point of order (last line of
  defence), so no route can reach the account.
- The pre-trade Telegram alert is suppressed in adviser mode — announcing a
  trade that will not happen is a lie.
- twin's autopilot toggle is removed.

This **supersedes the "CF never-stop" standing order for live execution**:
the CF still analyses continuously in adviser mode, but the earlier
"CF_AUTOSTART=live" auto-resume no longer results in trades. That is
deliberate and is the owner's current intent.

Two bugs found while wiring it (both real, both fixed):
- An ABORTED order was counted as a LOSS. `place_trade` returns
  `won=None` on abort, and `worst = "loss" if any(not o["won"])` reads
  `not None` as True. That corrupted the loss streak, the 2-loss bench
  trigger and the risk escalation — a refused/failed trade could bench the
  CF. Aborted orders are now excluded from settlement.
- The trade alert fired BEFORE the order was attempted, so a refused trade
  still broadcast as if placed.

**Sandbox caveat:** the dev sandbox runs in `us`, where Deriv geo-blocks
the symbol list, so the tape is DemoGenerator and the analytics there are
meaningless. That is the ENVIRONMENT, not the code — the Frankfurt service
streams real ticks. Do not judge the engine from a `us` sandbox.


## ANALYSIS-ONLY mode + the public feed (2026-09-20)

Owner's final instruction: **drop everything that needs a Deriv connection** —
no token, no account, no real money, analysis only. Supersedes adviser-only
(which still allowed a manual trade from the LIVE MONEY panel).

The unlock is a Deriv endpoint the repo had never used:

    wss://api.derivws.com/trading/v1/options/ws/public

**No authentication, no OTP, no token.** Verified from a `us` sandbox (the
same egress the generic endpoint geo-blocks): streams real R_100 ticks
(quote 589.55) and 41 open synthetic symbols — `active_symbols` there uses
`underlying_symbol` / `underlying_symbol_name`, not `symbol` / `display_name`.
This kills the geo-block problem AND the credential problem at once: the
deployment holds no secret, so there is nothing to leak or misuse, and the
endpoint cannot place an order — a structural limit, not a policy one.

- `settings.analysis_only` (default **True**; `ANALYSIS_ONLY=0` to re-enable
  trading). This is the top-level switch.
- `DerivClient._connect` uses `PUBLIC_WS_URL` when analysis-only; `stream()`
  skips `authorize()` entirely (the endpoint has no account session).
- `stream_lifecycle`: `token_configured = bool(await resolve_token()) or
  settings.analysis_only`. Without this the tokenless analysis box would fall
  back to `DemoGenerator` GBM ticks — **the exact fabricated-tape trap that
  produced the phantom edges**. Analysis-only is therefore ALWAYS live-only;
  an outage shows "reconnecting", never fake digits.
- Refused at every entry: `/trade`, `POST /auth/token`, `POST /live/account`,
  `_bootstrap_env_token` (no token is even loaded onto the box),
  `_autostart_cf`, `auto_trader.start`, and `deriv_trader.place_trade` (in
  front of the only `{"buy": ...}` send in the codebase).
- twin's LIVE MONEY panel removed; the cockpit is read-only.

**Kill switch is not enough on a stale deploy — and it is NOT durable.**
`POST /guard/kill` writes through `settings_store`, but that store is
`backend/data/store.json` inside the container. Render's filesystem is
ephemeral, so **every restart wipes the kill flag back to `killed: false`**.
Observed live: after a restart `/guard` returned `killed: false` and the CF
was back at `running: true, mode: live`. `auto_trader` does consult the guard
per scan (line ~828, `KILL_SWITCH` sets `running=False`), and the old code
does refuse live mode without a token — but on a service that already has
`DERIV_API_TOKEN` set, a cold boot with `CF_AUTOSTART=live` re-arms real
trading unattended. Only deploying this code fixes it.

**Emergency mitigation available without a deploy** (verified in `start()`):
clear `DERIV_API_TOKEN` (and `CF_AUTOSTART`) in the Render dashboard. The old
code's `start()` refuses live mode when no token resolves
(`"LIVE refused: no Deriv token connected"`), so the autostart ends up
started-but-not-trading. Belt and braces: also clear `DERIV_PAT_APP_ID`.

Deployed service `eaglex-backend-excn` (Render, frankfurt) had been trading
**real money**: balance ~$11,789.80, `trading_enabled: true`, two real trades
placed, both losses, CF benched. The kill switch was engaged there first.

Suite: 417 passed. `_patch.py` scratch file deleted.


## Band Predictor (2026-09-20) — OVER 3..UNDER 8, 68% floor, entry digit

Owner's spec: restrict OVER/UNDER to the middle band, attach the market's
ENTRY digit at that exact prediction, and require confidence > 68%. Worked
example: "over 3, entry digit 4, confidence 78%".

`CockpitEngine.band_predict()` (`backend/app/services/cockpit.py`), route
`GET /cockpit/band/{symbol}`, twin panel "BAND PREDICTOR · OVER 3 → UNDER 8".

- **Band:** barriers **3..8**, both sides (so it spans OVER 3 .. UNDER 8).
  Note UNDER 8 is the mirror of OVER 2, so taking both sides over 3..8 makes
  the range symmetric — the user's "over 3 to under 8" read literally.
- **Payout 10/winning-digits.** OVER 3 wins 4..9 (6 digits) -> 1.67x,
  breakeven 60%. UNDER 8 wins 0..7 (8 digits) -> 1.25x, breakeven 80%. The
  per-row `breakeven_pct` is the real bar; `BREAKEVEN_PCT` (90) is the Digit
  Differs bar and is NOT what OVER/UNDER is judged against.
- **Confidence = Wilson lower bound (95%)** of the observed band rate. This
  is deliberately conservative: the owner said 78%, and raw-vs-Wilson diverge
  a lot below n~200, so raw would have been the misleading number.
- **Gate:** `confidence >= 68% AND ev > 0`. On a fair tape nothing clears it
  and the verdict is FAIR — no trade, which is the correct answer.
- **ENTRY digit = the winning digit adjacent to the barrier**: OVER b -> b+1,
  UNDER b -> b-1. OVER 3 -> digit 4, exactly the owner's example. It is
  reported with its own tape share, and `inside` is False when that single
  digit is under 50% (an over 3 band can be 78% while digit 4 is only 12% —
  the band carries the edge, not the one digit). This is surfaced, never
  hidden.
- **Ranking:** by confidence, tie-break EV. A "guard the user asked for"
  (all barriers from b to the edge must also clear) was tried and REMOVED:
  it is unsatisfiable, because OVER 9 has zero winning digits, so no OVER
  band can ever be "closed". Do not re-add it.

Verified live: `n=136 · UNDER 8 · 83.5% confidence · ENTRY digit 7 · EV +$0.121`.

Tests: `tests/test_cockpit_band.py` (33 tests) incl. the owner's worked
example pinned to observed 85% -> confidence ~78%. Suite: 450 passed.

Still advisory — it builds a card and places nothing, like every cockpit
method.
