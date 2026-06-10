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
    AuditLog,
    ToyStatus,
    Child,
)

from app.core.redis import redis_client


def _generate_api_key() -> tuple[str, str]:
    """Return (raw_key, key_hash) — 64 hex chars, SHA-256 hashed."""
    raw = secrets.token_hex(32)
    return raw, hashlib.sha256(raw.encode()).hexdigest()


class ToyClaimService:

    # ================================
    # CLAIM TOY
    # ================================
    @staticmethod
    async def claim_toy(
        *,
        db: AsyncSession,
        parent_id,
        factory_device_id: str,
    ):
        factory_device_id = factory_device_id.strip().upper()

        # Row lock prevents two simultaneous claims of the same toy.
        result = await db.execute(
            select(Toy)
            .where(Toy.factory_device_id == factory_device_id)
            .with_for_update()
        )
        toy = result.scalar_one_or_none()

        if not toy:
            raise HTTPException(404, "Toy not provisioned by factory")

        # Idempotent retry: if this parent already owns the toy (network error
        # on the first claim response), rotate and return a fresh key so the
        # parent app can re-do BLE provisioning without hitting support.
        if toy.status == ToyStatus.ACTIVE and toy.owner_parent_id == parent_id:
            return await ToyClaimService._rotate_and_return(db, toy, parent_id, reason="claim_retry")

        if toy.status != ToyStatus.PROVISIONED:
            raise HTTPException(400, "Toy already claimed or unavailable")

        child_result = await db.execute(
            select(Child).where(
                Child.parent_id == parent_id,
                Child.is_deleted == False,
            )
        )
        child = child_result.scalar_one_or_none()
        if not child:
            raise HTTPException(400, "Create child profile before claiming toy")

        raw_api_key, key_hash = _generate_api_key()

        toy.owner_parent_id = parent_id
        toy.active_child_id = child.id
        toy.claimed_at = datetime.now(timezone.utc)
        toy.status = ToyStatus.ACTIVE
        toy.is_active = True

        db.add(APIKey(key_hash=key_hash, toy_id=toy.id, revoked=False))
        db.add(AuditLog(
            action="toy.claim",
            event_data={
                "device_id": factory_device_id,
                "toy_uuid": str(toy.toy_uuid),
                "parent_id": str(parent_id),
                "child_id": str(child.id),
            },
        ))

        await db.commit()
        await db.refresh(toy)

        await redis_client.set(f"toy_key:{key_hash}", str(toy.id), ex=86400)

        return {
            "toy_uuid": toy.toy_uuid,
            "toy_api_key": raw_api_key,
            "status": "claimed",
        }

    # ================================
    # ROTATE API KEY
    # ================================
    @staticmethod
    async def rotate_key(
        *,
        db: AsyncSession,
        parent_id,
        toy_id,
    ):
        result = await db.execute(select(Toy).where(Toy.id == toy_id))
        toy = result.scalar_one_or_none()

        if not toy:
            raise HTTPException(404, "Toy not found")

        if toy.owner_parent_id != parent_id:
            raise HTTPException(403, "Unauthorized")

        return await ToyClaimService._rotate_and_return(db, toy, parent_id, reason="parent_request")

    # ================================
    # INTERNAL: revoke old keys, issue new one, write audit log
    # ================================
    @staticmethod
    async def _rotate_and_return(db: AsyncSession, toy: Toy, parent_id, reason: str):
        old_keys_result = await db.execute(
            select(APIKey.key_hash).where(
                APIKey.toy_id == toy.id,
                APIKey.revoked == False,
            )
        )
        old_hashes = old_keys_result.scalars().all()

        await db.execute(
            update(APIKey).where(APIKey.toy_id == toy.id).values(revoked=True)
        )

        raw_api_key, key_hash = _generate_api_key()
        db.add(APIKey(key_hash=key_hash, toy_id=toy.id, revoked=False))
        db.add(AuditLog(
            action="toy.rotate_key",
            event_data={
                "device_id": toy.factory_device_id,
                "toy_uuid": str(toy.toy_uuid),
                "parent_id": str(parent_id),
                "reason": reason,
                "keys_revoked": len(old_hashes),
            },
        ))

        await db.commit()

        # Purge old Redis entries. If a delete fails, forcibly expire the key
        # in 5 minutes so the residual auth window is bounded even if Redis
        # is flaky.
        for old_hash in old_hashes:
            try:
                await redis_client.delete(f"toy_key:{old_hash}")
            except Exception:
                logger.error("Redis purge failed for %s — setting 5-min expiry", old_hash)
                try:
                    await redis_client.expire(f"toy_key:{old_hash}", 300)
                except Exception:
                    logger.error("Redis expire also failed for %s", old_hash)

        await redis_client.set(f"toy_key:{key_hash}", str(toy.id), ex=86400)

        return {
            "toy_api_key": raw_api_key,
            "status": "rotated",
        }
