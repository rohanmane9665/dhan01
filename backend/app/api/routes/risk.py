from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from app.worker.trading_worker import get_worker
from app.api.auth import verify_admin_token

router = APIRouter()

class KillSwitchReason(BaseModel):
    reason: str

@router.get("/")
async def get_risk_status():
    """Returns current risk state from the TradingWorker's RiskManager."""
    worker = get_worker()
    if not worker:
        return {"status": "unavailable"}

    rm = worker.risk_manager
    recon = worker.reconciliation

    return {
        "kill_switch_active": rm.kill_switch_active,
        "kill_switch_reason": rm.kill_switch.trigger_reason,
        "max_daily_loss": rm.max_daily_loss,
        "current_daily_pnl": round(rm.current_daily_pnl, 2),
        "max_open_positions": rm.max_open_positions,
        "current_open_positions": rm.current_open_positions,
        "max_trades_per_day": rm.max_trades_per_day,
        "trades_today": rm.trades_executed_today,
        "reconciliation_safe_mode": recon.safe_mode,
        "last_mismatch_reason": recon.last_mismatch_reason,
    }

@router.post("/kill-switch/activate", dependencies=[Depends(verify_admin_token)])
async def activate_kill_switch(payload: KillSwitchReason):
    """Triggers the global kill switch to block new trades."""
    worker = get_worker()
    if not worker:
        raise HTTPException(status_code=503, detail="Trading worker unavailable")
        
    await worker.risk_manager.activate_kill_switch(reason=payload.reason)
    return {"status": "success", "message": "Kill switch activated"}

@router.post("/kill-switch/reset", dependencies=[Depends(verify_admin_token)])
async def reset_kill_switch():
    """Resets the global kill switch."""
    worker = get_worker()
    if not worker:
        raise HTTPException(status_code=503, detail="Trading worker unavailable")
        
    await worker.risk_manager.reset_kill_switch(operator="API User")
    return {"status": "success", "message": "Kill switch reset"}
