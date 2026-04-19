from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException
from datetime import datetime

from app.database.models import InteractionSettings
from app.schemas.interaction_settings_schema import (
    InteractionSettingsUpdate,
)
from app.services.cache_service import CacheService


# =====================================================
# GET SETTINGS
# =====================================================
async def get_interaction_settings(
    db: AsyncSession,
    child_id,
):

    result = await db.execute(
        select(InteractionSettings).where(
            InteractionSettings.child_id == child_id
        )
    )

    return result.scalar_one_or_none()


# =====================================================
# CREATE OR UPDATE SETTINGS
# =====================================================
async def update_interaction_settings(
    db: AsyncSession,
    child_id,
    payload: InteractionSettingsUpdate,
):

    result = await db.execute(
        select(InteractionSettings).where(
            InteractionSettings.child_id == child_id
        )
    )

    settings = result.scalar_one_or_none()

    # =========================
    # CREATE NEW
    # =========================
    if not settings:

        settings = InteractionSettings(
            child_id=child_id,
            smart_adapt_mode=payload.smart_adapt_mode,
            custom_tune=payload.custom_tune,
            word_complexity=payload.word_complexity,
            speech_speed=payload.speech_speed,
            new_words_per_session=payload.new_words_per_session,
            question_frequency=payload.question_frequency,
            topic_focus=payload.topic_focus,
            command_steps=payload.command_steps,
            patience_level=payload.patience_level,
            created_at=datetime.utcnow(),
        )

        db.add(settings)

    # =========================
    # UPDATE EXISTING
    # =========================
    else:

        settings.smart_adapt_mode = payload.smart_adapt_mode
        settings.custom_tune = payload.custom_tune
        settings.word_complexity = payload.word_complexity
        settings.speech_speed = payload.speech_speed
        settings.new_words_per_session = payload.new_words_per_session
        settings.question_frequency = payload.question_frequency
        settings.topic_focus = payload.topic_focus
        settings.command_steps = payload.command_steps
        settings.patience_level = payload.patience_level
        settings.updated_at = datetime.utcnow()

    try:
        await db.commit()
        await db.refresh(settings)

    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Failed to save interaction settings"
        )

    # =========================
    # CACHE INVALIDATION
    # =========================
    await CacheService.delete(
        f"settings:{child_id}"
    )

    return settings