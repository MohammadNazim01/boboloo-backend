from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException
from datetime import datetime

from app.database.models import Parent, Child
from app.schemas.child_schema import (
    ChildCreate,
    ChildUpdate,
)
from app.services.cache_service import CacheService


# =====================================================
# CREATE CHILD
# =====================================================
async def create_child(
    db: AsyncSession,
    parent: Parent,
    child_data: ChildCreate,
):

    # one child per parent
    result = await db.execute(
        select(Child).where(
            Child.parent_id == parent.id,
            Child.is_deleted == False,
        )
    )

    existing = result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Child already exists"
        )

    child = Child(
        parent_id=parent.id,
        name=child_data.name.strip(),
        age=child_data.age,
        guardian_name=child_data.guardian_name.strip(),
        interests=child_data.interests or [],
        onboarding_completed=True,
        created_at=datetime.utcnow(),
    )

    db.add(child)

    try:
        await db.commit()
        await db.refresh(child)

    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Failed to create child"
        )

    # clear any stale cache
    await CacheService.delete(
        f"child:{child.id}"
    )

    return child


# =====================================================
# GET CHILD
# =====================================================
async def get_child(
    db: AsyncSession,
    parent: Parent,
):

    result = await db.execute(
        select(Child).where(
            Child.parent_id == parent.id,
            Child.is_deleted == False,
        )
    )

    return result.scalar_one_or_none()


# =====================================================
# UPDATE CHILD
# =====================================================
async def update_child(
    db: AsyncSession,
    parent: Parent,
    data: ChildUpdate,
):

    result = await db.execute(
        select(Child).where(
            Child.parent_id == parent.id,
            Child.is_deleted == False,
        )
    )

    child = result.scalar_one_or_none()

    if not child:
        raise HTTPException(
            status_code=404,
            detail="Child not found"
        )

    # =========================
    # PATCH FIELDS
    # =========================
    if data.name is not None:
        child.name = data.name.strip()

    if data.age is not None:
        child.age = data.age

    if data.guardian_name is not None:
        child.guardian_name = data.guardian_name.strip()

    if data.interests is not None:
        child.interests = data.interests

    if data.keywords_filter is not None:
        child.keywords_filter = data.keywords_filter

    if data.focus_topics is not None:
        child.focus_topics = data.focus_topics

    child.updated_at = datetime.utcnow()

    try:
        await db.commit()
        await db.refresh(child)

    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Failed to update child"
        )

    # =========================
    # CACHE INVALIDATION
    # =========================
    await CacheService.delete(
        f"child:{child.id}"
    )

    await CacheService.delete(
        f"analytics:{child.id}"
    )

    return child