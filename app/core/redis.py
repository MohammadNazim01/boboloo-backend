import redis.asyncio as redis
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


redis_client = redis.from_url(
    settings.REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=5,
    retry_on_timeout=True,
)


async def check_redis():
    """
    App startup health check
    """
    try:
        await redis_client.ping()
        logger.info("Redis connected successfully")

    except Exception as e:
        logger.error(f"Redis connection failed: {e}")