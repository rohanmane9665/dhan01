import logging
from typing import Optional, Tuple

from app.core.config import settings
from app.risk.kill_switch import KillSwitch
from app.strategies.models import Signal

logger = logging.getLogger(__name__)

# Module-level singleton shared across the process
_kill_switch = KillSwitch()


class RiskManager:
    """
    Centralized Deterministic Risk Manager.
    Every trade signal MUST pass through RiskManager before submission.
    AI agents cannot bypass or override RiskManager.
    """

    def __init__(self, kill_switch: Optional[KillSwitch] = None):
        # Use shared module-level kill switch so all instances share state
        self.kill_switch = kill_switch or _kill_switch
        self.max_daily_loss = settings.MAX_DAILY_LOSS if settings else None
        self.max_trades_per_day = settings.MAX_TRADES_PER_DAY if settings else None
        self.max_open_positions = settings.MAX_OPEN_POSITIONS if settings else 5
        self.max_order_quantity = settings.MAX_ORDER_QUANTITY if settings else None
        self.current_daily_pnl: float = 0.0
        self.trades_executed_today: int = 0
        self.current_open_positions: int = 0

    # ------------------------------------------------------------------ #
    # Properties used by API routes                                        #
    # ------------------------------------------------------------------ #
    @property
    def kill_switch_active(self) -> bool:
        return self.kill_switch.is_triggered()

    # ------------------------------------------------------------------ #
    # Kill-switch control (called from API routes)                         #
    # ------------------------------------------------------------------ #
    async def sync_kill_switch(self):
        """No-op: kill switch state is already in-process. Kept for API compat."""
        pass

    async def activate_kill_switch(self, reason: str = "Operator manual kill switch triggered"):
        self.kill_switch.trigger(reason)
        logger.critical(f"🚨 Kill switch activated via API: {reason}")
        from app.database.database import AsyncSessionLocal
        from app.database.repositories import EventRepository
        async with AsyncSessionLocal() as session:
            repo = EventRepository(session)
            await repo.log_system_event("KILL_SWITCH_TRIGGERED", "CRITICAL", reason)

    async def reset_kill_switch(self, operator: str = "Operator"):
        self.kill_switch.reset(operator)
        logger.info(f"✅ Kill switch reset via API by {operator}")
        from app.database.database import AsyncSessionLocal
        from app.database.repositories import EventRepository
        async with AsyncSessionLocal() as session:
            repo = EventRepository(session)
            await repo.log_system_event("KILL_SWITCH_RESET", "INFO", f"Reset by {operator}")

    # ------------------------------------------------------------------ #
    # Signal evaluation                                                    #
    # ------------------------------------------------------------------ #
    def evaluate_signal(self, signal: Signal, current_positions_count: int = 0) -> Tuple[bool, str]:
        """
        Evaluates pre-trade risk constraints.
        Returns (True, 'APPROVED') or (False, 'REJECTION_REASON').
        """
        # 1. Kill Switch Check
        if self.kill_switch.is_triggered():
            return False, f"REJECTED: Kill Switch active ({self.kill_switch.trigger_reason})"

        # 2. Max Quantity Check
        if self.max_order_quantity and signal.quantity > self.max_order_quantity:
            return False, f"REJECTED: Order quantity {signal.quantity} exceeds limit {self.max_order_quantity}"

        # 3. Max Open Positions Check
        if self.max_open_positions and current_positions_count >= self.max_open_positions:
            return False, f"REJECTED: Current positions ({current_positions_count}) at max limit ({self.max_open_positions})"

        # 4. Max Daily Trades Check
        if self.max_trades_per_day and self.trades_executed_today >= self.max_trades_per_day:
            return False, f"REJECTED: Daily trade limit reached ({self.trades_executed_today}/{self.max_trades_per_day})"

        # 5. Max Daily Loss Check
        if self.max_daily_loss and self.current_daily_pnl <= -abs(self.max_daily_loss):
            return False, f"REJECTED: Daily loss limit breached (PnL: ₹{self.current_daily_pnl:.2f})"

        return True, "APPROVED"

    def record_trade_execution(self, pnl: float = 0.0):
        self.trades_executed_today += 1
        self.current_open_positions += 1
        self.current_daily_pnl += pnl

    def record_position_closed(self, realized_pnl: float):
        self.current_open_positions = max(0, self.current_open_positions - 1)
        self.current_daily_pnl += realized_pnl

    def reset_daily_stats(self):
        """Called at market open each day."""
        self.current_daily_pnl = 0.0
        self.trades_executed_today = 0
        self.current_open_positions = 0
        logger.info("RiskManager daily stats reset.")
