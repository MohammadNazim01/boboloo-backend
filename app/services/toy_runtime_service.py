from datetime import datetime, timezone, date
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException
import logging
import time
from app.database.models import (
ToyStatus,
Toy,
Child,
Conversation,
Message,
InteractionSettings,
)
from app.core.job_queue import JobQueue
from app.services.cache_service import CacheService
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

    
        payload = {
                "word_complexity": settings.word_complexity if settings else 3,
                "speech_speed": settings.speech_speed if settings else 2,
                "question_frequency": settings.question_frequency if settings else "balanced",
        }

        await CacheService.set_json(cache_key, payload, ttl=300)
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
            select(Child).where(Child.id == child_id)
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

        await CacheService.set_json(cache_key, payload, ttl=300)
        return payload

    # =====================================================
    # 🧸 HANDLE QUESTION
    # =====================================================
    @staticmethod
    async def handle_question(
        *,
        db: AsyncSession,
        toy: Toy,
        question: str,
        toy_id: str,
        battery_level: int | None = None,
        wifi_signal: int | None = None,
    ):

        if toy.status != ToyStatus.ACTIVE:
            raise HTTPException(403, "Toy not active")

        if not question or not question.strip():
            raise HTTPException(400, "Question required")

        question = question.strip()

        if len(question) > ToyRuntimeService.MAX_QUESTION_LENGTH:
            raise HTTPException(400, "Question too long")

        if not toy.active_child_id:
            raise HTTPException(400, "No active child set")

        # =========================
        # LOAD CHILD
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
                "Complete onboarding first"
            )

        # =========================
        # LOAD SETTINGS
        # =========================
        settings = await ToyRuntimeService.load_settings(
            db,
            toy.active_child_id
        )

        today = date.today()
        now = datetime.now(timezone.utc)


        start_time = time.perf_counter()

        try:
            # =========================
            # UPDATE TOY STATE
            # =========================
            toy.last_seen = now

            if battery_level is not None:
                toy.battery_level = battery_level

            if wifi_signal is not None:
                toy.wifi_signal = wifi_signal

            # =========================
            # GET / CREATE CONVERSATION
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
            # SAVE USER MESSAGE
            # =========================
            db.add(
                Message(
                    conversation_id=conversation.id,
                    role="user",
                    content=question,
                )
            )

            await JobQueue.push(
                "process_child_interaction",
                {
                    "toy_id": toy_id,
                    "child_id": str(toy.active_child_id),
                    "conversation_id": str(conversation.id),
                    "question": question,
                    "settings": settings,
                }
            )

            await db.commit()

            end_time = time.perf_counter()

            logger.info(
                f"⚡ Runtime Latency | "
                f"Toy={toy_id} | "
                f"{round(end_time - start_time, 2)} sec"
            )

            return {
                "conversation_id": conversation.id,
                "status": "processing",
            }


        except HTTPException:
            await db.rollback()
            raise

        except Exception as e:
            await db.rollback()
            logger.exception(f"Toy runtime failed: {e}")

            raise HTTPException(
                500,
                "Internal server error"
            )

    # =====================================================
    # ❤️ HEARTBEAT
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
            if (
                not toy.last_seen
                or (now - toy.last_seen).total_seconds()
                > ToyRuntimeService.HEARTBEAT_WRITE_INTERVAL
            ):
                toy.last_seen = now
                await db.commit()

            await redis_client.hset(
                f"toy:{toy.id}",
                mapping={
                    "online": 1,
                    "last_seen": now.isoformat(),
                }
            )

            await redis_client.expire(f"toy:{toy.id}", 120)

        except Exception as e:
            await db.rollback()
            logger.exception(f"Heartbeat failed: {e}")

            raise HTTPException(
                500,
                "Heartbeat failed"
            )

        return {"status": "alive"}

