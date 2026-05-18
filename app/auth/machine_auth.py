import logging
from fastapi import Header, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_db
from app.database.models import Toy
from app.auth.toy_key_validator import resolve_toy_by_key

logger = logging.getLogger("machine_auth")


async def verify_toy(
    x_toy_key: str = Header(...),
    db: AsyncSession = Depends(get_db),
) -> Toy:

    if not x_toy_key:
        raise HTTPException(401, "Missing toy key")

    return await resolve_toy_by_key(x_toy_key, db)
