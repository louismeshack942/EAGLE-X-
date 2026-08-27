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

    # ---- Security ----
    # Session signing key. In production set a strong random value via env.
    secret_key: str = "dev-only-change-me-eaglex-0123456789abcdef"
    session_ttl_seconds: int = 3600 * 12
    cookie_secure: bool = False  # True when served over HTTPS in production

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