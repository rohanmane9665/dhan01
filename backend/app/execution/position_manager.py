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
        strategy_id: str = "index_breakout_nifty"
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

        self.trailing_levels = [
            {'price_level': 44, 'stop_loss': 25},
            {'price_level': 61, 'stop_loss': 41},
            {'price_level': 77, 'stop_loss': 57},
            {'price_level': 93, 'stop_loss': 72},
            {'price_level': 108, 'stop_loss': 88},
            {'price_level': 128, 'stop_loss': 104},
            {'price_level': 150, 'stop_loss': 120},
            {'price_level': 174, 'stop_loss': 138},
            {'price_level': 195, 'stop_loss': 159},
            {'price_level': 215, 'stop_loss': 183},
            {'price_level': 245, 'stop_loss': 210},
            {'price_level': 267, 'stop_loss': 235},
            {'price_level': 290, 'stop_loss': 250},
            {'price_level': 310, 'stop_loss': 307}  # Final exit level
        ]

    def update_price(self, current_price: float) -> Optional[str]:
        """
        Updates LTP, calculates unrealized P&L, checks protective Stop Loss and Trailing SL.
        Returns exit_reason string if Stop Loss is hit or Final target hit.
        """
        if current_price <= 0:
            return None
        self.current_price = current_price

        # Check Final Exit
        if current_price >= self.entry_price + 310:
            return f"FINAL_TARGET_HIT (LTP {current_price} >= Entry {self.entry_price} + 310)"

        # Check Protective/Trailing Stop Loss
        if current_price <= self.stop_loss:
            return f"STOP_LOSS_HIT (LTP {current_price} <= SL {self.stop_loss})"

        # Evaluate Trailing SL Tiers
        for level in self.trailing_levels[:-1]:  # Exclude final exit level
            price_threshold = self.entry_price + level['price_level']
            new_stop_loss = self.entry_price + level['stop_loss']
            
            if current_price >= price_threshold and new_stop_loss > self.stop_loss:
                old_sl = self.stop_loss
                self.stop_loss = new_stop_loss
                logger.info(f"📈 Trailing SL updated for {self.symbol}: {old_sl} -> {self.stop_loss} (Reached Threshold: {price_threshold})")
                break

        return None

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
