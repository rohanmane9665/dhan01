from fastapi import APIRouter, Body, HTTPException
from app.worker.trading_worker import get_worker
from app.core.redis import redis_client

router = APIRouter()


@router.get("/status")
async def get_status():
    """Returns current trading mode and system status."""
    worker = get_worker()
    mode = await redis_client.get("trading_mode") or "PAPER"
    system_status = await redis_client.get("system_status") or "RUNNING"

    if worker and worker.risk_manager.kill_switch_active:
        system_status = "KILLED"

    return {"status": system_status, "mode": mode}


@router.post("/mode")
async def set_mode(mode: str = Body(..., embed=True)):
    """Switch between PAPER and LIVE trading modes."""
    if mode not in ["PAPER", "LIVE"]:
        raise HTTPException(status_code=400, detail="Mode must be 'PAPER' or 'LIVE'")
    await redis_client.set("trading_mode", mode)
    return {"message": f"Trading mode set to {mode}", "mode": mode}


@router.post("/kill")
async def kill_switch():
    """Emergency kill switch — immediately blocks all new trades."""
    worker = get_worker()
    if worker:
        await worker.risk_manager.activate_kill_switch("Operator triggered via dashboard")
    return {"message": "🚨 Emergency kill switch activated. All new trading blocked.", "status": "KILLED"}


@router.post("/reset-kill")
async def reset_kill():
    """Reset the kill switch. Trading resumes on next valid signal."""
    worker = get_worker()
    if worker:
        await worker.risk_manager.reset_kill_switch("Dashboard Operator")
    return {"message": "✅ Kill switch reset. System monitoring for new signals.", "status": "RUNNING"}


@router.post("/close-position/{position_id}")
async def close_position(position_id: str):
    """Manually close a specific open position at market price."""
    worker = get_worker()
    if not worker:
        raise HTTPException(status_code=503, detail="Trading worker not available")

    pos = worker.position_manager.active_positions.get(position_id)
    if not pos:
        raise HTTPException(status_code=404, detail=f"Position {position_id} not found")

    result = await worker.execution_engine.close_position(position_id, pos.current_price)
    return result
