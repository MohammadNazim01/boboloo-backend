"""OTA Service — firmware release management and push logic.

Flow for pushing OTA to a toy:
  1. Admin registers a FirmwareRelease (version, s3_key, sha256, file_size)
  2. Admin calls push_ota(device_id, version)
  3. Service fetches the release, generates a 30-minute S3 pre-signed URL
  4. Service builds the MQTT OTA command: {type, version, url, sha256, size}
  5. Service pushes the command to OutboundQueue
  6. MQTT Gateway reads from OutboundQueue and publishes to boboloo/toy/{id}/cmd
  7. ESP32 receives the command, downloads, verifies SHA256, flashes, reboots
  8. Toy reports result via boboloo/toy/{id}/status → handle_toy_status updates DB
"""

import hashlib
import hmac
import logging
import json
from typing import Optional

import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.job_queue import OutboundQueue
from app.database.models import FirmwareRelease, Toy, ToyStatus
from app.schemas.ota_schema import (
    FirmwareReleaseCreate,
    OTAPushResult,
    OTAPushResponse,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# S3 CLIENT (lazy singleton)
# ─────────────────────────────────────────────────────────────

_s3_client = None


def _get_s3():
    global _s3_client
    if _s3_client is None:
        kwargs = {"region_name": settings.AWS_REGION}
        if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
            kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
            kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
        _s3_client = boto3.client("s3", **kwargs)
    return _s3_client


# ─────────────────────────────────────────────────────────────
# RELEASE MANAGEMENT
# ─────────────────────────────────────────────────────────────

async def register_release(
    db: AsyncSession,
    payload: FirmwareReleaseCreate,
    created_by: str | None = None,
) -> FirmwareRelease:
    """Register a new firmware release.

    The signed binary must already be uploaded to S3 at payload.s3_key.
    sha256 must be the hex digest of the signed binary — computed by the
    signing pipeline and passed in by the admin.
    """
    # Enforce: sha256 must be 64 lowercase hex chars
    if not all(c in "0123456789abcdef" for c in payload.sha256.lower()):
        raise HTTPException(400, "sha256 must be 64 lowercase hex characters")

    existing = await db.execute(
        select(FirmwareRelease).where(FirmwareRelease.version == payload.version)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"Firmware version '{payload.version}' already registered")

    # Verify the S3 object exists before registering
    if settings.S3_FIRMWARE_BUCKET:
        _assert_s3_object_exists(payload.s3_key)

    release = FirmwareRelease(
        version=payload.version,
        s3_key=payload.s3_key,
        sha256=payload.sha256.lower(),
        file_size=payload.file_size,
        is_stable=payload.is_stable,
        release_notes=payload.release_notes,
        created_by=created_by,
    )
    db.add(release)
    await db.commit()
    await db.refresh(release)

    logger.info(f"Firmware registered | version={payload.version} stable={payload.is_stable}")
    return release


async def list_releases(db: AsyncSession) -> list[FirmwareRelease]:
    result = await db.execute(
        select(FirmwareRelease).order_by(FirmwareRelease.created_at.desc())
    )
    return list(result.scalars().all())


async def get_release(db: AsyncSession, version: str) -> FirmwareRelease:
    result = await db.execute(
        select(FirmwareRelease).where(FirmwareRelease.version == version)
    )
    release = result.scalar_one_or_none()
    if not release:
        raise HTTPException(404, f"Firmware version '{version}' not found")
    return release


async def mark_stable(db: AsyncSession, version: str) -> FirmwareRelease:
    release = await get_release(db, version)
    release.is_stable = True
    await db.commit()
    await db.refresh(release)
    logger.info(f"Firmware marked stable | version={version}")
    return release


# ─────────────────────────────────────────────────────────────
# S3 PRE-SIGNED URL
# ─────────────────────────────────────────────────────────────

def _assert_s3_object_exists(s3_key: str):
    try:
        _get_s3().head_object(Bucket=settings.S3_FIRMWARE_BUCKET, Key=s3_key)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("404", "NoSuchKey"):
            raise HTTPException(
                400,
                f"S3 object not found: s3://{settings.S3_FIRMWARE_BUCKET}/{s3_key}"
            )
        raise HTTPException(500, f"S3 error checking object: {e}")
    except NoCredentialsError:
        raise HTTPException(500, "AWS credentials not configured")


def _generate_presigned_url(s3_key: str) -> str:
    if not settings.S3_FIRMWARE_BUCKET:
        raise HTTPException(500, "S3_FIRMWARE_BUCKET is not configured")
    try:
        url = _get_s3().generate_presigned_url(
            "get_object",
            Params={
                "Bucket": settings.S3_FIRMWARE_BUCKET,
                "Key": s3_key,
            },
            ExpiresIn=settings.S3_PRESIGN_EXPIRY,
        )
        return url
    except NoCredentialsError:
        raise HTTPException(500, "AWS credentials not configured")
    except Exception as e:
        raise HTTPException(500, f"Failed to generate pre-signed URL: {e}")


# ─────────────────────────────────────────────────────────────
# OTA PUSH
# ─────────────────────────────────────────────────────────────

async def push_ota_single(
    db: AsyncSession,
    device_id: str,
    version: str,
) -> OTAPushResult:
    """Push an OTA command to a single toy identified by factory_device_id."""
    device_id = device_id.strip().upper()

    # Verify toy exists and is eligible
    toy_result = await db.execute(
        select(Toy).where(Toy.factory_device_id == device_id)
    )
    toy = toy_result.scalar_one_or_none()

    if not toy:
        return OTAPushResult(device_id=device_id, status="error", reason="toy_not_found")

    if toy.status != ToyStatus.ACTIVE or not toy.is_active:
        return OTAPushResult(device_id=device_id, status="skipped", reason="toy_not_active")

    # Skip if already on this version
    if toy.firmware_version and toy.firmware_version == version:
        return OTAPushResult(
            device_id=device_id,
            status="skipped",
            reason=f"already_on_{version}",
        )

    # Get release and generate pre-signed URL
    release = await get_release(db, version)
    url = _generate_presigned_url(release.s3_key)

    await _enqueue_ota_command(device_id, release, url)

    logger.info(f"OTA queued | device={device_id} version={version}")
    return OTAPushResult(device_id=device_id, status="queued")


async def push_ota_batch(
    db: AsyncSession,
    from_version: str,
    to_version: str,
) -> OTAPushResponse:
    """Push OTA to all ACTIVE toys currently on from_version."""
    release = await get_release(db, to_version)
    url = _generate_presigned_url(release.s3_key)

    toys_result = await db.execute(
        select(Toy).where(
            Toy.status == ToyStatus.ACTIVE,
            Toy.is_active == True,
            Toy.firmware_version == from_version,
        )
    )
    toys = list(toys_result.scalars().all())

    results: list[OTAPushResult] = []

    for toy in toys:
        try:
            await _enqueue_ota_command(toy.factory_device_id, release, url)
            results.append(OTAPushResult(device_id=toy.factory_device_id, status="queued"))
        except Exception as e:
            logger.error(f"OTA batch enqueue failed for {toy.factory_device_id}: {e}")
            results.append(
                OTAPushResult(
                    device_id=toy.factory_device_id,
                    status="error",
                    reason=str(e),
                )
            )

    logger.info(
        f"OTA batch | from={from_version} to={to_version} "
        f"queued={sum(1 for r in results if r.status=='queued')}"
    )

    return OTAPushResponse(
        version=to_version,
        results=results,
        queued=sum(1 for r in results if r.status == "queued"),
        skipped=sum(1 for r in results if r.status == "skipped"),
        errors=sum(1 for r in results if r.status == "error"),
    )


async def _enqueue_ota_command(device_id: str, release: FirmwareRelease, url: str):
    """Push an OTA MQTT command to the outbound_queue for the Gateway to publish."""
    command = json.dumps({
        "type": "ota",
        "version": release.version,
        "url": url,
        "sha256": release.sha256,
        "size": release.file_size or 0,
    })

    topic = f"boboloo/toy/{device_id}/cmd"
    await OutboundQueue.push(topic, command, qos=1)
