from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.database import get_db
from app.auth.firebase_auth import get_current_parent
from app.database.models import Child, ChildAnalytics


async def analytics_ready_guard(
    parent = Depends(get_current_parent),
    db: AsyncSession = Depends(get_db),
):
    """
    Loads analytics once per request
    and reuses across endpoint.
    """

    result = await db.execute(
        select(Child).where(
            Child.parent_id == parent.id,
            Child.is_deleted == False,
        )
    )

    child = result.scalars().first()

    if not child:
        raise HTTPException(404, "Child not found")

    result = await db.execute(
        select(ChildAnalytics).where(
            ChildAnalytics.child_id == child.id
        )
    )

    analytics = result.scalars().first()

    if not analytics:
        raise HTTPException(
            status_code=404,
            detail="Analytics not ready yet"
        )

    return {
        "child": child,
        "analytics": analytics
    }