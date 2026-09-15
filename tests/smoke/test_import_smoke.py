"""
smoke/test_import_smoke.py
===========================
Smoke tests — verify all critical modules import without error.
No external services required.
"""

import pytest


def test_import_config():
    from app.core.config import Settings
    assert Settings is not None


def test_import_risk_manager():
    from app.risk.manager import RiskManager
    from app.risk.kill_switch import KillSwitch
    rm = RiskManager(kill_switch=KillSwitch())
    assert not rm.kill_switch_active


def test_import_strategy():
    pytest.importorskip("talib", reason="TA-Lib C library not installed — install from https://ta-lib.org/")
    from app.strategies.option_rsi import OptionRSIStrategy
    s = OptionRSIStrategy()
    assert s.rsi_period == 14


def test_import_candle_engine():
    from app.market.candle_engine import CandleEngine
    c = CandleEngine(interval_minutes=15)
    assert c.interval_minutes == 15


def test_import_paper_broker():
    from app.dhan.adapter import PaperBrokerAdapter
    b = PaperBrokerAdapter()
    assert b._connected is False


def test_import_execution_engine():
    from app.dhan.adapter import PaperBrokerAdapter
    from app.execution.engine import ExecutionEngine
    from app.execution.position_manager import PositionManager
    from app.risk.manager import RiskManager
    b = PaperBrokerAdapter()
    rm = RiskManager()
    pm = PositionManager()
    ee = ExecutionEngine(b, rm, pm)
    assert ee is not None


def test_import_validator():
    from app.market.validator import DataValidator
    v = DataValidator(stale_seconds=3)
    ok, msg = v.validate(100.0)
    assert ok


def test_import_reconciliation():
    from app.dhan.adapter import PaperBrokerAdapter
    from app.execution.position_manager import PositionManager
    from app.reconciliation.service import ReconciliationService
    b = PaperBrokerAdapter()
    pm = PositionManager()
    r = ReconciliationService(b, pm)
    assert not r.safe_mode


def test_import_market_calendar():
    from app.scheduler.market_calendar import MarketCalendar
    cal = MarketCalendar()
    from datetime import date
    # 2026-09-15 should NOT be a holiday (it's a Tuesday)
    assert cal.is_trading_day(date(2026, 9, 15))
    # Weekends should not be trading days
    assert not cal.is_trading_day(date(2026, 9, 13))  # Sunday
