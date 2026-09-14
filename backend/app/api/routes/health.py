from fastapi import APIRouter
from app.core.redis import redis_client
from app.worker.trading_worker import get_worker

router = APIRouter()


@router.get("/health")
async def health_check():
    """Basic liveness probe."""
    return {"status": "ok", "service": "dhan-algo-trading", "version": "2.0.0"}


@router.get("/ready")
async def ready_check():
    """Readiness probe — checks Redis and worker status."""
    components = {}

    # Redis check
    try:
        await redis_client.ping()
        components["redis"] = "ok"
    except Exception as e:
        components["redis"] = f"error: {e}"

    # Worker check
    worker = get_worker()
    components["trading_worker"] = "ok" if worker else "not_started"
    components["kill_switch"] = "ACTIVE" if (worker and worker.risk_manager.kill_switch_active) else "off"

    overall = "ready" if components.get("redis") == "ok" else "degraded"
    return {"status": overall, "components": components}
