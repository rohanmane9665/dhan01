import logging
from typing import Dict, Any, List, Tuple

from app.adapter.base import BrokerAdapter
from app.execution.position_manager import PositionManager
from app.database.database import AsyncSessionLocal
from app.database.repositories import PositionRepository

logger = logging.getLogger(__name__)


class ReconciliationService:
    """
    Mandatory Position Reconciliation Service.
    Compares local database state against authoritative broker positions.
    Enters SAFE_MODE on any un-reconciled mismatch.
    """

    def __init__(self, broker: BrokerAdapter, position_manager: PositionManager):
        self.broker = broker
        self.position_manager = position_manager
        self.safe_mode = False
        self.last_mismatch_reason = ""

    async def reconcile(self) -> Tuple[bool, str]:
        """
        Executes reconciliation check between local database state and broker API.
        """
        try:
            broker_positions = await self.broker.get_positions()
            
            async with AsyncSessionLocal() as session:
                pos_repo = PositionRepository(session)
                local_positions = await pos_repo.get_open_positions()

            # Map broker active net positions (netQty != 0)
            broker_map: Dict[str, int] = {}
            for bp in broker_positions:
                sec_id = str(bp.get("securityId", bp.get("security_id", "")))
                net_qty = int(bp.get("netQty", bp.get("quantity", 0)))
                if net_qty != 0 and sec_id:
                    broker_map[sec_id] = net_qty

            # Map local active net positions
            local_map: Dict[str, int] = {}
            for lp in local_positions:
                local_map[lp.symbol] = lp.quantity # assuming symbol maps to sec_id in this context, or we should use symbol

            # Check 1: Unexpected broker position missing locally
            for sec_id, qty in broker_map.items():
                if sec_id not in local_map:
                    reason = f"MISMATCH: Broker has active position for {sec_id} (qty {qty}) not tracked locally in DB"
                    self.enter_safe_mode(reason)
                    return False, reason

            # Check 2: Local position missing at broker or quantity mismatch
            for sec_id, qty in local_map.items():
                if sec_id not in broker_map:
                    reason = f"MISMATCH: Local position for {sec_id} (qty {qty}) does not exist at broker"
                    self.enter_safe_mode(reason)
                    return False, reason
                elif broker_map[sec_id] != qty:
                    reason = f"MISMATCH: Quantity mismatch for {sec_id}: Local({qty}) vs Broker({broker_map[sec_id]})"
                    self.enter_safe_mode(reason)
                    return False, reason

            # Match verified!
            self.safe_mode = False
            self.last_mismatch_reason = ""
            logger.info("✅ Position reconciliation successful. Database state matches broker.")
            return True, "RECONCILED"

        except Exception as e:
            reason = f"Reconciliation error: {e}"
            self.enter_safe_mode(reason)
            return False, reason

    def enter_safe_mode(self, reason: str):
        self.safe_mode = True
        self.last_mismatch_reason = reason
        logger.critical(f"🚨 ENTERING SAFE_MODE: {reason}")
