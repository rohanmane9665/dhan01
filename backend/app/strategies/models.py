from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class Signal(BaseModel):
    strategy_id: str = "option_rsi_sensex"
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    symbol: str
    security_id: str
    option_type: str  # "CE" or "PE"
    direction: str = "BUY"  # "BUY" or "SELL"
    quantity: int = 1
    entry_reference: float
    stop_loss_reference: float
    signal_reason: str
    candle_timestamp: Optional[datetime] = None
    strategy_version: str = "2.0.0"
