import pytest
import pandas as pd
from app.strategies.option_rsi import OptionRSIStrategy

def test_strategy_conditions():
    strategy = OptionRSIStrategy(rsi_period=14)
    df = pd.DataFrame({'close': [100] * 5, 'high': [100]*5, 'low': [100]*5, 'open': [100]*5})
    # Needs at least 18 candles (14 period + 4)
    market_data = {"option_df": df, "sensex_price": 72000, "option_ltp": 100.0}
    assert strategy.evaluate(market_data) is None
