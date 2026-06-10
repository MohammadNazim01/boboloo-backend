"""Worker job handlers.

Two execution paths converge here:

  MQTT Gateway path  → payload: {device_id, question}
    The gateway has no DB access; the worker resolves the toy, creates the
    conversation, saves the user message, then runs the shared AI core.

  HTTP runtime path  → payload: {toy_id, child_id, conversation_id, question, settings}
    ToyRuntimeService already resolved the toy, created the conversation, and
    saved the user message before enqueueing.  The worker only runs the AI core.

Both paths call _run_ai_interaction(), which loads history, calls OpenAI,
saves the assistant reply, and pushes the answer to the outbound_queue for
the MQTT Gateway to publish.
"""

import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select as sa_select

from app.database.database import AsyncSessionLocal
from app.database.models import (
    Child,
    Conversation,
    InteractionSettings,
    Message,
    Toy,
    ToyStatus,
)
from app.services.ai.ai_service import AIService
from app.core.job_queue import OutboundQueue
from app.core.redis import redis_client

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# PUBLIC HANDLERS (registered in worker.py HANDLERS dict)
# ─────────────────────────────────────────────────────────────

async def handle_interaction(data: dict):
    """Unified entry point for process_child_interaction jobs."""
    if "device_id" in data:
        await _handle_from_device(data)
    else:
        await _handle_from_payload(data)


async def handle_toy_status(data: dict):
    """Handle process_toy_status jobs pushed by the MQTT Gateway.

    Two concerns handled here:
      1. Redis presence update — always, low-cost, fast.
      2. DB firmware_version update — only on OTA status reports (ota_status field present).
         DB heartbeat writes are intentionally kept in the HTTP /heartbeat path only,
         to avoid a DB write on every MQTT status ping.
    """
    device_id: str = data.get("device_id", "")
    status_data: dict = data.get("data", {})

    if not device_id:
        return

    try:
        # ── 1. Update Redis presence hash ────────────────────────────────────
        mapping = {
            "online": "1",
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }
        for key in ("battery_level", "wifi_signal", "firmware_version", "ota_status"):
            if key in status_data:
                mapping[key] = str(status_data[key])

        redis_key = f"toy:status:{device_id}"
        await redis_client.hset(redis_key, mapping=mapping)
        await redis_client.expire(redis_key, 120)

        logger.debug(f"Status updated | device={device_id}")

    except Exception:
        logger.exception(f"handle_toy_status Redis error for device={device_id}")

    # ── 2. OTA result: persist new firmware_version to DB ───────────────────
    ota_status = status_data.get("ota_status")
    if not ota_status:
        return

    # Firmware sends "firmware_version" — "version" was a typo.
    new_version = status_data.get("firmware_version")

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                sa_select(Toy).where(Toy.factory_device_id == device_id)
            )
            toy = result.scalar_one_or_none()

            if not toy:
                logger.warning(f"OTA status report from unknown device: {device_id}")
                return

            if ota_status == "success" and new_version:
                toy.firmware_version = new_version
                await db.commit()
                logger.info(
                    f"OTA success | device={device_id} version={new_version}"
                )
            elif ota_status in ("failed", "rollback"):
                logger.warning(
                    f"OTA {ota_status} | device={device_id} "
                    f"reason={status_data.get('reason', 'unknown')}"
                )
            else:
                logger.debug(f"OTA status '{ota_status}' from {device_id}")

    except Exception:
        logger.exception(f"handle_toy_status DB error for device={device_id}")


# ─────────────────────────────────────────────────────────────
# MQTT GATEWAY PATH
# ─────────────────────────────────────────────────────────────

async def _handle_from_device(data: dict):
    """Resolve toy from device_id, set up conversation, then run AI core."""
    device_id: str = data["device_id"]
    question: str = data["question"]

    async with AsyncSessionLocal() as db:
        # ── Look up toy ──────────────────────────────────────────────────────
        result = await db.execute(
            select(Toy).where(Toy.factory_device_id == device_id)
        )
        toy = result.scalar_one_or_none()

        if not toy:
            logger.error(f"Toy not found for device_id={device_id!r}")
            return

        if toy.status != ToyStatus.ACTIVE or not toy.is_active:
            logger.warning(f"Toy {device_id} is not active — skipping interaction")
            return

        if not toy.active_child_id:
            logger.warning(f"Toy {device_id} has no active child — skipping")
            return

        # ── Load interaction settings ────────────────────────────────────────
        s_result = await db.execute(
            select(InteractionSettings).where(
                InteractionSettings.child_id == toy.active_child_id
            )
        )
        s = s_result.scalar_one_or_none()
        interaction_settings = {
            "word_complexity": s.word_complexity if s else 3,
            "speech_speed": s.speech_speed if s else 2,
            "question_frequency": s.question_frequency if s else "balanced",
        }

        # ── Get or create today's conversation ───────────────────────────────
        today = date.today()
        now = datetime.now(timezone.utc)

        conv_result = await db.execute(
            select(Conversation).where(
                Conversation.child_id == toy.active_child_id,
                Conversation.conversation_date == today,
            )
        )
        conversation = conv_result.scalar_one_or_none()

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

        # ── Save user message ────────────────────────────────────────────────
        db.add(Message(
            conversation_id=conversation.id,
            role="user",
            content=question,
        ))

        # Update toy last_seen (write-throttled via ORM; actual DB write on commit)
        toy.last_seen = now

        await db.commit()

        # ── Run AI core with the same session ────────────────────────────────
        await _run_ai_interaction(
            db=db,
            child_id=str(toy.active_child_id),
            conversation_id=str(conversation.id),
            question=question,
            device_id=device_id,
            interaction_settings=interaction_settings,
        )


# ─────────────────────────────────────────────────────────────
# HTTP RUNTIME PATH  (ToyRuntimeService already did the DB setup)
# ─────────────────────────────────────────────────────────────

async def _handle_from_payload(data: dict):
    """Run AI core using the pre-resolved IDs from ToyRuntimeService."""
    async with AsyncSessionLocal() as db:
        await _run_ai_interaction(
            db=db,
            child_id=data["child_id"],
            conversation_id=data["conversation_id"],
            question=data["question"],
            device_id=data["toy_id"],          # factory_device_id as stored by API
            interaction_settings=data["settings"],
        )


# ─────────────────────────────────────────────────────────────
# SHARED AI CORE
# ─────────────────────────────────────────────────────────────

async def _run_ai_interaction(
    *,
    db: AsyncSession,
    child_id: str,
    conversation_id: str,
    question: str,
    device_id: str,
    interaction_settings: dict,
):
    """Load history → call OpenAI → save reply → push to outbound_queue."""

    # ── Load child ───────────────────────────────────────────────────────────
    child = await db.get(Child, child_id)
    if not child:
        logger.error(f"Child {child_id!r} not found — aborting interaction")
        return

    # ── Load conversation ────────────────────────────────────────────────────
    conversation = await db.get(Conversation, conversation_id)
    if not conversation:
        logger.error(f"Conversation {conversation_id!r} not found — aborting")
        return

    # ── Load recent history (last 10 messages) ───────────────────────────────
    hist_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.asc())
    )
    all_messages = hist_result.scalars().all()

    history = [{"role": m.role.value, "content": m.content} for m in all_messages]
    history = history[-10:]

    # The last entry is the user message we just saved; exclude it from
    # the history list because it's passed separately as `question`.
    cleaned_history = history[:-1]

    # ── OpenAI call ──────────────────────────────────────────────────────────
    answer = await AIService.generate_child_reply(
        question=question,
        child_age=child.age,
        interests=child.interests or [],
        settings=interaction_settings,
        history=cleaned_history,
        conversation_id=conversation_id,
    )

    # ── Save assistant reply ─────────────────────────────────────────────────
    db.add(Message(
        conversation_id=conversation.id,
        role="assistant",
        content=answer,
    ))
    await db.commit()

    # ── Push reply to MQTT Gateway via outbound_queue ────────────────────────
    # QoS 0 (fire-and-forget): audio responses must not be buffered by the
    # broker. A toy that reconnects after being offline should not receive
    # hours-old AI answers out of context.
    topic = f"boboloo/toy/{device_id}/audio/out"
    await OutboundQueue.push(topic, answer, qos=0)

    logger.info(f"Interaction complete | device={device_id} | child={child_id}")
