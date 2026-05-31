import hashlib
import hmac
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.database import get_db
from app.database.models import APIKey, Child, Parent, Toy, ToyStatus
from app.core.config import settings
from app.core.redis import redis_client

from app.schemas.factory_schema import (
    FactoryProvisionRequest,
    FactoryProvisionResponse,
    FactoryBatchProvisionRequest,
)

router = APIRouter(
    prefix="/api/v1/factory",
    tags=["Factory"],
)


# =====================================================
# SINGLE TOY PROVISION
# =====================================================
@router.post("/provision", response_model=FactoryProvisionResponse)
async def provision_toy(
    payload: FactoryProvisionRequest,
    factory_secret: str = Header(...),
    db: AsyncSession = Depends(get_db),
):

    # Validate factory secret
    if not hmac.compare_digest(factory_secret, settings.FACTORY_SECRET_KEY):
        raise HTTPException(status_code=403, detail="Invalid factory secret")

    device_id = payload.factory_device_id.strip().upper()

    # Check existing toy
    result = await db.execute(
        select(Toy).where(Toy.factory_device_id == device_id)
    )
    existing = result.scalar_one_or_none()

    # Idempotent behaviour
    if existing:
        return {
            "toy_uuid": str(existing.toy_uuid),
            "status": existing.status.value,
        }

    # Create toy with device secret
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

    await db.commit()
    await db.refresh(toy)

    return {
        "toy_uuid": str(toy.toy_uuid),
        "status": toy.status.value,
    }


# =====================================================
# BATCH TOY PROVISION
# =====================================================
@router.post("/provision-batch")
async def provision_batch(
    payload: FactoryBatchProvisionRequest,
    factory_secret: str = Header(...),
    db: AsyncSession = Depends(get_db),
):

    if not hmac.compare_digest(factory_secret, settings.FACTORY_SECRET_KEY):
        raise HTTPException(status_code=403, detail="Invalid factory secret")

    device_ids = [d.strip().upper() for d in payload.device_ids]

    # Fetch already existing toys
    result = await db.execute(
        select(Toy.factory_device_id).where(
            Toy.factory_device_id.in_(device_ids)
        )
    )

    existing_ids = {row[0] for row in result.fetchall()}

    toys = []

    for device_id in device_ids:

        if device_id in existing_ids:
            continue

        toy = Toy(
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

    await db.commit()

    return {
        "batch_id": payload.batch_id,
        "requested": len(device_ids),
        "created": len(toys),
        "duplicates": len(device_ids) - len(toys),
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

    if not hmac.compare_digest(factory_secret, settings.FACTORY_SECRET_KEY):
        raise HTTPException(status_code=403, detail="Invalid factory secret")

    device_id = factory_device_id.strip().upper()

    result = await db.execute(select(Toy).where(Toy.factory_device_id == device_id))
    toy = result.scalar_one_or_none()
    if not toy:
        raise HTTPException(status_code=404, detail="Toy not provisioned. Call /provision first.")

    # Ensure a dev parent exists
    dev_uid = "dev_test_parent_001"
    p_result = await db.execute(select(Parent).where(Parent.firebase_uid == dev_uid))
    parent = p_result.scalar_one_or_none()
    if not parent:
        parent = Parent(firebase_uid=dev_uid, email="dev@boboloo.local", name="Dev Parent")
        db.add(parent)
        await db.flush()

    # Ensure a dev child exists under this parent
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

    # Activate the toy (set status ACTIVE, link to parent + child)
    toy.owner_parent_id = parent.id
    toy.active_child_id = child.id
    toy.claimed_at = datetime.now(timezone.utc)
    toy.status = ToyStatus.ACTIVE
    toy.is_active = True

    # Generate API key identical to claim flow
    raw_key  = secrets.token_hex(32)  # 64 hex chars — matches firmware TOY_API_KEY_LEN
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    db.add(APIKey(key_hash=key_hash, toy_id=toy.id, revoked=False))

    await db.commit()

    # Cache in Redis so machine auth works immediately
    await redis_client.set(f"toy_key:{key_hash}", str(toy.id), ex=86400)

    return {
        "toy_uuid":    str(toy.toy_uuid),
        "toy_api_key": raw_key,
        "device_id":   device_id,
        "status":      "active",
        "note":        "DEV ONLY — this endpoint does not exist in production",
    }