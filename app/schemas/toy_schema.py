from pydantic import BaseModel, Field
from uuid import UUID
from typing import Optional


# =========================
# TOY CLAIM
# =========================

class ToyClaimRequest(BaseModel):
    factory_device_id: str = Field(
        ...,
        description="Factory printed device id from QR"
    )


class ToyClaimResponse(BaseModel):
    toy_uuid: UUID
    toy_api_key: str
    status: str


# =========================
# TOY ASK (RUNTIME)
# =========================

class ToyAskRequest(BaseModel):
    question: str = Field(..., description="Child question to toy")

    # optional telemetry
    battery_level: Optional[int] = Field(
        None,
        description="Battery percentage reported by toy"
    )

    wifi_signal: Optional[int] = Field(
        None,
        description="WiFi RSSI signal strength"
    )


class ToyAskResponse(BaseModel):
    conversation_id: UUID
    status: str
    answer: Optional[str] = None


# =========================
# TOY KEY ROTATION
# =========================

class ToyRotateResponse(BaseModel):
    toy_api_key: str
    status: str