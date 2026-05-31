import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from fastapi import HTTPException
import logging

logger = logging.getLogger(__name__)

from app.database.models import (
    Toy,
    APIKey,
    ToyStatus,
    Child,
)

from app.core.redis import redis_client

class ToyClaimService:

    # ================================
    # 🧸 CLAIM TOY
    # ================================
    @staticmethod
    async def claim_toy(
        *,
        db: AsyncSession,
        parent_id,
        factory_device_id: str,
    ):

        # Normalize input
        factory_device_id = factory_device_id.strip().upper()

        # 🔒 Fetch toy WITH ROW LOCK
        result = await db.execute(
            select(Toy)
            .where(Toy.factory_device_id == factory_device_id)
            .with_for_update()
        )

        toy = result.scalar_one_or_none()

        if not toy:
            raise HTTPException(
                status_code=404,
                detail="Toy not provisioned by factory",
            )

        # 🔒 Ensure toy not already claimed
        if toy.status != ToyStatus.PROVISIONED:
            raise HTTPException(
                status_code=400,
                detail="Toy already claimed or unavailable",
            )

        # 🔎 Fetch parent's child
        child_result = await db.execute(
            select(Child).where(
                Child.parent_id == parent_id,
                Child.is_deleted == False,
            )
        )

        child = child_result.scalar_one_or_none()

        if not child:
            raise HTTPException(
                status_code=400,
                detail="Create child profile before claiming toy",
            )

        # ✅ Activate toy
        toy.owner_parent_id = parent_id
        toy.active_child_id = child.id
        toy.claimed_at = datetime.now(timezone.utc)
        toy.status = ToyStatus.ACTIVE
        toy.is_active = True

        # 🔑 Generate API key — 64 hex chars to match firmware TOY_API_KEY_LEN
        raw_api_key = secrets.token_hex(32)

        key_hash = hashlib.sha256(
            raw_api_key.encode()
        ).hexdigest()

        db.add(
            APIKey(
                key_hash=key_hash,
                toy_id=toy.id,
                revoked=False,
            )
        )

        await db.commit()
        await db.refresh(toy)

        # 🔥 CACHE IN REDIS (IMPORTANT)
        await redis_client.set(
            f"toy_key:{key_hash}",
            str(toy.id),
            ex=86400
        )

        return {
            "toy_uuid": toy.toy_uuid,
            "toy_api_key": raw_api_key,
            "status": "claimed",
        }

    # ================================
    # 🔑 ROTATE API KEY
    # ================================
    @staticmethod
    async def rotate_key(
        *,
        db: AsyncSession,
        parent_id,
        toy_id,
    ):

        # 🔎 Fetch toy
        result = await db.execute(
            select(Toy).where(Toy.id == toy_id)
        )

        toy = result.scalar_one_or_none()

        if not toy:
            raise HTTPException(
                status_code=404,
                detail="Toy not found",
            )

        # 🔒 Ensure ownership
        if toy.owner_parent_id != parent_id:
            raise HTTPException(
                status_code=403,
                detail="Unauthorized",
            )

        # 🔄 Fetch old key hashes before revoking so we can purge Redis
        old_keys_result = await db.execute(
            select(APIKey.key_hash).where(
                APIKey.toy_id == toy.id,
                APIKey.revoked == False,
            )
        )
        old_hashes = old_keys_result.scalars().all()

        await db.execute(
            update(APIKey)
            .where(APIKey.toy_id == toy.id)
            .values(revoked=True)
        )

        # Purge stale Redis entries so old keys stop authenticating immediately
        for old_hash in old_hashes:
            try:
                await redis_client.delete(f"toy_key:{old_hash}")
            except Exception as e:
                logger.error(f"Failed to purge old toy_key from Redis: {e}")

        # 🔑 Generate new API key — 64 hex chars to match firmware TOY_API_KEY_LEN
        raw_api_key = secrets.token_hex(32)

        key_hash = hashlib.sha256(
            raw_api_key.encode()
        ).hexdigest()

        db.add(
            APIKey(
                key_hash=key_hash,
                toy_id=toy.id,
                revoked=False,
            )
        )

        await db.commit()

        # 🔥 CACHE NEW KEY
        await redis_client.set(
            f"toy_key:{key_hash}",
            str(toy.id),
            ex=86400
        )

        return {
            "toy_api_key": raw_api_key,
            "status": "rotated",
        }