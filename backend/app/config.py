"""EAGLE-X configuration — all settings come from environment variables."""
from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"
    secret_key: str = "dev-secret-change-me"

    # Deriv
    deriv_ws_url: str = "wss://ws.derivws.com/websockets/v3"
    deriv_app_id: int = 1089
    deriv_api_token: str = ""
    # Full digit-trading board: every synthetic that offers digit contracts.
    # A wider board means more tables the truth gate can find a real skew on.
    deriv_active_symbols: str = (
        "R_10,R_25,R_50,R_75,R_100,"
        "1HZ10V,1HZ25V,1HZ30V,1HZ50V,1HZ75V,"
        "1HZ100V,1HZ150V,1HZ200V,1HZ250V,1HZ300V"
    )
    # OAuth: public client, no secret. Register your own app at api.deriv.com
    # and set DERIV_APP_ID for a branded OAuth screen.
    deriv_oauth_url: str = "https://auth.deriv.com/oauth2/auth"
    deriv_client_secret: str = ""   # needed for modern OAuth code exchange
    deriv_rest_base: str = "https://api.derivws.com/trading/v1"  # modern REST base (PAT flow)
    frontend_url: str = ""
    # Directory of the statically-exported frontend, served by this same app.
    frontend_dir: str = ""

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "eaglex"
    postgres_user: str = "eaglex_user"
    postgres_password: str = ""
    database_available: bool = False

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_available: bool = False

    # Demo generator
    demo_seed: int = 42
    demo_start_price: float = 100.0
    demo_drift: float = 0.0001
    demo_volatility: float = 0.001
    demo_tick_interval_ms: int = 100

    # Notifications
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # AI Copilot
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # Tick buffer
    max_ticks_per_symbol: int = 2000
    tick_reconnect_max_attempts: int = 3

    @property
    def active_symbols(self) -> List[str]:
        return [s.strip() for s in self.deriv_active_symbols.split(",") if s.strip()]

    @property
    def has_deriv_token(self) -> bool:
        return bool(self.deriv_api_token.strip())


@lru_cache()
def get_settings() -> Settings:
    return Settings()
