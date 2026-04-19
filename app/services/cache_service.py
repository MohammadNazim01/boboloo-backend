import json
import logging

from app.core.redis import redis_client

logger = logging.getLogger(__name__)


class CacheService:

    @staticmethod
    async def get(key: str):
        """
        Get raw string value
        """
        try:
            return await redis_client.get(key)
        except Exception as e:
            logger.warning(f"Redis GET failed {key}: {e}")
            return None

    @staticmethod
    async def set(key: str, value: str, ttl: int = 3600):
        """
        Set raw string value with TTL
        """
        try:
            await redis_client.setex(key, ttl, value)
        except Exception as e:
            logger.warning(f"Redis SET failed {key}: {e}")

    @staticmethod
    async def delete(key: str):
        """
        Delete cache key
        """
        try:
            await redis_client.delete(key)
        except Exception as e:
            logger.warning(f"Redis DELETE failed {key}: {e}")

    @staticmethod
    async def get_json(key: str):
        """
        Get JSON object
        """
        try:
            data = await redis_client.get(key)

            if not data:
                return None

            return json.loads(data)

        except Exception as e:
            logger.warning(f"Redis GET_JSON failed {key}: {e}")
            return None

    @staticmethod
    async def set_json(
        key: str,
        value,
        ttl: int = 3600
    ):
        """
        Set JSON object
        """
        try:
            payload = json.dumps(
                value,
                default=str
            )

            await redis_client.setex(
                key,
                ttl,
                payload
            )

        except Exception as e:
            logger.warning(f"Redis SET_JSON failed {key}: {e}")

    @staticmethod
    async def exists(key: str):
        """
        Check key exists
        """
        try:
            return await redis_client.exists(key)
        except Exception:
            return 0