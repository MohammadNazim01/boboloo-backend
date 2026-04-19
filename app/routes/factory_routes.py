import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.database import get_db
from app.database.models import Toy, ToyStatus
from app.core.config import settings

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
    if factory_secret != settings.FACTORY_SECRET_KEY:
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

    if factory_secret != settings.FACTORY_SECRET_KEY:
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