import logging
import math
import pytz
import numpy as np
import pandas as pd
from datetime import datetime, time
from typing import Optional, Dict, Any, Tuple

from app.strategies.base import BaseStrategy
from app.strategies.models import Signal

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# ── TA-Lib is an optional C-binary dependency.
# ── If not installed, fall back to pure-Python Wilder's RSI (identical output).
try:
    import talib as _talib
    _TALIB_AVAILABLE = True
    logger.debug("TA-Lib loaded — using C-accelerated RSI.")
except ImportError:
    _talib = None
    _TALIB_AVAILABLE = False
    logger.warning(
        "TA-Lib not installed. Using pure-Python RSI fallback. "
        "Install TA-Lib for production: https://ta-lib.org/"
    )


def _rsi_python(prices: np.ndarray, period: int) -> np.ndarray:
    """
    Wilder's RSI — numerically identical to TA-Lib's RSI implementation.
    Returns an array of the same length as `prices`; first `period` values are NaN.
    """
    prices = prices.astype(float)
    n = len(prices)
    rsi = np.full(n, np.nan)
    if n < period + 1:
        return rsi

    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # Initial averages (simple mean over first `period` bars)
    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()

    for i in range(period, n - 1):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i + 1] = 100.0 - (100.0 / (1.0 + rs))

    return rsi


class OptionRSIStrategy(BaseStrategy):
    """
    SENSEX Options 15-Minute RSI Breakout Strategy.
    Preserves exact quantitative rules from legacy Option_RSI_strat.py.
    """

    def __init__(self, rsi_period: int = 14, signal_gap_minutes: int = 15):
        self.strategy_id = "option_rsi_sensex"
        self.rsi_period = rsi_period
        self.signal_gap_minutes = signal_gap_minutes
        self.last_signal_time: Optional[datetime] = None
        self.no_fresh_trade_time = time(14, 40)
        self.market_close_time = time(15, 25)

    def convert_to_strike_price_for_sensex(self, sensex_price: float) -> int:
        """Rounds SENSEX index price up to nearest 100 multiple for strike price."""
        return math.ceil(sensex_price / 100) * 100

    def calculate_rsi(self, prices: np.ndarray) -> Optional[np.ndarray]:
        """Calculates RSI. Uses TA-Lib when available, else pure-Python Wilder's RSI."""
        if len(prices) < self.rsi_period + 1:
            return None
        try:
            if _TALIB_AVAILABLE:
                return _talib.RSI(prices.astype(float), timeperiod=self.rsi_period)
            else:
                return _rsi_python(prices, self.rsi_period)
        except Exception as e:
            logger.error(f"RSI calculation error: {e}")
            return None

    def check_rsi_pattern(self, df: pd.DataFrame) -> Tuple[bool, str]:
        """
        Checks 15-minute candle RSI pattern sequence:
        RSI[iloc[-4]] < 59.99
        RSI[iloc[-3]] < 59.99
        RSI[iloc[-2]] > 59.99
        """
        min_required = max(10, self.rsi_period + 4)
        if len(df) < min_required:
            return False, f"Insufficient candles: {len(df)} < {min_required}"

        closes = df["close"].values
        rsi_values = self.calculate_rsi(closes)
        if rsi_values is None or len(rsi_values) < 4:
            return False, "Insufficient RSI series data"

        rsi_4 = rsi_values[-4]
        rsi_3 = rsi_values[-3]
        rsi_2 = rsi_values[-2]

        cond1 = rsi_4 < 59.99
        cond2 = rsi_3 < 59.99
        cond3 = rsi_2 > 59.99

        pattern_met = cond1 and cond2 and cond3
        reason = f"RSI[-4]:{rsi_4:.2f}, RSI[-3]:{rsi_3:.2f}, RSI[-2]:{rsi_2:.2f}"
        return pattern_met, reason

    def evaluate(self, market_data: Dict[str, Any]) -> Optional[Signal]:
        """
        Evaluates market data. Returns structured Signal if entry pattern and conditions match.
        """
        now = datetime.now(IST)
        current_time = now.time()

        # Check trading hours cutoff
        if current_time >= self.no_fresh_trade_time:
            return None

        # Check signal gap constraint
        if self.last_signal_time:
            diff_min = (now - self.last_signal_time).total_seconds() / 60.0
            if diff_min < self.signal_gap_minutes:
                return None

        sensex_price = market_data.get("sensex_price", 0.0)
        if sensex_price <= 0:
            return None

        option_type = market_data.get("option_type", "PE")
        option_df = market_data.get("option_df")
        if option_df is None or not isinstance(option_df, pd.DataFrame) or len(option_df) < 3:
            return None

        pattern_met, reason = self.check_rsi_pattern(option_df)
        if not pattern_met:
            return None

        # Check Breakout Condition: Current Option Price > iloc[-2] High
        option_ltp = market_data.get("option_ltp", 0.0)
        iloc_2_high = option_df.iloc[-2]["high"]
        if option_ltp <= iloc_2_high:
            return None

        # Calculate Initial Stop Loss from iloc[-2] Low
        initial_sl = float(option_df.iloc[-2]["low"])
        strike = self.convert_to_strike_price_for_sensex(sensex_price)
        security_id = market_data.get("security_id", f"SENSEX_{strike}_{option_type}")

        self.last_signal_time = now

        return Signal(
            strategy_id=self.strategy_id,
            timestamp=now,
            symbol=f"SENSEX {strike} {option_type}",
            security_id=str(security_id),
            option_type=option_type,
            direction="BUY",
            quantity=int(market_data.get("quantity", 1)),
            entry_reference=float(option_ltp),
            stop_loss_reference=initial_sl,
            signal_reason=f"BreakoutConfirmed: LTP({option_ltp}) > High({iloc_2_high}) | {reason}",
            candle_timestamp=option_df.iloc[-1].get("timestamp"),
            strategy_version="2.0.0"
        )
