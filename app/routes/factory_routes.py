import hashlib
import hmac
import logging
import re
import secrets
import uuid as _uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.database import get_db
from app.database.models import APIKey, AuditLog, Child, Parent, Toy, ToyStatus
from app.core.config import settings
from app.core.redis import redis_client

from app.schemas.factory_schema import (
    DEVICE_ID_PATTERN,
    FactoryBatchProvisionRequest,
    FactoryDisableRequest,
    FactoryProvisionRequest,
    FactoryProvisionResponse,
)

logger = logging.getLogger("factory")

router = APIRouter(
    prefix="/api/v1/factory",
    tags=["Factory"],
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _check_secret(factory_secret: str) -> None:
    if not hmac.compare_digest(factory_secret, settings.FACTORY_SECRET_KEY):
        raise HTTPException(status_code=403, detail="Invalid factory secret")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# =====================================================
# SINGLE TOY PROVISION
# =====================================================
@router.post("/provision", response_model=FactoryProvisionResponse)
async def provision_toy(
    payload: FactoryProvisionRequest,
    request: Request,
    factory_secret: str = Header(...),
    db: AsyncSession = Depends(get_db),
):
    _check_secret(factory_secret)

    device_id = payload.factory_device_id.strip().upper()
    ip = _client_ip(request)

    result = await db.execute(
        select(Toy).where(Toy.factory_device_id == device_id)
    )
    existing = result.scalar_one_or_none()

    if existing:
        logger.info("factory.provision_duplicate device_id=%s toy_uuid=%s ip=%s",
                    device_id, existing.toy_uuid, ip)
        db.add(AuditLog(
            action="factory.provision_duplicate",
            event_data={
                "device_id": device_id,
                "toy_uuid": str(existing.toy_uuid),
                "status": existing.status.value,
                "ip": ip,
            },
        ))
        await db.commit()
        return {
            "toy_uuid": str(existing.toy_uuid),
            "status": existing.status.value,
        }

    toy = Toy(
        factory_device_id=device_id,
        status=ToyStatus.PROVISIONED,
        is_active=True,
        manufactured_at=datetime.now(timezone.utc),
        firmware_version=payload.firmware_version,
        hardware_revision=payload.hardware_revision,
        factory_batch=payload.batch_id,
    )
    db.add(toy)

    db.add(AuditLog(
        action="factory.provision",
        event_data={
            "device_id": device_id,
            "batch_id": payload.batch_id,
            "firmware_version": payload.firmware_version,
            "hardware_revision": payload.hardware_revision,
            "ip": ip,
        },
    ))

    await db.commit()
    await db.refresh(toy)

    logger.info("factory.provision device_id=%s toy_uuid=%s batch=%s ip=%s",
                device_id, toy.toy_uuid, payload.batch_id, ip)

    return {"toy_uuid": str(toy.toy_uuid), "status": toy.status.value}


# =====================================================
# BATCH TOY PROVISION
# =====================================================
@router.post("/provision-batch")
async def provision_batch(
    payload: FactoryBatchProvisionRequest,
    request: Request,
    factory_secret: str = Header(...),
    db: AsyncSession = Depends(get_db),
):
    _check_secret(factory_secret)

    # Validate each device ID against the agreed format before touching the DB.
    bad = [d for d in payload.device_ids if not re.match(DEVICE_ID_PATTERN, d)]
    if bad:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid device_id format: {bad[:5]}{'…' if len(bad) > 5 else ''}",
        )

    ip = _client_ip(request)
    # dict.fromkeys preserves insertion order while deduplicating, so a factory
    # retry that re-sends the same ID in one batch doesn't hit the unique constraint.
    device_ids = list(dict.fromkeys(d.strip().upper() for d in payload.device_ids))

    result = await db.execute(
        select(Toy.factory_device_id)
        .where(Toy.factory_device_id.in_(device_ids))
    )
    existing_ids = set(result.scalars().all())

    toys = []
    for device_id in device_ids:
        if device_id in existing_ids:
            continue
        # Generate toy_uuid explicitly so it is known before the DB flush.
        # SQLAlchemy's column-level default= is only called during flush,
        # so t.toy_uuid would be None if we relied on it here.
        toy = Toy(
            toy_uuid=_uuid.uuid4(),
            factory_device_id=device_id,
            status=ToyStatus.PROVISIONED,
            is_active=True,
            manufactured_at=datetime.now(timezone.utc),
            firmware_version=payload.firmware_version,
            hardware_revision=payload.hardware_revision,
            factory_batch=payload.batch_id,
        )
        toys.append(toy)

    db.add_all(toys)

    created = [
        {"device_id": t.factory_device_id, "toy_uuid": str(t.toy_uuid)}
        for t in toys
    ]

    db.add(AuditLog(
        action="factory.provision_batch",
        event_data={
            "batch_id": payload.batch_id,
            "requested": len(device_ids),
            "created": len(toys),
            "duplicates": len(existing_ids),
            "ip": ip,
        },
    ))

    await db.commit()

    logger.info(
        "factory.provision_batch batch=%s requested=%d created=%d duplicates=%d ip=%s",
        payload.batch_id, len(device_ids), len(toys), len(existing_ids), ip,
    )

    return {
        "batch_id": payload.batch_id,
        "requested": len(device_ids),
        "created": len(toys),
        "duplicates": len(existing_ids),
        "toys": created,
    }


# =====================================================
# DISABLE TOY
# Marks the toy DISABLED and revokes all its API keys.
# Idempotent: calling it on an already-disabled toy is a no-op.
# =====================================================
@router.post("/disable")
async def disable_toy(
    payload: FactoryDisableRequest,
    request: Request,
    factory_secret: str = Header(...),
    db: AsyncSession = Depends(get_db),
):
    _check_secret(factory_secret)

    device_id = payload.factory_device_id.strip().upper()
    ip = _client_ip(request)

    result = await db.execute(select(Toy).where(Toy.factory_device_id == device_id))
    toy = result.scalar_one_or_none()
    if not toy:
        raise HTTPException(status_code=404, detail="Toy not found")

    if toy.status == ToyStatus.DISABLED:
        logger.info("factory.disable_noop device_id=%s ip=%s", device_id, ip)
        return {
            "device_id": device_id,
            "toy_uuid": str(toy.toy_uuid),
            "status": "already_disabled",
            "keys_revoked": 0,
        }

    active_keys = [k for k in toy.api_keys if not k.revoked]
    for key in active_keys:
        key.revoked = True

    prev_status = toy.status.value
    toy.status = ToyStatus.DISABLED
    toy.is_active = False

    db.add(AuditLog(
        action="factory.disable",
        event_data={
            "device_id": device_id,
            "toy_uuid": str(toy.toy_uuid),
            "previous_status": prev_status,
            "keys_revoked": len(active_keys),
            "ip": ip,
        },
    ))

    await db.commit()

    # Purge Redis only after the DB commit succeeds. If commit had failed, the
    # ORM rollback would restore revoked=False in the session, and leaving Redis
    # intact means the toy can still authenticate via the cached key — consistent
    # with the DB state. Deleting from Redis after a failed commit would create a
    # split-brain where the toy is locked out of the fast path but the DB says active.
    for key in active_keys:
        await redis_client.delete(f"toy_key:{key.key_hash}")

    logger.info(
        "factory.disable device_id=%s toy_uuid=%s keys_revoked=%d ip=%s",
        device_id, toy.toy_uuid, len(active_keys), ip,
    )

    return {
        "device_id": device_id,
        "toy_uuid": str(toy.toy_uuid),
        "status": "disabled",
        "keys_revoked": len(active_keys),
    }


# =====================================================
# DEV ONLY — Generate toy API key without Firebase
# Available in ENVIRONMENT=development only.
# Creates a test parent + child if none exist, then
# generates an APIKey exactly as the claim flow would.
# =====================================================
@router.post("/dev-issue-key")
async def dev_issue_key(
    factory_device_id: str,
    factory_secret: str = Header(...),
    db: AsyncSession = Depends(get_db),
):
    if settings.ENVIRONMENT != "development":
        raise HTTPException(status_code=404, detail="Not found")

    _check_secret(factory_secret)

    device_id = factory_device_id.strip().upper()

    result = await db.execute(select(Toy).where(Toy.factory_device_id == device_id))
    toy = result.scalar_one_or_none()
    if not toy:
        raise HTTPException(status_code=404, detail="Toy not provisioned. Call /provision first.")

    dev_uid = "dev_test_parent_001"
    p_result = await db.execute(select(Parent).where(Parent.firebase_uid == dev_uid))
    parent = p_result.scalar_one_or_none()
    if not parent:
        parent = Parent(firebase_uid=dev_uid, email="dev@boboloo.local", name="Dev Parent")
        db.add(parent)
        await db.flush()

    c_result = await db.execute(
        select(Child).where(Child.parent_id == parent.id, Child.is_deleted == False)
    )
    child = c_result.scalar_one_or_none()
    if not child:
        child = Child(parent_id=parent.id, name="Dev Child", age=6, guardian_name="Dev Parent", onboarding_completed=True)
        db.add(child)
        await db.flush()
    else:
        child.onboarding_completed = True

    toy.owner_parent_id = parent.id
    toy.active_child_id = child.id
    toy.claimed_at = datetime.now(timezone.utc)
    toy.status = ToyStatus.ACTIVE
    toy.is_active = True
    raw_key = secrets.token_hex(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    db.add(APIKey(key_hash=key_hash, toy_id=toy.id, revoked=False))

    await db.commit()

    await redis_client.set(f"toy_key:{key_hash}", str(toy.id), ex=86400)

    return {
        "toy_uuid":    str(toy.toy_uuid),
        "toy_api_key": raw_key,
        "device_id":   device_id,
        "status":      "active",
        "note":        "DEV ONLY — this endpoint does not exist in production",
    }
