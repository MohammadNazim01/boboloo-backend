from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.database.database import get_db

from app.schemas.interaction_settings_schema import (
    InteractionSettingsResponse,
    InteractionSettingsUpdate,
)

from app.services.interaction_settings_service import (
    get_interaction_settings,
    update_interaction_settings,
)


router = APIRouter(
    prefix="/api/v1/interaction-settings",
    tags=["Interaction Settings"],
)


# ==========================================
# DEFAULT SETTINGS HELPER
# ==========================================
def default_settings_payload():
    return InteractionSettingsUpdate(
        smart_adapt_mode=True,
        custom_tune=False,
        word_complexity=3,
        speech_speed=2,
        new_words_per_session=3,
        question_frequency="balanced",
        topic_focus=3,
        command_steps=2,
        patience_level=3,
    )


# ==========================================
# FETCH SETTINGS
# ==========================================
@router.get("/{child_id}", response_model=InteractionSettingsResponse)
async def fetch_settings(
    child_id: UUID,
    db: AsyncSession = Depends(get_db),
):

    settings = await get_interaction_settings(db, child_id)

    # auto-create if not exists
    if not settings:
        settings = await update_interaction_settings(
            db,
            child_id,
            default_settings_payload(),
        )

    return settings


# ==========================================
# UPDATE SETTINGS
# ==========================================
@router.put("/{child_id}", response_model=InteractionSettingsResponse)
async def update_settings(
    child_id: UUID,
    payload: InteractionSettingsUpdate,
    db: AsyncSession = Depends(get_db),
):

    settings = await update_interaction_settings(
        db,
        child_id,
        payload,
    )

    return settings
