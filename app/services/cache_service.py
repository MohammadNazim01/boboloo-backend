import json
import logging
from typing import Any, Optional

from app.core.redis import redis_client

logger = logging.getLogger(__name__)

class CacheService:

    # =====================================================
    # GET RAW VALUE
    # =====================================================
    @staticmethod
    async def get(key: str) -> Optional[str]:
        try:
            return await redis_client.get(key)
        except Exception as e:
            logger.warning(f"Redis GET failed [{key}]: {e}")
            return None

    # =====================================================
    # SET RAW VALUE
    # =====================================================
    @staticmethod
    async def set(
        key: str,
        value: str,
        ttl: int = 3600
    ) -> None:
        try:
            await redis_client.setex(key, ttl, value)
        except Exception as e:
            logger.warning(f"Redis SET failed [{key}]: {e}")

    # =====================================================
    # DELETE KEY
    # =====================================================
    @staticmethod
    async def delete(key: str) -> None:
        try:
            await redis_client.delete(key)
        except Exception as e:
            logger.warning(f"Redis DELETE failed [{key}]: {e}")

    # =====================================================
    # GET JSON VALUE
    # =====================================================
    @staticmethod
    async def get_json(key: str) -> Optional[Any]:
        try:
            data = await redis_client.get(key)

            if not data:
                return None

            try:
                return json.loads(data)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON in cache [{key}]")
                return None

        except Exception as e:
            logger.warning(f"Redis GET_JSON failed [{key}]: {e}")
            return None

    # =====================================================
    # SET JSON VALUE
    # =====================================================
    @staticmethod
    async def set_json(
        key: str,
        value: Any,
        ttl: int = 3600
    ) -> None:
        try:
            payload = json.dumps(value, default=str)

            await redis_client.setex(
                key,
                ttl,
                payload
            )

        except Exception as e:
            logger.warning(f"Redis SET_JSON failed [{key}]: {e}")

    # =====================================================
    # EXISTS CHECK
    # =====================================================
    @staticmethod
    async def exists(key: str) -> bool:
        try:
            return bool(await redis_client.exists(key))
        except Exception as e:
            logger.warning(f"Redis EXISTS failed [{key}]: {e}")
            return False

    # =====================================================
    # INCREMENT (RATE LIMITING)
    # =====================================================
    @staticmethod
    async def incr(key: str, ttl: int = 60) -> int:
        try:
            value = await redis_client.incr(key)

            if value == 1:
                await redis_client.expire(key, ttl)

            return value

        except Exception as e:
            logger.warning(f"Redis INCR failed [{key}]: {e}")
            return 0
