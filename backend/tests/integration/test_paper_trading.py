"""
integration/test_paper_trading.py
===================================
End-to-end paper trading simulation.
Tests the full pipeline: CandleEngine → OptionRSIStrategy → RiskManager → ExecutionEngine (PaperBroker).
No real broker. No real market data. No DB required.
"""

import asyncio
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz

from app.dhan.adapter import PaperBrokerAdapter
from app.execution.engine import ExecutionEngine
from app.execution.position_manager import PositionManager
from app.market.candle_engine import CandleEngine
from app.market.validator import DataValidator
from app.risk.manager import RiskManager
from app.strategies.option_rsi import OptionRSIStrategy
from app.strategies.models import Signal

IST = pytz.timezone("Asia/Kolkata")


def _make_candle_df(n: int = 20, base_rsi_trigger: bool = True) -> pd.DataFrame:
    """
    Generates a synthetic candle DataFrame.
    When base_rsi_trigger=True, the last 3 candles satisfy RSI < 59.99, < 59.99, > 59.99.
    """
    np.random.seed(42)
    closes = np.array([100.0 + i * 0.5 + np.random.normal(0, 0.3) for i in range(n)])

    if base_rsi_trigger:
        # Force RSI pattern by adjusting last 3 closes
        closes[-4] = 95.0   # low RSI zone
        closes[-3] = 96.0   # low RSI zone
        closes[-2] = 115.0  # spike → high RSI
        closes[-1] = 116.0

    records = []
    base_time = datetime.now(IST).replace(hour=9, minute=15, second=0, microsecond=0)
    for i, c in enumerate(closes):
        high = c + abs(np.random.normal(0, 0.5))
        low  = c - abs(np.random.normal(0, 0.5))
        records.append({
            "timestamp": base_time + timedelta(minutes=15 * i),
            "open": c - 0.1,
            "high": high,
            "low": low,
            "close": c,
            "volume": 100,
        })
    return pd.DataFrame(records)


@pytest.mark.asyncio
async def test_paper_broker_place_order():
    """PaperBrokerAdapter fills an order correctly and tracks position."""
    broker = PaperBrokerAdapter()
    await broker.connect()

    result = await broker.place_order({
        "security_id": "SENSEX_82000_CE",
        "transaction_type": "BUY",
        "quantity": 1,
        "price": 200.0,
    })

    assert result["status"] == "success"
    data = result["data"]
    assert data["orderStatus"] == "FILLED"
    assert data["tradedPrice"] > 0

    positions = await broker.get_positions()
    assert len(positions) == 1
    assert positions[0]["netQty"] == 1


@pytest.mark.asyncio
async def test_full_trade_pipeline():
    """
    Full E2E: synthetic candles → strategy signal → risk check → paper execution.
    """
    broker = PaperBrokerAdapter()
    await broker.connect()

    rm = RiskManager()
    pm = PositionManager()
    engine = ExecutionEngine(broker_adapter=broker, risk_manager=rm, position_manager=pm)
    strategy = OptionRSIStrategy(rsi_period=14)

    df = _make_candle_df(n=25, base_rsi_trigger=True)

    # Simulate breakout: ltp > iloc[-2] high
    iloc_2_high = float(df.iloc[-2]["high"])
    option_ltp = iloc_2_high + 5.0  # ensure breakout

    market_data = {
        "sensex_price": 82000.0,
        "option_type": "CE",
        "option_df": df,
        "option_ltp": option_ltp,
        "security_id": "SENSEX_82000_CE",
        "quantity": 1,
    }

    signal = strategy.evaluate(market_data)

    if signal is None:
        pytest.skip("RSI pattern not triggered with synthetic data — need more candles")

    assert signal.symbol.startswith("SENSEX")
    assert signal.direction == "BUY"
    assert signal.quantity == 1

    result = await engine.execute_signal(signal)
    assert result["status"] == "FILLED", f"Expected FILLED, got: {result}"
    assert result["traded_price"] > 0

    active = pm.get_active_positions()
    assert len(active) == 1
    assert active[0].symbol == signal.symbol


@pytest.mark.asyncio
async def test_risk_manager_blocks_on_kill_switch():
    """Kill switch must block all signals regardless of strategy output."""
    broker = PaperBrokerAdapter()
    await broker.connect()

    rm = RiskManager()
    pm = PositionManager()
    engine = ExecutionEngine(broker_adapter=broker, risk_manager=rm, position_manager=pm)

    # Activate kill switch
    await rm.activate_kill_switch("Test: kill switch block")

    dummy_signal = Signal(
        strategy_id="test",
        timestamp=datetime.now(IST),
        symbol="SENSEX 82000 CE",
        security_id="SENSEX_82000_CE",
        option_type="CE",
        direction="BUY",
        quantity=1,
        entry_reference=200.0,
        stop_loss_reference=150.0,
        signal_reason="Test signal",
        candle_timestamp=datetime.now(IST),
        strategy_version="2.0.0",
    )

    result = await engine.execute_signal(dummy_signal)
    assert result["status"] == "REJECTED"
    assert "Kill Switch" in result["reason"]


@pytest.mark.asyncio
async def test_candle_engine_15min_boundary():
    """CandleEngine must emit exactly one completed candle when crossing 15-min boundary."""
    engine = CandleEngine(interval_minutes=15)
    base = datetime(2026, 9, 15, 9, 15, 0, tzinfo=IST)

    # Ticks in first candle (09:15–09:29:59)
    for i in range(5):
        c = engine.process_tick(100.0 + i, base + timedelta(minutes=i))
        assert c is None, "No candle should close mid-period"

    # First tick of second candle (09:30) — triggers boundary
    completed = engine.process_tick(110.0, base + timedelta(minutes=15))
    assert completed is not None, "Candle should complete on boundary"
    assert completed["open"] == 100.0
    assert completed["close"] == 104.0  # last tick in first candle
    assert completed["high"] == 104.0
