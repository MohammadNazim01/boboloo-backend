import hmac

from fastapi import Header, HTTPException
from app.core.config import settings


async def verify_internal(
    x_internal_secret: str = Header(...),
):
    if not hmac.compare_digest(x_internal_secret, settings.INTERNAL_CRON_SECRET):
        raise HTTPException(
            status_code=401,
            detail="Invalid internal secret",
        )

    return True
