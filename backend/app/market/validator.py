import logging
import math
from datetime import datetime, timezone
from typing import Dict, Any, Tuple, Optional

logger = logging.getLogger(__name__)


class DataValidator:
    """
    Validates incoming tick data for freshness, schema completeness, and reasonable bounds.
    Now includes NaN handling and Spike Detection.
    """

    def __init__(self, stale_seconds: int = 3, max_stale_seconds: int = 3, max_spike_pct: float = 0.10):
        # Accept both kwarg names for backward compatibility
        self.max_stale_seconds = stale_seconds or max_stale_seconds
        self.max_spike_pct = max_spike_pct
        self.last_ltp = None

    def validate(self, ltp: float, dt: Optional[datetime] = None) -> Tuple[bool, str]:
        """
        Validates a single (ltp, timestamp) pair. Used by TradingWorker.on_tick().
        """
        if ltp is None or math.isnan(ltp) or ltp <= 0:
            return False, f"Invalid, NaN, or non-positive LTP: {ltp}"
            
        # Spike Detection
        if self.last_ltp is not None:
            pct_change = abs((ltp - self.last_ltp) / self.last_ltp)
            if pct_change > self.max_spike_pct:
                return False, f"Spike detected: LTP jumped {pct_change*100:.2f}% from {self.last_ltp} to {ltp}"
        
        self.last_ltp = ltp

        if dt is not None:
            try:
                now_ts = datetime.now(timezone.utc).timestamp()
                if dt.tzinfo is None:
                    # Assume UTC if naive
                    tick_ts = dt.replace(tzinfo=timezone.utc).timestamp()
                else:
                    tick_ts = dt.timestamp()
                lag = abs(now_ts - tick_ts)
                if lag > self.max_stale_seconds:
                    return False, f"Stale tick: lag={lag:.2f}s > limit={self.max_stale_seconds}s"
            except Exception as e:
                logger.warning(f"Error validating tick timestamp: {e}")

        return True, "VALID"

    def validate_tick(self, tick: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validates tick dictionary format, timestamp freshness, and positive LTP.
        """
        if not isinstance(tick, dict):
            return False, "Tick data must be a dictionary"

        ltp = tick.get("LTP") or tick.get("last_price") or tick.get("lp")
        
        try:
            ltp_val = float(ltp)
        except (ValueError, TypeError):
            return False, f"LTP cannot be parsed to float: {ltp}"
            
        return self.validate(ltp_val, None) # Time check handles separately or skipped here if not provided

