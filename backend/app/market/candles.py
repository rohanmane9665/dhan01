import pandas as pd
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

class CandleEngine:
    def __init__(self, timeframe_minutes: int = 15):
        self.timeframe_minutes = timeframe_minutes
        self.candles = pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        self.current_candle = None

    def process_tick(self, timestamp: datetime, price: float, volume: int = 0):
        """
        Process a new tick and return a completed candle if the timeframe closed.
        """
        # align timestamp to the start of the timeframe
        minute = timestamp.minute - (timestamp.minute % self.timeframe_minutes)
        candle_start = timestamp.replace(minute=minute, second=0, microsecond=0)
        
        if self.current_candle is None:
            self.current_candle = {
                'timestamp': candle_start,
                'open': price,
                'high': price,
                'low': price,
                'close': price,
                'volume': volume
            }
            return None

        # Check if we moved to a new candle
        if candle_start > self.current_candle['timestamp']:
            completed_candle = self.current_candle.copy()
            
            # append to history
            new_row = pd.DataFrame([completed_candle])
            if self.candles.empty:
                self.candles = new_row
            else:
                self.candles = pd.concat([self.candles, new_row], ignore_index=True)
                
            # Keep last 100
            if len(self.candles) > 100:
                self.candles = self.candles.tail(100).reset_index(drop=True)
                
            # start new candle
            self.current_candle = {
                'timestamp': candle_start,
                'open': price,
                'high': price,
                'low': price,
                'close': price,
                'volume': volume
            }
            return completed_candle
        else:
            # update current candle
            self.current_candle['high'] = max(self.current_candle['high'], price)
            self.current_candle['low'] = min(self.current_candle['low'], price)
            self.current_candle['close'] = price
            self.current_candle['volume'] += volume
            return None
            
    def get_history(self) -> pd.DataFrame:
        return self.candles
