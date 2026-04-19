from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.database.database import get_db

from app.auth.admin_auth import get_current_admin
from app.auth.admin_internal import verify_admin_internal

from app.services import admin_service

from app.schemas.admin_schema import (
    AdminDashboardResponse,
    AdminParentResponse,
    AdminToyResponse,
    AdminConversationResponse,
    AdminAnalyticsResponse,
)

router = APIRouter(
    prefix="/sys/control",
    tags=["Admin"],
    include_in_schema=False,   # ✅ hidden from swagger
    dependencies=[Depends(verify_admin_internal)],
)

# =====================================================
# DASHBOARD
# =====================================================
@router.get(
    "/dashboard",
    response_model=AdminDashboardResponse,
)
async def dashboard(
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    return await admin_service.dashboard_stats(db)


# =====================================================
# PARENTS LIST
# =====================================================
@router.get(
    "/parents",
    response_model=list[AdminParentResponse],
)
async def parents(
    page: int = Query(1, ge=1),
    limit: int = Query(20, le=100),
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    return await admin_service.get_parents(
        db,
        page,
        limit,
    )


# =====================================================
# TOYS LIST
# =====================================================
@router.get(
    "/toys",
    response_model=list[AdminToyResponse],
)
async def toys(
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    return await admin_service.get_toys(db)


# =====================================================
# CHILD CONVERSATIONS
# =====================================================
@router.get(
    "/conversations/{child_id}",
    response_model=list[AdminConversationResponse],
)
async def conversations(
    child_id: UUID,
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    return await admin_service.get_conversations(
        db=db,
        child_id=child_id,
        limit=limit,
    )


# =====================================================
# CHILD ANALYTICS
# =====================================================
@router.get(
    "/analytics/{child_id}",
    response_model=AdminAnalyticsResponse,
)
async def analytics(
    child_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    return await admin_service.get_child_analytics(
        db=db,
        child_id=child_id,
    )