"""MQTT broker auth/ACL endpoints consumed by EMQX's HTTP auth plugin.

EMQX configuration (set these in your EMQX Cloud dashboard):
  Authentication → HTTP Server
    URL:     https://your-api.example.com/internal/mqtt/auth
    Method:  POST
    Headers: X-Mqtt-Auth-Secret: <value of MQTT_AUTH_SECRET env var>

  Authorization → HTTP Server
    URL:     https://your-api.example.com/internal/mqtt/acl
    Method:  POST
    Headers: X-Mqtt-Auth-Secret: <value of MQTT_AUTH_SECRET env var>

These endpoints are NOT protected by the standard internal secret because
they ARE the authentication mechanism — they carry their own shared secret.
"""

import hmac
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.database import get_db
from app.auth.toy_key_validator import resolve_toy_by_key
from fastapi import Depends

logger = logging.getLogger("mqtt_auth")

router = APIRouter(
    prefix="/internal/mqtt",
    tags=["MQTT Auth"],
    include_in_schema=False,  # hide from public Swagger docs
)

# ─────────────────────────────────────────────────────────────
# SHARED SECRET GUARD
# ─────────────────────────────────────────────────────────────

def _verify_emqx_secret(request: Request) -> bool:
    """Verify the shared secret that EMQX sends on every auth/ACL call.

    EMQX Cloud (production): secret is set via the dashboard as a custom
      request header (X-Mqtt-Auth-Secret). This is the canonical path.

    Local EMQX via Docker (dev): EMQX 5.x env-var parsing can't represent
      hyphenated header names, so the header is silently dropped.  As a
      fallback, the secret may be passed as the URL query parameter
      `_emqx_secret` — set in the EMQX_AUTHENTICATION/AUTHORIZATION URL
      env vars in docker-compose.dev.yml.
    """
    expected = settings.MQTT_AUTH_SECRET
    if not expected:
        logger.error("MQTT_AUTH_SECRET is not set — rejecting all EMQX auth requests")
        return False
    incoming = (
        request.headers.get("x-mqtt-auth-secret", "")
        or request.query_params.get("_emqx_secret", "")
    )
    return hmac.compare_digest(incoming, expected)


def _deny():
    return JSONResponse({"result": "deny"}, status_code=200)


def _allow():
    return JSONResponse({"result": "allow"}, status_code=200)


# ─────────────────────────────────────────────────────────────
# POST /internal/mqtt/auth
# Called by EMQX for every new MQTT client connection.
# ─────────────────────────────────────────────────────────────

@router.post("/auth")
async def mqtt_auth(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if not _verify_emqx_secret(request):
        logger.warning("MQTT auth request with invalid or missing secret")
        return _deny()

    try:
        body = await request.json()
    except Exception:
        return _deny()

    username = body.get("username", "").strip().upper()
    password = body.get("password", "").strip()
    client_id = body.get("clientid", "")

    if not username or not password:
        return _deny()

    # The gateway service account authenticates via EMQX's built-in user DB,
    # not this endpoint. If it somehow ends up here, reject — the static user
    # is not in our api_keys table.
    if username.lower() == settings.MQTT_GATEWAY_CLIENT_ID.lower():
        logger.warning("Gateway client unexpectedly hitting HTTP auth — check EMQX config")
        return _deny()

    try:
        toy = await resolve_toy_by_key(password, db)
    except Exception:
        logger.warning(f"MQTT auth failed for device {username!r} (client={client_id!r})")
        return _deny()

    # Cross-check: the API key must belong to the device claiming this username.
    if toy.factory_device_id.upper() != username:
        logger.warning(
            f"MQTT auth key/device mismatch: "
            f"key owner={toy.factory_device_id!r}, claimed={username!r}"
        )
        return _deny()

    logger.info(f"MQTT auth OK | device={username} | client={client_id!r}")

    # Return inline ACL so EMQX enforces topic isolation without a second call.
    return JSONResponse({
        "result": "allow",
        "acl": [
            {
                "permission": "allow",
                "action": "publish",
                "topic": f"boboloo/toy/{username}/audio/in",
            },
            {
                "permission": "allow",
                "action": "publish",
                "topic": f"boboloo/toy/{username}/status",
            },
            {
                "permission": "allow",
                "action": "subscribe",
                "topic": f"boboloo/toy/{username}/audio/out",
            },
            {
                "permission": "allow",
                "action": "subscribe",
                "topic": f"boboloo/toy/{username}/cmd",
            },
            # Catch-all deny: device cannot touch any other topic.
            {
                "permission": "deny",
                "action": "all",
                "topic": "#",
            },
        ],
    }, status_code=200)


# ─────────────────────────────────────────────────────────────
# POST /internal/mqtt/acl
# Called by EMQX for each pub/sub action (fallback ACL check).
# Used if EMQX is configured to do a separate authz call rather
# than relying on the inline ACL returned by /auth above.
# ─────────────────────────────────────────────────────────────

@router.post("/acl")
async def mqtt_acl(request: Request):
    if not _verify_emqx_secret(request):
        return _deny()

    try:
        body = await request.json()
    except Exception:
        return _deny()

    username = body.get("username", "").strip().upper()
    topic: str = body.get("topic", "")
    action: str = body.get("action", "").lower()  # "publish" or "subscribe"

    # Gateway service account has full wildcard access.
    if username.lower() == settings.MQTT_GATEWAY_CLIENT_ID.lower():
        return _allow()

    # Per-device ACL: a device may only interact with its own subtree.
    allowed_pub = {
        f"boboloo/toy/{username}/audio/in",
        f"boboloo/toy/{username}/status",
    }
    allowed_sub = {
        f"boboloo/toy/{username}/audio/out",
        f"boboloo/toy/{username}/cmd",
    }

    if action == "publish" and topic in allowed_pub:
        return _allow()
    if action == "subscribe" and topic in allowed_sub:
        return _allow()

    logger.warning(
        f"MQTT ACL denied | device={username} | action={action} | topic={topic!r}"
    )
    return _deny()
