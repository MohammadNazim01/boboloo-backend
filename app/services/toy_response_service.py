from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from fastapi import HTTPException

from app.database.models import (
Conversation,
Message,
Toy,
)

class ToyResponseService:

    @staticmethod
    async def get_latest_answer(
        *,
        db: AsyncSession,
        toy: Toy,
        conversation_id,
    ):

        # =========================
        # VERIFY CONVERSATION
        # =========================
        conversation = await db.get(
            Conversation,
            conversation_id
        )

        if not conversation:
            raise HTTPException(
                404,
                "Conversation not found"
            )

        # security check
        if conversation.child_id != toy.active_child_id:
            raise HTTPException(
                403,
                "Unauthorized conversation"
            )

        # =========================
        # GET LATEST AI MESSAGE
        # =========================
        result = await db.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation.id,
                Message.role == "assistant",
            )
            .order_by(desc(Message.created_at))
            .limit(1)
        )

        message = result.scalar_one_or_none()

        # =========================
        # STILL PROCESSING
        # =========================
        if not message:
            return {
                "status": "processing",
                "answer": None,
            }

        # =========================
        # ANSWER READY
        # =========================
        return {
            "status": "completed",
            "answer": message.content,
            "created_at": message.created_at,
        }

