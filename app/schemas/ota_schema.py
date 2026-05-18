from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────
# FIRMWARE RELEASE REGISTRATION
# ─────────────────────────────────────────────────────────────

class FirmwareReleaseCreate(BaseModel):
    version: str = Field(..., min_length=1, max_length=32,
                         description="Semantic version, e.g. '1.2.3'")
    s3_key: str = Field(..., min_length=1, max_length=512,
                        description="S3 object key of the signed binary")
    sha256: str = Field(..., min_length=64, max_length=64,
                        description="Hex SHA256 of the signed binary (64 chars)")
    file_size: int | None = Field(None, ge=0, description="File size in bytes")
    is_stable: bool = Field(False, description="Mark as stable for production rollout")
    release_notes: str | None = None


class FirmwareReleaseResponse(BaseModel):
    id: UUID
    version: str
    s3_key: str
    sha256: str
    file_size: int | None
    is_stable: bool
    release_notes: str | None
    created_at: datetime
    created_by: str | None

    class Config:
        from_attributes = True


# ─────────────────────────────────────────────────────────────
# OTA PUSH REQUEST
# ─────────────────────────────────────────────────────────────

class OTAPushSingle(BaseModel):
    """Push OTA to one specific toy."""
    target: Literal["single"]
    device_id: str = Field(..., description="factory_device_id of the target toy")
    version: str = Field(..., description="Firmware version to deploy")


class OTAPushBatch(BaseModel):
    """Push OTA to all toys currently running a specific firmware version."""
    target: Literal["batch"]
    from_version: str = Field(..., description="Only update toys on this firmware version")
    version: str = Field(..., description="Firmware version to deploy")


OTAPushRequest = OTAPushSingle | OTAPushBatch


# ─────────────────────────────────────────────────────────────
# OTA PUSH RESPONSE
# ─────────────────────────────────────────────────────────────

class OTAPushResult(BaseModel):
    device_id: str
    status: Literal["queued", "skipped", "error"]
    reason: str | None = None


class OTAPushResponse(BaseModel):
    version: str
    results: list[OTAPushResult]
    queued: int
    skipped: int
    errors: int
