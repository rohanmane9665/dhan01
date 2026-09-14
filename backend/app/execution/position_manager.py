import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class Position:
    """
    Stateful Position Tracking Object.
    Authoritative entry price is set ONLY from actual broker fill price.
    """

    def __init__(
        self,
        position_id: str,
        symbol: str,
        security_id: str,
        option_type: str,
        quantity: int,
        entry_price: float,
        stop_loss: float,
        strategy_id: str = "option_rsi_sensex"
    ):
        self.position_id = position_id
        self.symbol = symbol
        self.security_id = security_id
        self.option_type = option_type
        self.quantity = quantity
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.strategy_id = strategy_id
        self.current_price = entry_price
        self.status = "OPEN"
        self.entry_time = datetime.now(timezone.utc)
        self.exit_time: Optional[datetime] = None
        self.exit_price: Optional[float] = None
        self.realized_pnl: float = 0.0
        self.trailed_sl: bool = False

    def update_price(self, current_price: float) -> Optional[str]:
        """
        Updates LTP, calculates unrealized P&L, checks protective Stop Loss and Trailing SL.
        Returns exit_reason string if Stop Loss is hit.
        """
        if current_price <= 0:
            return None
        self.current_price = current_price

        # Check Protective Stop Loss
        if current_price <= self.stop_loss:
            return f"STOP_LOSS_HIT (LTP {current_price} <= SL {self.stop_loss})"

        return None

    def trail_stop_loss(self, iloc_2_range: float, iloc_3_low: float):
        """
        Trails SL when price rises by iloc[-2] range: Entry + Range. Updates SL to iloc[-3] Low.
        """
        trigger_level = self.entry_price + iloc_2_range
        if self.current_price >= trigger_level and iloc_3_low > self.stop_loss:
            old_sl = self.stop_loss
            self.stop_loss = iloc_3_low
            self.trailed_sl = True
            logger.info(f"📈 Trailing SL updated for {self.symbol}: {old_sl} -> {self.stop_loss}")

    @property
    def unrealized_pnl(self) -> float:
        return (self.current_price - self.entry_price) * self.quantity


class PositionManager:
    """
    Manages active positions and handles state updates.
    """

    def __init__(self):
        self.active_positions: Dict[str, Position] = {}

    def add_position(self, pos: Position):
        self.active_positions[pos.position_id] = pos
        logger.info(f"Position opened: {pos.symbol} (ID: {pos.position_id}, Entry: ₹{pos.entry_price})")

    def close_position(self, position_id: str, exit_price: float) -> Optional[Position]:
        pos = self.active_positions.pop(position_id, None)
        if pos:
            pos.status = "CLOSED"
            pos.exit_price = exit_price
            pos.exit_time = datetime.now(timezone.utc)
            pos.realized_pnl = (exit_price - pos.entry_price) * pos.quantity
            logger.info(f"Position closed: {pos.symbol} @ ₹{exit_price} (PnL: ₹{pos.realized_pnl:.2f})")
            return pos
        return None

    def get_active_positions(self) -> List[Position]:
        return list(self.active_positions.values())
