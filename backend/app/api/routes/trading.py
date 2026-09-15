import json
from fastapi import APIRouter, Body, HTTPException
from app.core.redis import redis_client

router = APIRouter()


@router.get("/status")
async def get_status():
    """Returns current trading mode and system status."""
    mode = await redis_client.get("trading_mode") or "PAPER"
    system_status = await redis_client.get("system_status") or "RUNNING"
    
    # Check if kill switch is active from the latest market snapshot
    snapshot_str = await redis_client.get("market_snapshot")
    if snapshot_str:
        try:
            snapshot = json.loads(snapshot_str)
            if snapshot.get("kill_switch"):
                system_status = "KILLED"
        except Exception:
            pass

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
    await redis_client.publish("trading_commands", json.dumps({
        "command": "kill",
        "reason": "Operator triggered via dashboard"
    }))
    return {"message": "🚨 Emergency kill switch signal sent. All new trading blocked.", "status": "KILLED_PENDING"}


@router.post("/reset-kill")
async def reset_kill():
    """Reset the kill switch. Trading resumes on next valid signal."""
    await redis_client.publish("trading_commands", json.dumps({
        "command": "reset_kill",
        "reason": "Dashboard Operator"
    }))
    return {"message": "✅ Kill switch reset signal sent. System monitoring for new signals.", "status": "RUNNING_PENDING"}


@router.post("/close-position/{position_id}")
async def close_position(position_id: str):
    """Manually close a specific open position at market price."""
    await redis_client.publish("trading_commands", json.dumps({
        "command": "close_position",
        "position_id": position_id
    }))
    return {"message": f"Close position signal sent for {position_id}"}

