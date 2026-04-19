from fastapi import Header, HTTPException
from app.core.config import settings


async def verify_admin_internal(
    x_admin_secret: str | None = Header(default=None),
):
    """
    Internal protection layer.

    Prevents public access even if
    admin token leaks.
    """

    if not x_admin_secret:
        raise HTTPException(
            status_code=401,
            detail="Missing admin internal secret",
        )

    if x_admin_secret != settings.ADMIN_INTERNAL_SECRET:
        raise HTTPException(
            status_code=403,
            detail="Admin internal access denied",
        )

    return True