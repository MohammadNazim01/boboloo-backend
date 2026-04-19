from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database.database import get_db
from app.database.models import Parent
from app.core.firebase import verify_firebase_token

security = HTTPBearer()

async def get_current_parent(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
):

    try:
        decoded_token = verify_firebase_token(
            credentials.credentials
        )
    except Exception:
        raise HTTPException(401, "Invalid Firebase token")

    firebase_uid = decoded_token["uid"]
    email = decoded_token.get("email")

    result = await db.execute(
        select(Parent).where(
            Parent.firebase_uid == firebase_uid
        )
    )

    parent = result.scalars().first()
    if parent:
        return parent

    try:
        parent = Parent(
            firebase_uid=firebase_uid,
            email=email,
            name=email.split("@")[0] if email else None,
        )
        db.add(parent)
        await db.commit()
        await db.refresh(parent)
        return parent

    except IntegrityError:
        await db.rollback()
        result = await db.execute(
            select(Parent).where(
                Parent.firebase_uid == firebase_uid
            )
        )
        return result.scalars().first()