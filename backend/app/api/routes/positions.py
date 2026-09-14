from fastapi import APIRouter
from typing import List, Dict, Any

from app.worker.trading_worker import get_worker

router = APIRouter()


@router.get("/")
async def get_positions() -> List[Dict[str, Any]]:
    """Returns all currently open positions from the in-process PositionManager."""
    worker = get_worker()
    if not worker:
        return []

    positions = worker.position_manager.get_active_positions()
    return [
        {
            "position_id": p.position_id,
            "symbol": p.symbol,
            "option_type": p.option_type,
            "security_id": p.security_id,
            "quantity": p.quantity,
            "entry_price": p.entry_price,
            "current_price": p.current_price,
            "stop_loss": p.stop_loss,
            "unrealized_pnl": round(p.unrealized_pnl, 2),
            "trailed_sl": p.trailed_sl,
            "status": p.status,
            "entry_time": p.entry_time.isoformat() if p.entry_time else None,
        }
        for p in positions
    ]
