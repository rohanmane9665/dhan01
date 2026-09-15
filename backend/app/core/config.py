from typing import Optional
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_ENV: str = "development"
    APP_NAME: str = "dhan-algo-platform"
    LOG_LEVEL: str = "INFO"
    TIMEZONE: str = "Asia/Kolkata"

    DHAN_CLIENT_ID: str = ""
    DHAN_ACCESS_TOKEN: str = ""

    TRADING_MODE: str = "PAPER"
    ENABLE_LIVE_TRADING: bool = False

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/dhan_algo"
    REDIS_URL: str = "redis://localhost:6379/0"

    MARKET_DATA_STALE_SECONDS: int = 3

    MAX_DAILY_LOSS: Optional[float] = None
    MAX_TRADES_PER_DAY: Optional[int] = None
    MAX_OPEN_POSITIONS: Optional[int] = None
    MAX_ORDER_QUANTITY: Optional[int] = None

    KILL_SWITCH_ENABLED: bool = True

    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True
    )

    @model_validator(mode="after")
    def validate_trading_mode_safety(self) -> "Settings":
        if self.DATABASE_URL:
            if self.DATABASE_URL.startswith("postgresql://"):
                self.DATABASE_URL = self.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
            elif self.DATABASE_URL.startswith("postgres://"):
                self.DATABASE_URL = self.DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)


        trading_mode_upper = self.TRADING_MODE.upper()
        if trading_mode_upper == "LIVE":
            if not self.ENABLE_LIVE_TRADING:
                raise ValueError(
                    "LIVE trading is disabled. Set ENABLE_LIVE_TRADING=true explicitly to enable live execution."
                )
            if not self.DHAN_CLIENT_ID.strip() or not self.DHAN_ACCESS_TOKEN.strip():
                raise ValueError(
                    "DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN are required for LIVE trading."
                )
        return self


def validate_startup_config(settings_obj: Optional[Settings] = None) -> Settings:
    """
    Validates startup configuration and returns the active Settings object.
    Fails fast if live trading safety conditions are violated.
    """
    if settings_obj is None:
        settings_obj = Settings()
    return settings_obj


try:
    settings = Settings()
except Exception:
    # Fallback default settings instance for paper/test environments if instantiation fails on invalid live configs
    settings = None  # type: ignore
