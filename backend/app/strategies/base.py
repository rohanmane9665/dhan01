from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
import pandas as pd
from app.strategies.models import Signal


class BaseStrategy(ABC):
    """
    Abstract Base Class for Deterministic Quantitative Trading Strategies.
    Strategies produce pure Signal objects and NEVER place broker orders directly.
    """

    @abstractmethod
    def evaluate(self, market_data: Dict[str, Any]) -> Optional[Signal]:
        """
        Evaluates market data and completed candles, returning a Signal if conditions trigger.
        """
        pass
