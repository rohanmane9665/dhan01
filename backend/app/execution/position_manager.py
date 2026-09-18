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
        self.initial_quantity = quantity
        self.entry_price = entry_price
        self.initial_stop_loss = stop_loss
        self.stop_loss = stop_loss
        self.strategy_id = strategy_id
        self.current_price = entry_price
        self.status = "OPEN"
        self.entry_time = datetime.now(timezone.utc)
        self.exit_time: Optional[datetime] = None
        self.exit_price: Optional[float] = None
        self.realized_pnl: float = 0.0

        # Calculated risk diff
        self.risk_diff = self.entry_price - self.initial_stop_loss
        if self.risk_diff <= 0:
            self.risk_diff = 1.0  # Failsafe
            
        self.target_1 = self.entry_price + (2 * self.risk_diff)
        self.target_1_hit = False
        
        # Max tracked level for trailing
        self.current_trail_level = 0

    def update_price(self, current_price: float) -> Optional[str]:
        """
        Updates LTP, handles Partial Exits at Target 1, and updates Trailing SL.
        Returns:
            - 'PARTIAL_EXIT' if target 1 is hit.
            - exit_reason string if full close.
            - None if no action.
        """
        if current_price <= 0:
            return None
        self.current_price = current_price

        # Check Protective/Trailing Stop Loss First
        if current_price <= self.stop_loss:
            return f"STOP_LOSS_HIT (LTP {current_price} <= SL {self.stop_loss})"

        # Check Target 1 (Partial Exit)
        if not self.target_1_hit and current_price >= self.target_1:
            self.target_1_hit = True
            self.stop_loss = self.entry_price
            logger.info(f"🎯 Target 1 Hit at {current_price}! Trailing SL moved to breakeven ({self.stop_loss}).")
            return "PARTIAL_EXIT"

        # Check Trailing Tiers if Target 1 was hit
        if self.target_1_hit:
            # Check multipliers 3 to 8
            for n in range(3, 9):
                price_threshold = self.entry_price + (n * self.risk_diff)
                if current_price >= price_threshold and self.current_trail_level < n:
                    self.current_trail_level = n
                    new_sl = self.entry_price + ((n - 2) * self.risk_diff)
                    if new_sl > self.stop_loss:
                        old_sl = self.stop_loss
                        self.stop_loss = new_sl
                        logger.info(f"📈 Trailing SL Tier {n} reached at {current_price}! SL updated: {old_sl} -> {self.stop_loss}")

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
        logger.info(f"Position opened: {pos.symbol} (ID: {pos.position_id}, Entry: ₹{pos.entry_price}, Qty: {pos.quantity})")

    def partial_close(self, position_id: str, close_qty: int, exit_price: float):
        pos = self.active_positions.get(position_id)
        if pos and pos.quantity >= close_qty:
            pos.quantity -= close_qty
            pnl = (exit_price - pos.entry_price) * close_qty
            pos.realized_pnl += pnl
            logger.info(f"Partial Close: {pos.symbol} sold {close_qty} @ ₹{exit_price} (PnL: ₹{pnl:.2f}). Remaining Qty: {pos.quantity}")

    def close_position(self, position_id: str, exit_price: float) -> Optional[Position]:
        pos = self.active_positions.pop(position_id, None)
        if pos:
            pos.status = "CLOSED"
            pos.exit_price = exit_price
            pos.exit_time = datetime.now(timezone.utc)
            pos.realized_pnl += (exit_price - pos.entry_price) * pos.quantity
            pos.quantity = 0
            logger.info(f"Position full closed: {pos.symbol} @ ₹{exit_price} (Total PnL: ₹{pos.realized_pnl:.2f})")
            return pos
        return None

    def get_active_positions(self) -> List[Position]:
        return list(self.active_positions.values())
