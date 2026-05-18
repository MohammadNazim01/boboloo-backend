import hashlib
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException

from app.database.models import APIKey, Toy, ToyStatus
from app.core.redis import redis_client

logger = logging.getLogger("toy_key_validator")


async def resolve_toy_by_key(raw_key: str, db: AsyncSession) -> Toy:
    """Validate a raw toy API key and return the active Toy it belongs to.

    Used by both the HTTP header path (machine_auth.py) and the MQTT
    message envelope path (main.py), so auth logic stays in one place.
    """

    raw_key = raw_key.strip()

    if len(raw_key) < 20:
        raise HTTPException(401, "Invalid toy key format")

    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    # Redis fast path
    try:
        toy_id = await redis_client.get(f"toy_key:{key_hash}")

        if toy_id:
            toy = await db.get(Toy, toy_id)

            if not toy:
                raise HTTPException(404, "Toy not found")

            if toy.status != ToyStatus.ACTIVE:
                raise HTTPException(403, "Toy not active")

            if not toy.is_active:
                raise HTTPException(403, "Toy disabled")

            return toy

    except HTTPException:
        raise
    except Exception as e:
        # Only Redis connection/serialization errors fall through to DB
        logger.error(f"Redis error in resolve_toy_by_key: {e}")

    # DB fallback
    result = await db.execute(
        select(APIKey).where(
            APIKey.key_hash == key_hash,
            APIKey.revoked == False,
        )
    )

    api_key = result.scalars().first()

    if not api_key:
        logger.warning("Invalid toy key attempt")
        raise HTTPException(401, "Invalid toy key")

    toy = await db.get(Toy, api_key.toy_id)

    if not toy:
        raise HTTPException(404, "Toy not found")

    if toy.status != ToyStatus.ACTIVE:
        raise HTTPException(403, "Toy not active")

    if not toy.is_active:
        raise HTTPException(403, "Toy disabled")

    # Self-heal Redis cache
    try:
        await redis_client.set(
            f"toy_key:{key_hash}",
            str(toy.id),
            ex=86400,
        )
    except Exception as e:
        logger.error(f"Redis self-heal failed: {e}")

    return toy
