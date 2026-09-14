from fastapi import APIRouter
from fastapi.responses import StreamingResponse
import asyncio
import json

from app.core.redis import redis_client

router = APIRouter()


async def _sse_generator():
    """
    Server-Sent Events generator.
    Reads latest market snapshot from Redis on each tick.
    Falls back to a heartbeat if Redis is unavailable.
    """
    while True:
        try:
            snapshot = await redis_client.get("market_snapshot")
            if snapshot:
                yield f"data: {snapshot}\n\n"
            else:
                # Heartbeat when no data yet
                heartbeat = json.dumps({"type": "HEARTBEAT", "message": "Waiting for market data..."})
                yield f"data: {heartbeat}\n\n"
        except Exception as e:
            error = json.dumps({"type": "ERROR", "message": str(e)})
            yield f"data: {error}\n\n"
        await asyncio.sleep(1)


@router.get("/events")
async def sse_events():
    """SSE endpoint — streams real-time market snapshots from Redis to the frontend."""
    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
