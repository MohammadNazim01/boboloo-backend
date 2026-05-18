from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database.models import (
    Parent,
    Child,
    Toy,
    Conversation,
    Message,
    ChildAnalytics,
    AuditLog,
)

from app.schemas.admin_schema import (
    AdminAnalyticsResponse,
    AdminParentResponse,
    AdminToyResponse,
    AdminConversationResponse,
    AdminMessage,
)


# =====================================================
# DASHBOARD STATS
# =====================================================
async def dashboard_stats(db: AsyncSession):

    parents = await db.scalar(
        select(func.count()).select_from(Parent)
    )

    children = await db.scalar(
        select(func.count()).select_from(Child)
    )

    toys = await db.scalar(
        select(func.count()).select_from(Toy)
    )

    active_toys = await db.scalar(
        select(func.count())
        .select_from(Toy)
        .where(Toy.is_active == True)
    )

    # ✅ admin audit log
    db.add(
        AuditLog(
            action="ADMIN_VIEW_DASHBOARD"
        )
    )
    await db.commit()

    return {
        "parents": parents or 0,
        "children": children or 0,
        "toys": toys or 0,
        "active_toys": active_toys or 0,
    }


# =====================================================
# PAGINATED PARENTS
# =====================================================
async def get_parents(
    db: AsyncSession,
    page: int,
    limit: int,
):

    offset = (page - 1) * limit

    result = await db.execute(
        select(Parent)
        .order_by(Parent.id.desc())
        .offset(offset)
        .limit(limit)
    )

    parents = result.scalars().all()

    return [
        AdminParentResponse(
            id=p.id,
            email=p.email,
            name=p.name,
        )
        for p in parents
    ]


# =====================================================
# TOYS LIST
# =====================================================
async def get_toys(db: AsyncSession):

    result = await db.execute(
        select(Toy)
        .order_by(Toy.claimed_at.desc())
    )

    toys = result.scalars().all()

    return [
        AdminToyResponse(
            id=t.id,
            toy_uuid=t.toy_uuid,
            status=t.status.value,
            last_seen=t.last_seen,
        )
        for t in toys
    ]


# =====================================================
# CHILD CONVERSATIONS (SAFE)
# =====================================================
async def get_conversations(
    db: AsyncSession,
    child_id,
    limit,
):

    conv_result = await db.execute(
        select(Conversation)
        .where(Conversation.child_id == child_id)
        .order_by(Conversation.started_at.desc())
        .limit(limit)
    )

    conversations = conv_result.scalars().all()

    output = []

    for conv in conversations:

        msg_result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )

        msgs = msg_result.scalars().all()

        output.append(
            AdminConversationResponse(
                conversation_id=conv.id,
                messages=[
                    AdminMessage(
                        role=m.role,
                        content=m.content,
                        created_at=m.created_at,
                    )
                    for m in msgs
                ],
            )
        )

    return output


# =====================================================
# CHILD ANALYTICS
# =====================================================
async def get_child_analytics(
    db: AsyncSession,
    child_id,
):

    result = await db.execute(
        select(ChildAnalytics)
        .where(ChildAnalytics.child_id == child_id)
    )

    analytics = result.scalars().first()

    if not analytics:
        return {"message": "No analytics found"}

    return AdminAnalyticsResponse.model_validate(
    analytics,
    from_attributes=True,
)