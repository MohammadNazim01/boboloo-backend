import logging

from app.core.redis import redis_client

logger = logging.getLogger(__name__)


class RateLimitService:

    @staticmethod
    async def allow(
        key: str,
        limit: int = 20,
        window: int = 60
    ) -> bool:
        """
        limit = max requests
        window = seconds
        """

        try:
            current = await redis_client.incr(key)

            if current == 1:
                await redis_client.expire(
                    key,
                    window
                )

            return current <= limit

        except Exception as e:
            logger.warning(
                f"Rate limit failed {key}: {e}"
            )

            # fail open (allow request)
            return True

    @staticmethod
    async def remaining(
        key: str,
        limit: int = 20
    ) -> int:

        try:
            used = await redis_client.get(key)

            used = int(used or 0)

            remain = limit - used

            return max(remain, 0)

        except Exception:
            return limit

    @staticmethod
    async def reset(key: str):

        try:
            await redis_client.delete(key)
        except Exception:
            pass