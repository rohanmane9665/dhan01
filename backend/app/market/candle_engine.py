import logging
import pytz
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Callable

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class CandleEngine:
    """
    Deterministic Exchange-Timestamp Driven 15-Minute OHLCV Candle Resampler.
    No time.sleep() loops. Emits completed candle events on timestamp boundary crosses.
    """

    def __init__(self, interval_minutes: int = 15, on_candle_close: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.interval_minutes = interval_minutes
        self.interval_seconds = interval_minutes * 60
        self.on_candle_close = on_candle_close
        self.candles: List[Dict[str, Any]] = []
        self.current_candle: Optional[Dict[str, Any]] = None

    def _get_candle_start_time(self, dt: datetime) -> datetime:
        """
        Calculates the start boundary of the N-minute candle for a given IST timestamp.
        """
        if dt.tzinfo is None:
            dt = IST.localize(dt)
        else:
            dt = dt.astimezone(IST)

        minute = (dt.minute // self.interval_minutes) * self.interval_minutes
        return dt.replace(minute=minute, second=0, microsecond=0)

    def process_tick(self, ltp: float, dt: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        """
        Processes a single price tick. Returns completed candle dict if a candle boundary was crossed.
        """
        if dt is None:
            dt = datetime.now(IST)
        elif dt.tzinfo is None:
            dt = IST.localize(dt)
        else:
            dt = dt.astimezone(IST)

        candle_start = self._get_candle_start_time(dt)
        completed_candle = None

        if self.current_candle is None:
            self.current_candle = {
                "timestamp": candle_start,
                "open": ltp,
                "high": ltp,
                "low": ltp,
                "close": ltp,
                "volume": 0
            }
        elif self.current_candle["timestamp"] == candle_start:
            # Update active candle
            self.current_candle["high"] = max(self.current_candle["high"], ltp)
            self.current_candle["low"] = min(self.current_candle["low"], ltp)
            self.current_candle["close"] = ltp
        else:
            # Candle boundary crossed! Complete existing candle
            completed_candle = dict(self.current_candle)
            self.candles.append(completed_candle)
            if len(self.candles) > 100:
                self.candles = self.candles[-100:]

            if self.on_candle_close:
                try:
                    self.on_candle_close(completed_candle)
                except Exception as e:
                    logger.error(f"Error in on_candle_close callback: {e}")

            # Start new candle
            self.current_candle = {
                "timestamp": candle_start,
                "open": ltp,
                "high": ltp,
                "low": ltp,
                "close": ltp,
                "volume": 0
            }

        return completed_candle

    def get_dataframe(self) -> pd.DataFrame:
        """
        Returns all accumulated completed and current candles as a Pandas DataFrame.
        """
        all_candles = list(self.candles)
        if self.current_candle:
            all_candles.append(dict(self.current_candle))
        if not all_candles:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        return pd.DataFrame(all_candles)
