from pydantic import BaseModel


# =========================
# BASE SCHEMA
# =========================
class InteractionSettingsBase(BaseModel):

    smart_adapt_mode: bool
    custom_tune: bool

    word_complexity: int
    speech_speed: int

    new_words_per_session: int

    question_frequency: str

    topic_focus: int

    command_steps: int

    patience_level: int


# =========================
# UPDATE REQUEST
# =========================
class InteractionSettingsUpdate(InteractionSettingsBase):
    pass


# =========================
# RESPONSE
# =========================
class InteractionSettingsResponse(InteractionSettingsBase):

    class Config:
        from_attributes = True

