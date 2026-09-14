import redis.asyncio as redis
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

async def check_redis_connection():
    try:
        await redis_client.ping()
        logger.info("Successfully connected to Redis.")
        return True
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")
        return False
