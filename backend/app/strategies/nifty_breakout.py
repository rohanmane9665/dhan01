import logging
import pandas as pd
from typing import Optional, Dict, Any, Tuple
from app.strategies.base import BaseStrategy
from app.strategies.models import Signal
import numpy as np

logger = logging.getLogger(__name__)

# Fallback python RSI in case TA-Lib is absent
def _rsi_python(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    
    avg_gain = pd.Series(index=series.index, dtype=float)
    avg_loss = pd.Series(index=series.index, dtype=float)
    
    if len(series) > period:
        avg_gain.iloc[period] = gain.iloc[1:period+1].mean()
        avg_loss.iloc[period] = loss.iloc[1:period+1].mean()
        
        for i in range(period + 1, len(series)):
            avg_gain.iloc[i] = (avg_gain.iloc[i-1] * (period - 1) + gain.iloc[i]) / period
            avg_loss.iloc[i] = (avg_loss.iloc[i-1] * (period - 1) + loss.iloc[i]) / period
            
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    rsi = 100 - (100 / (1 + rs))
    return rsi

class NiftyBreakoutStrategy(BaseStrategy):
    """
    Evaluates 5-minute NIFTY index candles for the C1-C5 and C12 pattern.
    If conditions are met, it returns the B1 Low price to trigger the breakout monitor state.
    """
    def __init__(self, rsi_period: int = 14):
        self.rsi_period = rsi_period

    def calculate_rsi(self, prices: pd.Series) -> Optional[pd.Series]:
        if len(prices) < self.rsi_period + 1:
            return None
        return _rsi_python(prices, self.rsi_period)

    def evaluate(self, df: pd.DataFrame) -> Tuple[bool, Optional[float], Dict[str, Any]]:
        """
        Returns:
            bool: True if pattern formed
            float: B1 low price (target for breakout)
            dict: condition debug values
        """
        if df.empty or len(df) < 3:
            return False, None, {}

        # The user looks at B1 (iloc[-3]) and B2 (iloc[-2])
        # The currently forming candle is iloc[-1]
        B1 = df.iloc[-3]
        B2 = df.iloc[-2]

        # Calculate RSI on closes up to B2
        closes_upto_b2 = df.iloc[:-1]['close']
        
        if len(closes_upto_b2) >= 15:
            rsi_series = self.calculate_rsi(closes_upto_b2)
            b2_rsi = rsi_series.iloc[-1] if rsi_series is not None else None
            C12 = (b2_rsi > 40) if b2_rsi is not None else False
        else:
            b2_rsi = None
            C12 = False

        c_vals = {
            'C1': B1['close'] > B1['open'],
            'C2': B2['close'] < B2['open'],
            'C3': B2['high'] > B1['high'],
            'C4': B1['low'] < B2['low'],
            'C5': (B2['high'] - B1['low']) < 22,
            'C12': C12,
            'b2_rsi': b2_rsi,
            'b1_low': B1['low']
        }

        pattern_formed = all([
            c_vals['C1'], c_vals['C2'], c_vals['C3'], 
            c_vals['C4'], c_vals['C5'], c_vals['C12']
        ])

        return pattern_formed, B1['low'], c_vals
