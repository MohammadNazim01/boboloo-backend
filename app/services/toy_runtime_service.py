from datetime import datetime, timezone, date
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException
import logging

from app.database.models import (
    ToyStatus,
    Toy,
    Child,
    Conversation,
    Message,
    InteractionSettings,
)

from app.services.ai.ai_service import AIService
from app.services.cache_service import CacheService
from app.services.rate_limit_service import RateLimitService
from app.core.redis import redis_client


logger = logging.getLogger(__name__)


class ToyRuntimeService:

    MAX_QUESTION_LENGTH = 500
    HEARTBEAT_WRITE_INTERVAL = 60

    # =====================================================
    # LOAD INTERACTION SETTINGS (CACHE FIRST)
    # =====================================================
    @staticmethod
    async def load_settings(db: AsyncSession, child_id):

        cache_key = f"settings:{child_id}"

        cached = await CacheService.get_json(cache_key)

        if cached:
            return cached

        result = await db.execute(
            select(InteractionSettings).where(
                InteractionSettings.child_id == child_id
            )
        )

        settings = result.scalar_one_or_none()

        if not settings:
            payload = {
                "word_complexity": 3,
                "speech_speed": 2,
                "question_frequency": "balanced",
            }

            await CacheService.set_json(
                cache_key,
                payload,
                ttl=3600
            )

            return payload

        payload = {
            "word_complexity": settings.word_complexity,
            "speech_speed": settings.speech_speed,
            "question_frequency": settings.question_frequency,
        }

        await CacheService.set_json(
            cache_key,
            payload,
            ttl=3600
        )

        return payload

    # =====================================================
    # LOAD CHILD (CACHE FIRST)
    # =====================================================
    @staticmethod
    async def load_child(db: AsyncSession, child_id):

        cache_key = f"child:{child_id}"

        cached = await CacheService.get_json(cache_key)

        if cached:
            return cached

        result = await db.execute(
            select(Child).where(
                Child.id == child_id
            )
        )

        child = result.scalar_one_or_none()

        if not child:
            return None

        payload = {
            "id": str(child.id),
            "age": child.age,
            "interests": child.interests or [],
            "onboarding_completed": child.onboarding_completed,
        }

        await CacheService.set_json(
            cache_key,
            payload,
            ttl=3600
        )

        return payload

    # =====================================================
    # TOY ASK QUESTION
    # =====================================================
    @staticmethod
    async def handle_question(
        *,
        db: AsyncSession,
        toy: Toy,
        question: str,
        battery_level: int | None = None,
        wifi_signal: int | None = None,
    ):

        if toy.status != ToyStatus.ACTIVE:
            raise HTTPException(403, "Toy not active")

        # =========================
        # RATE LIMIT
        # =========================
        allowed = await RateLimitService.allow(
            key=f"toyask:{toy.id}",
            limit=20,
            window=60,
        )

        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Too many requests",
            )

        if not question:
            raise HTTPException(400, "Question required")

        question = question.strip()

        if not question:
            raise HTTPException(400, "Question required")

        if len(question) > ToyRuntimeService.MAX_QUESTION_LENGTH:
            raise HTTPException(400, "Question too long")

        if not toy.active_child_id:
            raise HTTPException(400, "No active child set")

        # =========================
        # FETCH CHILD (CACHE)
        # =========================
        child = await ToyRuntimeService.load_child(
            db,
            toy.active_child_id
        )

        if not child:
            raise HTTPException(404, "Child not found")

        if not child["onboarding_completed"]:
            raise HTTPException(
                403,
                "Complete onboarding before using toy"
            )

        # =========================
        # SETTINGS (CACHE)
        # =========================
        settings = await ToyRuntimeService.load_settings(
            db,
            toy.active_child_id
        )

        today = date.today()
        now = datetime.now(timezone.utc)

        try:

            # =========================
            # UPDATE TOY TELEMETRY
            # =========================
            toy.last_seen = now

            if battery_level is not None:
                toy.battery_level = battery_level

            if wifi_signal is not None:
                toy.wifi_signal = wifi_signal

            # =========================
            # DAILY CONVERSATION
            # =========================
            result = await db.execute(
                select(Conversation).where(
                    Conversation.child_id == toy.active_child_id,
                    Conversation.conversation_date == today,
                )
            )

            conversation = result.scalar_one_or_none()

            if not conversation:
                conversation = Conversation(
                    child_id=toy.active_child_id,
                    conversation_date=today,
                    started_at=now,
                    last_activity=now,
                )

                db.add(conversation)
                await db.flush()

            else:
                conversation.last_activity = now

            # =========================
            # USER MESSAGE
            # =========================
            db.add(
                Message(
                    conversation_id=conversation.id,
                    role="user",
                    content=question,
                )
            )

            await db.flush()

            # =========================
            # LAST 6 MESSAGES
            # =========================
            msg_result = await db.execute(
                select(Message)
                .where(
                    Message.conversation_id
                    == conversation.id
                )
                .order_by(Message.created_at.desc())
                .limit(6)
            )

            history = msg_result.scalars().all()

            history_messages = [
                {
                    "role": m.role,
                    "content": m.content
                }
                for m in reversed(history)
            ]

            # =========================
            # AI RESPONSE
            # =========================
            answer = await AIService.generate_child_reply(
                question=question,
                child_age=child["age"],
                interests=child["interests"],
                settings=settings,
                history=history_messages,
                conversation_id=str(conversation.id),
            )

            # =========================
            # ASSISTANT MESSAGE
            # =========================
            db.add(
                Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content=answer,
                )
            )

            await db.commit()

        except HTTPException:
            await db.rollback()
            raise

        except Exception as e:
            await db.rollback()

            logger.exception(
                f"Toy request failed: {e}"
            )

            raise HTTPException(
                status_code=500,
                detail="Failed to process toy request"
            )

        return {
            "conversation_id": conversation.id,
            "answer": answer,
        }

    # =====================================================
    # HEARTBEAT
    # =====================================================
    @staticmethod
    async def heartbeat(
        *,
        db: AsyncSession,
        toy: Toy,
    ):

        if toy.status != ToyStatus.ACTIVE:
            raise HTTPException(403, "Toy not active")

        now = datetime.now(timezone.utc)

        try:

            # DB write only occasionally
            if (
                not toy.last_seen
                or (
                    now - toy.last_seen
                ).total_seconds()
                > ToyRuntimeService.HEARTBEAT_WRITE_INTERVAL
            ):
                toy.last_seen = now
                await db.commit()

            # Redis live presence
            await redis_client.hset(
                f"toy:{toy.id}",
                mapping={
                    "online": 1,
                    "last_seen": now.isoformat(),
                }
            )

            await redis_client.expire(
                f"toy:{toy.id}",
                120
            )

        except Exception as e:
            await db.rollback()

            logger.exception(
                f"Heartbeat failed: {e}"
            )

            raise HTTPException(
                500,
                "Heartbeat update failed"
            )

        return {"status": "alive"}