from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.auth.firebase_auth import get_current_parent
from app.database.database import get_db
from app.database.models import Parent
from app.schemas.toy_schema import (
    ToyClaimRequest,
    ToyClaimResponse,
    ToyRotateResponse,
)
from app.services.toy_claim_service import ToyClaimService


router = APIRouter(
    prefix="/api/v1/toy",
    tags=["Toy"],
)


# ======================================
# 🧸 CLAIM TOY
# ======================================
@router.post("/claim", response_model=ToyClaimResponse)
async def claim_toy(
    data: ToyClaimRequest,
    parent: Parent = Depends(get_current_parent),
    db: AsyncSession = Depends(get_db),
):
    return await ToyClaimService.claim_toy(
        db=db,
        parent_id=parent.id,
        factory_device_id=data.factory_device_id,
    )


# ======================================
# 🔑 ROTATE KEY
# ======================================
@router.post("/rotate-key/{toy_id}", response_model=ToyRotateResponse)
async def rotate_key(
    toy_id: UUID,
    parent: Parent = Depends(get_current_parent),
    db: AsyncSession = Depends(get_db),
):
    return await ToyClaimService.rotate_key(
        db=db,
        parent_id=parent.id,
        toy_id=toy_id,
    )