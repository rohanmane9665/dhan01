import sys
from pathlib import Path
import pytest
from pydantic import ValidationError

# Ensure backend root and app directory are in sys.path
backend_dir = Path(__file__).resolve().parents[1]
project_root = Path(__file__).resolve().parents[2]
for p in (str(backend_dir), str(project_root)):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from app.core.config import Settings, validate_startup_config
except ImportError:
    from backend.app.core.config import Settings, validate_startup_config


def test_1_default_configuration_mode():
    """Test 1: Default configuration defaults to PAPER mode and ENABLE_LIVE_TRADING=False."""
    settings = Settings(
        _env_files=(),  # Ignore external .env
        DHAN_CLIENT_ID="",
        DHAN_ACCESS_TOKEN=""
    )
    assert settings.TRADING_MODE.upper() == "PAPER"
    assert settings.ENABLE_LIVE_TRADING is False


def test_2_valid_paper_mode_configuration():
    """Test 2: TRADING_MODE=PAPER and ENABLE_LIVE_TRADING=False is valid."""
    settings = Settings(
        _env_files=(),
        TRADING_MODE="PAPER",
        ENABLE_LIVE_TRADING=False
    )
    assert settings.TRADING_MODE == "PAPER"
    assert settings.ENABLE_LIVE_TRADING is False


def test_3_live_mode_without_explicit_enable_fails():
    """Test 3: TRADING_MODE=LIVE and ENABLE_LIVE_TRADING=False must fail fast."""
    with pytest.raises((ValidationError, ValueError)) as exc_info:
        Settings(
            _env_files=(),
            TRADING_MODE="LIVE",
            ENABLE_LIVE_TRADING=False,
            DHAN_CLIENT_ID="123",
            DHAN_ACCESS_TOKEN="abc"
        )
    assert "LIVE trading is disabled" in str(exc_info.value)


def test_4_live_mode_without_credentials_fails():
    """Test 4: TRADING_MODE=LIVE and ENABLE_LIVE_TRADING=True without credentials must fail fast."""
    with pytest.raises((ValidationError, ValueError)) as exc_info:
        Settings(
            _env_files=(),
            TRADING_MODE="LIVE",
            ENABLE_LIVE_TRADING=True,
            DHAN_CLIENT_ID="",
            DHAN_ACCESS_TOKEN=""
        )
    assert "DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN are required" in str(exc_info.value)


def test_5_valid_live_mode_configuration_accepted():
    """Test 5: LIVE mode + dual enablement + valid credentials is accepted."""
    settings = Settings(
        _env_files=(),
        TRADING_MODE="LIVE",
        ENABLE_LIVE_TRADING=True,
        DHAN_CLIENT_ID="1100996819",
        DHAN_ACCESS_TOKEN="valid_jwt_token_sample"
    )
    assert settings.TRADING_MODE == "LIVE"
    assert settings.ENABLE_LIVE_TRADING is True
    assert settings.DHAN_CLIENT_ID == "1100996819"
