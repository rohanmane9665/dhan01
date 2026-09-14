import pytest
import pandas as pd
import numpy as np
from datetime import datetime, time
import pytz
from app.strategies.option_rsi import OptionRSIStrategy

IST = pytz.timezone("Asia/Kolkata")

def create_mock_dataframe(closes) -> pd.DataFrame:
    """Helper to create a simple OHLC dataframe."""
    return pd.DataFrame({
        "timestamp": [datetime.now(IST)] * len(closes),
        "open": closes,
        "high": closes + 5,
        "low": closes - 5,
        "close": closes,
        "volume": [1000] * len(closes)
    })

def test_legacy_rsi_breakout_exact_match():
    """
    STRICT REGRESSION TEST:
    Ensures that OptionRSIStrategy perfectly matches the legacy Option_RSI_strat.py behavior.
    
    Legacy Rule 1: RSI[-4] < 60
    Legacy Rule 2: RSI[-3] < 60
    Legacy Rule 3: RSI[-2] > 60
    Legacy Rule 4: LTP > High[-2]
    Legacy SL: Low[-2]
    """
    strategy = OptionRSIStrategy(rsi_period=14, signal_gap_minutes=0)
    
    # We mock calculate_rsi to return exact values to isolate the logic testing
    strategy.calculate_rsi = lambda closes: np.array(
        [50.0] * (len(closes) - 4) + [59.0, 58.0, 61.0, 62.0]
    )
    
    # Create 20 candles
    df = create_mock_dataframe(np.arange(100, 120))
    # df.iloc[-2]["high"] = 118 + 5 = 123
    # df.iloc[-2]["low"] = 118 - 5 = 113
    
    # Test case 1: Perfect match
    market_data = {
        "sensex_price": 72050,
        "option_type": "CE",
        "option_df": df,
        "option_ltp": 125.0, # > 123 (High[-2])
        "quantity": 15
    }
    
    # Fake time to 10:00 AM (valid trading time)
    now = datetime.now(IST).replace(hour=10, minute=0)
    
    signal = strategy.evaluate(market_data)
    assert signal is not None
    assert signal.direction == "BUY"
    assert signal.option_type == "CE"
    assert signal.entry_reference == 125.0
    assert signal.stop_loss_reference == 113.0 # df.iloc[-2]['low']
    assert signal.symbol == "SENSEX 72100 CE"  # 72050 -> 72100
    assert "BreakoutConfirmed" in signal.signal_reason
    
    # Test case 2: LTP <= High[-2]
    market_data["option_ltp"] = 123.0
    assert strategy.evaluate(market_data) is None
    
    # Test case 3: RSI[-4] >= 60
    strategy.calculate_rsi = lambda closes: np.array([50.0] * 16 + [60.5, 58.0, 61.0, 62.0])
    market_data["option_ltp"] = 125.0
    assert strategy.evaluate(market_data) is None
    
    # Test case 4: Time past 14:40
    strategy.no_fresh_trade_time = time(0, 1) # Artificially make current time > cutoff
    assert strategy.evaluate(market_data) is None
