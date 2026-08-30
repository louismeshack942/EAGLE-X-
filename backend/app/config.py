from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """EAGLE-X application settings, loaded from environment (see .env.example).

    Secrets are read from env vars only. Never commit the .env file.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ---- App ----
    app_name: str = "EAGLE-X"
    env: str = "development"  # development | test | production
    debug: bool = False
    allowed_origins: str = "http://localhost:3000"  # comma-separated

    # ---- Database ----
    # production example: postgresql+psycopg://user:pass@host:5432/eaglex
    database_url: str = "sqlite:///./eaglex_dev.db"

    # ---- Redis ----
    redis_url: str = "redis://localhost:6379/0"

    # ---- Frontend ----
    # Path to the exported Next.js build (`frontend/out`). Empty => API only.
    frontend_dir: str = ""

    # ---- Deriv OAuth2 (set real values in production .env) ----
    deriv_oauth_client_id: str = ""
    deriv_oauth_client_secret: str = ""
    deriv_oauth_redirect_uri: str = "http://localhost:8000/auth/deriv/callback"
    deriv_oauth_authorize_url: str = "https://oauth.deriv.com/oauth2/authorize"
    deriv_oauth_token_url: str = "https://api.deriv.com/oauth2/token"
    # Public (unauthenticated) endpoints use different base:
    deriv_ws_url: str = "wss://ws.derivws.com/websockets/v3"
    deriv_rest_base: str = "https://api.derivws.com/trading/v1"
    # Fall back to the public endpoint when OAuth not configured:
    use_unauth_public_data: bool = True
    # Live PAT token (env-configured, never in repo) — the session validates and
    # stores it in the 0600 vault; DERIV_PAT_APP_ID is required for pat_ tokens.
    deriv_api_token: str = ""
    deriv_pat_app_id: str = ""

    # ---- Security ----
    # Session signing key. In production set a strong random value via env.
    secret_key: str = "dev-only-change-me-eaglex-0123456789abcdef"
    session_ttl_seconds: int = 3600 * 12
    cookie_secure: bool = False  # True when served over HTTPS in production

    # ================= Phase 4 — signal engine ================================
    # All thresholds are configurable, documented, and default conservative (NO TRADE).
    signal_min_sample: int = 40                       # min observations for a probability estimate
    signal_min_edge_pp: float = 2.0                   # min observed-vs-breakeven margin (percentage points)
    signal_min_ev: float = 0.0                        # EV must be strictly > this to qualify
    signal_max_stale_age_secs: float = 15.0           # ticks older than this => STALE
    signal_max_proposal_age_secs: float = 30.0        # proposal quote older than this => stale
    signal_max_lifetime_secs: float = 60.0            # max age of an executable signal
    signal_required_multi_window: str = "SUPPORTED"   # STABLE|SUPPORTED min agreement to qualify
    signal_beta_alpha0: float = 1.0                   # Beta prior alpha0 (<= 1 => conservative)
    signal_beta_beta0: float = 1.0                    # Beta prior beta0
    signal_max_open: int = 1                          # max concurrently open trades
    # ---- risk thresholds (Phase 4 §9 / Phase 5 §32) ----
    risk_max_stake: float = 5.0                       # per-trade stake cap (real $1 default; see LIVE stake)
    risk_min_stake: float = 0.1
    risk_daily_loss_limit: float = 10.0               # max daily realized loss (USD)
    risk_session_loss_limit: float = 10.0
    risk_max_consecutive_losses: int = 3
    risk_max_daily_loss_in_pct: float = 25.0          # % of starting session bankroll
    risk_cooldown_after_loss_secs: float = 30.0
    risk_cooldown_after_error_secs: float = 60.0
    risk_min_reserve: float = 5.0                     # min balance that must remain after stake

    # ================= Phase 5 — execution =====================================
    execution_mode_default: str = "HARNESS"           # HARNESS | PAPER | LIVE
    execution_live_enabled: bool = False              # THE master live-money switch. server-side only.
    execution_paper_enabled: bool = True
    execution_max_trades_per_session: int = 50
    execution_max_trades_per_day: int = 200
    execution_max_open: int = 1
    execution_confirm_timeout_secs: float = 15.0
    execution_reconcile_poll_secs: float = 2.0
    # The maximum stake the system is allowed to send to Deriv when a live purchase is
    # ever enabled. Development/real "$" stake remains DISABLED by adding the
    # EXPLICIT gate `execution_live_enabled`. Live stake forced tiny (1 $) until disabled.
    live_stake_max: float = 1.0
    # MARTINGALE IS PROHIBITED. There is intentionally NO config for auto stake growth.
    balance_provider_enabled: bool = True

    # ================= Phase 6 — automated trader =============================
    # The automated trader is a CLIENT of the Phase 4/5 pipeline. It never bypasses
    # signal/risk/execution. Defaults are DISABLED and conservative (NO TRADE).
    automation_enabled: bool = False              # master switch: OFF by default
    automation_mode_default: str = "OFF"          # OFF | MONITOR | PAPER | LIVE
    automation_max_trades_per_session: int = 20
    automation_max_trades_per_day: int = 50
    automation_max_open: int = 1                  # never exceed execution_max_open
    automation_max_daily_loss: float = 5.0
    automation_max_session_loss: float = 5.0
    automation_max_consecutive_losses: int = 3
    automation_cooldown_secs: float = 30.0        # after a loss / error
    automation_max_signal_age_secs: float = 5.0   # revalidate before every execution
    automation_min_signal_quality: float = 60.0   # floor on signal composite quality (0-100)
    automation_scan_interval_secs: float = 1.0    # min gap between scan cycles per symbol
    automation_max_stake: float = 1.0             # capped by risk_max_stake & live_stake_max
    automation_min_stake: float = 0.1
    automation_allowed_markets: str = "R_10,R_25,R_50,R_75,R_100"  # comma-separated
    automation_allowed_families: str = "MATCHES,OVER,UNDER,ODD,EVEN,DIFFERS"
    # NOTE: automation can NEVER enable execution_live_enabled itself. That master
    # switch remains server-side ONLY, off by default.

    # ---- CORS ----
    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def oauth_configured(self) -> bool:
        return bool(self.deriv_oauth_client_id and self.deriv_oauth_client_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()