"""OTA admin routes — firmware release registration and push.

All routes sit behind the existing admin double-auth:
  - X-Admin-Secret header (verify_admin_internal)
  - Firebase Bearer token with role=admin (get_current_admin)

Typical admin workflow:
  1. CI/CD builds + signs firmware, uploads to S3
  2. POST /sys/control/ota/releases  — register the release (version + s3_key + sha256)
  3. POST /sys/control/ota/releases/{version}/stable — mark it stable after QA
  4. POST /sys/control/ota/push — push to a single device or batch by from_version
  5. GET  /sys/control/ota/toys/{device_id}/status — check per-toy OTA status (Redis)
"""

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.admin_auth import get_current_admin
from app.auth.admin_internal import verify_admin_internal
from app.database.database import get_db
from app.core.redis import redis_client
from app.schemas.ota_schema import (
    FirmwareReleaseCreate,
    FirmwareReleaseResponse,
    OTAPushBatch,
    OTAPushRequest,
    OTAPushResponse,
    OTAPushResult,
    OTAPushSingle,
)
from app.services import ota_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/sys/control/ota",
    tags=["OTA"],
    include_in_schema=False,
    dependencies=[Depends(verify_admin_internal)],
)


# ─────────────────────────────────────────────────────────────
# FIRMWARE RELEASE MANAGEMENT
# ─────────────────────────────────────────────────────────────

@router.post("/releases", response_model=FirmwareReleaseResponse, status_code=201)
async def register_release(
    payload: FirmwareReleaseCreate,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """Register a firmware release.  The signed binary must already be in S3."""
    admin_uid = admin.get("uid", "unknown")
    return await ota_service.register_release(db, payload, created_by=admin_uid)


@router.get("/releases", response_model=list[FirmwareReleaseResponse])
async def list_releases(
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """List all registered firmware releases, newest first."""
    return await ota_service.list_releases(db)


@router.get("/releases/{version}", response_model=FirmwareReleaseResponse)
async def get_release(
    version: str,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    return await ota_service.get_release(db, version)


@router.post("/releases/{version}/stable", response_model=FirmwareReleaseResponse)
async def mark_stable(
    version: str,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """Mark a firmware version as stable (safe for production batch rollout)."""
    return await ota_service.mark_stable(db, version)


# ─────────────────────────────────────────────────────────────
# OTA PUSH
# ─────────────────────────────────────────────────────────────

@router.post("/push")
async def push_ota(
    request: OTAPushRequest,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """Push OTA to one toy (target=single) or a firmware-version batch (target=batch).

    The OTA command is published to the toy via MQTT Gateway within seconds.
    The toy downloads, verifies SHA256, flashes, and reboots automatically.
    """
    if isinstance(request, OTAPushSingle):
        result = await ota_service.push_ota_single(
            db=db,
            device_id=request.device_id,
            version=request.version,
        )
        return OTAPushResponse(
            version=request.version,
            results=[result],
            queued=1 if result.status == "queued" else 0,
            skipped=1 if result.status == "skipped" else 0,
            errors=1 if result.status == "error" else 0,
        )

    # Batch push
    return await ota_service.push_ota_batch(
        db=db,
        from_version=request.from_version,
        to_version=request.version,
    )


# ─────────────────────────────────────────────────────────────
# OTA STATUS (per-toy)
# ─────────────────────────────────────────────────────────────

@router.get("/toys/{device_id}/status")
async def toy_ota_status(
    device_id: str,
    admin=Depends(get_current_admin),
):
    """Return the latest OTA status for a toy from Redis (low latency, near-realtime)."""
    device_id = device_id.strip().upper()
    redis_key = f"toy:status:{device_id}"

    status = await redis_client.hgetall(redis_key)

    if not status:
        return {"device_id": device_id, "online": False, "ota_status": None}

    return {
        "device_id": device_id,
        "online": status.get("online") == "1",
        "last_seen": status.get("last_seen"),
        "firmware_version": status.get("firmware_version"),
        "ota_status": status.get("ota_status"),
        "battery_level": status.get("battery_level"),
        "wifi_signal": status.get("wifi_signal"),
    }
