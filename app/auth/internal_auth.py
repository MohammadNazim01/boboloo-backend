from fastapi import Header, HTTPException
from app.core.config import settings


async def verify_internal(
    x_internal_secret: str = Header(...),
):

    if x_internal_secret != settings.INTERNAL_CRON_SECRET:

        raise HTTPException(
            status_code=401,
            detail="Invalid internal secret",
        )

    return True