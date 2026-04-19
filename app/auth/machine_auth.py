import hashlib
from fastapi import Header, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.database import get_db
from app.database.models import APIKey, Toy, ToyStatus


async def verify_toy(
    x_toy_key: str = Header(...),
    db: AsyncSession = Depends(get_db),
) -> Toy:

    if not x_toy_key:
        raise HTTPException(status_code=401, detail="Missing toy key")

    # Clean header input
    x_toy_key = x_toy_key.strip()

    # Basic format validation
    if len(x_toy_key) < 20:
        raise HTTPException(status_code=401, detail="Invalid toy key format")

    # Hash key
    key_hash = hashlib.sha256(x_toy_key.encode()).hexdigest()

    # Check API key exists & not revoked
    result = await db.execute(
        select(APIKey).where(
            APIKey.key_hash == key_hash,
            APIKey.revoked == False,
        )
    )

    api_key = result.scalars().first()

    if not api_key:
        raise HTTPException(status_code=401, detail="Invalid toy key")

    # Fetch associated toy
    toy = await db.get(Toy, api_key.toy_id)

    if not toy:
        raise HTTPException(status_code=404, detail="Toy not found")

    # Enforce lifecycle
    if toy.status != ToyStatus.ACTIVE:
        raise HTTPException(status_code=403, detail="Toy not active")

    if not toy.is_active:
        raise HTTPException(status_code=403, detail="Toy disabled")

    return toy
